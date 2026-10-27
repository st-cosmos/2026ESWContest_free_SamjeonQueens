"""shelfcoap 코덱/서버 단위 테스트.

실행:  python -m unittest   (gateway/ 디렉터리에서; 외부 의존성 불필요)

노드 펌웨어(src/shelf_coap.c)가 실제로 만드는 것과 같은 바이트열을 손으로
만들어 파싱을 검증한다.
"""

import asyncio
import unittest

import shelfcoap
from shelfcoap import Message


NODE_ID = "1e0582a6f932714e"

# 노드의 LED 폴: CON GET /shelf/led?id=<hwid>, 토큰 8바이트
LED_POLL = bytes(
    [0x48, 0x01, 0x12, 0x34]            # ver1|CON|tkl8, GET, MID 0x1234
) + bytes(range(1, 9)) + (              # token 01..08
    bytes([0xB5]) + b"shelf" +          # URI_PATH(11) "shelf"  (delta 11, len 5)
    bytes([0x03]) + b"led" +            # URI_PATH     "led"    (delta 0,  len 3)
    bytes([0x4D, 19 - 13]) + f"id={NODE_ID}".encode()  # URI_QUERY(15), len 19
)

# 노드의 무게 보고: NON POST /shelf/weight + JSON 페이로드
REPORT_JSON = (b'{"id":"' + NODE_ID.encode() +
               b'","seq":12,"raw":1140384,"mg":11403840,"mv":4855}')
WEIGHT_REPORT = bytes(
    [0x58, 0x02, 0xAB, 0xCD]            # ver1|NON|tkl8, POST, MID 0xABCD
) + bytes(range(1, 9)) + (
    bytes([0xB5]) + b"shelf" +
    bytes([0x06]) + b"weight" +
    b"\xFF" + REPORT_JSON
)


class CodecTest(unittest.TestCase):
    def test_parse_led_poll(self):
        msg = shelfcoap.parse(LED_POLL)
        self.assertEqual(msg.type, shelfcoap.CON)
        self.assertEqual(msg.code, shelfcoap.GET)
        self.assertEqual(msg.mid, 0x1234)
        self.assertEqual(msg.token, bytes(range(1, 9)))
        self.assertEqual(msg.uri_path(), ["shelf", "led"])
        self.assertEqual(msg.uri_queries(), {"id": NODE_ID})
        self.assertEqual(msg.payload, b"")

    def test_parse_weight_report(self):
        msg = shelfcoap.parse(WEIGHT_REPORT)
        self.assertEqual(msg.type, shelfcoap.NON)
        self.assertEqual(msg.code, shelfcoap.POST)
        self.assertEqual(msg.uri_path(), ["shelf", "weight"])
        self.assertEqual(msg.payload, REPORT_JSON)

    def test_roundtrip(self):
        original = Message(
            type=shelfcoap.NON, code=shelfcoap.POST, mid=0xBEEF,
            token=b"\xAA\xBB",
            options=[(shelfcoap.OPTION_URI_PATH, b"shelf"),
                     (shelfcoap.OPTION_URI_PATH, b"weight")],
            payload=b'{"mg":1}')
        decoded = shelfcoap.parse(shelfcoap.encode(original))
        self.assertEqual(decoded, original)

    def test_option_extended_encodings(self):
        # 델타 269(=14+2바이트 확장의 경계), 큰 델타, 13..268 구간, 긴 값까지.
        original = Message(
            type=shelfcoap.CON, code=shelfcoap.GET, mid=1, token=b"t",
            options=[(11, b"a"), (24, b"b"), (280, b"bb"),
                     (66000, b"x" * 300)])
        decoded = shelfcoap.parse(shelfcoap.encode(original))
        self.assertEqual(decoded.options, original.options)

    def test_encode_sorts_options(self):
        msg = Message(type=shelfcoap.ACK, code=shelfcoap.CONTENT, mid=2,
                      token=b"t",
                      options=[(shelfcoap.OPTION_URI_QUERY, b"id=x"),
                               (shelfcoap.OPTION_URI_PATH, b"led")],
                      payload=b"1")
        decoded = shelfcoap.parse(shelfcoap.encode(msg))
        self.assertEqual([n for n, _ in decoded.options],
                         [shelfcoap.OPTION_URI_PATH,
                          shelfcoap.OPTION_URI_QUERY])
        self.assertEqual(decoded.payload, b"1")

    def test_malformed(self):
        for bad in (
            b"",                          # 빈 데이터그램
            b"\x48\x01\x12",              # 헤더 미달
            b"\x88\x01\x12\x34",          # CoAP 버전 2
            b"\x4C\x01\x12\x34\x01",      # tkl 12 > 8
            b"\x48\x01\x12\x34\x01\x02",  # 토큰 잘림
            b"\x40\x01\x12\x34\xF5a",     # 옵션 델타 니블 15 (예약)
            b"\x40\x01\x12\x34\xB5sh",    # 옵션 값 잘림
            b"\x40\x01\x12\x34\xFF",      # 페이로드 마커 뒤가 빔
        ):
            with self.assertRaises(ValueError, msg=repr(bad)):
                shelfcoap.parse(bad)


class FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class ServerTest(unittest.TestCase):
    """CoapServer 의 응답 규칙: CON -> piggyback ACK, NON -> 무응답, ping -> RST."""

    ADDR = ("fd11:22::1", 5683, 0, 0)

    def _serve(self, *datagrams, handler=None):
        async def default_handler(msg, addr):
            if msg.uri_path() == ["shelf", "led"]:
                return shelfcoap.CONTENT, b"1"
            return shelfcoap.NOT_FOUND, b""

        async def run():
            proto = shelfcoap.CoapServer(handler or default_handler)
            transport = FakeTransport()
            proto.connection_made(transport)
            for d in datagrams:
                proto.datagram_received(d, self.ADDR)
            while proto._tasks:
                await asyncio.gather(*list(proto._tasks))
            return transport.sent

        return asyncio.run(run())

    def test_con_request_gets_piggyback_ack(self):
        sent = self._serve(LED_POLL)
        self.assertEqual(len(sent), 1)
        data, addr = sent[0]
        self.assertEqual(addr, self.ADDR)
        rsp = shelfcoap.parse(data)
        self.assertEqual(rsp.type, shelfcoap.ACK)
        self.assertEqual(rsp.mid, 0x1234)              # 요청과 같은 MID
        self.assertEqual(rsp.token, bytes(range(1, 9)))  # 요청과 같은 토큰
        self.assertEqual(rsp.code, shelfcoap.CONTENT)
        self.assertEqual(rsp.payload, b"1")

    def test_non_request_gets_no_response(self):
        seen = []

        async def handler(msg, addr):
            seen.append(msg.uri_path())
            return shelfcoap.CHANGED, b""

        sent = self._serve(WEIGHT_REPORT, handler=handler)
        self.assertEqual(seen, [["shelf", "weight"]])   # 핸들러는 호출되고
        self.assertEqual(sent, [])                      # 응답은 없다

    def test_ping_gets_rst(self):
        sent = self._serve(bytes([0x40, 0x00, 0x77, 0x88]))
        rsp = shelfcoap.parse(sent[0][0])
        self.assertEqual(rsp.type, shelfcoap.RST)
        self.assertEqual(rsp.code, 0)
        self.assertEqual(rsp.mid, 0x7788)

    def test_garbage_is_ignored(self):
        sent = self._serve(b"", b"\x00\x01\x02", b"not coap at all")
        self.assertEqual(sent, [])

    def test_handler_crash_answers_5_00(self):
        async def handler(msg, addr):
            raise RuntimeError("boom")

        sent = self._serve(LED_POLL, handler=handler)
        rsp = shelfcoap.parse(sent[0][0])
        self.assertEqual(rsp.code, shelfcoap.INTERNAL_SERVER_ERROR)
        self.assertEqual(rsp.token, bytes(range(1, 9)))


class RequestTest(unittest.TestCase):
    """CON 클라이언트(request): 토큰 매칭, 재전송, 최종 타임아웃."""

    ADDR = ("fd11:22::2", 5683, 0, 0)

    @staticmethod
    def _endpoint():
        proto = shelfcoap.CoapServer(None)   # 요청 수신은 안 쓴다
        transport = FakeTransport()
        proto.connection_made(transport)
        return proto, transport

    @staticmethod
    def _mode_put():
        return Message(type=shelfcoap.CON, code=shelfcoap.PUT,
                       options=[(shelfcoap.OPTION_URI_PATH, b"shelf"),
                                (shelfcoap.OPTION_URI_PATH, b"mode")],
                       payload=b"active")

    def test_answered_first_try(self):
        async def run():
            proto, transport = self._endpoint()
            task = asyncio.ensure_future(
                proto.request(self._mode_put(), self.ADDR,
                              ack_timeout=0.5, retries=1))
            await asyncio.sleep(0.01)
            self.assertEqual(len(transport.sent), 1)

            sent = shelfcoap.parse(transport.sent[0][0])
            self.assertEqual(sent.type, shelfcoap.CON)
            self.assertEqual(len(sent.token), 8)         # 자동 생성 토큰
            ack = Message(type=shelfcoap.ACK, code=shelfcoap.CHANGED,
                          mid=sent.mid, token=sent.token)
            proto.datagram_received(shelfcoap.encode(ack), self.ADDR)

            rsp = await task
            self.assertEqual(rsp.code, shelfcoap.CHANGED)
            self.assertEqual(proto._pending, {})          # 대기 목록 정리됨
        asyncio.run(run())

    def test_retransmits_until_answered(self):
        async def run():
            proto, transport = self._endpoint()
            task = asyncio.ensure_future(
                proto.request(self._mode_put(), self.ADDR,
                              ack_timeout=0.05, retries=2))
            # 첫 전송은 무시하고 재전송을 기다린다.
            while len(transport.sent) < 2:
                await asyncio.sleep(0.01)
            first = shelfcoap.parse(transport.sent[0][0])
            second = shelfcoap.parse(transport.sent[1][0])
            self.assertEqual(first.mid, second.mid)       # 같은 MID 재전송
            self.assertEqual(first.token, second.token)

            ack = Message(type=shelfcoap.ACK, code=shelfcoap.CHANGED,
                          mid=second.mid, token=second.token)
            proto.datagram_received(shelfcoap.encode(ack), self.ADDR)
            rsp = await task
            self.assertEqual(rsp.code, shelfcoap.CHANGED)
        asyncio.run(run())

    def test_gives_up_after_retries(self):
        async def run():
            proto, transport = self._endpoint()
            with self.assertRaises(TimeoutError):
                await proto.request(self._mode_put(), self.ADDR,
                                    ack_timeout=0.02, retries=2)
            self.assertEqual(len(transport.sent), 3)      # 원 전송 + 재전송 2
            self.assertEqual(proto._pending, {})
        asyncio.run(run())

    def test_wrong_token_is_ignored(self):
        async def run():
            proto, transport = self._endpoint()
            task = asyncio.ensure_future(
                proto.request(self._mode_put(), self.ADDR,
                              ack_timeout=0.2, retries=0))
            await asyncio.sleep(0.01)
            sent = shelfcoap.parse(transport.sent[0][0])

            stray = Message(type=shelfcoap.ACK, code=shelfcoap.CHANGED,
                            mid=sent.mid, token=b"\x99" * 8)
            proto.datagram_received(shelfcoap.encode(stray), self.ADDR)
            with self.assertRaises(TimeoutError):
                await task
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
