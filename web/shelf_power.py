"""앱 세션 기반 선반 전력 모드 (docs/power-modes.md).

로그인한 앱 사용자가 한 명이라도 있으면 "active", 아무도 없으면 "idle".
Thread 게이트웨이가 `GET /api/shelf-power` 를 폴링해 이 값을 모든 선반
노드에 전파한다 (active = 상시 수신 + 0.2초 측정, idle = 슬립).

세션의 출입:
  * enter — `/api/users/login-pin` 성공 시 서버가 내부에서 호출 (앱 무수정),
    또는 `POST /api/app-session/enter` (하트비트 겸용, TTL 연장).
  * leave — 앱 로그아웃 버튼이 `POST /api/app-session/leave` 를 호출.
  * TTL — 앱이 강제 종료돼 leave 를 못 보낸 경우의 안전망. 만료되면
    자동으로 세션에서 제거된다 (기본 30분; 30분 × ACTIVE ~8.6mA ≈ 4.3mAh
    라 배터리 손실은 무시할 수준).

인메모리라 서버 재시작 시 초기화된다 — 재시작 직후에는 idle 로 판정되므로
로그인 중이던 사용자는 앱에서 재로그인해야 선반이 다시 깨어난다.
(게이트웨이는 서버가 죽어 있는 동안 마지막 모드를 유지하고, 노드는 자체
failsafe 로 idle 에 떨어진다.)

모드가 뒤집힐 때(active↔idle)는 set_notifier() 로 등록된 콜백을 부른다 —
main.py 가 이를 WebSocket `{"type":"shelf_power",...}` 브로드캐스트로
연결해 게이트웨이가 폴링 주기를 기다리지 않고 즉시 반응한다. enter 는
sync 라우트(login-pin, FastAPI 스레드풀)에서도 불리므로 콜백은 스레드
안전해야 한다 (main.py 는 run_coroutine_threadsafe 를 쓴다). TTL 만료로
인한 전환은 콜백 없이 게이트웨이의 백업 폴링(2초)이 알아챈다.

sync 라우트 핸들러는 FastAPI 스레드풀에서 돌므로 Lock 으로 보호한다.
"""

import time
from threading import Lock
from typing import Callable

# 앱이 leave 없이 사라졌을 때 세션이 자동 만료되는 시간.
TTL_SECONDS = 30 * 60.0

_lock = Lock()
_sessions: dict[str, float] = {}  # username -> 만료 시각 (time.time() 기준)
_notify: Callable[[dict], None] | None = None


def set_notifier(fn: Callable[[dict], None] | None):
    """모드 전환(active↔idle) 시 상태 dict 로 불릴 콜백을 등록한다."""
    global _notify
    _notify = fn


def _prune(now: float):
    for user, expires in list(_sessions.items()):
        if expires <= now:
            del _sessions[user]


def enter(username: str) -> dict:
    """로그인 또는 하트비트: 세션 TTL 을 갱신하고 현재 상태를 반환한다."""
    now = time.time()
    with _lock:
        _prune(now)
        was_active = bool(_sessions)
        _sessions[username] = now + TTL_SECONDS
        status = _status(now)
    if not was_active and _notify is not None:
        _notify(status)   # idle -> active
    return status


def leave(username: str) -> dict:
    """로그아웃: 세션을 제거하고 현재 상태를 반환한다 (없어도 무해)."""
    now = time.time()
    with _lock:
        _prune(now)
        was_active = bool(_sessions)
        _sessions.pop(username, None)
        now_active = bool(_sessions)
        status = _status(now)
    if was_active and not now_active and _notify is not None:
        _notify(status)   # active -> idle
    return status


def mode() -> str:
    now = time.time()
    with _lock:
        _prune(now)
        return "active" if _sessions else "idle"


def status() -> dict:
    now = time.time()
    with _lock:
        _prune(now)
        return _status(now)


def _status(now: float) -> dict:
    """호출 측이 _lock 을 쥔 상태에서만 부른다."""
    if not _sessions:
        return {"mode": "idle", "users": 0, "ttl_s": None}
    return {
        "mode": "active",
        "users": len(_sessions),
        # 마지막 세션이 만료되기까지 남은 시간 (하트비트가 없다면)
        "ttl_s": round(max(_sessions.values()) - now, 1),
    }


def reset():
    """테스트용: 모든 세션을 비운다."""
    with _lock:
        _sessions.clear()
