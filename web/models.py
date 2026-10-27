from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)          # 웹 로그인용 (8자 이상, 숫자·특수문자 포함)
    pin = Column(String, nullable=False, default="0000")  # 안드로이드 앱 로그인용 4자리 PIN
    nickname = Column(String, nullable=False)
    role = Column(String, default="연구원")  # 관리자, 연구원
    created_at = Column(DateTime, default=datetime.utcnow)

class Shelf(Base):
    __tablename__ = "shelves"

    id = Column(String, primary_key=True, index=True)  # Device ID, e.g., SHELF-A3F2, ESP-01
    name = Column(String, nullable=True)               # e.g., 수납칸 A1
    parent_shelf = Column(String, nullable=True)       # e.g., A, B
    row = Column(Integer, nullable=True)               # 1-indexed
    col = Column(Integer, nullable=True)               # 1-indexed
    weight = Column(Float, default=0.0)
    prev_weight = Column(Float, default=0.0)
    battery = Column(Integer, default=100)
    status = Column(String, default="unregistered")     # registered, unregistered
    led_on = Column(Boolean, default=False)
    led_message = Column(String, default="")
    updated_time = Column(String, default="-")

class Chemical(Base):
    __tablename__ = "chemicals"

    id = Column(String, primary_key=True, index=True)  # e.g., chem_1234
    name = Column(String, index=True, nullable=False)
    cas_no = Column(String, nullable=True)
    formula = Column(String, nullable=True)
    weight = Column(Float, default=0.0)
    shelf_id = Column(String, ForeignKey("shelves.id"), nullable=True)
    shelf_row = Column(Integer, nullable=True)
    shelf_col = Column(Integer, nullable=True)
    current_status = Column(String, default="비치중")   # 비치중, 반출중
    holder_username = Column(String, nullable=True)
    time_in = Column(String, nullable=True)
    time_out = Column(String, nullable=True)
    expiration_date = Column(String, nullable=True)     # YYYY-MM-DD
    manufacturer = Column(String, nullable=True)
    # 가득 찼을 때 총 무게(내용물+용기, kg) — 잔량 % 의 분모.
    # 라벨 규격/사진으로 추정하고 반입 실측 무게로 래칫된다. 없으면 클라이언트가 0.5 폴백.
    capacity_kg = Column(Float, nullable=True)
    incompatible_chemicals = Column(String, nullable=True)  # JSON 문자열 (LLM 추출 혼재 금지 시약 목록)
    incompatible_reason = Column(String, nullable=True)     # LLM 반응 위험 원인 설명


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    chemical_id = Column(String, nullable=False)
    chemical_name = Column(String, nullable=False)
    action = Column(String, nullable=False)             # 반입, 반출
    operator_name = Column(String, nullable=False)      # Nickname or Username
    details = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    chemical_name = Column(String, nullable=False)
    formula = Column(String, nullable=True)
    manufacturer = Column(String, nullable=True)
    current_qty = Column(String, nullable=True)         # e.g., 15%
    threshold_qty = Column(String, nullable=True)       # e.g., 30%
    price = Column(Integer, default=0)                  # Price in KRW
    selected = Column(Boolean, default=False)
    status = Column(String, default="pending")          # pending, ordered
    purchase_link = Column(String, nullable=True)       # 시약 구매 사이트 URL 메모

class ShelfConfig(Base):
    __tablename__ = "shelf_configs"

    id = Column(String, primary_key=True)               # e.g., A, B
    name = Column(String, nullable=False)               # e.g., 선반 A, 선반 B
    rows = Column(Integer, default=3)
    cols = Column(Integer, default=4)

class Setting(Base):
    """연구실 공통 설정 (key-value). 예: default_expiry_months = "12"."""
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


class ChemicalAlias(Base):
    """OCR 매칭용 별칭 사전. alias 는 정규화된 문자열(소문자, 한글/영문/숫자만)."""
    __tablename__ = "chemical_aliases"

    id = Column(Integer, primary_key=True, index=True)
    alias = Column(String, unique=True, index=True, nullable=False)
    standard_name = Column(String, nullable=False)      # e.g., 에탄올 95%

class BarcodeMap(Base):
    """병에 이미 붙어 있는 바코드/QR → 시약 매핑. 첫 스캔에서 학습된다."""
    __tablename__ = "barcode_map"

    barcode = Column(String, primary_key=True, index=True)  # 디코딩된 원문 (불투명 키)
    chemical_name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

