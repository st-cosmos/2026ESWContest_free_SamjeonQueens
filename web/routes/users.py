import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models
import schemas
import shelf_power

router = APIRouter(prefix="/api/users", tags=["users"])


def _validate_password(password: str):
    """웹 로그인용 비밀번호 규칙: 8자 이상, 숫자·특수문자 포함."""
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="비밀번호는 8자 이상이어야 합니다.")
    if not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="비밀번호에 숫자를 포함해야 합니다.")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise HTTPException(status_code=400, detail="비밀번호에 특수문자를 포함해야 합니다.")


def _validate_pin(pin: str):
    """앱 로그인용 PIN 규칙: 정확히 4자리 숫자."""
    if not re.fullmatch(r"\d{4}", pin or ""):
        raise HTTPException(status_code=400, detail="PIN번호는 4자리 숫자여야 합니다.")


@router.post("/register", response_model=schemas.UserResponse)
def register_user(req: schemas.UserRegister, db: Session = Depends(get_db)):
    # 아이디 규칙(4자 이상, 영문·숫자·._-)은 schemas.UserRegister 의 validator 가 먼저 검사한다.
    if not req.nickname.strip():
        raise HTTPException(status_code=400, detail="별명을 입력하세요.")
    _validate_password(req.password)
    _validate_pin(req.pin)

    db_user = db.query(models.User).filter(models.User.username == req.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 사용자 이름입니다.")

    new_user = models.User(
        username=req.username,
        password=req.password,
        nickname=req.nickname,
        pin=req.pin,
        role=req.role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return schemas.UserResponse(
        username=new_user.username,
        nickname=new_user.nickname,
        role=new_user.role,
        created_at=new_user.created_at.strftime("%Y-%m-%d %H:%M:%S")
    )

@router.post("/login", response_model=schemas.UserResponse)
def login_user(req: schemas.UserLogin, db: Session = Depends(get_db)):
    """웹 로그인: 긴 비밀번호로 인증."""
    user = db.query(models.User).filter(models.User.username == req.username).first()
    if not user or user.password != req.password:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    return schemas.UserResponse(
        username=user.username,
        nickname=user.nickname,
        role=user.role,
        created_at=user.created_at.strftime("%Y-%m-%d %H:%M:%S")
    )

@router.post("/login-pin", response_model=schemas.UserResponse)
def login_user_pin(req: schemas.UserPinLogin, db: Session = Depends(get_db)):
    """안드로이드 앱 로그인: 4자리 PIN으로 인증.

    성공하면 앱 세션을 등록해 선반들을 깨운다 (docs/power-modes.md —
    게이트웨이가 /api/shelf-power 를 폴링해 전파하므로 앱 수정 불필요).
    """
    user = db.query(models.User).filter(models.User.username == req.username).first()
    if not user or user.pin != req.pin:
        raise HTTPException(status_code=401, detail="PIN번호가 올바르지 않습니다.")

    shelf_power.enter(user.username)

    return schemas.UserResponse(
        username=user.username,
        nickname=user.nickname,
        role=user.role,
        created_at=user.created_at.strftime("%Y-%m-%d %H:%M:%S")
    )


@router.put("/{username}", response_model=schemas.UserResponse)
def update_profile(username: str, req: schemas.UserProfileUpdate, db: Session = Depends(get_db)):
    """본인 프로필 수정: 별명은 필수, 비밀번호·PIN은 입력했을 때만 검증 후 변경한다."""
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    nickname = (req.nickname or "").strip()
    if not nickname:
        raise HTTPException(status_code=400, detail="별명을 입력하세요.")

    if req.password:
        _validate_password(req.password)
        user.password = req.password
    if req.pin:
        _validate_pin(req.pin)
        user.pin = req.pin

    user.nickname = nickname
    db.commit()
    db.refresh(user)

    return schemas.UserResponse(
        username=user.username,
        nickname=user.nickname,
        role=user.role,
        created_at=user.created_at.strftime("%Y-%m-%d %H:%M:%S")
    )

@router.get("", response_model=List[schemas.UserResponse])
def get_users(db: Session = Depends(get_db)):
    users = db.query(models.User).all()
    return [
        schemas.UserResponse(
            username=u.username,
            nickname=u.nickname,
            role=u.role,
            created_at=u.created_at.strftime("%Y-%m-%d %H:%M:%S")
        ) for u in users
    ]


@router.delete("/{username}")
def delete_user(username: str, db: Session = Depends(get_db)):
    """사용자 삭제 API"""
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    db.delete(user)
    db.commit()
    return {"status": "ok", "message": f"{username} 사용자가 삭제되었습니다."}
