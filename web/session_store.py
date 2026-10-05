import time

CHECKIN_TIMEOUT_S = 15.0
# 지정 위치로 옮기기: 병을 다시 들어 다른 칸에 놓는 시간까지 포함하므로 더 길게 둔다
RELOCATE_TIMEOUT_S = 30.0

checkin_session = {
    "active": False,
    "chemical_name": "",
    "start_time": 0.0,
    "timeout_seconds": CHECKIN_TIMEOUT_S,
    "username": "",
    "expiration_date": None,
    "capacity_kg": None,     # 라벨/사진으로 추정한 가득 총 무게 — 안착 시 병에 기록
    # 지정 위치로 옮기기 세션: 이미 반입 기록된 병을 다른 칸으로 옮겨 놓는 중.
    # 값이 있으면 무게 증가를 신규 반입이 아니라 이 병의 위치 이동으로 처리한다.
    "relocate_chemical_id": "",
    "target_shelf_id": "",
}


def reset_checkin():
    """반입 세션 종료 (완료·타임아웃·취소 공통)."""
    checkin_session["active"] = False
    checkin_session["chemical_name"] = ""
    checkin_session["start_time"] = 0.0
    checkin_session["timeout_seconds"] = CHECKIN_TIMEOUT_S
    checkin_session["username"] = ""
    checkin_session["expiration_date"] = None
    checkin_session["capacity_kg"] = None
    checkin_session["relocate_chemical_id"] = ""
    checkin_session["target_shelf_id"] = ""


# 반출 세션: 스캔 → 선반 무게 감소 감지로 확정 (checkout_flow.py 에서 관리)
checkout_session = {
    "active": False,
    "chemical_name": "",
    "chemical_id": "",      # 직접 선택으로 특정 병이 지정된 경우
    "start_time": 0.0,
    "timeout_seconds": 20.0,
    "username": "",
    "last_event": None,     # 진행 중 경고 (예: 다른 시약 회수 감지) — 앱이 1회 읽으면 소거
    "last_result": None,    # 확정 결과 — 다음 세션 시작 전까지 유지
}
