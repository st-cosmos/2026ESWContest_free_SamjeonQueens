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
    val shelf_desc: String
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

data class ChemicalAlerts(
    val unscanned_checkouts: List<UnscannedCheckout>,
    val expired_chemicals: List<ExpiredChemical>,
    val co_storage_warnings: List<CoStorageWarning>
)

data class ScanInRequest(
    val ocr_text: String,
    val username: String,
    val chemical_name: String? = null  // 매칭/확인이 끝난 표준명 (서버 매칭 생략)
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
    val timeout_seconds: Double
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
    val image_b64: String? = null
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
    val matched_token: String? = null
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
    val username: String
)

data class CheckinSessionState(
    val active: Boolean,
    val chemical_name: String,
    val time_left: Double,
    val timeout: Boolean,
    val username: String? = null
)
