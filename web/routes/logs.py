from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models
import schemas

router = APIRouter(prefix="/api/logs", tags=["logs"])

@router.get("", response_model=List[schemas.LogResponse])
def get_logs(db: Session = Depends(get_db)):
    logs = db.query(models.Log).order_by(models.Log.timestamp.desc()).all()
    # Format the timestamp
    resp = []
    for l in logs:
        # custom date format
        dt_str = l.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        # For display like '오늘 14:32' or '어제 16:40' we can do it in front-end or here
        resp.append(schemas.LogResponse(
            id=l.id,
            chemical_id=l.chemical_id,
            chemical_name=l.chemical_name,
            action=l.action,
            operator_name=l.operator_name,
            details=l.details,
            timestamp=dt_str
        ))
    return resp
