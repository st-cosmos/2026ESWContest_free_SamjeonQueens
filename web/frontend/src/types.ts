// 백엔드 API 응답 타입 (web/schemas.py 의 *Response 모델과 routes/ 의 dict 응답 기준)

export interface User {
  username: string;
  nickname: string;
  role: string; // 관리자 | 연구원
  created_at: string;
}

export interface Chemical {
  id: string;
  name: string;
  cas_no: string | null;
  formula: string | null;
  weight: number;
  shelf_id: string | null;
  shelf_row: number | null;
  shelf_col: number | null;
  current_status: string; // 비치중 | 반출중
  holder_username: string | null;
  time_in: string | null;
  time_out: string | null;
  expiration_date: string | null;
  manufacturer: string | null;
  incompatible_chemicals?: string | null;
  incompatible_reason?: string | null;
  // 가득 찼을 때 총 무게(kg) — 라벨/사진 추정 + 반입 실측 래칫. 없으면 500g 폴백.
  capacity_kg?: number | null;
}

export interface TransactionLog {
  id: number;
  chemical_id: string;
  chemical_name: string;
  action: string; // 반입 | 반출
  operator_name: string;
  details: string | null;
  timestamp: string;
}

export interface ShelfDevice {
  id: string;
  name: string | null;
  parent_shelf: string | null;
  row: number | null;
  col: number | null;
  weight: number;
  prev_weight: number;
  battery: number;
  status: string; // registered | unregistered
  led_on: boolean;
  led_message: string;
  updated_time: string;
  recently_reset?: boolean; // 방금 리셋 버튼이 눌려 다시 잡힌 기기 (블링크 식별용)
}

export interface ShelfConfig {
  id: string;
  name: string;
  rows: number;
  cols: number;
}

export interface Order {
  id: number;
  chemical_name: string;
  formula: string | null;
  manufacturer: string | null;
  current_qty: string | null; // 예: "15%"
  threshold_qty: string | null; // 예: "30%"
  price: number;
  selected: boolean;
  status: string; // pending | ordered
  purchase_link: string | null; // 시약 구매 사이트 URL 메모
}

export interface ExpiredAlert {
  chemical_id: string;
  chemical_name: string;
  expiration_date: string;
  days_over: number;
  warning?: string; // "임박"
}

export interface CoStorageAlert {
  chemical_1_id: string;
  chemical_1_name: string;
  chemical_2_id: string;
  chemical_2_name: string;
  shelf_desc: string;
  message: string;
  reason?: string;
}

export interface UnscannedCheckout {
  chemical_id: string;
  chemical_name: string;
  shelf_id: string;
  shelf_desc: string;
}

export interface Alerts {
  unscanned_checkouts: UnscannedCheckout[];
  expired_chemicals: ExpiredAlert[];
  co_storage_warnings: CoStorageAlert[];
}

export const EMPTY_ALERTS: Alerts = {
  unscanned_checkouts: [],
  expired_chemicals: [],
  co_storage_warnings: [],
};

export interface SessionUser {
  username: string;
  nickname: string;
  role: string;
}
