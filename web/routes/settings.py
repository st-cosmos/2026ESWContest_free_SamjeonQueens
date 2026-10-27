from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict
from database import get_db
import models

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 유통기한 미인식 시 직접 입력 달력의 기본값: 반입일로부터 N개월 뒤
DEFAULT_EXPIRY_MONTHS = 12
EXPIRY_KEY = "default_expiry_months"


@router.get("/default-expiry")
def get_default_expiry(db: Session = Depends(get_db)):
    row = db.query(models.Setting).filter(models.Setting.key == EXPIRY_KEY).first()
    months = DEFAULT_EXPIRY_MONTHS
    if row is not None:
        try:
            months = max(1, int(row.value))
        except ValueError:
            pass
    return {"months": months}


@router.post("/default-expiry")
def set_default_expiry(req: Dict[str, int], db: Session = Depends(get_db)):
    months = req.get("months")
    if not isinstance(months, int) or months < 1 or months > 120:
        raise HTTPException(status_code=400, detail="개월 수는 1~120 사이여야 합니다.")
    row = db.query(models.Setting).filter(models.Setting.key == EXPIRY_KEY).first()
    if row:
        row.value = str(months)
    else:
        db.add(models.Setting(key=EXPIRY_KEY, value=str(months)))
    db.commit()
    return {"months": months}
