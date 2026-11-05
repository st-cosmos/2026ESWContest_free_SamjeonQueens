"""반출 세션: 스캔 → 선반 무게 감소 감지 → 확정.

무게 정보를 두 가지로 활용한다.
 1) 개체 식별 — 같은 이름의 병이 여러 개여도 "어느 칸에서 무게가 빠졌는가"로
    실제 회수된 병을 특정한다.
 2) 부수 검증 — 감소량을 기록된 병 무게와 대조해, 다른 병을 집었거나
    일부만 덜어낸 경우 weight_verified=False 로 경고한다.

세션 시작 시 후보 병들의 선반 LED 를 켜서 위치를 안내하고,
타임아웃(20초) 내 감지가 없으면 앱이 '무게 확인 없이 기록'을 선택할 수 있다.
"""
import time
from datetime import datetime

from sqlalchemy.orm import Session

import models
from session_store import checkout_session as state

LED_PREFIX = "반출 대상"

# 이 무게(kg) 미만이면 선반이 비어 있는 것으로 본다 (사후 스캔 즉시 확정 판정)
EMPTY_SHELF_KG = 0.1

# '병을 먼저 들고 와서 스캔'하는 자연스러운 순서 지원:
# 반출 세션 없이 발생한 무게 감소를 칸별로 기억해 두고, 이 시간 안에 온
# 스캔이 그 감소를 청구(claim)하면 대기 없이 즉시 반출을 확정한다.
UNCLAIMED_DROP_TTL_S = 180.0
# 칸마다 감소를 여러 건 쌓는다 (오래된 것이 앞). 두 병을 연달아 빼면 두 건이 남아 각각
# 무게로 병을 지목한다 — 예전에는 한 건만 기억해 마지막 병만 지목됐다.
_unclaimed_drops: dict = {}  # shelf_id -> [(delta_kg, timestamp), ...]
MAX_UNCLAIMED_DROPS = 5
# 이 시간 안에 이어지는 감소는 한 병을 두 단계로 든 것으로 보고 직전 건에 합친다
# (연달아 빼는 두 병은 보통 이보다 느리다). 감지 시각은 첫 단계 것을 유지한다.
DROP_MERGE_WINDOW_S = 3.0


def note_unclaimed_drop(shelf_id: str, delta_kg: float):
    """세션이 소비하지 않은 무게 감소를 기억한다 (routes/shelves.update_weight)."""
    now = time.time()
    lst = _unclaimed_drops.setdefault(shelf_id, [])
    if lst and now - lst[-1][1] <= DROP_MERGE_WINDOW_S:
        prev_kg, prev_ts = lst[-1]
        lst[-1] = (round(prev_kg + delta_kg, 2), prev_ts)
    else:
        lst.append((round(delta_kg, 2), now))
    del lst[:-MAX_UNCLAIMED_DROPS]


def unclaimed_drops(shelf_id) -> list:
    """이 칸의 미청구 감소 목록 [(delta_kg, timestamp)], 오래된 순 — 알림 판정용."""
    return list(_unclaimed_drops.get(shelf_id, ())) if shelf_id else []


def clear_unclaimed_drop(shelf_id: str, rise_kg=None):
    """무게가 다시 올라오면(병 복귀) 감소 기록을 무효화한다.

    rise_kg 가 있으면 그 증가량과 맞는 감소 한 건만 지운다 (두 병을 뺐다가 하나만 돌려놓은
    경우 나머지는 계속 경보 대상). 맞는 게 없거나 rise_kg 가 없으면(이동 시작 등) 전부 지운다."""
    lst = _unclaimed_drops.get(shelf_id)
    if not lst:
        return
    if rise_kg is not None:
        matched = [d for d in lst if weight_within_tolerance(d[0], rise_kg)]
        if matched:
            lst.remove(min(matched, key=lambda d: abs(d[0] - rise_kg)))
            if not lst:
                _unclaimed_drops.pop(shelf_id, None)
            return
    _unclaimed_drops.pop(shelf_id, None)


def claim_recent_drop(shelf_id, expected_kg=None):
    """이 칸의 미청구 감소 한 건을 소비하고 감소량을 반환한다. 없으면 None.

    expected_kg(스캔한 병의 기록 무게)와 맞는 건이 있으면 나이와 무관하게 그 건을 쓴다 —
    무단 반출 경보 뒤 한참 지나 '지금 스캔하기'로 와도 그 회수를 청구해 즉시 확정된다.
    맞는 건이 없으면 최근(UNCLAIMED_DROP_TTL_S 이내) 건만 청구한다."""
    if not shelf_id:
        return None
    lst = _unclaimed_drops.get(shelf_id)
    if not lst:
        return None
    now = time.time()
    matched = ([d for d in lst if weight_within_tolerance(expected_kg, d[0])]
               if expected_kg else [])
    fresh = [d for d in lst if now - d[1] <= UNCLAIMED_DROP_TTL_S]
    if matched:
        pick = min(matched, key=lambda d: abs(d[0] - expected_kg))
    elif fresh:
        pick = fresh[-1]
    else:
        return None
    lst.remove(pick)
    if not lst:
        _unclaimed_drops.pop(shelf_id, None)
    return pick[0]


def weight_within_tolerance(expected_kg, measured_kg) -> bool:
    """허용 오차: ±20% 또는 최소 ±0.1kg. 기록 무게가 없으면 검증 생략."""
    if not expected_kg or expected_kg <= 0:
        return True
    return abs(measured_kg - expected_kg) <= max(0.1, expected_kg * 0.2)


def _chem_dict(chem: models.Chemical) -> dict:
    return {
        "id": chem.id, "name": chem.name, "cas_no": chem.cas_no,
        "formula": chem.formula, "weight": chem.weight,
        "shelf_id": chem.shelf_id, "shelf_row": chem.shelf_row,
        "shelf_col": chem.shelf_col, "current_status": chem.current_status,
        "holder_username": chem.holder_username,
        "time_in": chem.time_in, "time_out": chem.time_out,
        "expiration_date": chem.expiration_date, "manufacturer": chem.manufacturer,
    }


def _clear_leds(db: Session):
    rows = db.query(models.Shelf).filter(
        models.Shelf.led_message.like(f"{LED_PREFIX}%")
    ).all()
    for s in rows:
        s.led_on = False
        s.led_message = ""


def _shelf_desc(db: Session, shelf_id) -> str:
    shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first() if shelf_id else None
    if not shelf:
        return "위치 미상"
    return f"선반 {shelf.parent_shelf} · {shelf.row}행 {shelf.col}열"


def _operator_name(db: Session, username: str) -> str:
    user = db.query(models.User).filter(models.User.username == username).first()
    return user.nickname if user else username


def _reset(keep_result: bool = True):
    state["active"] = False
    state["chemical_name"] = ""
    state["chemical_id"] = ""
    state["start_time"] = 0.0
    state["username"] = ""
    state["last_event"] = None
    if not keep_result:
        state["last_result"] = None


def is_expired() -> bool:
    return state["active"] and (time.time() - state["start_time"]) > state["timeout_seconds"]


def start(db: Session, chemical_name: str, username: str, chemical_id=None) -> int:
    """세션 시작 + 후보 병들의 선반 LED 점등. 후보 수를 반환한다."""
    _clear_leds(db)
    query = db.query(models.Chemical).filter(
        models.Chemical.name == chemical_name,
        models.Chemical.current_status == "비치중",
    )
    if chemical_id:
        query = query.filter(models.Chemical.id == chemical_id)
    candidates = query.all()
    if not candidates:
        db.commit()
        return 0

    for chem in candidates:
        if chem.shelf_id:
            shelf = db.query(models.Shelf).filter(models.Shelf.id == chem.shelf_id).first()
            if shelf:
                shelf.led_on = True
                shelf.led_message = f"{LED_PREFIX}: {chemical_name}"
                shelf.updated_time = datetime.now().strftime("%H:%M:%S")

    state["active"] = True
    state["chemical_name"] = chemical_name
    state["chemical_id"] = chemical_id or ""
    state["start_time"] = time.time()
    state["username"] = username
    state["last_event"] = None
    state["last_result"] = None
    db.commit()
    return len(candidates)


def handle_weight_drop(db: Session, shelf: models.Shelf, delta_kg: float):
    """선반 무게 감소 시 호출 (routes/shelves.update_weight).

    감소가 일어난 칸으로 병을 특정하고, 감소량으로 검증한 뒤 반출을 확정한다.
    세션 대상이 아닌 병이 들리면 경고 이벤트만 남기고 세션은 유지한다.
    """
    if not state["active"]:
        return None
    if is_expired():
        expire(db)
        return None

    chem = db.query(models.Chemical).filter(
        models.Chemical.name == state["chemical_name"],
        models.Chemical.current_status == "비치중",
        models.Chemical.shelf_id == shelf.id,
    ).first()

    if chem is None:
        other = db.query(models.Chemical).filter(
            models.Chemical.current_status == "비치중",
            models.Chemical.shelf_id == shelf.id,
        ).first()
        if other:
            state["last_event"] = {
                "type": "wrong_item",
                "message": f"다른 시약({other.name})이 들린 것 같습니다. "
                           f"{state['chemical_name']}은(는) LED가 켜진 칸에 있습니다.",
            }
        return None

    if state["chemical_id"] and chem.id != state["chemical_id"]:
        state["last_event"] = {
            "type": "wrong_item",
            "message": f"선택한 병이 아닌 다른 {chem.name} 병이 들렸습니다.",
        }
        return None

    verified = weight_within_tolerance(chem.weight, delta_kg)
    return finalize(db, chem, measured_delta=round(delta_kg, 2), verified=verified)


def finalize(db: Session, chem: models.Chemical, measured_delta=None, verified=None):
    """반출 확정: 상태 변경 + LED 소등 + 로그 + 세션 결과 기록.

    verified=None 은 무게 확인 없이 기록한 경우(force·사후 스캔)다.
    """
    username = state["username"] or ""
    operator = _operator_name(db, username) if username else "알수없음"
    shelf_desc = _shelf_desc(db, chem.shelf_id)

    chem.current_status = "반출중"
    chem.holder_username = username or chem.holder_username
    chem.time_out = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _clear_leds(db)
    # 해당 병 선반에 다른 이유(검색 안내 등)로 켜져 있던 LED 도 함께 소등
    if chem.shelf_id:
        shelf = db.query(models.Shelf).filter(models.Shelf.id == chem.shelf_id).first()
        if shelf and shelf.led_on:
            shelf.led_on = False
            shelf.led_message = ""

    if verified is True:
        details = f"반출 확인: {shelf_desc}에서 {measured_delta}kg 감소 감지 (기록 {chem.weight}kg)"
    elif verified is False:
        details = (f"반출 기록(무게 불일치 주의): 기록 {chem.weight}kg 대비 "
                   f"실측 {measured_delta}kg 감소 — {shelf_desc}")
    else:
        details = f"무게 확인 없이 반출 기록됨 — 보관 위치 {shelf_desc}"

    db.add(models.Log(
        chemical_id=chem.id,
        chemical_name=chem.name,
        action="반출",
        operator_name=operator,
        details=details,
    ))

    result = {
        "chemical": _chem_dict(chem),
        "weight_verified": verified,
        "measured_delta": measured_delta,
    }
    _reset()
    state["last_result"] = result
    db.commit()
    return {"event": "checkout_complete", **result}


def force_finalize(db: Session, chem: models.Chemical, username: str):
    """타임아웃 후 사용자가 '무게 확인 없이 기록'을 선택한 경우."""
    state["username"] = username or state["username"]
    return finalize(db, chem, measured_delta=None, verified=None)


def finalize_already_removed(db: Session, chem: models.Chemical, username: str,
                             measured_delta=None):
    """이미 선반에서 회수된 병의 사후 스캔 — 대기 없이 즉시 확정.

    '병 먼저 들고 스캔' 흐름과 '반출 스캔 미완료' 알림 흐름 공용.
    measured_delta(청구된 미청구 감소량)가 있으면 그 값으로, 없으면 선반의
    직전 무게(prev_weight) 차이로 사후 검증까지 수행한다.
    """
    delta = measured_delta
    if delta is None and chem.shelf_id:
        shelf = db.query(models.Shelf).filter(models.Shelf.id == chem.shelf_id).first()
        if shelf and shelf.prev_weight:
            drop = round(shelf.prev_weight - shelf.weight, 2)
            if drop >= 0.05:
                delta = drop
    verified = weight_within_tolerance(chem.weight, delta) if delta is not None else None
    state["username"] = username or state["username"]
    return finalize(db, chem, measured_delta=delta, verified=verified)


def expire(db: Session):
    """타임아웃 처리: LED 소등 + 세션 종료 (결과 없음)."""
    _clear_leds(db)
    _reset()
    db.commit()


def cancel(db: Session):
    """사용자 취소: LED 소등 + 세션·결과 모두 소거."""
    _clear_leds(db)
    _reset(keep_result=False)
    db.commit()
