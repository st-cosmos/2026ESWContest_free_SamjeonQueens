from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import time

import checkout_flow
from database import get_db
import models
import schemas
from session_store import (
    RELOCATE_TIMEOUT_S, checkin_session, checkout_session, reset_checkin,
)

router = APIRouter(prefix="/api/checkin-session", tags=["session"])
checkout_router = APIRouter(prefix="/api/checkout-session", tags=["session"])


@router.get("")
def get_checkin_session():
    if checkin_session["active"]:
        elapsed = time.time() - checkin_session["start_time"]
        if elapsed > checkin_session["timeout_seconds"]:
            # Timeout has occurred
            reset_checkin()
            return {
                "active": False,
                "chemical_name": "",
                "time_left": 0.0,
                "timeout": True,
            }
        return {
            "active": True,
            "chemical_name": checkin_session["chemical_name"],
            "time_left": max(0.0, round(checkin_session["timeout_seconds"] - elapsed, 1)),
            "timeout": False,
        }
    return {
        "active": False,
        "chemical_name": "",
        "time_left": 0.0,
        "timeout": False,
    }


@router.post("/cancel")
def cancel_checkin_session():
    reset_checkin()
    return {"status": "success"}


@router.post("/relocate")
def relocate_checkin(req: schemas.RelocateRequest, db: Session = Depends(get_db)):
    """잘못된 칸에 안착된 병을 지정 위치로 옮겨 놓기.

    반입은 병이 놓인 칸으로 이미 기록돼 있다. 여기서 위치 이동 세션을 열면
    다음 무게 증가(안착)가 신규 반입이 아니라 이 병의 위치 변경으로 처리된다
    (routes/shelves._handle_checkin_increase). 앱은 기존 반입 세션 폴링
    (GET /api/checkin-session)으로 완료·타임아웃을 그대로 감지한다.
    """
    import routes.shelves as shelf_routes

    chem = db.query(models.Chemical).filter(models.Chemical.id == req.chemical_id).first()
    if not chem or chem.current_status != "비치중":
        raise HTTPException(status_code=404, detail="옮길 시약을 찾을 수 없습니다.")
    target = db.query(models.Shelf).filter(models.Shelf.id == req.target_shelf_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="지정 위치를 찾을 수 없습니다.")

    reset_checkin()
    checkin_session["active"] = True
    checkin_session["chemical_name"] = chem.name
    checkin_session["start_time"] = time.time()
    checkin_session["timeout_seconds"] = RELOCATE_TIMEOUT_S
    checkin_session["username"] = req.username or ""
    checkin_session["relocate_chemical_id"] = chem.id
    checkin_session["target_shelf_id"] = target.id

    target.led_on = True
    target.led_message = f"기존 반입 이력 위치: {chem.name}"
    target.updated_time = datetime.now().strftime("%H:%M:%S")
    db.commit()

    # 버튼을 누르기 전에 이미 옮겨 놓은 경우 — 지정 칸의 최근 안착을 청구해 즉시 확정
    already_moved = False
    rise = shelf_routes.claim_recent_rise(target.id)
    if rise is not None and checkout_flow.weight_within_tolerance(chem.weight, rise):
        already_moved = shelf_routes._handle_checkin_increase(db, target, rise) is not None

    return {
        "status": "success",
        "already_moved": already_moved,
        "timeout_seconds": RELOCATE_TIMEOUT_S,
    }

@router.post("/expiration")
def set_checkin_expiration(req: schemas.ExpirationRequest):
    if checkin_session["active"]:
        checkin_session["expiration_date"] = req.expiration_date
        return {"status": "success"}
    return {"status": "error", "message": "No active session"}


# --- 반출 세션 (무게 감소 확정 대기) ---

@checkout_router.get("")
def get_checkout_session(db: Session = Depends(get_db)):
    """반출 세션 상태 폴링. event(경고)는 1회 전달 후 소거되고,
    result(확정 결과)는 다음 세션 시작 전까지 유지된다."""
    if checkout_session["active"] and checkout_flow.is_expired():
        checkout_flow.expire(db)
        return {
            "active": False,
            "chemical_name": "",
            "time_left": 0.0,
            "timeout": True,
            "event": None,
            "result": None,
        }

    event = checkout_session["last_event"]
    checkout_session["last_event"] = None  # 1회 전달

    if checkout_session["active"]:
        elapsed = time.time() - checkout_session["start_time"]
        return {
            "active": True,
            "chemical_name": checkout_session["chemical_name"],
            "time_left": max(0.0, round(checkout_session["timeout_seconds"] - elapsed, 1)),
            "timeout": False,
            "event": event,
            "result": None,
        }

    return {
        "active": False,
        "chemical_name": "",
        "time_left": 0.0,
        "timeout": False,
        "event": event,
        "result": checkout_session["last_result"],
    }


@checkout_router.post("/cancel")
def cancel_checkout_session(db: Session = Depends(get_db)):
    checkout_flow.cancel(db)
    return {"status": "success"}
