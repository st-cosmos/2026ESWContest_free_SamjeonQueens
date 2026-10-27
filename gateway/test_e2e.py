"""게이트웨이 종단 스모크 테스트 — 실제 UDP 소켓 위에서 노드 트래픽을 재현한다.

웹 서버(HTTP 클라이언트)만 스텁으로 바꾸고, CoAP 서버·게이트웨이 로직·소켓
경로는 실제 코드 그대로 사용한다. 외부 의존성 불필요.

실행:  python -m unittest test_e2e -v
"""

import asyncio
import copy
import json
import socket
import sys
import unittest

import gateway
import shelfcoap
from shelfcoap import Message

# Windows 의 Proactor 루프는 (일부 파이썬 빌드에서) UDP 수신이 동작하지 않는다
# — 실측으로 확인. 게이트웨이 실환경은 파이(Linux)라 무관하지만, 개발 PC 에서도
# 테스트가 돌도록 Selector 루프를 쓴다. gateway.main() 도 같은 처리를 한다.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

HWID = "1e0582a6f932714e"


class FakeResponse:
    """aiohttp 응답 흉내: `async with session.get(...) as rsp` 용법만 지원."""

    def __init__(self, status=200, data=None):
        self.status = status
        self._data = data or {}

    async def json(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeHttp:
    """웹 서버 역할: LED/전력 모드 상태를 돌려주고, 무게 POST 를 기록한다."""

    def __init__(self, led_on=False, power_mode="active"):
        self.led_on = led_on
        self.power_mode = power_mode
        self.gets = []
        self.posts = []

    def get(self, url):
        self.gets.append(url)
        if url.endswith("/api/shelf-power"):
            return FakeResponse(data={"mode": self.power_mode, "ttl_s": 600})
        return FakeResponse(data={"on": self.led_on, "time": "12:00:00"})

    def post(self, url, json=None):
        self.posts.append((url, json))
        return FakeResponse(data={"status": "success"})


class ClientProtocol(asyncio.DatagramProtocol):
    """노드 역할의 UDP 클라이언트.

    응답은 inbox 큐에 원본 그대로 쌓고, 게이트웨이가 밀어넣는 CON 요청
    (PUT shelf/mode, PUT shelf/led)은 requests 큐에 넣은 뒤 펌웨어처럼
    2.04 piggyback ACK 로 답한다.
    """

    def __init__(self):
        self.inbox = asyncio.Queue()
        self.requests = asyncio.Queue()
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            msg = shelfcoap.parse(data)
        except ValueError:
            return
        if msg.is_request():
            self.requests.put_nowait(msg)
            if msg.type == shelfcoap.CON:
                ack = Message(type=shelfcoap.ACK, code=shelfcoap.CHANGED,
                              mid=msg.mid, token=msg.token)
                self.transport.sendto(shelfcoap.encode(ack), addr)
            return
        self.inbox.put_nowait(data)


def led_poll(mid, token):
    return shelfcoap.encode(Message(
        type=shelfcoap.CON, code=shelfcoap.GET, mid=mid, token=token,
        options=[(shelfcoap.OPTION_URI_PATH, b"shelf"),
                 (shelfcoap.OPTION_URI_PATH, b"led"),
                 (shelfcoap.OPTION_URI_QUERY, f"id={HWID}".encode())]))


def weight_report(mid, mg, mv):
    payload = json.dumps(
        {"id": HWID, "seq": 1, "raw": 842317, "mg": mg, "mv": mv}).encode()
    return shelfcoap.encode(Message(
        type=shelfcoap.NON, code=shelfcoap.POST, mid=mid, token=b"\x99" * 8,
        options=[(shelfcoap.OPTION_URI_PATH, b"shelf"),
                 (shelfcoap.OPTION_URI_PATH, b"weight")],
        payload=payload))


async def until(cond, what, timeout=2.0):
    """UDP 수신·백그라운드 태스크가 조건을 만족할 때까지 폴링 대기."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


class EndToEndTest(unittest.TestCase):
    def test_full_node_conversation(self):
        asyncio.run(self._run())

    async def _run(self):
        cfg = copy.deepcopy(gateway.DEFAULT_CONFIG)
        cfg["nodes"] = {HWID: "SHELF-A1"}
        gw = gateway.Gateway(cfg)
        gw.http = FakeHttp(led_on=True)

        loop = asyncio.get_running_loop()

        server_tr, server_proto = await loop.create_datagram_endpoint(
            lambda: shelfcoap.CoapServer(gw.coap_handler),
            local_addr=("::1", 0), family=socket.AF_INET6)
        server_addr = server_tr.get_extra_info("sockname")[:2]
        gw.coap = server_proto   # run() 이 하는 배선 — 푸시 경로에 필요

        client_tr, client = await loop.create_datagram_endpoint(
            ClientProtocol, local_addr=("::1", 0), family=socket.AF_INET6)

        try:
            # 1) 첫 LED 폴 — 캐시가 비어 있으니 "0", 서버 조회가 백그라운드로 뜬다.
            token = bytes(range(8))
            client_tr.sendto(led_poll(0x1111, token), server_addr)
            rsp = shelfcoap.parse(
                await asyncio.wait_for(client.inbox.get(), timeout=1.0))
            self.assertEqual(rsp.type, shelfcoap.ACK)
            self.assertEqual(rsp.mid, 0x1111)
            self.assertEqual(rsp.token, token)
            self.assertEqual(rsp.code, shelfcoap.CONTENT)
            self.assertEqual(rsp.payload, b"0")

            # 새 노드 등록이 서버 LED 조회를 즉시 띄운다 -> 캐시 갱신 대기
            await until(lambda: gw.nodes[HWID].led_known, "LED cache refresh")
            self.assertIn(f"{gw.base_url}/api/led/SHELF-A1", gw.http.gets)
            self.assertTrue(gw.nodes[HWID].led_on)

            # 2) 두 번째 폴 — 갱신된 캐시로 "1".
            client_tr.sendto(led_poll(0x2222, b"\x02" * 8), server_addr)
            rsp = shelfcoap.parse(
                await asyncio.wait_for(client.inbox.get(), timeout=1.0))
            self.assertEqual(rsp.payload, b"1")

            # 3) 무게 보고 (NON) — 응답은 없어야 하고, 웹 서버로 POST 가 나간다.
            client_tr.sendto(weight_report(0x3333, mg=8423499, mv=3750),
                             server_addr)
            await until(lambda: gw.http.posts, "weight POST")
            self.assertEqual(gw.http.posts, [(
                f"{gw.base_url}/api/weight/SHELF-A1",
                {"value": 8423, "battery": 50},
            )])
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(client.inbox.get(), timeout=0.2)

            # 4) USB 전원 보고 — battery 필드 없이 무게만 나간다.
            client_tr.sendto(weight_report(0x4444, mg=9000000, mv=4855),
                             server_addr)
            await until(lambda: len(gw.http.posts) == 2, "second weight POST")
            self.assertEqual(gw.http.posts[-1], (
                f"{gw.base_url}/api/weight/SHELF-A1",
                {"value": 9000},
            ))

            node = gw.nodes[HWID]
            self.assertEqual(node.reports, 2)
            self.assertEqual(node.polls, 2)
            self.assertEqual(node.battery, 50)  # USB 값이 마지막 배터리를 덮지 않음

            # 5) LED 변화 푸시 (ACTIVE) — 서버 값이 바뀌면 노드로 CON PUT.
            gw.http.led_on = False
            await gw._refresh_led(node)
            push = await asyncio.wait_for(client.requests.get(), timeout=2.0)
            self.assertEqual(push.uri_path(), ["shelf", "led"])
            self.assertEqual(push.payload, b"0")
            await until(lambda: node.led_pushed is False, "LED push ACK")

            # 6) 로그아웃 → 전 노드 슬립 푸시.
            gw._set_mode("idle")
            push = await asyncio.wait_for(client.requests.get(), timeout=2.0)
            self.assertEqual(push.uri_path(), ["shelf", "mode"])
            self.assertEqual(push.payload, b"idle")
            await until(lambda: node.mode_confirmed == "idle", "mode push ACK")

            # 7) 부팅한 노드의 모드 질의 — 현재 모드(idle)로 답한다.
            token = b"\x07" * 8
            client_tr.sendto(shelfcoap.encode(Message(
                type=shelfcoap.CON, code=shelfcoap.GET, mid=0x5555, token=token,
                options=[(shelfcoap.OPTION_URI_PATH, b"shelf"),
                         (shelfcoap.OPTION_URI_PATH, b"mode"),
                         (shelfcoap.OPTION_URI_QUERY, f"id={HWID}".encode())])),
                server_addr)
            rsp = shelfcoap.parse(
                await asyncio.wait_for(client.inbox.get(), timeout=1.0))
            self.assertEqual(rsp.token, token)
            self.assertEqual(rsp.code, shelfcoap.CONTENT)
            self.assertEqual(rsp.payload, b"idle")

            # 8) 웨이크를 놓친 노드 교정 — md:"i" 보고가 오면 (재시도 간격을
            #    지난 뒤) active 푸시가 나간다.
            gw._set_mode("active")
            push = await asyncio.wait_for(client.requests.get(), timeout=2.0)
            self.assertEqual(push.payload, b"active")     # 전환 즉시 푸시
            await until(lambda: node.mode_confirmed == "active",
                        "wake push ACK")
            # 웨이크 ACK 직후에는 LED 동기화 푸시가 따라온다 (설계 §4).
            follow = await asyncio.wait_for(client.requests.get(), timeout=2.0)
            self.assertEqual(follow.uri_path(), ["shelf", "led"])
            node.mode = "idle"                            # 놓친 노드로 위장
            node.mode_push_at = 0.0                       # 재시도 간격 해제
            client_tr.sendto(weight_report(0x6666, mg=9100000, mv=3800),
                             server_addr)
            push = await asyncio.wait_for(client.requests.get(), timeout=2.0)
            self.assertEqual(push.uri_path(), ["shelf", "mode"])
            self.assertEqual(push.payload, b"active")
        finally:
            client_tr.close()
            server_tr.close()


if __name__ == "__main__":
    unittest.main()
