package com.example.cameramessage

data class ChemicalData(
    val id: String,
    val name: String,
    val cas_no: String?,
    val formula: String?,
    val weight: Double,
    val shelf_id: String?,
    val shelf_row: Int?,
    val shelf_col: Int?,
    val current_status: String,
    val holder_username: String?,
    val time_in: String?,
    val time_out: String?,
    val expiration_date: String?,
    val manufacturer: String?,
    // 가득 찼을 때 총 무게(kg) — 라벨/사진 추정 + 반입 실측 래칫. 없으면 500g 폴백.
    val capacity_kg: Double? = null
)

data class TransactionLog(
    val id: Int,
    val chemical_id: String,
    val chemical_name: String,
    val action: String,
    val operator_name: String,
    val details: String?,
    val timestamp: String
)

data class UnscannedCheckout(
    val chemical_id: String,
    val chemical_name: String,
    val shelf_id: String,
    val shelf_desc: String,
    val since: Double? = null  // 이 회수가 감지된 시각 — 같은 회수를 또 띄우지 않기 위한 식별자
)

// 무단 반입(반입 스캔 없는 적재) — 유예가 지나도록 청구되지 않은 적재 (현재 상태)
data class UnscannedCheckin(
    val shelf_id: String,
    val shelf_desc: String,
    val delta_kg: Double,
    val since: Double? = null
)

// 서버가 유예 시간 뒤 확정한 무단 반입 이벤트 (WebSocket unscanned_checkin 과 동일 필드)
data class UnscannedCheckinEvent(
    val event_id: String,
    val shelf_id: String,
    val shelf_desc: String,
    val delta_kg: Double,
    val detected_at: String
)

data class ExpiredChemical(
    val chemical_id: String,
    val chemical_name: String,
    val expiration_date: String,
    val days_over: Int,
    val warning: String? = null
)

data class CoStorageWarning(
    val chemical_1_id: String,
    val chemical_1_name: String,
    val chemical_2_id: String,
    val chemical_2_name: String,
    val shelf_desc: String,
    val message: String,
    val reason: String? = null,
    val recommended_safe_shelf_id: String? = null,
    val recommended_safe_shelf_desc: String? = null
)

// 서버가 유예 시간 뒤 확정한 무단 반출 이벤트 (WebSocket unscanned_checkout 과 동일 필드)
data class UnscannedEvent(
    val event_id: String,
    val chemical_id: String,
    val chemical_name: String,
    val shelf_desc: String,
    val detected_at: String
)

data class ChemicalAlerts(
    val unscanned_checkouts: List<UnscannedCheckout>,
    val expired_chemicals: List<ExpiredChemical>,
    val co_storage_warnings: List<CoStorageWarning>,
    // 구버전 서버는 이 필드가 없을 수 있어 nullable (Gson 은 누락 필드를 null 로 둔다)
    val unscanned_events: List<UnscannedEvent>? = null,
    val unscanned_checkins: List<UnscannedCheckin>? = null,
    val unscanned_checkin_events: List<UnscannedCheckinEvent>? = null
)

data class ScanInRequest(
    val ocr_text: String,
    val username: String,
    val chemical_name: String? = null,  // 매칭/확인이 끝난 표준명 (서버 매칭 생략)
    // 병을 이미 올려놓은 칸 (무단 반입 알림의 '지금 스캔하기') — 서버가 그 적재를 청구해 즉시 확정
    val claim_shelf_id: String? = null
)

data class ExpirationRequest(
    val expiration_date: String
)

data class ScanInResponse(
    val status: String,
    val chemical_name: String,
    val has_history: Boolean,
    val recommended_shelf: String?,
    val recommended_shelf_desc: String,
    val timeout_seconds: Double,
    // 병이 이미 선반에 있어 스캔만으로 즉시 확정된 경우 (안착 대기 없음)
    val already_placed: Boolean? = null,
    val chemical_id: String? = null,
    val placed_shelf_id: String? = null
)

data class ScanOutRequest(
    val ocr_text: String,
    val username: String,
    val chemical_name: String? = null, // 매칭/확인이 끝난 표준명 (서버 매칭 생략)
    val chemical_id: String? = null    // 직접 선택으로 특정 병을 지정한 경우
)

// --- OCR/바코드 매칭 (POST /api/chemicals/match) ---

data class MatchRequest(
    val ocr_text: String,
    val barcode: String? = null,
    // LLM 폴백용 라벨 사진 (JPEG base64) — 비전 모델이 OCR 오독을 바로잡는다
    val image_b64: String? = null,
    // 스캔 정확도 측정용 (ScanExperiment): 스캔 세션 id, 실험 라벨 번호, 보관용 프레임
    val session_id: String? = null,
    val label_id: String? = null,
    val frame_b64: String? = null
)

// 측정 로그 전용 이벤트 (POST /api/chemicals/match/event) — 인식 동작에는 영향 없음
data class ScanEventRequest(
    val session_id: String,
    val event: String,              // session_start | decision
    val label_id: String? = null,
    val chemical_name: String? = null,
    val source: String? = null,     // auto | confirm | llm | manual | decline
    val method: String? = null,
    val confidence: Double? = null,
    val mode: String? = null        // checkin | checkout
)

// --- 병별 용량(가득 총 무게) 추정 (POST /api/chemicals/estimate-capacity) ---

data class CapacityEstimateRequest(
    val chemical_name: String,
    val ocr_text: String,
    val image_b64: String? = null,             // (구버전 호환) 단일 사진
    val images_b64: List<String>? = null       // 글자 최다 프레임 + 최신 프레임
)

data class CapacityEstimateResponse(
    val status: String,
    val capacity_kg: Double? = null,
    val source: String? = null,   // llm | label
    val detail: String? = null
)

data class MatchCandidate(
    val name: String,
    val score: Double
)

data class MatchResult(
    val status: String,          // matched | needs_confirmation | no_match
    val method: String?,         // barcode | cas | alias | fuzzy
    val chemical_name: String?,
    val confidence: Double,
    val candidates: List<MatchCandidate>,
    val matched_token: String?
)

data class MatchConfirmRequest(
    val chemical_name: String,
    val barcode: String? = null,
    val matched_token: String? = null,
    val session_id: String? = null   // 측정 로그용
)

// scan-out 은 즉시 확정하지 않고 반출 세션을 시작한다 (status = "pending").
// force 기록 응답은 chemical 을 담아 온다 (status = "success").
data class ScanOutResponse(
    val status: String,
    val chemical_name: String? = null,
    val candidates: Int? = null,
    val timeout_seconds: Double? = null,
    val chemical: ChemicalData? = null,
    val weight_verified: Boolean? = null,   // 사후 스캔 즉시 확정 시 검증 결과
    val measured_delta: Double? = null,
    val already_removed: Boolean? = null    // 이미 선반에서 회수된 병의 사후 스캔 여부
)

data class ScanOutForceRequest(
    val username: String,
    val chemical_name: String? = null,
    val chemical_id: String? = null
)

// --- 반출 세션 (GET /api/checkout-session) ---

data class CheckoutEvent(
    val type: String,      // wrong_item 등
    val message: String?
)

data class CheckoutResultData(
    val chemical: ChemicalData,
    val weight_verified: Boolean?,  // true=검증 통과, false=불일치, null=무게 확인 없이 기록
    val measured_delta: Double?
)

data class CheckoutSessionState(
    val active: Boolean,
    val chemical_name: String,
    val time_left: Double,
    val timeout: Boolean,
    val event: CheckoutEvent? = null,
    val result: CheckoutResultData? = null
)

data class SelectLedRequest(
    val chem_id: String
)

data class DisposeRequest(
    val username: String
)

data class DefaultExpirySetting(
    val months: Int
)

data class SelectLedResponse(
    val status: String,
    val shelf_id: String,
    val chemical_name: String,
    val parent_shelf: String,
    val row: Int,
    val col: Int
)

// 잘못된 칸에 안착된 병을 지정 위치로 옮겨 놓기 (위치 이동 세션 시작)
data class RelocateRequest(
    val chemical_id: String,
    val target_shelf_id: String,
    val username: String,
    // "co_storage": 혼재 경고 → 안전 위치로 이동 (완료 시 처음 칸 기록을 남기지 않음)
    val reason: String? = null
)

data class CheckinSessionState(
    val active: Boolean,
    val chemical_name: String,
    val time_left: Double,
    val timeout: Boolean,
    val username: String? = null
)
