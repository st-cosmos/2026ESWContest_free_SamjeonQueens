"""무단 반출·무단 반입(스캔 없는 회수/적재) 감지 → 앱 푸시 알림용 이벤트.

두 경보는 타이밍이 다르다.
  * 무단 반출 — 병을 먼저 들고 나서 스캔하는 순서는 정상 사용이다. 세션 없는 감소는
    '미청구 감소'로 남겨 두고, GRACE_S 안에 스캔이 오면 그 스캔이 감소를 청구해 확정한다.
    유예가 지나도 청구되지 않으면 감소량과 무게가 맞는 병을 무단 반출로 확정한다 (suspects).
    맞는 병이 없으면 칸이 빈 경우에만 남은 비치중 병 전부를 확정한다.
  * 무단 반입 — 스캔 없이 병을 먼저 올려놓는 것은 언제나 절차 위반이다 (정상 반입은
    스캔 → 세션 → 안착 순서뿐). 유예 없이 감지 즉시 경보하고, 사용자는 '지금 스캔하기'로
    그 적재를 청구해 기록을 맞춘다. 미청구 증가 원장은 routes/shelves 가 관리한다.
WebSocket(type=unscanned_checkout / unscanned_checkin) 으로 브로드캐스트한다.

앱의 AlertMonitorService 가 이 이벤트를 받아 휴대폰·태블릿에 경보 알림을 띄우고,
WebSocket 유실에 대비해 GET /api/chemicals/alerts 의 unscanned_events /
unscanned_checkin_events 도 폴링한다.
"""
import asyncio
import time
from datetime import datetime

import checkout_flow
import models
from database import SessionLocal
from session_store import checkin_session

# 반출: 감소 감지 후 이 시간 안에 스캔이 오면 정상 순서(병 먼저 들고 스캔)로 본다.
# 병을 들고 앱을 열어 라벨을 비추기까지의 시간. 반입에는 유예가 없다 (모듈 설명 참고).
GRACE_S = 12.0
# 확정 이벤트 보관 시간 (앱 폴링이 놓친 이벤트를 다시 받아갈 수 있는 창)
EVENT_TTL_S = 30 * 60
# 같은 병에 대한 재경보 최소 간격 (노이즈로 감소가 여러 번 잡혀도 한 번만 울린다)
REALERT_COOLDOWN_S = 10 * 60
# 이 무게 미만의 가벼운 병은 반출 감지 대상에서 제외 (GET /alerts 와 동일 기준)
MIN_BOTTLE_KG = 0.2
# 이 무게 미만의 증가는 병이 놓인 것으로 보지 않는다 (무단 반입 감지·스캔 청구 공통).
# 같은 칸의 연속 단계 보고는 routes/shelves 가 하나로 합치므로 합계가 이 값을 넘는 순간 경보한다.
MIN_RISE_KG = 0.15

_events: list = []          # 확정된 무단 반출 이벤트 (최신이 뒤)
_alerted_at: dict = {}      # chemical_id -> 마지막 경보 시각
_checkin_events: list = []  # 확정된 무단 반입 이벤트 (최신이 뒤)


def _prune(now: float):
    cutoff = now - EVENT_TTL_S
    _events[:] = [e for e in _events if e["_ts"] >= cutoff]
    _checkin_events[:] = [e for e in _checkin_events if e["_ts"] >= cutoff]


def _shelf_desc(shelf) -> str:
    return (f"선반 {shelf.parent_shelf} · {shelf.row}행 {shelf.col}열"
            if shelf and shelf.parent_shelf else "위치 미상")


def _strip(events: list) -> list:
    return [{k: v for k, v in e.items() if k != "_ts"} for e in reversed(events)]


def suspects(db, shelf_id: str, min_age: float = 0.0):
    """이 칸에서 무단 반출로 볼 병들 — [(병, 감지 시각)] (현재 상태).
    푸시(watch)와 앱 모달(GET /alerts)이 함께 쓴다. min_age 미만 나이의 감소는 아직
    유예 중으로 보고 건너뛴다.

    1) 미청구 감소 한 건마다 그 감소량과 무게가 허용 오차 안에서 맞는 비치중 병을 고른다
       (여러 개면 가장 가까운 병 하나, 이미 지목된 병은 제외). 한 칸에 병이 여러 개라
       하나를 빼도 칸이 비지 않는 경우와 두 병을 연달아 뺀 경우를 잡는다 — 예전에는
       칸이 비어야만 울려서 병이 둘 이상인 칸은 감지되지 않았다.
    2) 맞는 병이 하나도 없으면 칸이 비었을 때(EMPTY_SHELF_KG 미만)만 남은 비치중 병 전부를
       든다 (감소 기록이 없는 서버 재시작 직후나, 기록 무게와 동떨어진 감소에 대한 보루).
       유예 중인 감소가 남아 있으면 보루도 기다린다.
    """
    shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first()
    if not shelf:
        return shelf, []
    chems = db.query(models.Chemical).filter(
        models.Chemical.shelf_id == shelf_id,
        models.Chemical.current_status == "비치중",
    ).all()
    # 위치 이동 중인 병(들어 올리기 전에 버튼을 눌렀든, 든 뒤에 눌렀든)은 무단 반출이 아니다
    relocating_id = checkin_session.get("relocate_chemical_id") if checkin_session["active"] else ""
    chems = [c for c in chems
             if (c.weight or 0.0) >= MIN_BOTTLE_KG and c.id != relocating_id]

    now = time.time()
    drops = checkout_flow.unclaimed_drops(shelf_id)   # 오래된 순
    ready = [d for d in drops if now - d[1] >= min_age]
    pool = list(chems)
    out = []
    for drop_kg, ts in ready:
        matched = [c for c in pool if checkout_flow.weight_within_tolerance(c.weight, drop_kg)]
        if matched:
            best = min(matched, key=lambda c: abs(c.weight - drop_kg))
            pool.remove(best)
            out.append((best, ts))
    if out:
        return shelf, out

    if len(ready) == len(drops) and shelf.weight < checkout_flow.EMPTY_SHELF_KG:
        since = drops[-1][1] if drops else None
        return shelf, [(c, since) for c in chems]
    return shelf, []


async def watch(shelf_id: str, broadcast):
    """미청구 감소가 생긴 칸을 GRACE_S 뒤에 다시 확인해 무단 반출을 확정한다.
    (반입 쪽 notify_rise 와 달리 유예가 있다 — 병 먼저 들고 스캔하는 순서가 정상이므로)"""
    await asyncio.sleep(GRACE_S)
    db = SessionLocal()
    try:
        shelf, pairs = suspects(db, shelf_id, min_age=GRACE_S)
        snapshot = [(c.id, c.name) for c, _ in pairs]
        desc = _shelf_desc(shelf)
    finally:
        db.close()

    now = time.time()
    _prune(now)
    for chem_id, name in snapshot:
        if now - _alerted_at.get(chem_id, 0.0) < REALERT_COOLDOWN_S:
            continue
        _alerted_at[chem_id] = now
        event = {
            "event_id": f"{chem_id}:{int(now)}",
            "chemical_id": chem_id,
            "chemical_name": name,
            "shelf_id": shelf_id,
            "shelf_desc": desc,
            "detected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _events.append({**event, "_ts": now})
        await broadcast({"type": "unscanned_checkout", **event})


def recent_events() -> list:
    """최근 EVENT_TTL_S 안의 확정 무단 반출 이벤트 (최신 순). 앱 폴링용."""
    _prune(time.time())
    return _strip(_events)


# ---------- 무단 반입 (반입 스캔 없는 적재) ----------
# 미청구 증가의 원장(_unclaimed_rises)은 routes/shelves 가 관리한다. routes.shelves →
# routes.chemicals → unscanned_alert 순환 import 를 피하려고 함수 안에서 가져온다.


async def notify_rise(shelf_id: str, delta_kg: float, rise_ts: float, broadcast):
    """미청구 증가를 유예 없이 즉시 무단 반입으로 경보한다.

    routes/shelves.update_weight 가 한 적재의 합계가 MIN_RISE_KG 를 처음 넘은 시점에
    한 번만 넘겨준다 (연속 단계 보고는 합쳐지므로 한 번의 적재에 한 번만 울린다)."""
    db = SessionLocal()
    try:
        shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first()
        desc = _shelf_desc(shelf)
    finally:
        db.close()

    now = time.time()
    _prune(now)
    event = {
        "event_id": f"{shelf_id}:{int(rise_ts)}",
        "shelf_id": shelf_id,
        "shelf_desc": desc,
        "delta_kg": delta_kg,
        "since": rise_ts,
        "detected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _checkin_events.append({**event, "_ts": now})
    await broadcast({"type": "unscanned_checkin", **event})


def pending_checkins(db) -> list:
    """현재 상태: 청구되지 않은 적재 목록 (앱 모달용, 유예 없음).
    반입 스캔이 청구하거나 병이 다시 내려가면 목록에서 사라진다."""
    import routes.shelves as shelf_routes
    out = []
    for shelf_id, delta, ts in shelf_routes.pending_rises(min_kg=MIN_RISE_KG):
        shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first()
        out.append({
            "shelf_id": shelf_id,
            "shelf_desc": _shelf_desc(shelf),
            "delta_kg": delta,
            "since": ts,
        })
    return out


def recent_checkin_events() -> list:
    """최근 EVENT_TTL_S 안의 확정 무단 반입 이벤트 (최신 순). 앱 폴링용."""
    _prune(time.time())
    return _strip(_checkin_events)
