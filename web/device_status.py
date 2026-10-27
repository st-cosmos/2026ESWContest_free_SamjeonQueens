"""선반 디바이스 하트비트 추적 (in-memory).

게이트웨이가 노드를 대신해 1초 주기로 LED 상태를 폴링하므로
(GET /api/led/{id}) 마지막 접속 시각으로 온라인 여부를 판단한다.
서버를 재시작하면 초기화되지만, 살아 있는 디바이스는 수 초 안에
다시 기록된다.

IDLE 전력 모드(앱 로그인 없음, docs/power-modes.md)에서는 게이트웨이의
대행 폴링이 5초로 느려지고 노드 접촉도 30초 하트비트뿐이라, 판정 창을
모드에 따라 넓힌다.
"""
import time

import shelf_power

# 이 시간 안에 접속 기록이 있어야 온라인으로 본다 (폴링 주기 1초 대비 여유)
ONLINE_WINDOW_SECONDS = 15.0
# IDLE 모드: 게이트웨이 대행 폴링 5초 + 노드 하트비트 30초 대비 여유
IDLE_ONLINE_WINDOW_SECONDS = 90.0

# 접속 공백이 이 시간을 넘겼다가 돌아오면 리셋(재부팅)으로 판정한다.
# ACTIVE 모드 폴링이 1초라 정상 상태에선 공백이 생기지 않고,
# 리셋 버튼을 누르면 부팅+Thread 재합류로 수 초 이상 끊긴다.
RESET_GAP_SECONDS = 8.0
# 리셋 판정 후 이 시간 동안 recently_reset 로 표시한다 (앱/웹 블링크 식별용)
RESET_HIGHLIGHT_SECONDS = 30.0

_last_seen = {}
_boot_at = {}


def mark_seen(device_id: str):
    now = time.time()
    prev = _last_seen.get(device_id)
    # 이전 접속 기록이 있어야 리셋으로 본다 — 서버 재시작 직후의 첫 접촉을
    # 전부 리셋으로 오인해 일제히 깜빡이는 것을 막는다.
    if prev is not None and (now - prev) >= RESET_GAP_SECONDS:
        _boot_at[device_id] = now
    _last_seen[device_id] = now


def mark_reset(device_id: str):
    """게이트웨이발 명시적 리셋 신호 (POST /api/device-reset/{id}).

    게이트웨이가 노드의 부팅 질의·seq 역행으로 재부팅을 직접 감지해 알린다.
    게이트웨이가 죽은 노드도 TTL 동안 대행 폴링을 계속해 하트비트 공백이
    생기지 않으므로, 공백 추정(mark_seen)만으로는 리셋을 잡지 못한다."""
    _boot_at[device_id] = time.time()


def recently_reset(device_id: str) -> bool:
    """방금(RESET_HIGHLIGHT_SECONDS 이내) 리셋되어 다시 잡힌 기기인가."""
    boot = _boot_at.get(device_id)
    return boot is not None and (time.time() - boot) <= RESET_HIGHLIGHT_SECONDS


def is_online(device_id: str) -> bool:
    seen = _last_seen.get(device_id)
    if seen is None:
        return False
    window = (ONLINE_WINDOW_SECONDS if shelf_power.mode() == "active"
              else IDLE_ONLINE_WINDOW_SECONDS)
    return (time.time() - seen) <= window
