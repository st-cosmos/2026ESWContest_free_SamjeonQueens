from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict
from database import get_db
import models
import schemas
import services.capacity as capacity

router = APIRouter(prefix="/api/orders", tags=["orders"])

# 잔량 % 환산: 병별 capacity_kg(라벨/사진 추정 + 실측 래칫)을 우선 쓰고,
# 미추정 병만 기본값 520g 가정 (용량 추정 기본값, 프런트 재고 관리와 동일 기준)
CAPACITY_KG = capacity.DEFAULT_CAPACITY_KG
# 이 잔량(%) 이하로 떨어지면 주문 목록에 자동 추가한다 (반출중·비치중 무관)
LOW_STOCK_PCT = 25


def _sync_low_stock_orders(db: Session):
    """잔량이 LOW_STOCK_PCT 이하인 시약을 주문 대기 목록에 자동 등록한다.

    - 같은 이름의 병이 여러 개면 가장 적게 남은 병 기준으로 판단
    - 같은 이름의 pending 주문이 이미 있으면 현재 잔량 표기만 갱신
    - ordered 상태 주문만 있으면 재추가하지 않음 (입고 대기 중으로 간주)
    """
    by_name: Dict[str, list] = {}
    for o in db.query(models.Order).all():
        by_name.setdefault(o.chemical_name, []).append(o)

    lowest: Dict[str, int] = {}
    chem_of: Dict[str, models.Chemical] = {}
    for chem in db.query(models.Chemical).all():
        cap = chem.capacity_kg or CAPACITY_KG
        pct = max(0, min(100, round((chem.weight or 0.0) / cap * 100)))
        if chem.name not in lowest or pct < lowest[chem.name]:
            lowest[chem.name] = pct
            chem_of[chem.name] = chem

    changed = False
    for name, pct in lowest.items():
        if pct > LOW_STOCK_PCT:
            continue
        existing = by_name.get(name, [])
        pending = next((o for o in existing if o.status == "pending"), None)
        if pending is not None:
            if pending.current_qty != f"{pct}%":
                pending.current_qty = f"{pct}%"
                changed = True
        elif not existing:
            chem = chem_of[name]
            db.add(models.Order(
                chemical_name=name,
                formula=chem.formula,
                manufacturer=chem.manufacturer,
                current_qty=f"{pct}%",
                threshold_qty=f"{LOW_STOCK_PCT}%",
                price=0,
            ))
            changed = True
    if changed:
        db.commit()


@router.get("")
def get_orders(db: Session = Depends(get_db)):
    # 조회 시마다 잔량 저하 시약을 주문 목록과 동기화한다
    _sync_low_stock_orders(db)
    return db.query(models.Order).all()

@router.post("")
def create_order(req: schemas.OrderCreate, db: Session = Depends(get_db)):
    new_ord = models.Order(
        chemical_name=req.chemical_name,
        formula=req.formula,
        manufacturer=req.manufacturer,
        current_qty=req.current_qty,
        threshold_qty=req.threshold_qty,
        price=req.price,
        purchase_link=req.purchase_link
    )
    db.add(new_ord)
    db.commit()
    db.refresh(new_ord)
    return new_ord

@router.put("/{order_id}")
def update_order(order_id: int, req: schemas.OrderUpdate, db: Session = Depends(get_db)):
    ord_item = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not ord_item:
        raise HTTPException(status_code=404, detail="주문 항목을 찾을 수 없습니다.")
        
    if req.selected is not None:
        ord_item.selected = req.selected
    if req.status:
        ord_item.status = req.status
    if req.purchase_link is not None:
        # 빈 문자열이 오면 링크 삭제로 처리한다
        ord_item.purchase_link = req.purchase_link.strip() or None

    db.commit()
    db.refresh(ord_item)
    return ord_item

@router.post("/confirm")
def confirm_orders(db: Session = Depends(get_db)):
    # Find all selected orders
    selected_items = db.query(models.Order).filter(models.Order.selected == True).all()
    if not selected_items:
        raise HTTPException(status_code=400, detail="선택된 주문 항목이 없습니다.")
        
    for item in selected_items:
        item.status = "ordered"
        item.selected = False # uncheck after ordering
        
    db.commit()
    return {"status": "success", "count": len(selected_items)}
