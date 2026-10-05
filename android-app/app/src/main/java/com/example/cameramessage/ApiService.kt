package com.example.cameramessage

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

/**
 * Smart Chemical Shelf FastAPI 서버와 통신하는 인터페이스.
 * 엔드포인트 계약은 web/routes/ 의 FastAPI 라우터와 1:1 대응합니다.
 */
interface ApiService {

    // --- Users ---
    @GET("api/users")
    suspend fun getUsers(): List<UserData>

    @POST("api/users/register")
    suspend fun registerUser(@Body request: UserRegisterRequest): UserData

    // 앱 로그인은 4자리 PIN 방식(login-pin)을 사용한다. (웹은 비밀번호 방식 /login)
    // 성공하면 서버가 앱 세션을 등록해 선반들을 깨운다 (docs/power-modes.md).
    @POST("api/users/login-pin")
    suspend fun loginPin(@Body request: UserPinLoginRequest): UserData

    // 로그아웃: 앱 세션 해제 — 마지막 사용자가 나가면 선반들이 슬립으로 돌아간다.
    @POST("api/app-session/leave")
    suspend fun appSessionLeave(@Body request: AppSessionRequest): AppSessionState

    // --- Shelves ---
    @GET("api/shelves")
    suspend fun getShelves(): List<ShelfDevice>

    @GET("api/shelves/configs")
    suspend fun getShelfConfigs(): List<ShelfConfig>

    @POST("api/shelves/configs/{configId}")
    suspend fun updateShelfConfig(
        @Path("configId") configId: String,
        @Body request: Map<String, Int>
    ): ShelfConfig

    @POST("api/shelves/register/{shelfId}")
    suspend fun registerShelf(
        @Path("shelfId") shelfId: String,
        @Body request: ShelfRegister
    ): ShelfDevice

    @POST("api/shelves/{shelfId}/weight")
    suspend fun updateShelfWeight(
        @Path("shelfId") shelfId: String,
        @Body request: ShelfWeightUpdate
    ): ShelfWeightResponse

    @POST("api/shelves/{shelfId}/led")
    suspend fun setLedState(
        @Path("shelfId") shelfId: String,
        @Body request: ShelfLedUpdate
    ): ShelfDevice

    // --- Chemicals ---
    @GET("api/chemicals")
    suspend fun getChemicals(): List<ChemicalData>

    @GET("api/chemicals/ocr-chemicals")
    suspend fun getOcrChemicals(): List<String>

    @GET("api/chemicals/known-names")
    suspend fun getKnownNames(): List<String>

    // OCR 누적 텍스트 + 바코드로 시약 후보를 조회 (스캔 중 주기 호출)
    @POST("api/chemicals/match")
    suspend fun matchChemical(@Body request: MatchRequest): MatchResult

    // 사전 매칭 실패가 지속될 때 1회 호출하는 LLM 폴백 (결과는 항상 확인 모달을 거침)
    @POST("api/chemicals/match/llm")
    suspend fun matchChemicalLlm(@Body request: MatchRequest): MatchResult

    // 사용자 확인/직접 선택 결과를 서버에 학습 (별칭·바코드 사전 갱신)
    @POST("api/chemicals/match/confirm")
    suspend fun confirmMatch(@Body request: MatchConfirmRequest): Map<String, Any>

    // 반입 스캔 직후 라벨 OCR/사진으로 병의 가득 총 무게(잔량 % 분모)를 추정
    @POST("api/chemicals/estimate-capacity")
    suspend fun estimateCapacity(@Body request: CapacityEstimateRequest): CapacityEstimateResponse

    @POST("api/chemicals/scan-in")
    suspend fun scanIn(@Body request: ScanInRequest): ScanInResponse

    @POST("api/chemicals/scan-out")
    suspend fun scanOut(@Body request: ScanOutRequest): ScanOutResponse

    // 무게 감지 타임아웃 후 '무게 확인 없이 기록'
    @POST("api/chemicals/scan-out/force")
    suspend fun scanOutForce(@Body request: ScanOutForceRequest): ScanOutResponse

    @POST("api/chemicals/select-led")
    suspend fun selectLed(@Body request: SelectLedRequest): SelectLedResponse

    // 유통기한 경고 시약 폐기 등록 — 재고에서 삭제 + '폐기' 로그
    @POST("api/chemicals/{chemId}/dispose")
    suspend fun disposeChemical(
        @Path("chemId") chemId: String,
        @Body request: DisposeRequest
    ): Map<String, String>

    @GET("api/chemicals/alerts")
    suspend fun getAlerts(): ChemicalAlerts

    // --- Settings ---
    // 유통기한 직접 입력 달력의 기본값 (반입일 + N개월) — 웹 관리자 설정과 공유
    @GET("api/settings/default-expiry")
    suspend fun getDefaultExpiry(): DefaultExpirySetting

    // --- Logs ---
    @GET("api/logs")
    suspend fun getLogs(): List<TransactionLog>

    // --- Check-in Session ---
    @GET("api/checkin-session")
    suspend fun getCheckinSession(): CheckinSessionState

    @POST("api/checkin-session/cancel")
    suspend fun cancelCheckinSession(): Map<String, String>

    // 잘못된 칸에 놓인 병을 지정 위치로 옮기기 — 지정 칸 안착 감지 시 위치가 변경된다
    @POST("api/checkin-session/relocate")
    suspend fun relocateCheckin(@Body request: RelocateRequest): Map<String, Any>

    @POST("api/checkin-session/expiration")
    suspend fun setCheckinExpiration(@Body request: ExpirationRequest): Map<String, String>

    // --- Check-out Session (선반 무게 감소로 반출 확정) ---
    @GET("api/checkout-session")
    suspend fun getCheckoutSession(): CheckoutSessionState

    @POST("api/checkout-session/cancel")
    suspend fun cancelCheckoutSession(): Map<String, String>
}
