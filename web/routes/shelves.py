from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Dict, List
from database import get_db
import checkout_flow
import device_status
import models
import routes.chemicals as chemical_routes
import schemas
import services.capacity as capacity
import services.llm_safety as llm_safety
import unscanned_alert
from session_store import checkin_session, reset_checkin
from datetime import datetime
import time

router = APIRouter(prefix="/api/shelves", tags=["shelves"])

@router.get("", response_model=List[schemas.ShelfResponse])
def get_shelves(db: Session = Depends(get_db)):
    """등록된 선반은 항상 반환하고, 미등록 기기는 실제로 접속 중(하트비트가
    살아 있는 경우)일 때만 노출한다. 꺼진 기기의 잔여 행이 앱/웹의
    '신규 선반 기기 감지' 알림을 계속 띄우는 문제를 막는다."""
    shelves = db.query(models.Shelf).all()
    visible = [
        s for s in shelves
        if s.status == "registered" or device_status.is_online(s.id)
    ]
    # 리셋 버튼으로 방금 다시 잡힌 기기 표시 (미등록 기기 식별 블링크용)
    for s in visible:
        s.recently_reset = device_status.recently_reset(s.id)
    return visible

@router.get("/configs")
def get_shelf_configs(db: Session = Depends(get_db)):
    # id(생성 순서: A, B, C…) 기준 고정 정렬 — 정렬이 없으면 Postgres 가 UPDATE 된
    # 행을 뒤로 보내서, 방금 수정한 선반이 화면 뒤쪽으로 튀는 문제가 있었다
    configs = db.query(models.ShelfConfig).order_by(models.ShelfConfig.id).all()
    return configs

@router.post("/configs/{shelf_id}")
def update_shelf_config(shelf_id: str, req: Dict[str, int], db: Session = Depends(get_db)):
    cfg = db.query(models.ShelfConfig).filter(models.ShelfConfig.id == shelf_id).first()
    if not cfg:
        cfg = models.ShelfConfig(id=shelf_id, name=f"선반 {shelf_id}", rows=3, cols=3)
        db.add(cfg)
    
    if "rows" in req:
        cfg.rows = req["rows"]
    if "cols" in req:
        cfg.cols = req["cols"]
        
    db.commit()
    db.refresh(cfg)
    return cfg

@router.post("/register/{shelf_id}", response_model=schemas.ShelfResponse)
def register_shelf(shelf_id: str, req: schemas.ShelfRegister, db: Session = Depends(get_db)):
    shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first()
    if not shelf:
        # If it doesn't exist, create it as registered
        shelf = models.Shelf(id=shelf_id)
        db.add(shelf)
    
    shelf.name = req.name
    shelf.parent_shelf = req.parent_shelf
    shelf.row = req.row
    shelf.col = req.col
    shelf.status = "registered"
    shelf.updated_time = datetime.now().strftime("%H:%M:%S")
    
    db.commit()
    db.refresh(shelf)
    return shelf


def _unregister_device(shelf: models.Shelf):
    """기기를 미등록 상태로 되돌린다. 게이트웨이에 접속 중이면
    get_shelves()의 is_online 조건에 따라 신규 등록 대기 목록에 다시 나타난다."""
    shelf.status = "unregistered"
    shelf.parent_shelf = None
    shelf.row = None
    shelf.col = None
    shelf.led_on = False
    shelf.led_message = ""
    shelf.updated_time = datetime.now().strftime("%H:%M:%S")


@router.post("/unregister/{shelf_id}", response_model=schemas.ShelfResponse)
def unregister_shelf(shelf_id: str, db: Session = Depends(get_db)):
    """선반 수정 모드에서 셀 삭제 → 해당 기기 등록 해제."""
    shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first()
    if not shelf:
        raise HTTPException(status_code=404, detail="기기를 찾을 수 없습니다.")
    _unregister_device(shelf)
    db.commit()
    db.refresh(shelf)
    return shelf


@router.delete("/configs/{shelf_id}")
def delete_config(shelf_id: str, db: Session = Depends(get_db)):
    """선반 삭제: 선반 설정을 제거하고, 등록돼 있던 기기들은 모두 등록 해제한다."""
    cfg = db.query(models.ShelfConfig).filter(models.ShelfConfig.id == shelf_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="선반 설정을 찾을 수 없습니다.")

    removed = 0
    devices = db.query(models.Shelf).filter(
        models.Shelf.parent_shelf == shelf_id,
        models.Shelf.status == "registered",
    ).all()
    for d in devices:
        _unregister_device(d)
        removed += 1
    db.delete(cfg)
    db.commit()
    return {"status": "success", "shelf_id": shelf_id, "removed_devices": removed}


@router.post("/configs/{shelf_id}/delete-row")
def delete_config_row(shelf_id: str, req: Dict[str, int], db: Session = Depends(get_db)):
    """행 삭제: 해당 행의 기기들은 등록 해제, 아래 행 기기들은 한 칸 위로 당긴다."""
    cfg = db.query(models.ShelfConfig).filter(models.ShelfConfig.id == shelf_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="선반 설정을 찾을 수 없습니다.")
    row = req.get("row", 0)
    if row < 1 or row > cfg.rows:
        raise HTTPException(status_code=400, detail="잘못된 행 번호입니다.")
    # 행/열 없는 빈 선반도 유효한 상태이므로 마지막 행 삭제를 허용한다

    removed = 0
    devices = db.query(models.Shelf).filter(
        models.Shelf.parent_shelf == shelf_id,
        models.Shelf.status == "registered",
    ).all()
    for d in devices:
        if d.row == row:
            _unregister_device(d)
            removed += 1
        elif d.row and d.row > row:
            d.row -= 1
    cfg.rows -= 1
    db.commit()
    db.refresh(cfg)
    return {"status": "success", "removed_devices": removed, "config": {
        "id": cfg.id, "name": cfg.name, "rows": cfg.rows, "cols": cfg.cols
    }}


@router.post("/configs/{shelf_id}/delete-col")
def delete_config_col(shelf_id: str, req: Dict[str, int], db: Session = Depends(get_db)):
    """열 삭제: 해당 열의 기기들은 등록 해제, 오른쪽 열 기기들은 한 칸 왼쪽으로 당긴다."""
    cfg = db.query(models.ShelfConfig).filter(models.ShelfConfig.id == shelf_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="선반 설정을 찾을 수 없습니다.")
    col = req.get("col", 0)
    if col < 1 or col > cfg.cols:
        raise HTTPException(status_code=400, detail="잘못된 열 번호입니다.")
    # 행/열 없는 빈 선반도 유효한 상태이므로 마지막 열 삭제를 허용한다

    removed = 0
    devices = db.query(models.Shelf).filter(
        models.Shelf.parent_shelf == shelf_id,
        models.Shelf.status == "registered",
    ).all()
    for d in devices:
        if d.col == col:
            _unregister_device(d)
            removed += 1
        elif d.col and d.col > col:
            d.col -= 1
    cfg.cols -= 1
    db.commit()
    db.refresh(cfg)
    return {"status": "success", "removed_devices": removed, "config": {
        "id": cfg.id, "name": cfg.name, "rows": cfg.rows, "cols": cfg.cols
    }}


# 노이즈로 인한 미세 변화를 반입/반출 이벤트로 오인하지 않기 위한 최소 변화량 (kg)
MIN_EVENT_DELTA_KG = 0.05


# 반입 세션 없이 발생한 무게 증가(= 스캔 없이 올려진 병)를 칸별로 기억해 두고, 뒤따르는
# 요청이 청구(claim)하면 다시 들었다 놓을 필요 없이 즉시 확정한다.
#  - 위치 이동 요청(routes/session.relocate_checkin): UNCLAIMED_RISE_TTL_S 안의 증가만
#  - 반입 스캔(routes/chemicals.scan_in): 무단 반입 알림을 보고 스캔하러 오기까지
#    시간이 걸리므로 UNCLAIMED_RISE_KEEP_S 까지 청구 가능
# 스캔 없는 적재는 절차 위반이므로 유예 없이 즉시 무단 반입으로 경보된다 (unscanned_alert).
UNCLAIMED_RISE_TTL_S = 60.0
UNCLAIMED_RISE_KEEP_S = 15 * 60.0
# 병을 놓는 동안 무게가 여러 단계로 보고되면(300 g → 500 g, 손 떼기) 같은 적재로 합친다.
# 이 시간 안에 같은 칸에 또 증가가 오면 새 적재가 아니라 이어지는 단계로 보고 합산한다.
RISE_MERGE_WINDOW_S = 30.0
_unclaimed_rises: dict = {}  # shelf_id -> (delta_kg, timestamp)


def _record_rise(shelf_id: str, delta_kg: float):
    """미청구 증가를 기록하고, 이번 보고로 적재가 '처음 경보 대상이 된' 경우에만
    (delta_kg, timestamp) 를 돌려준다. 그 외(합산만 됐거나 아직 가벼움)는 None.

    합산 시 timestamp 는 첫 단계의 것을 유지한다 — 앱이 `since` 로 같은 경보를 다시
    띄우지 않도록 식별하고, 한 번의 적재에 경보가 한 번만 나가게 하기 위해서다."""
    now = time.time()
    prev = _unclaimed_rises.get(shelf_id)
    if prev is not None and now - prev[1] <= RISE_MERGE_WINDOW_S:
        total, ts = round(prev[0] + delta_kg, 2), prev[1]
        was_alertable = prev[0] >= unscanned_alert.MIN_RISE_KG
    else:
        total, ts = round(delta_kg, 2), now
        was_alertable = False
    entry = (total, ts)
    _unclaimed_rises[shelf_id] = entry
    if not was_alertable and total >= unscanned_alert.MIN_RISE_KG:
        return entry
    return None


def _absorb_drop_into_rise(shelf_id: str, drop_kg: float) -> bool:
    """감소를 이 칸의 미청구 증가에서 차감한다. 방금 놓은 병에서 손을 떼거나 자리를 고쳐
    무게가 조금 줄어도 적재 기록(과 '지금 스캔하기' 청구)이 사라지지 않게 한다.

    감소가 이 증가로 설명되면 True (반출 쪽 미청구 감소로 남기지 않는다). 증가분을 넘는
    큰 감소는 기록돼 있던 다른 병까지 들린 것일 수 있으므로 False 를 돌려 반출 판정에 맡긴다."""
    entry = _unclaimed_rises.get(shelf_id)
    if entry is None:
        return False
    rise, ts = entry
    net = round(rise - drop_kg, 2)
    if net >= MIN_EVENT_DELTA_KG:
        _unclaimed_rises[shelf_id] = (net, ts)
        return True
    # 놓았던 병을 도로 들어 올렸다 — 적재 자체가 무효
    _unclaimed_rises.pop(shelf_id, None)
    return drop_kg <= rise + MIN_EVENT_DELTA_KG


def claim_recent_rise(shelf_id, max_age: float = UNCLAIMED_RISE_TTL_S):
    """이 칸의 최근 미청구 증가량을 소비하고 반환한다. 없거나 너무 오래됐으면 None."""
    entry = _unclaimed_rises.pop(shelf_id, None)
    if entry is None:
        return None
    delta, at = entry
    if time.time() - at > max_age:
        return None
    return delta


def pending_rises(min_age: float = 0.0, min_kg: float = 0.0) -> list:
    """아직 청구되지 않은 증가 [(shelf_id, delta_kg, timestamp)]. 보관 기한이 지난 것은 버린다."""
    now = time.time()
    for sid in [s for s, (_d, t) in _unclaimed_rises.items() if now - t > UNCLAIMED_RISE_KEEP_S]:
        _unclaimed_rises.pop(sid, None)
    return [(sid, d, t) for sid, (d, t) in _unclaimed_rises.items()
            if now - t >= min_age and d >= min_kg]


def _rewrite_intake_location(db: Session, chem: models.Chemical, old_desc: str, new_desc: str):
    """가장 최근 반입 기록의 위치 문구를 새 칸으로 고쳐 쓴다.

    혼재 경고 → 안전 위치 이동이 추천 칸에서 끝났을 때 쓴다. '위치 변경' 기록을
    따로 남기지 않으므로, 혼재 금지 칸에 잠깐 놓였던 흔적이 기록에 남지 않는다."""
    intake = db.query(models.Log).filter(
        models.Log.chemical_id == chem.id,
        models.Log.action == "반입",
    ).order_by(models.Log.timestamp.desc()).first()
    if intake is None:
        return
    text = intake.details or ""
    intake.details = (text.replace(old_desc, new_desc) if old_desc in text
                      else f"{text} — 보관 위치 {new_desc}")


def _relocating_from(db: Session, shelf: models.Shelf) -> bool:
    """위치 이동 세션 중 옮길 병이 지금 이 칸에 기록돼 있는가 (= 이 칸에서 들어 올릴 차례)."""
    chem_id = checkin_session["active"] and checkin_session.get("relocate_chemical_id")
    if not chem_id:
        return False
    chem = db.query(models.Chemical).filter(models.Chemical.id == chem_id).first()
    return chem is not None and chem.shelf_id == shelf.id


def _handle_checkin_increase(db: Session, shelf: models.Shelf, delta_kg: float):
    """체크인 세션 중 무게 증가(안착) 처리.

    증가량(delta)을 병 무게로 기록하고, 같은 이름의 반출중 병 중
    무게가 허용 오차 내로 가장 근접한 병이 있으면 그 병을 복귀 처리해
    (동일 이름 개체 식별) 신규 행 난립을 막는다.

    위치 이동 세션(relocate_chemical_id)이면 새 병을 만들지 않고, 이미 반입
    기록된 그 병의 보관 칸만 안착이 감지된 칸으로 옮긴다.
    """
    if not (checkin_session["active"] and checkin_session["chemical_name"]):
        return None

    chem_name = checkin_session["chemical_name"]
    username = checkin_session.get("username", "알수없음")
    user = db.query(models.User).filter(models.User.username == username).first()
    operator_name = user.nickname if user else username
    measured = round(delta_kg, 2)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    shelf_desc = f"선반 {shelf.parent_shelf} · {shelf.row}행 {shelf.col}열"

    relocate_id = checkin_session.get("relocate_chemical_id")
    moving = None
    if relocate_id:
        moving = db.query(models.Chemical).filter(models.Chemical.id == relocate_id).first()
        if moving is None:
            reset_checkin()
            return None
        if moving.shelf_id == shelf.id:
            # 원래 칸에 다시 내려놓았다 — 옮긴 것이 아니므로 계속 대기
            return None

    # 반출중인 같은 이름 병 중 실측 증가량과 가장 근접한 병 → 복귀로 판정
    restored = None
    best_diff = None
    outgone = [] if moving is not None else db.query(models.Chemical).filter(
        models.Chemical.name == chem_name,
        models.Chemical.current_status == "반출중",
    ).all()
    for cand in outgone:
        if cand.weight and checkout_flow.weight_within_tolerance(cand.weight, delta_kg):
            diff = abs(cand.weight - delta_kg)
            if best_diff is None or diff < best_diff:
                best_diff, restored = diff, cand

    if moving is not None:
        old_shelf = db.query(models.Shelf).filter(models.Shelf.id == moving.shelf_id).first()
        old_desc = (f"선반 {old_shelf.parent_shelf} · {old_shelf.row}행 {old_shelf.col}열"
                    if old_shelf else "위치 미상")
        # 잘못 놓였던 칸에 남은 혼재 경고 LED 소등
        if old_shelf and (old_shelf.led_message or "").startswith("🚨"):
            old_shelf.led_on = False
            old_shelf.led_message = ""
        # 옮기려고 들어 올린 감소가 '반출 스캔 대기'로 남지 않게 한다
        if moving.shelf_id:
            checkout_flow.clear_unclaimed_drop(moving.shelf_id)
        moving.shelf_id = shelf.id
        moving.shelf_row = shelf.row or 1
        moving.shelf_col = shelf.col or 1
        chem = moving
        on_target = shelf.id == checkin_session.get("target_shelf_id")
        if on_target and checkin_session.get("relocate_reason") == "co_storage":
            # 혼재 경고 → 안전 위치로 이동 완료: '위치 변경' 기록 대신 반입 기록 자체를
            # 안전 위치로 고쳐 써서 혼재 금지 칸에 있었던 흔적을 남기지 않는다
            _rewrite_intake_location(db, moving, old_desc, shelf_desc)
            details = None
        else:
            details = (f"지정 위치로 이동 완료: {old_desc} → {shelf_desc}" if on_target
                       else f"위치 변경: {old_desc} → {shelf_desc} (지정 위치 아님)")
    elif restored is not None:
        restored.current_status = "비치중"
        restored.shelf_id = shelf.id
        restored.shelf_row = shelf.row or 1
        restored.shelf_col = shelf.col or 1
        restored.holder_username = None
        restored.time_in = now_str
        restored.time_out = None
        restored.weight = measured
        chem = restored
        details = f"재반입 완료: {shelf_desc}에 적재됨 (실측 {measured}kg — 기존 병 복귀)"
    else:
        chem = models.Chemical(
            id=f"chem_{int(datetime.now().timestamp())}",
            name=chem_name,
            shelf_id=shelf.id,
            shelf_row=shelf.row or 1,
            shelf_col=shelf.col or 1,
            weight=measured,
            current_status="비치중",
            time_in=now_str,
            expiration_date=checkin_session.get("expiration_date")
        )
        db.add(chem)
        db.commit()
        db.refresh(chem)
        details = f"신규 반입 완료: {shelf_desc}에 적재됨 (실측 {measured}kg)"

    # 병별 용량(가득 총 무게) 래칫 — 라벨/사진 추정치와 실측 무게 중 큰 값.
    # 가득 병이 반입되면 실측이 곧 용량이고, 반쯤 쓴 병 재반입은 기존 값을 유지한다.
    # 내용량 표기 없이 정한 기본값(0.52/0.12 kg)은 처음 등록하는 병에만 쓴다 — 이미 용량이
    # 있는 병에 쓰면 래칫이라 다시 내려오지 않아 작은 병이 0.52 kg 으로 굳는다.
    is_new = moving is None and restored is None
    est_cap = checkin_session.get("capacity_kg")
    if est_cap and checkin_session.get("capacity_is_default") and not is_new:
        est_cap = None
    chem.capacity_kg = round(
        max(v for v in (est_cap, measured, chem.capacity_kg) if v), 2
    )
    if is_new and not checkin_session.get("capacity_kg"):
        # 추정이 아직 안 왔다 — 늦게 오는 기본값을 이 새 병에만 적용할 수 있게 표시
        capacity.mark_new_without_estimate(chem.id)

    # 병별 혼재 목록(화면 표시·대체 규칙용)은 여기서 채우지 않는다. 무게 보고 처리는 비동기
    # 루프에서 돌아 Gemini 를 기다리면 서버 전체가 멈춘다. 다음 경고·재고 조회가 채운다.
    # 혼재 판정 자체는 반입 스캔 때 받아 둔 시약 쌍 판정(services/costorage)을 쓴다.

    # 추천 위치(기존 이력 칸·안전 위치) 안내 LED 소등
    recommended = db.query(models.Shelf).filter(
        or_(models.Shelf.led_message.like("%기존 반입%"),
            models.Shelf.led_message.like("안전 위치 안내%"))
    ).all()
    for r_shelf in recommended:
        r_shelf.led_on = False
        r_shelf.led_message = ""

    if details is not None:
        db.add(models.Log(
            chemical_id=chem.id,
            chemical_name=chem.name,
            action="위치 변경" if moving is not None else "반입",
            operator_name=operator_name,
            details=details,
        ))

    # 반입 직후 인접 수납칸 혼재 금지 시약 배치 여부 검사
    safe_id, safe_desc = llm_safety.find_recommended_safe_shelf(chem, db)
    co_warning = None
    alerts = chemical_routes.compute_alerts(db, placed_chemical_id=chem.id, ensure_lists=False)
    for warning in alerts.get("co_storage_warnings", []):
        if warning["chemical_1_id"] == chem.id or warning["chemical_2_id"] == chem.id:
            co_warning = warning
            shelf.led_on = True
            shelf.led_message = f"🚨 혼재 위험! 추천 이송: {safe_desc}"
            break

    reset_checkin()
    # 이 칸의 미청구 증가는 방금 확정된 안착으로 소비됐다 (다른 칸의 무단 반입 대기는 유지)
    _unclaimed_rises.pop(shelf.id, None)
    db.commit()

    return {
        "event": "checkin_complete",
        "restored": restored is not None,
        "relocated": moving is not None,
        "co_warning": co_warning,
        "recommended_safe_shelf_desc": safe_desc,
        "chemical": {
            "id": chem.id,
            "name": chem.name,
            "shelf_id": chem.shelf_id,
            "shelf_row": chem.shelf_row,
            "shelf_col": chem.shelf_col,
            "weight": chem.weight,
        },
    }


@router.post("/{shelf_id}/weight")
def update_weight(shelf_id: str, req: schemas.WeightUpdate, db: Session = Depends(get_db)):
    shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first()
    if not shelf:
        raise HTTPException(status_code=404, detail="선반을 찾을 수 없습니다.")

    prev_w = shelf.weight
    shelf.prev_weight = prev_w
    shelf.weight = req.weight
    if req.battery is not None:
        shelf.battery = req.battery
    shelf.updated_time = datetime.now().strftime("%H:%M:%S")

    db.commit()

    # 무게 변화량으로 반입(증가)/반출(감소) 세션을 진행시킨다
    session_result = None
    unclaimed_drop = False
    unclaimed_rise = None
    delta = req.weight - prev_w
    if delta >= MIN_EVENT_DELTA_KG:
        session_result = _handle_checkin_increase(db, shelf, delta)
        if session_result is None:
            # 세션이 소비하지 않은 증가 = 스캔 없이 올려진 병. 위치 이동 요청·반입 스캔이
            # 청구할 수 있도록 기록하고, 경보 대상이 되는 순간 즉시 무단 반입으로 알린다
            # (unscanned_alert.notify_rise — 반출과 달리 유예 없음).
            unclaimed_rise = _record_rise(shelf.id, delta)
        # 병이 다시 올라왔으면 직전의 미청구 감소 기록은 무효
        checkout_flow.clear_unclaimed_drop(shelf.id)
    elif delta <= -MIN_EVENT_DELTA_KG:
        # 방금 스캔 없이 놓인 병이 있으면 그 증가에서 먼저 차감한다 (손 떼기·자리 고치기)
        absorbed = _absorb_drop_into_rise(shelf.id, -delta)
        session_result = checkout_flow.handle_weight_drop(db, shelf, -delta)
        if session_result is None and not absorbed and not _relocating_from(db, shelf):
            # 반출 세션이 소비하지 않은 감소 — 사용자가 병을 먼저 들고 온
            # 경우다. 곧이어 올 스캔이 이 감소를 청구해 즉시 확정한다.
            # 유예 시간 안에 스캔이 없으면 무단 반출로 확정된다 (unscanned_alert).
            checkout_flow.note_unclaimed_drop(shelf.id, -delta)
            unclaimed_drop = True

    db.commit()
    db.refresh(shelf)
    return {
        "status": "success",
        "shelf": shelf,
        "session_result": session_result,
        "unclaimed_drop": unclaimed_drop,
        "unclaimed_rise": unclaimed_rise,
    }

@router.post("/{shelf_id}/led")
def update_led(shelf_id: str, req: schemas.LedUpdate, db: Session = Depends(get_db)):
    shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first()
    if not shelf:
        raise HTTPException(status_code=404, detail="선반을 찾을 수 없습니다.")
        
    shelf.led_on = req.led_on
    shelf.led_message = req.led_message
    shelf.updated_time = datetime.now().strftime("%H:%M:%S")
    
    db.commit()
    db.refresh(shelf)
    return shelf
