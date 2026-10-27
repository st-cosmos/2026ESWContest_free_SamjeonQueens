"""최소 CoAP (RFC 7252) 코덱 + asyncio UDP 엔드포인트 (서버 + CON 클라이언트).

일부러 외부 의존성 없이 구현했다. 선반 노드는 CoAP 의 좁은 부분집합만 쓰고
(URI_PATH/URI_QUERY 옵션이 붙은 CON GET, JSON 페이로드의 NON POST), 게이트웨이는
노드의 400 ms 응답 데드라인 안에 반드시 답해야 한다. 라이브러리 버전에 따라
동작이 달라질 여지를 없애고, ff03::1 멀티캐스트 가입을 위해 소켓을 직접 다룬다.

클라이언트 쪽(`CoapServer.request`)은 전력 모드 도입으로 생겼다: 게이트웨이가
노드에 `PUT shelf/mode` / `PUT shelf/led` 를 CON 으로 밀어넣는다. 잠든(SED)
노드에는 부모가 다음 데이터 폴에 실어 배달하므로, ACK 타임아웃 재전송이
유일하게 필요한 신뢰성 장치다 (piggyback 응답을 토큰으로 매칭).

구현하지 않은 것 (노드 프로토콜에 필요 없음): 메시지 중복 제거, blockwise
전송, observe. 노드는 폴링마다 새 토큰을 쓰고, 게이트웨이의 푸시는 멱등한
PUT 이라 중복 수신이 무해하다.
"""

from __future__ import annotations

import asyncio
import errno
import itertools
import logging
import os
import socket
import struct
from dataclasses import dataclass, field

log = logging.getLogger("shelfcoap")

COAP_PORT = 5683

# 메시지 타입
CON = 0
NON = 1
ACK = 2
RST = 3

# 메서드 코드 (0.xx)
GET = 1
POST = 2
PUT = 3
DELETE = 4

# 응답 코드 (class.detail -> (class << 5) | detail)
CHANGED = (2 << 5) | 4              # 2.04
CONTENT = (2 << 5) | 5              # 2.05
BAD_REQUEST = (4 << 5) | 0          # 4.00
NOT_FOUND = (4 << 5) | 4            # 4.04
METHOD_NOT_ALLOWED = (4 << 5) | 5   # 4.05
INTERNAL_SERVER_ERROR = (5 << 5) | 0  # 5.00

OPTION_URI_PATH = 11
OPTION_CONTENT_FORMAT = 12
OPTION_URI_QUERY = 15


def code_str(code: int) -> str:
    return f"{code >> 5}.{code & 0x1f:02d}"


@dataclass
class Message:
    type: int = CON
    code: int = 0
    mid: int = 0
    token: bytes = b""
    # (옵션 번호, 값) 목록. 번호 오름차순이 아니어도 encode() 가 정렬한다.
    options: list[tuple[int, bytes]] = field(default_factory=list)
    payload: bytes = b""

    def is_request(self) -> bool:
        return 1 <= self.code <= 31

    def uri_path(self) -> list[str]:
        return [v.decode("utf-8", "replace")
                for num, v in self.options if num == OPTION_URI_PATH]

    def uri_queries(self) -> dict[str, str]:
        """"id=abc" 형태의 URI_QUERY 옵션들을 dict 로 반환한다 ("=" 없으면 값은 "")."""
        out: dict[str, str] = {}
        for num, v in self.options:
            if num != OPTION_URI_QUERY:
                continue
            text = v.decode("utf-8", "replace")
            key, sep, val = text.partition("=")
            out[key] = val
        return out


def _decode_ext(nibble: int, data: bytes, i: int) -> tuple[int, int]:
    """옵션 델타/길이 니블의 확장 인코딩(13/14)을 푼다. (값, 다음 인덱스) 반환."""
    if nibble < 13:
        return nibble, i
    if nibble == 13:
        if i >= len(data):
            raise ValueError("truncated option extension")
        return data[i] + 13, i + 1
    if nibble == 14:
        if i + 2 > len(data):
            raise ValueError("truncated option extension")
        return int.from_bytes(data[i:i + 2], "big") + 269, i + 2
    # 15 는 페이로드 마커(0xFF) 전용. 옵션 헤더에 나오면 포맷 오류다.
    raise ValueError("reserved option nibble 15")


def parse(data: bytes) -> Message:
    """UDP 데이터그램 하나를 Message 로 파싱한다. 형식 오류는 ValueError."""
    if len(data) < 4:
        raise ValueError("short datagram")

    version = data[0] >> 6
    if version != 1:
        raise ValueError(f"unsupported CoAP version {version}")

    msg = Message(type=(data[0] >> 4) & 0x3, code=data[1],
                  mid=int.from_bytes(data[2:4], "big"))

    tkl = data[0] & 0x0F
    if tkl > 8:
        raise ValueError(f"invalid token length {tkl}")
    if len(data) < 4 + tkl:
        raise ValueError("truncated token")
    msg.token = data[4:4 + tkl]

    i = 4 + tkl
    number = 0
    while i < len(data):
        b = data[i]
        i += 1
        if b == 0xFF:
            if i == len(data):
                raise ValueError("payload marker without payload")
            msg.payload = data[i:]
            break
        delta, i = _decode_ext(b >> 4, data, i)
        length, i = _decode_ext(b & 0x0F, data, i)
        number += delta
        if i + length > len(data):
            raise ValueError("truncated option value")
        msg.options.append((number, data[i:i + length]))
        i += length

    return msg


def _encode_ext(value: int) -> tuple[int, bytes]:
    if value < 13:
        return value, b""
    if value < 269:
        return 13, bytes([value - 13])
    if value < 65536 + 269:
        return 14, (value - 269).to_bytes(2, "big")
    raise ValueError(f"option delta/length {value} out of range")


def encode(msg: Message) -> bytes:
    if len(msg.token) > 8:
        raise ValueError("token longer than 8 bytes")

    out = bytearray()
    out.append((1 << 6) | ((msg.type & 0x3) << 4) | len(msg.token))
    out.append(msg.code & 0xFF)
    out += msg.mid.to_bytes(2, "big")
    out += msg.token

    number = 0
    for opt_num, value in sorted(msg.options, key=lambda o: o[0]):
        delta_n, delta_ext = _encode_ext(opt_num - number)
        len_n, len_ext = _encode_ext(len(value))
        out.append((delta_n << 4) | len_n)
        out += delta_ext + len_ext + value
        number = opt_num

    if msg.payload:
        out.append(0xFF)
        out += msg.payload

    return bytes(out)


class CoapServer(asyncio.DatagramProtocol):
    """CoAP 엔드포인트: 서버 역할 + CON 클라이언트(`request`).

    handler 는 `async def handler(msg, addr) -> (응답 코드, 페이로드 bytes)`.
    응답 규칙:
      * CON 요청  -> 같은 MID/토큰으로 piggyback ACK (핸들러 예외는 5.00)
      * NON 요청  -> 응답 없음 (노드의 무게 보고는 NON 이고 응답을 읽지 않는다 —
                     메시 트래픽을 아끼기 위해 아예 보내지 않는다)
      * 빈 CON (CoAP ping) -> RST
    수신한 응답은 `request()` 가 기다리는 토큰과 매칭되고, 그 외는 무시한다.
    """

    def __init__(self, handler):
        self._handler = handler
        self._tasks: set[asyncio.Task] = set()
        self.transport: asyncio.DatagramTransport | None = None
        self._pending: dict[bytes, asyncio.Future] = {}
        self._mid = itertools.count(int.from_bytes(os.urandom(2), "big"))

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            msg = parse(data)
        except ValueError as e:
            log.debug("malformed datagram from %s: %s", addr, e)
            return

        if msg.code == 0:  # empty message
            if msg.type == CON:  # CoAP ping -> RST
                self._send(Message(type=RST, code=0, mid=msg.mid), addr)
            return

        if not msg.is_request():
            # 우리가 보낸 CON 요청의 응답 — 토큰으로 대기자를 찾는다.
            fut = self._pending.get(bytes(msg.token))
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return

        task = asyncio.get_running_loop().create_task(self._serve(msg, addr))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def request(self, msg: Message, addr, *, ack_timeout: float = 2.0,
                      retries: int = 3) -> Message:
        """CON 요청을 보내고 piggyback 응답을 기다린다 (타임아웃 시 재전송).

        토큰/MID 가 비어 있으면 채워 넣는다. 재전송은 CoAP 규칙대로 같은
        MID/토큰을 그대로 다시 보낸다 (노드 쪽 PUT 은 멱등이라 중복 무해).
        잠든(SED) 노드는 부모가 다음 데이터 폴(기본 1 s)에 실어 배달하므로
        첫 ack_timeout 안에 대부분 응답이 온다.

        응답 Message 를 반환하고, 끝내 응답이 없으면 TimeoutError.
        """
        if self.transport is None:
            raise RuntimeError("transport not ready")
        if not msg.token:
            msg.token = os.urandom(8)
        if msg.mid == 0:
            msg.mid = next(self._mid) & 0xFFFF

        token = bytes(msg.token)
        data = encode(msg)
        fut = asyncio.get_running_loop().create_future()
        self._pending[token] = fut
        try:
            timeout = ack_timeout
            for _ in range(retries + 1):
                self.transport.sendto(data, addr)
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout)
                except asyncio.TimeoutError:
                    timeout *= 2
            raise TimeoutError(f"no CoAP answer from {addr[0]}")
        finally:
            self._pending.pop(token, None)

    async def _serve(self, msg: Message, addr):
        try:
            code, payload = await self._handler(msg, addr)
        except Exception:
            log.exception("handler failed for %s from %s",
                          "/".join(msg.uri_path()), addr)
            code, payload = INTERNAL_SERVER_ERROR, b""

        if msg.type != CON:
            return

        rsp = Message(type=ACK, code=code, mid=msg.mid, token=msg.token,
                      payload=payload)
        if payload:
            # text/plain;charset=utf-8 (0) 은 빈 옵션 값으로 인코딩된다.
            rsp.options.append((OPTION_CONTENT_FORMAT, b""))
        self._send(rsp, addr)

    def _send(self, msg: Message, addr):
        if self.transport is not None:
            self.transport.sendto(encode(msg), addr)


def join_multicast(sock: socket.socket, group: str, ifname: str) -> bool:
    """IPv6 멀티캐스트 그룹에 가입한다.

    True = 새로 가입함, False = 이미 가입돼 있음.
    인터페이스가 없으면 OSError (호출 쪽에서 재시도).

    wpan0 은 ot-daemon 이 재시작하면 새로 만들어지고 기존 가입은 조용히
    사라지므로, 주기적으로 다시 호출해서 가입을 유지해야 한다.
    """
    ifindex = socket.if_nametoindex(ifname)
    mreq = socket.inet_pton(socket.AF_INET6, group) + struct.pack("@I", ifindex)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)
        return True
    except OSError as e:
        if e.errno == errno.EADDRINUSE:  # 이미 멤버
            return False
        raise
