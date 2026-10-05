import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List

class UserRegister(BaseModel):
    username: str = Field(..., min_length=4)
    password: str        # 웹 로그인용 (8자 이상, 숫자·특수문자 포함)
    nickname: str
    pin: str             # 앱 로그인용 4자리 PIN
    role: str

    @field_validator('username')
    @classmethod
    def validate_username(cls, v: str) -> str:
        v_str = (v or "").strip()
        if len(v_str) < 4:
            raise ValueError('아이디는 4자 이상이어야 합니다.')
        if not re.match(r'^[a-zA-Z0-9._-]+$', v_str):
            raise ValueError('아이디는 영문, 숫자 및 특수기호(._-)만 허용됩니다.')
        return v_str

class UserLogin(BaseModel):
    # 웹 로그인: 긴 비밀번호로 인증
    username: str
    password: str

class UserPinLogin(BaseModel):
    # 안드로이드 앱 로그인: 4자리 PIN으로 인증
    username: str
    pin: str

class UserProfileUpdate(BaseModel):
    # 본인 프로필 수정: 별명은 필수, 비밀번호·PIN은 비우면 기존 값 유지
    nickname: str
    password: Optional[str] = None
    pin: Optional[str] = None

class UserResponse(BaseModel):
    username: str
    nickname: str
    role: str
    created_at: str

    class Config:
        from_attributes = True

class WeightUpdate(BaseModel):
    weight: float
    battery: Optional[int] = None

class LedUpdate(BaseModel):
    led_on: bool
    led_message: str = ""

class ScanInRequest(BaseModel):
    ocr_text: str
    username: str  # who scanned it
    chemical_name: Optional[str] = None  # 앱에서 매칭/확인이 끝난 표준명 (있으면 매칭 생략)

class ExpirationRequest(BaseModel):
    expiration_date: str

class RelocateRequest(BaseModel):
    # 잘못된 칸에 안착된 병을 지정 위치로 옮겨 놓기
    chemical_id: str
    target_shelf_id: str
    username: Optional[str] = None

class ScanOutRequest(BaseModel):
    ocr_text: str
    username: str  # who scanned it
    chemical_name: Optional[str] = None  # 앱에서 매칭/확인이 끝난 표준명 (있으면 매칭 생략)
    chemical_id: Optional[str] = None    # 직접 선택으로 특정 병을 지정한 경우

class ScanOutForceRequest(BaseModel):
    # 무게 감지 타임아웃 후 '무게 확인 없이 기록' 선택 시
    username: str
    chemical_name: Optional[str] = None
    chemical_id: Optional[str] = None

class MatchRequest(BaseModel):
    # 앱이 스캔 중 주기적으로 보내는 인식 시도 (OCR 누적 텍스트 + 병의 바코드/QR)
    ocr_text: str = ""
    barcode: Optional[str] = None
    # LLM 폴백용 라벨 사진 (JPEG base64) — 비전 모델이 OCR 오독을 바로잡는다
    image_b64: Optional[str] = None

class CapacityEstimateRequest(BaseModel):
    # 반입 스캔 직후 병의 가득 무게(용량) 추정 요청 — 잔량 % 분모
    chemical_name: str
    ocr_text: str = ""
    image_b64: Optional[str] = None            # (구버전 호환) 단일 사진
    images_b64: Optional[List[str]] = None     # 글자 최다 프레임 + 최신 프레임 등 여러 장

class CapacityUpdateRequest(BaseModel):
    # 웹 재고 관리에서 병 용량(가득 총 무게)을 수동 수정
    capacity_kg: float = Field(..., gt=0.04, le=30)

class MatchConfirmRequest(BaseModel):
    # 사용자 확인/직접 선택 결과 학습용
    chemical_name: str
    barcode: Optional[str] = None
    matched_token: Optional[str] = None

class SelectLedRequest(BaseModel):
    chem_id: str

class ShelfRegister(BaseModel):
    name: str               # e.g. 수납칸 A7
    parent_shelf: str       # e.g. A, B
    row: int
    col: int

class OrderCreate(BaseModel):
    chemical_name: str
    formula: Optional[str] = None
    manufacturer: Optional[str] = None
    current_qty: str
    threshold_qty: str
    price: int
    purchase_link: Optional[str] = None

class OrderUpdate(BaseModel):
    selected: Optional[bool] = None
    status: Optional[str] = None
    purchase_link: Optional[str] = None

class ShelfResponse(BaseModel):
    id: str
    name: Optional[str] = None
    parent_shelf: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None
    weight: float
    prev_weight: float
    battery: int
    status: str
    led_on: bool
    led_message: str
    updated_time: str
    # 방금 리셋 버튼이 눌려 다시 잡힌 기기 — 미등록 기기 목록에서 블링크 식별용
    recently_reset: bool = False

    class Config:
        from_attributes = True

class ChemicalResponse(BaseModel):
    id: str
    name: str
    cas_no: Optional[str] = None
    formula: Optional[str] = None
    weight: float
    shelf_id: Optional[str] = None
    shelf_row: Optional[int] = None
    shelf_col: Optional[int] = None
    current_status: str
    holder_username: Optional[str] = None
    time_in: Optional[str] = None
    time_out: Optional[str] = None
    expiration_date: Optional[str] = None
    manufacturer: Optional[str] = None
    incompatible_chemicals: Optional[str] = None
    incompatible_reason: Optional[str] = None
    capacity_kg: Optional[float] = None

    class Config:
        from_attributes = True

class LogResponse(BaseModel):
    id: int
    chemical_id: str
    chemical_name: str
    action: str
    operator_name: str
    details: Optional[str] = None
    timestamp: str

    class Config:
        from_attributes = True

# Legacy support
class LedCommand(BaseModel):
    on: bool
    by: str

class PotentiometerEvent(BaseModel):
    value: int
    esp_id: str = "ESP-01"
