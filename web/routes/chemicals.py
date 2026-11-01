import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import checkout_flow
import matching
import models
import schemas
import services.capacity as capacity
import services.llm_match as llm_match
import services.llm_safety as llm_safety
import unscanned_alert
from database import get_db
from session_store import checkin_session, checkout_session, reset_checkin

router = APIRouter(prefix="/api/chemicals", tags=["chemicals"])


def _resolve_chemical_name(db: Session, req, error_detail: str) -> str:
    """scan-in/out 공통: 앱이 확정한 chemical_name 이 있으면 그대로,
    없으면(구버전 클라이언트) 서버 매칭을 시도하되 자동 확정 수준일 때만 인정한다."""
    if req.chemical_name:
        return req.chemical_name
    result = matching.match(db, req.ocr_text)
    if result["status"] == "matched":
        return result["chemical_name"]
    raise HTTPException(status_code=400, detail=error_detail)


@router.get("", response_model=List[schemas.ChemicalResponse])
def get_chemicals(db: Session = Depends(get_db)):
    chemicals = db.query(models.Chemical).all()
    for c in chemicals:
        llm_safety.ensure_chemical_incompatibility_info(c, db)
    return chemicals

@router.get("/known-names")
def get_known_names(db: Session = Depends(get_db)):
    """앱의 '직접 선택' 폴백 UI 용 표준명 목록."""
    return matching.known_names(db)

@router.post("/match")
def match_chemical(req: schemas.MatchRequest, db: Session = Depends(get_db)):
    """스캔 중 인식 시도. 바코드 > CAS > 별칭 > 유사도 순으로 매칭하고
    확신이 없으면 needs_confirmation 으로 후보를 돌려준다."""
    import services.scan_log as scan_log
    t0 = time.perf_counter()
    result = matching.match(db, req.ocr_text, req.barcode)
    scan_log.record(
        "match", req.session_id, req.label_id,
        ocr_text=req.ocr_text, barcode=req.barcode,
        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        frame=scan_log.save_frame(req.session_id, req.frame_b64),
        result=scan_log.summarize_result(result),
    )
    return result

@router.post("/match/llm")
def match_chemical_llm(req: schemas.MatchRequest, db: Session = Depends(get_db)):
    """사전 매칭 실패가 지속될 때의 LLM 폴백 — 결과는 항상 사용자 확인을 거친다.
    확인되면 /match/confirm 이 label 문구를 별칭으로 학습해 다음부터 즉시 인식된다."""
    import services.scan_log as scan_log
    t0 = time.perf_counter()
    result = llm_match.identify_chemical_from_ocr(
        req.ocr_text, matching.known_names(db), image_b64=req.image_b64
    )
    if not result:
        response = {
            "status": "no_match", "method": "llm", "chemical_name": None,
            "confidence": 0.0, "candidates": [], "matched_token": None,
        }
    else:
        name = result["name"]
        response = {
            "status": "needs_confirmation", "method": "llm",
            "chemical_name": name, "confidence": 0.75,
            "candidates": [{"name": name, "score": 0.75}],
            "matched_token": result.get("label_text"),
        }
    scan_log.record(
        "match_llm", req.session_id, req.label_id,
        ocr_text=req.ocr_text, barcode=req.barcode, has_image=bool(req.image_b64),
        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        frame=scan_log.save_frame(req.session_id, req.image_b64 if req.frame_b64 is None else req.frame_b64),
        result=scan_log.summarize_result(response),
    )
    return response

@router.post("/match/event")
def scan_event(req: schemas.ScanEventRequest):
    """앱이 보내는 스캔 이벤트(세션 시작·사용자 결정·거절)를 측정 로그에만 남긴다.
    인식·재고 동작에는 아무 영향이 없다."""
    import services.scan_log as scan_log
    scan_log.record(
        req.event, req.session_id, req.label_id,
        chemical_name=req.chemical_name, source=req.source, method=req.method,
        confidence=req.confidence, mode=req.mode,
    )
    return {"status": "success", "logging": scan_log.enabled()}


@router.post("/estimate-capacity")
def estimate_capacity(req: schemas.CapacityEstimateRequest, db: Session = Depends(get_db)):
    """반입 스캔 직후 앱이 백그라운드로 호출 — 라벨 OCR/사진으로 병의 가득
    총 무게(capacity_kg, 잔량 % 분모)를 추정한다. 결과는 진행 중인 반입 세션에
    실리고, 세션이 이미 끝났으면 방금 반입된 같은 이름의 병에 바로 반영한다."""
    images = req.images_b64 or ([req.image_b64] if req.image_b64 else [])
    est = capacity.estimate_capacity(req.chemical_name, req.ocr_text, images)

    if checkin_session["active"] and checkin_session["chemical_name"] == req.chemical_name:
        checkin_session["capacity_kg"] = est["capacity_kg"]
        checkin_session["capacity_is_default"] = est["is_default"]
    else:
        chem = db.query(models.Chemical).filter(
            models.Chemical.name == req.chemical_name
        ).order_by(models.Chemical.time_in.desc()).first()
        # 내용량 표기 없이 정한 기본값은 이번 반입으로 처음 등록된 병에만 쓴다 —
        # 이미 용량이 있는 병을 기본값으로 덮지 않는다 (래칫이라 다시 내려오지 않음)
        if chem and (not est["is_default"] or capacity.take_new_without_estimate(chem.id)):
            chem.capacity_kg = round(
                max(est["capacity_kg"], chem.weight or 0.0, chem.capacity_kg or 0.0), 2
            )
            db.commit()
    return {"status": "success", **est}


@router.post("/match/confirm")
def confirm_match(req: schemas.MatchConfirmRequest, db: Session = Depends(get_db)):
    """확정된 (바코드/토큰 → 시약) 매핑을 학습해 다음 스캔부터 즉시 인식되게 한다."""
    learned = matching.confirm(db, req.chemical_name, req.barcode, req.matched_token)
    import services.scan_log as scan_log
    scan_log.record(
        "confirm_learn", req.session_id, None,
        chemical_name=req.chemical_name, barcode=req.barcode,
        matched_token=req.matched_token, learned=learned,
    )
    return {"status": "success", "learned": learned}

@router.post("/scan-in")
def scan_in(req: schemas.ScanInRequest, db: Session = Depends(get_db)):
    matched_std_name = _resolve_chemical_name(
        db, req,
        f"인식된 텍스트 '{req.ocr_text}'에서 보관 가능한 시약을 매칭하지 못했습니다."
    )
        
    # Check if there is history of this chemical name to find designated shelf location
    prev_chem = db.query(models.Chemical).filter(
        models.Chemical.name == matched_std_name
    ).order_by(models.Chemical.time_in.desc()).first()
    
    prev_shelf_id = None
    prev_shelf_desc = ""
    
    if prev_chem and prev_chem.shelf_id:
        prev_shelf_id = prev_chem.shelf_id
        shelf = db.query(models.Shelf).filter(models.Shelf.id == prev_shelf_id).first()
        if shelf:
            prev_shelf_desc = f"선반 {shelf.parent_shelf} · {shelf.row}행 {shelf.col}열"
            # Turn on LED for this shelf
            shelf.led_on = True
            shelf.led_message = f"기존 반입 이력 위치: {matched_std_name}"
            shelf.updated_time = datetime.now().strftime("%H:%M:%S")
            db.commit()

    # Start active check-in session
    # 직전 세션의 잔재(유통기한·용량·위치 이동 대상)가 새 병에 붙지 않도록 초기화
    reset_checkin()
    checkin_session["active"] = True
    checkin_session["chemical_name"] = matched_std_name
    checkin_session["start_time"] = time.time()
    checkin_session["username"] = req.username

    response = {
        "status": "success",
        "chemical_name": matched_std_name,
        "has_history": prev_chem is not None,
        "recommended_shelf": prev_shelf_id,
        "recommended_shelf_desc": prev_shelf_desc,
        "timeout_seconds": checkin_session["timeout_seconds"]
    }

    # 병을 먼저 올려놓고 스캔한 경우 — 그 칸의 미청구 증가를 청구해 즉시 확정한다
    placed = _claim_placed_bottle(db, req.claim_shelf_id)
    if placed is not None:
        response["already_placed"] = True
        response["chemical_id"] = placed["chemical"]["id"]
        response["placed_shelf_id"] = placed["chemical"]["shelf_id"]
    return response


def _claim_placed_bottle(db: Session, claim_shelf_id):
    """반입 스캔 직전에 이미 선반에 올려진 병의 무게 증가를 청구해 바로 안착 처리한다.

    - 앱이 칸을 지정하면(무단 반입 알림 → 지금 스캔하기) 보관 기한 안의 증가를 청구
    - 지정이 없으면 '방금(60초 안) 생긴 유일한 미청구 증가'만 청구 — 병을 놓고 바로
      스캔하는 자연스러운 순서는 살리되, 오래된 다른 병의 적재를 잘못 가져가지 않는다
    """
    import routes.shelves as shelf_routes  # routes.shelves 가 이 모듈을 import 하므로 지연 import
    max_age = shelf_routes.UNCLAIMED_RISE_KEEP_S
    shelf_id = claim_shelf_id
    if not shelf_id:
        recent = shelf_routes.pending_rises(min_kg=unscanned_alert.MIN_RISE_KG)
        recent = [r for r in recent if time.time() - r[2] <= shelf_routes.UNCLAIMED_RISE_TTL_S]
        if len(recent) != 1:
            return None
        shelf_id = recent[0][0]
        max_age = shelf_routes.UNCLAIMED_RISE_TTL_S
    rise = shelf_routes.claim_recent_rise(shelf_id, max_age=max_age)
    if rise is None:
        return None
    shelf = db.query(models.Shelf).filter(models.Shelf.id == shelf_id).first()
    if shelf is None:
        return None
    return shelf_routes._handle_checkin_increase(db, shelf, rise)

@router.post("/scan-out")
def scan_out(req: schemas.ScanOutRequest, db: Session = Depends(get_db)):
    """반출 스캔 → 즉시 확정하지 않고 반출 세션을 시작한다.

    후보 병(들)의 선반 LED 를 켜 위치를 안내하고, 실제 무게 감소가 감지된
    칸으로 어느 병인지 확정한다 (동일 이름 병 개체 식별 + 감소량 검증).
    확정/타임아웃 여부는 GET /api/checkout-session 폴링으로 전달된다.
    """
    matched_std_name = _resolve_chemical_name(
        db, req,
        f"인식된 텍스트 '{req.ocr_text}'에서 반출할 시약을 식별할 수 없습니다."
    )

    query = db.query(models.Chemical).filter(
        models.Chemical.name == matched_std_name,
        models.Chemical.current_status == "비치중",
    )
    if req.chemical_id:
        query = query.filter(models.Chemical.id == req.chemical_id)
    candidate_chems = query.all()
    if not candidate_chems:
        raise HTTPException(
            status_code=404,
            detail=f"비치 중인 시약 목록에서 '{matched_std_name}'을(를) 찾을 수 없습니다."
        )

    # 병을 먼저 들고 와서 스캔한 경우 = 회수가 이미 일어난 사후 스캔
    # → 무게 감소를 다시 기다리지 않고 즉시 확정한다. 판정 두 갈래:
    #  (a) 그 칸에서 방금(180초 내) 세션 없이 무게가 빠짐 — 칸에 다른 병이
    #      남아 있어도 동작하며, 감소량으로 어느 병인지 식별·검증까지 된다
    #  (b) 칸이 아예 비어 있음 (1병 1칸의 기존 판정, 백업)
    for chem in candidate_chems:
        if not chem.shelf_id:
            continue
        shelf = db.query(models.Shelf).filter(models.Shelf.id == chem.shelf_id).first()
        if not shelf:
            continue
        drop = checkout_flow.claim_recent_drop(chem.shelf_id)
        if drop is not None or shelf.weight < checkout_flow.EMPTY_SHELF_KG:
            result = checkout_flow.finalize_already_removed(
                db, chem, req.username, measured_delta=drop
            )
            db.refresh(chem)
            return {
                "status": "success",
                "already_removed": True,
                "chemical": chem,
                "weight_verified": result["weight_verified"],
                "measured_delta": result["measured_delta"],
            }

    candidates = checkout_flow.start(
        db, matched_std_name, req.username, chemical_id=req.chemical_id
    )
    return {
        "status": "pending",
        "chemical_name": matched_std_name,
        "candidates": candidates,
        "timeout_seconds": checkout_session["timeout_seconds"],
    }

@router.post("/scan-out/force")
def scan_out_force(req: schemas.ScanOutForceRequest, db: Session = Depends(get_db)):
    """무게 감소가 감지되지 않았을 때(타임아웃) 사용자가 확인 없이 기록하는 경로."""
    query = db.query(models.Chemical).filter(models.Chemical.current_status == "비치중")
    if req.chemical_id:
        query = query.filter(models.Chemical.id == req.chemical_id)
    elif req.chemical_name:
        query = query.filter(models.Chemical.name == req.chemical_name)
    else:
        raise HTTPException(status_code=400, detail="chemical_id 또는 chemical_name 이 필요합니다.")

    chem = query.first()
    if not chem:
        raise HTTPException(status_code=404, detail="비치 중인 해당 시약을 찾을 수 없습니다.")

    checkout_flow.force_finalize(db, chem, req.username)
    db.refresh(chem)
    return {"status": "success", "chemical": chem}

@router.post("/select-led")
def select_led(req: schemas.SelectLedRequest, db: Session = Depends(get_db)):
    chem = db.query(models.Chemical).filter(models.Chemical.id == req.chem_id).first()
    if not chem:
        raise HTTPException(status_code=404, detail="시약을 찾을 수 없습니다.")
        
    if chem.current_status != "비치중":
        raise HTTPException(status_code=400, detail="반입 상태인 시약에 대해서만 LED를 켤 수 있습니다.")
        
    shelf = db.query(models.Shelf).filter(models.Shelf.id == chem.shelf_id).first()
    if not shelf:
        raise HTTPException(status_code=404, detail="보관된 선반 모듈을 찾을 수 없습니다.")
        
    shelf.led_on = True
    shelf.led_message = f"찾는 시약 위치: {chem.name}"
    shelf.updated_time = datetime.now().strftime("%H:%M:%S")
    
    db.commit()
    return {
        "status": "success",
        "shelf_id": shelf.id,
        "chemical_name": chem.name,
        "parent_shelf": shelf.parent_shelf,
        "row": shelf.row,
        "col": shelf.col
    }

@router.post("/{chem_id}/expiration")
def set_chemical_expiration(chem_id: str, req: schemas.ExpirationRequest, db: Session = Depends(get_db)):
    """반입 때 '나중에 입력하기'로 미뤄둔 유통기한을 웹에서 입력한다."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.expiration_date):
        raise HTTPException(status_code=400, detail="날짜는 YYYY-MM-DD 형식이어야 합니다.")
    chem = db.query(models.Chemical).filter(models.Chemical.id == chem_id).first()
    if not chem:
        raise HTTPException(status_code=404, detail="시약을 찾을 수 없습니다.")
    chem.expiration_date = req.expiration_date
    db.commit()
    return {"status": "success", "chemical_id": chem_id, "expiration_date": req.expiration_date}


@router.post("/{chem_id}/capacity")
def set_chemical_capacity(chem_id: str, req: schemas.CapacityUpdateRequest,
                          db: Session = Depends(get_db)):
    """병 용량(가득 총 무게)을 웹에서 수동 수정 — 자동 추정이 틀리거나 실패한
    병을 바로잡는 최종 수단. 실측 래칫과 달리 하향 수정도 허용하지 않으면
    의미가 없으므로 그대로 덮어쓴다."""
    chem = db.query(models.Chemical).filter(models.Chemical.id == chem_id).first()
    if not chem:
        raise HTTPException(status_code=404, detail="시약을 찾을 수 없습니다.")
    chem.capacity_kg = round(req.capacity_kg, 2)
    db.commit()
    return {"status": "success", "chemical_id": chem_id, "capacity_kg": chem.capacity_kg}


@router.post("/{chem_id}/dispose")
def dispose_chemical(chem_id: str, req: Dict[str, str], db: Session = Depends(get_db)):
    """폐기: 시약을 재고에서 삭제하고, 그 시약의 반입·반출 기록도 모두 지운다.
    행 자체가 삭제되므로 재고 리스트·유통기한/혼재 경고·입출고 기록에서 함께 사라진다.
    (로그는 시약 id 로 묶여 있어 같은 이름의 다른 병 기록은 남는다)"""
    chem = db.query(models.Chemical).filter(models.Chemical.id == chem_id).first()
    if not chem:
        raise HTTPException(status_code=404, detail="시약을 찾을 수 없습니다.")

    chem_name = chem.name
    removed_logs = db.query(models.Log).filter(
        models.Log.chemical_id == chem.id
    ).delete(synchronize_session=False)
    db.delete(chem)
    db.commit()
    return {"status": "success", "chemical_id": chem_id, "chemical_name": chem_name,
            "removed_logs": str(removed_logs)}

@router.get("/alerts")
def get_alerts(db: Session = Depends(get_db), placed_chemical_id: Optional[str] = None):
    # placed_chemical_id: 반입 직후 확인에서 방금 놓은 병. 그 병이 낀 혼재 경고는 그 병을
    # 옮길 병으로 보고 추천 칸을 고른다 (없으면 기존대로 경고 쌍의 첫 병 기준).
    # 1. Un-scanned checkouts:
    # A chemical with status "비치중" but its shelf weight is near 0
    unscanned_checkouts = []
    chemicals = db.query(models.Chemical).filter(models.Chemical.current_status == "비치중").all()
    # 위치 이동(지정 위치/안전 위치로 옮기기) 중인 병은 옮기려고 들어 올려 칸이 비어도
    # 무단 반출이 아니다 — 이동 세션이 끝날 때까지 판정에서 제외한다
    relocating_id = checkin_session.get("relocate_chemical_id") if checkin_session["active"] else ""
    for c in chemicals:
        if c.id == relocating_id:
            continue
        if c.shelf_id:
            shelf = db.query(models.Shelf).filter(models.Shelf.id == c.shelf_id).first()
            # 유예(GRACE_S) 안에는 띄우지 않는다 — 병을 먼저 들고 스캔하러 오는 정상 순서.
            # 푸시 알림(unscanned_alert.watch)과 같은 타이밍에 모달이 뜨게 맞춘다.
            if (shelf and shelf.weight < 0.1 and c.weight >= 0.2
                    and unscanned_alert.drop_past_grace(c.shelf_id)):
                # since: 이 회수가 감지된 시각 — 앱이 '같은 회수를 또 띄우지 않기' 판정에 쓴다
                # (이름이 같은 다른 회수는 새 경보로 떠야 하므로 이름이 아니라 시각으로 구분)
                drop = checkout_flow.peek_unclaimed_drop(c.shelf_id)
                unscanned_checkouts.append({
                    "chemical_id": c.id,
                    "chemical_name": c.name,
                    "shelf_id": c.shelf_id,
                    "shelf_desc": f"선반 {shelf.parent_shelf} · {shelf.row}행 {shelf.col}열",
                    "since": drop[1] if drop else None,
                })
                
    # 2. Expired chemicals:
    expired_chemicals = []
    current_date = datetime.now().date()
    for c in chemicals:
        if c.expiration_date:
            try:
                exp_date = datetime.strptime(c.expiration_date, "%Y-%m-%d").date()
                if exp_date < current_date:
                    days_over = (current_date - exp_date).days
                    expired_chemicals.append({
                        "chemical_id": c.id,
                        "chemical_name": c.name,
                        "expiration_date": c.expiration_date,
                        "days_over": days_over
                    })
                elif (exp_date - current_date).days <= 30: # Expiring within 30 days
                    expired_chemicals.append({
                        "chemical_id": c.id,
                        "chemical_name": c.name,
                        "expiration_date": c.expiration_date,
                        "days_over": 0,
                        "warning": "임박"
                    })
            except ValueError:
                pass
                
    # 3. Co-storage safety warning (LLM / Rule-based dynamic incompatibility check):
    co_storage_warnings = []

    # Ensure all checked-in chemicals have incompatible_chemicals analyzed
    for c in chemicals:
        llm_safety.ensure_chemical_incompatibility_info(c, db)

    stored_chems = [c for c in chemicals if c.shelf_id]
    n = len(stored_chems)

    for i in range(n):
        for j in range(i + 1, n):
            c1 = stored_chems[i]
            c2 = stored_chems[j]

            shelf1 = db.query(models.Shelf).filter(models.Shelf.id == c1.shelf_id).first()
            shelf2 = db.query(models.Shelf).filter(models.Shelf.id == c2.shelf_id).first()

            if not shelf1 or not shelf2:
                continue

            # 혼재 판정 범위: 같은 선반의 같은 행 (열 무관).
            # 이전의 8방향 거리 1 기준에서 행 단위 기준으로 변경.
            is_adjacent = False
            if shelf1.parent_shelf and shelf2.parent_shelf and shelf1.parent_shelf == shelf2.parent_shelf:
                if (shelf1.row or 1) == (shelf2.row or 1):
                    is_adjacent = True
            elif shelf1.id == shelf2.id:
                is_adjacent = True

            if not is_adjacent:
                continue

            # 같은 물질(농도만 다른 병 포함)끼리는 혼재가 아니다
            if llm_safety.same_substance(c1.name, c2.name):
                continue

            # Check incompatibility matching between c1 and c2
            incomp1 = json.loads(c1.incompatible_chemicals) if c1.incompatible_chemicals else []
            incomp2 = json.loads(c2.incompatible_chemicals) if c2.incompatible_chemicals else []

            # 데모 시약끼리는 고정 혼재표로만 판정한다. 그 외에는 직접 지목 + 카테고리명
            # ("강산"·"강염기" 등) 확장 매칭, 산끼리는 직접 지목만 인정
            is_incompatible, fixed_reason = llm_safety.pair_incompatible(
                c1.name, incomp1, c2.name, incomp2)

            if is_incompatible:
                # 옮길 병: 방금 놓은 병이 이 쌍에 있으면 그 병, 아니면 첫 병. 예전에는 늘 첫 병
                # 기준이라, 방금 놓은 병은 갈 곳이 있어도 상대 병이 갈 곳이 없으면 '선반 C'가 떴다
                mover = c2 if placed_chemical_id and c2.id == placed_chemical_id else c1
                safe_id, safe_desc = llm_safety.find_recommended_safe_shelf(mover, db)
                s1_desc = f"{shelf1.parent_shelf}·{shelf1.row}행{shelf1.col}열"
                s2_desc = f"{shelf2.parent_shelf}·{shelf2.row}행{shelf2.col}열"
                co_storage_warnings.append({
                    "chemical_1_id": c1.id,
                    "chemical_1_name": c1.name,
                    "chemical_2_id": c2.id,
                    "chemical_2_name": c2.name,
                    "shelf_desc": f"선반 {s1_desc} 및 {s2_desc}",
                    "message": f"🚨 [혼재 위험] {c1.name}와(과) {c2.name}은(는) 인접 보관 금지 시약입니다.",
                    "reason": fixed_reason or c1.incompatible_reason or c2.incompatible_reason or "인접 보관 시 격렬한 반응, 유독가스 또는 화재/폭발 위험",
                    "recommended_safe_shelf_id": safe_id,
                    "recommended_safe_shelf_desc": safe_desc,
                    "move_chemical_id": mover.id,
                    "move_chemical_name": mover.name,
                })

    return {
        "unscanned_checkouts": unscanned_checkouts,
        "expired_chemicals": expired_chemicals,
        "co_storage_warnings": co_storage_warnings,
        # 유예 시간 뒤 확정된 무단 반출 이벤트 — 앱 감시 서비스가 WebSocket 유실 대비로 폴링
        "unscanned_events": unscanned_alert.recent_events(),
        # 무단 반입: 유예가 지나도록 청구되지 않은 적재(현재 상태) + 확정 이벤트(폴링용)
        "unscanned_checkins": unscanned_alert.pending_checkins(db),
        "unscanned_checkin_events": unscanned_alert.recent_checkin_events(),
    }
