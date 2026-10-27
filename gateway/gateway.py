#!/usr/bin/env python3
"""스마트 시약장 Thread 게이트웨이 — CoAP(메시) ↔ FastAPI 웹 서버(LAN) 브리지.

라즈베리파이에서 ot-daemon(wpan0) 옆에 상주하면서:

  * 노드의 무게 보고 (CoAP NON POST shelf/weight, JSON) 를 받아
    웹 서버의 `POST /api/weight/{device_id}` 로 전달한다 (mg -> g 환산,
    배터리 mV -> % 환산 포함).
  * 노드의 LED 폴링 (CoAP CON GET shelf/led?id=...) 에 캐시된 LED 상태로
    즉시 응답한다. 캐시는 백그라운드에서 `GET /api/led/{device_id}` 를
    1초 주기로 읽어 갱신하며, 이 폴링이 웹 서버 쪽 하트비트(mark_seen)
    역할도 겸한다 — 기존 ESP8266 이 1초 폴링하던 것과 같은 효과.
  * ff03::1 (realm-local all-nodes) 멀티캐스트에 가입해 노드의 게이트웨이
    탐색 요청을 받는다. 노드는 LED 폴에 응답한 유니캐스트 주소를 기억한다.
  * 전력 모드 브리지 (docs/power-modes.md): 웹 서버의 `GET /api/shelf-power`
    를 폴링해 앱 로그인 여부(active/idle)를 알아내고, 바뀌면 모든 노드에
    `PUT shelf/mode` 를 CON 으로 밀어넣는다. 노드 부팅 시의 `GET shelf/mode`
    에도 같은 값으로 답한다. 무게 보고의 `md` 필드로 노드의 실제 모드를
    보고, 어긋난 노드는 백오프를 두고 다시 푸시한다 (웨이크 놓침 자동 교정).
    ACTIVE 중 LED 상태가 바뀌면 해당 노드에 `PUT shelf/led` 를 즉시 푸시한다
    (노드의 700 ms 폴링을 대체 — 노드는 5 s 백업 폴만 남긴다).
    구버전 웹 서버(`/api/shelf-power` 404)에서는 active 로 간주해 절전 도입
    전과 동일하게 동작한다.

노드가 400 ms 안에 LED 응답을 받아야 하므로, CoAP 응답 경로에서는 절대
HTTP 를 기다리지 않는다 (항상 캐시 응답 + 비동기 갱신).

노드 쪽 프로토콜 규격: ../firmware/docs/build-and-flash.md §7
설정: gateway.toml (gateway.example.toml 참고)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import socket
import sys
import time
import tomllib
from pathlib import Path

import shelfcoap

log = logging.getLogger("gateway")

DEFAULT_CONFIG: dict = {
    "server": {
        "base_url": "http://127.0.0.1:8000",
        "timeout_s": 3.0,
    },
    "coap": {
        "bind": "::",
        "port": shelfcoap.COAP_PORT,
        "multicast_group": "ff03::1",
        "interface": "wpan0",
        "rejoin_interval_s": 10.0,
        "base_path": "shelf",  # 노드 CONFIG_SHELF_COAP_BASE_PATH 와 일치해야 함
    },
    "led": {
        "poll_interval_s": 1.0,
        # 이 시간 동안 CoAP 접촉이 없는 노드는 서버 폴링을 중단한다.
        # 서버의 온라인 판정 창(15초)과 맞춰 두면 죽은 노드가 웹에서
        # 온라인으로 남는 시간이 최소화된다.
        "node_ttl_s": 15.0,
        # IDLE 모드에서는 노드 접촉이 30 s 하트비트뿐이므로 폴링/유지 시간을
        # 그에 맞춰 늘린다 (LED 캐시 갱신 + 서버 하트비트 대행은 계속한다).
        "idle_poll_interval_s": 5.0,
        "idle_node_ttl_s": 90.0,
    },
    "power": {
        "poll_interval_s": 2.0,   # GET /api/shelf-power 주기 (로그인 감지 지연의 상한)
        "push_retry_s": 5.0,      # 모드 미확인/불일치 노드 재푸시 최소 간격
    },
    "status": {
        "bind": "0.0.0.0",
        "port": 8080,  # 0 = 상태 조회 HTTP 비활성
    },
    "nodes": {},  # 하드웨어 ID(16 hex) -> 웹 서버 선반 ID 매핑
}


def load_config(path: Path) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in DEFAULT_CONFIG.items()}
    with open(path, "rb") as f:
        user = tomllib.load(f)
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def battery_percent(mv: int | None) -> int | None:
    """노드가 보낸 VDDH 전압(mV)을 웹 서버의 battery(%) 로 환산한다.

    펌웨어의 `shelf battery` 명령과 같은 기준:
      * 음수/None = 측정 실패 -> None (서버 값 유지)
      * 4400 mV 초과 = USB 전원, Q5 가 배터리를 분리한 상태라 배터리 전압이
        아니다 -> None (서버 값 유지)
      * 그 외 = 1S Li-ion 선형 추정 (3.3 V 빈 것 .. 4.2 V 가득)
    """
    if mv is None or mv < 0:
        return None
    if mv > 4400:
        return None
    pct = round((mv - 3300) * 100 / (4200 - 3300))
    return max(0, min(100, pct))


def parse_report(payload: bytes) -> dict:
    """노드의 무게 보고 JSON 을 검증해 정규화한다. 형식 오류는 ValueError.

    예: {"id":"1e0582a6f932714e","seq":12,"raw":1140384,"mg":11403840,"mv":4855}
    """
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("payload is not a JSON object")

    hwid = data.get("id")
    if not isinstance(hwid, str) or not hwid:
        raise ValueError("missing node id")
    mg = data.get("mg")
    if not isinstance(mg, (int, float)) or isinstance(mg, bool):
        raise ValueError("missing weight (mg)")

    def _int_or_none(key):
        v = data.get(key)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    # 전력 모드 펌웨어가 붙이는 "md":"a"/"i". 없으면(구펌웨어) None.
    md = data.get("md")
    mode = {"a": "active", "i": "idle"}.get(md) if isinstance(md, str) else None

    return {
        "id": hwid,
        "mg": int(mg),
        "seq": _int_or_none("seq"),
        "raw": _int_or_none("raw"),
        "mv": _int_or_none("mv"),
        "mode": mode,
    }


class Node:
    """게이트웨이가 기억하는 노드 하나의 상태."""

    def __init__(self, hwid: str, device_id: str):
        self.hwid = hwid
        self.device_id = device_id
        self.addr: tuple | None = None
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.last_report: float | None = None
        self.seq: int | None = None
        self.raw: int | None = None
        self.mg: int | None = None
        self.grams: int | None = None
        self.mv: int | None = None
        self.battery: int | None = None
        self.led_on = False
        self.led_known = False   # 서버에서 LED 값을 한 번이라도 읽었는가
        self.http_ok: bool | None = None  # 서버 통신 상태 (전이 시에만 로그)
        self.reports = 0
        self.polls = 0
        # 전력 모드 (docs/power-modes.md)
        self.mode: str | None = None            # 노드가 md 필드로 보고한 실제 모드
        self.mode_confirmed: str | None = None  # 마지막으로 ACK 받은 푸시 값
        self.mode_push_at = 0.0                 # 마지막 푸시 시각 (재시도 간격 제한)
        self.mode_push_interval = 0.0           # 실패 백오프 (성공/모드 변경 시 리셋)
        self.led_pushed: bool | None = None     # 마지막으로 ACK 받은 LED 푸시 값

    def status(self) -> dict:
        now = time.time()
        return {
            "hwid": self.hwid,
            "device_id": self.device_id,
            "addr": self.addr[0] if self.addr else None,
            "grams": self.grams,
            "raw": self.raw,
            "mv": self.mv,
            "battery": self.battery,
            "seq": self.seq,
            "led_on": self.led_on,
            "reports": self.reports,
            "polls": self.polls,
            "last_seen_age_s": round(now - self.last_seen, 1),
            "last_report_age_s":
                round(now - self.last_report, 1) if self.last_report else None,
            "server_ok": self.http_ok,
            "mode": self.mode,
            "mode_confirmed": self.mode_confirmed,
        }


class Gateway:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.base_url: str = cfg["server"]["base_url"].rstrip("/")
        self.base_path: str = cfg["coap"]["base_path"]
        self.id_map: dict[str, str] = dict(cfg.get("nodes") or {})
        self.nodes: dict[str, Node] = {}
        self.http = None     # aiohttp.ClientSession, run() 에서 생성
        self.http_ws = None  # ws 전용 세션 (타임아웃 없음), run() 에서 생성
        self.coap: shelfcoap.CoapServer | None = None  # run() 에서 생성
        self.started = time.time()
        self._bg: set[asyncio.Task] = set()
        # 세션 모드. 서버에 물어보기 전(그리고 구버전 서버의 404)에는 active —
        # 절전 도입 전과 같은 안전측 기본값이다.
        self.mode = "active"
        self._power_404_logged = False

    # ------------------------------------------------------------- CoAP 쪽

    async def coap_handler(self, msg: shelfcoap.Message, addr):
        path = msg.uri_path()

        if path == [self.base_path, "weight"]:
            if msg.code != shelfcoap.POST:
                return shelfcoap.METHOD_NOT_ALLOWED, b""
            try:
                report = parse_report(msg.payload)
            except ValueError as e:
                log.warning("잘못된 무게 보고 (%s from %s): %s",
                            e, addr[0], msg.payload[:64])
                return shelfcoap.BAD_REQUEST, b""
            self._handle_report(report, addr)
            return shelfcoap.CHANGED, b""

        if path == [self.base_path, "led"]:
            if msg.code != shelfcoap.GET:
                return shelfcoap.METHOD_NOT_ALLOWED, b""
            hwid = msg.uri_queries().get("id", "")
            if not hwid:
                return shelfcoap.BAD_REQUEST, b""
            node = self._node_for(hwid, addr)
            node.polls += 1
            # 절대 여기서 HTTP 를 기다리지 않는다 — 노드 타임아웃이 400 ms 다.
            return shelfcoap.CONTENT, (b"1" if node.led_on else b"0")

        if path == [self.base_path, "mode"]:
            # 노드의 부팅 시 모드 동기화 질의 (docs/power-modes.md §4).
            if msg.code != shelfcoap.GET:
                return shelfcoap.METHOD_NOT_ALLOWED, b""
            hwid = msg.uri_queries().get("id", "")
            if not hwid:
                return shelfcoap.BAD_REQUEST, b""
            node = self._node_for(hwid, addr)
            # 이 질의는 부팅 직후에만 온다 = 리셋 버튼/전원 인가 신호.
            # 서버에 알리면 앱/웹 미등록 기기 목록에서 해당 기기가 블링크된다.
            self._spawn(self._post_reset(node))
            return shelfcoap.CONTENT, self.mode.encode()

        return shelfcoap.NOT_FOUND, b""

    def _node_for(self, hwid: str, addr) -> Node:
        node = self.nodes.get(hwid)
        if node is None:
            node = Node(hwid, self.id_map.get(hwid, hwid))
            self.nodes[hwid] = node
            log.info("새 노드 %s -> 선반 ID '%s' (%s)",
                     hwid, node.device_id, addr[0])
            # 첫 폴에는 기본값(꺼짐)으로 답하고, 진짜 값을 바로 당겨온다.
            self._spawn(self._refresh_led(node))
        node.last_seen = time.time()
        node.addr = addr
        return node

    def _handle_report(self, report: dict, addr):
        node = self._node_for(report["id"], addr)
        node.reports += 1
        node.last_report = time.time()
        # seq 역행 = 노드 재부팅 (부팅 mode 질의를 놓쳤을 때의 보조 감지)
        if (node.seq is not None and report["seq"] is not None
                and report["seq"] < node.seq):
            log.info("노드 %s('%s') seq 역행 (%s -> %s) — 재부팅으로 판단",
                     node.hwid, node.device_id, node.seq, report["seq"])
            self._spawn(self._post_reset(node))
        node.seq = report["seq"]
        node.raw = report["raw"]
        node.mg = report["mg"]
        node.mv = report["mv"]
        node.grams = round(report["mg"] / 1000)
        if report["mode"] is not None:
            node.mode = report["mode"]

        battery = battery_percent(node.mv)
        if battery is not None:
            node.battery = battery

        log.info("무게 보고 %s('%s') seq=%s raw=%s -> %s g, %s mV%s",
                 node.hwid, node.device_id, node.seq, node.raw, node.grams,
                 node.mv, f" (배터리 ~{battery}%)" if battery is not None else "")

        self._spawn(self._post_weight(node, node.grams, battery))
        # 웨이크/슬립을 놓친 노드는 여기서 잡힌다 (하트비트마다 md 가 온다).
        self._maybe_push_mode(node)

    # ------------------------------------------------------- 웹 서버 쪽

    async def _post_weight(self, node: Node, grams: int, battery: int | None):
        url = f"{self.base_url}/api/weight/{node.device_id}"
        body: dict = {"value": grams}
        if battery is not None:
            body["battery"] = battery
        try:
            async with self.http.post(url, json=body) as rsp:
                if rsp.status == 200:
                    self._server_state(node, True)
                else:
                    self._server_state(
                        node, False, f"POST {url} -> HTTP {rsp.status}")
        except Exception as e:  # aiohttp.ClientError, asyncio.TimeoutError, OSError...
            self._server_state(node, False, f"POST {url} 실패: {e!r}")

    async def _post_reset(self, node: Node):
        """노드 재부팅(리셋 버튼) 신호를 서버에 전달한다.

        서버는 이 신호로 recently_reset 을 켜고, 앱/웹의 미등록 기기
        목록에서 해당 기기를 블링크로 식별시킨다. 구버전 서버(404)는 무시."""
        url = f"{self.base_url}/api/device-reset/{node.device_id}"
        try:
            async with self.http.post(url) as rsp:
                if rsp.status == 200:
                    log.info("리셋 신호 전달 %s('%s')", node.hwid, node.device_id)
                else:
                    log.debug("리셋 신호 응답 HTTP %s ('%s')",
                              rsp.status, node.device_id)
        except Exception as e:
            log.debug("리셋 신호 전달 실패 ('%s'): %r", node.device_id, e)

    async def _refresh_led(self, node: Node):
        """서버의 LED 상태를 캐시로 당겨온다. 서버 쪽 하트비트(mark_seen)도 겸함."""
        url = f"{self.base_url}/api/led/{node.device_id}"
        try:
            async with self.http.get(url) as rsp:
                if rsp.status != 200:
                    self._server_state(
                        node, False, f"GET {url} -> HTTP {rsp.status}")
                    return
                data = await rsp.json()
        except Exception as e:  # aiohttp.ClientError, asyncio.TimeoutError, OSError...
            self._server_state(node, False, f"GET {url} 실패: {e!r}")
            return

        self._server_state(node, True)
        self._apply_led_state(node, bool(data.get("on")))

    def _apply_led_state(self, node: Node, want: bool):
        """서버발 LED 상태를 캐시에 반영하고, 바뀌었으면 ACTIVE 노드에는
        폴을 기다리게 하지 않고 즉시 밀어넣는다. (폴링과 ws 양쪽 공용)"""
        changed = node.led_known and want != node.led_on
        if not node.led_known or changed:
            log.info("LED %s('%s') -> %s",
                     node.hwid, node.device_id, "켜짐" if want else "꺼짐")
        node.led_on = want
        node.led_known = True

        if changed and self.mode == "active" and node.addr is not None:
            self._spawn(self._push_led(node))

    def _server_state(self, node: Node, ok: bool, detail: str = ""):
        """서버 통신 성공/실패를 전이 시에만 로그로 남긴다 (스팸 방지)."""
        if ok and node.http_ok is not True:
            if node.http_ok is False:
                log.info("웹 서버 통신 복구 ('%s')", node.device_id)
            node.http_ok = True
        elif not ok and node.http_ok is not False:
            log.warning("웹 서버 통신 실패 ('%s'): %s", node.device_id, detail)
            node.http_ok = False

    async def _led_poll_loop(self):
        while True:
            # IDLE 세션에서는 노드 접촉이 30 s 하트비트뿐이라 폴링을 늦추고
            # 유지 시간을 늘린다. 서버 하트비트 대행(mark_seen)은 계속된다.
            if self.mode == "active":
                interval = float(self.cfg["led"]["poll_interval_s"])
                ttl = float(self.cfg["led"]["node_ttl_s"])
            else:
                interval = float(self.cfg["led"]["idle_poll_interval_s"])
                ttl = float(self.cfg["led"]["idle_node_ttl_s"])
            now = time.time()
            active = [n for n in self.nodes.values()
                      if now - n.last_seen <= ttl]
            if active:
                # _refresh_led 는 자체적으로 예외를 삼키지만, 어떤 예외도
                # 이 루프를 죽여 LED 캐시를 영영 묵히게 둬서는 안 된다.
                await asyncio.gather(*(self._refresh_led(n) for n in active),
                                     return_exceptions=True)
            await asyncio.sleep(interval)

    # ------------------------------------------------------- 전력 모드 브리지

    def _alive_nodes(self) -> list[Node]:
        ttl = float(self.cfg["led"]["idle_node_ttl_s"])
        now = time.time()
        return [n for n in self.nodes.values()
                if n.addr is not None and now - n.last_seen <= ttl]

    def _set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.mode = mode
        log.info("전력 모드 전환: %s — 노드 %d대에 푸시",
                 mode, len(self._alive_nodes()))
        for node in self._alive_nodes():
            # 전환은 재시도 간격 제한 없이 즉시 나간다.
            node.mode_push_interval = 0.0
            node.mode_push_at = time.time()
            self._spawn(self._push_mode(node))

    def _maybe_push_mode(self, node: Node):
        """보고된 모드(md)가 세션 상태와 어긋난 노드를 교정한다 (백오프 포함).

        md 는 노드가 실제로 적용한 모드라 이것만이 근거다 — 푸시 ACK 여부
        (mode_confirmed)는 조건에 넣지 않는다. 이미 일치하는 노드에 확인
        푸시를 반복하면 IDLE 메시가 5 초마다 깨어나는 것과 다름없다.
        """
        if node.addr is None:
            return
        if node.mode is None or node.mode == self.mode:
            return
        retry = max(float(self.cfg["power"]["push_retry_s"]),
                    node.mode_push_interval)
        if time.time() - node.mode_push_at < retry:
            return
        node.mode_push_at = time.time()
        self._spawn(self._push_mode(node))

    async def _push_mode(self, node: Node):
        want = self.mode
        msg = shelfcoap.Message(
            type=shelfcoap.CON, code=shelfcoap.PUT,
            options=[(shelfcoap.OPTION_URI_PATH, self.base_path.encode()),
                     (shelfcoap.OPTION_URI_PATH, b"mode")],
            payload=want.encode())
        try:
            rsp = await self.coap.request(msg, node.addr)
        except (TimeoutError, RuntimeError) as e:
            # 구펌웨어(수신 소켓 없음)나 죽은 노드 — 백오프를 두 배로 (최대 120 s)
            node.mode_push_interval = min(
                max(node.mode_push_interval * 2,
                    float(self.cfg["power"]["push_retry_s"])), 120.0)
            log.debug("모드 푸시 무응답 %s('%s'): %s",
                      node.hwid, node.device_id, e)
            return
        node.mode_push_interval = 0.0
        if rsp.code == shelfcoap.CHANGED:
            node.mode_confirmed = want
            node.mode = want
            log.info("모드 푸시 %s('%s') -> %s (ACK)",
                     node.hwid, node.device_id, want)
            if want == "active":
                # 깨어난 노드에 현재 LED 상태를 바로 맞춰 준다.
                await self._push_led(node)
        else:
            log.warning("모드 푸시 %s('%s') 거부: %s",
                        node.hwid, node.device_id, shelfcoap.code_str(rsp.code))

    async def _push_led(self, node: Node):
        want = node.led_on
        msg = shelfcoap.Message(
            type=shelfcoap.CON, code=shelfcoap.PUT,
            options=[(shelfcoap.OPTION_URI_PATH, self.base_path.encode()),
                     (shelfcoap.OPTION_URI_PATH, b"led")],
            payload=(b"1" if want else b"0"))
        try:
            rsp = await self.coap.request(msg, node.addr)
        except (TimeoutError, RuntimeError) as e:
            log.debug("LED 푸시 무응답 %s('%s'): %s",
                      node.hwid, node.device_id, e)
            return
        if rsp.code == shelfcoap.CHANGED:
            node.led_pushed = want
            log.info("LED 푸시 %s('%s') -> %s (ACK)",
                     node.hwid, node.device_id, "켜짐" if want else "꺼짐")

    # ------------------------------------------------------- ws 구독 (빠른 경로)

    def _handle_ws_event(self, data: dict):
        """웹 서버 ws 이벤트 한 건을 처리한다.

        * shelf_power — 앱 로그인/로그아웃 전환. 백업 폴(2초)을 기다리지
          않고 즉시 전 노드에 모드를 푸시한다.
        * led_update — 웹/앱에서 LED 를 바꾼 것. ACTIVE 노드에 즉시 푸시.
        나머지 타입(weight_update 등, 브라우저용)은 무시한다.
        """
        kind = data.get("type")

        if kind == "shelf_power":
            mode = data.get("mode")
            if mode in ("active", "idle"):
                self._set_mode(mode)
            return

        if kind == "led_update":
            device_id = data.get("device_id")
            want = bool((data.get("data") or {}).get("on"))
            for node in self.nodes.values():
                if node.device_id == device_id:
                    self._apply_led_state(node, want)
                    return

    async def _ws_loop(self):
        """웹 서버의 /ws 를 구독한다 — 폴링은 그대로 두고 지연만 없앤다.

        서버가 없거나 구버전이면 백오프를 두고 재시도할 뿐, 동작은 백업
        폴링(_power_poll_loop/_led_poll_loop)이 이어간다. 재접속 직후의
        상태 불일치도 다음 백업 폴(≤2초)이 맞춘다.
        """
        import aiohttp

        url = self.base_url.replace("http", "ws", 1) + "/ws"
        backoff = 1.0
        was_ok: bool | None = None
        while True:
            try:
                # self.http 의 3초 total 타임아웃이 상시 연결을 끊지 않도록
                # ws 전용 세션을 쓴다 (run() 에서 생성).
                async with self.http_ws.ws_connect(url, heartbeat=30.0) as ws:
                    if was_ok is not True:
                        log.info("웹 서버 ws 구독: %s", url)
                    was_ok = True
                    backoff = 1.0
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            self._handle_ws_event(json.loads(msg.data))
                        except (ValueError, TypeError) as e:
                            log.debug("ws 이벤트 무시: %s (%s)", msg.data[:80], e)
            except Exception as e:
                if was_ok is not False:
                    log.warning("웹 서버 ws 연결 실패(%s) — 폴링으로 동작, "
                                "백오프 재시도: %r", url, e)
                was_ok = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _power_poll_loop(self):
        """웹 서버의 앱 세션 상태(active/idle)를 폴링해 모드를 따라간다.

        ws 구독(_ws_loop)이 빠른 경로이고, 이 폴은 ws 유실·TTL 만료·재접속
        직후의 불일치를 주기적으로 바로잡는 신뢰성 경로다."""
        url = f"{self.base_url}/api/shelf-power"
        interval = float(self.cfg["power"]["poll_interval_s"])
        while True:
            try:
                async with self.http.get(url) as rsp:
                    if rsp.status == 200:
                        data = await rsp.json()
                        mode = data.get("mode")
                        if mode in ("active", "idle"):
                            self._power_404_logged = False
                            self._set_mode(mode)
                    elif rsp.status == 404:
                        # 구버전 웹 서버 — 항상 active 로 동작 (절전 이전과 동일)
                        if not self._power_404_logged:
                            log.warning("서버에 /api/shelf-power 가 없어 "
                                        "항상 active 모드로 동작합니다")
                            self._power_404_logged = True
                        self._set_mode("active")
            except Exception:
                # 서버가 죽어 있으면 마지막 모드를 유지한다. 노드 쪽은 자체
                # failsafe(60 s)로 IDLE 에 떨어지므로 여기서 손댈 것 없다.
                pass
            await asyncio.sleep(interval)

    # ----------------------------------------------------- 멀티캐스트 유지

    async def _multicast_loop(self, sock: socket.socket):
        """ff03::1 가입을 유지한다. wpan0 은 ot-daemon 재시작 때 새로 만들어져
        기존 가입이 사라지므로 주기적으로 재가입해야 한다."""
        group = self.cfg["coap"]["multicast_group"]
        ifname = self.cfg["coap"]["interface"]
        interval = float(self.cfg["coap"]["rejoin_interval_s"])
        was_ok: bool | None = None
        while True:
            try:
                fresh = shelfcoap.join_multicast(sock, group, ifname)
                if fresh or was_ok is not True:
                    log.info("멀티캐스트 %s @ %s 가입", group, ifname)
                was_ok = True
            except OSError as e:
                if was_ok is not False:
                    log.warning("멀티캐스트 가입 실패 (%s @ %s): %s — "
                                "ot-daemon/wpan0 대기, %ss 마다 재시도",
                                group, ifname, e, interval)
                was_ok = False
            await asyncio.sleep(interval)

    # ------------------------------------------------------------ 상태 조회

    def status(self) -> dict:
        return {
            "uptime_s": round(time.time() - self.started, 1),
            "server": self.base_url,
            "interface": self.cfg["coap"]["interface"],
            "mode": self.mode,
            "nodes": [n.status() for n in self.nodes.values()],
        }

    async def _start_status_server(self):
        port = int(self.cfg["status"]["port"])
        if port <= 0:
            return None
        from aiohttp import web

        async def handle(_request):
            return web.json_response(self.status())

        app = web.Application()
        app.add_routes([web.get("/", handle), web.get("/status", handle)])
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self.cfg["status"]["bind"], port)
        await site.start()
        log.info("상태 조회 HTTP: http://%s:%d/status",
                 self.cfg["status"]["bind"], port)
        return runner

    # ---------------------------------------------------------------- 실행

    def _spawn(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def run(self):
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=float(self.cfg["server"]["timeout_s"]))
        self.http = aiohttp.ClientSession(timeout=timeout)
        # ws 상시 연결용 — total 타임아웃이 걸린 self.http 와 분리한다.
        self.http_ws = aiohttp.ClientSession()

        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: shelfcoap.CoapServer(self.coap_handler),
            local_addr=(self.cfg["coap"]["bind"], int(self.cfg["coap"]["port"])),
            family=socket.AF_INET6,
        )
        self.coap = protocol
        sock = transport.get_extra_info("socket")
        log.info("CoAP 서버 시작: [%s]:%s, 웹 서버: %s",
                 self.cfg["coap"]["bind"], self.cfg["coap"]["port"], self.base_url)
        if self.id_map:
            for hwid, device_id in self.id_map.items():
                log.info("노드 매핑: %s -> '%s'", hwid, device_id)

        status_runner = await self._start_status_server()

        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # Windows
                loop.add_signal_handler(sig, stop.set)

        workers = [
            loop.create_task(self._led_poll_loop()),
            loop.create_task(self._multicast_loop(sock)),
            loop.create_task(self._power_poll_loop()),
            loop.create_task(self._ws_loop()),
        ]
        try:
            await stop.wait()
            log.info("종료 중...")
        finally:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            transport.close()
            if status_runner is not None:
                await status_runner.cleanup()
            await self.http_ws.close()
            await self.http.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="스마트 시약장 Thread 게이트웨이 (CoAP <-> 웹 서버 브리지)")
    parser.add_argument("-c", "--config", default=None,
                        help="설정 파일 (기본: 스크립트 옆의 gateway.toml)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="디버그 로그")
    args = parser.parse_args(argv)

    # Windows 의 기본 Proactor 루프는 (일부 파이썬 빌드에서) UDP 수신이 동작하지
    # 않는다 — 개발 PC 에서 시험 구동할 때를 위한 처리. 파이(Linux)에서는 무관.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.config:
        cfg_path = Path(args.config)
    else:
        cfg_path = Path(__file__).resolve().parent / "gateway.toml"
    if not cfg_path.exists():
        log.error("설정 파일이 없습니다: %s\n"
                  "gateway.example.toml 을 gateway.toml 로 복사한 뒤 "
                  "웹 서버 주소를 채워 넣으세요.", cfg_path)
        return 2

    try:
        cfg = load_config(cfg_path)
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.error("설정 파일을 읽을 수 없습니다 (%s): %s", cfg_path, e)
        return 2

    gw = Gateway(cfg)
    try:
        asyncio.run(gw.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
