package com.example.cameramessage

import android.os.SystemClock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * 반입/반출 공용 시약 인식 파이프라인.
 *
 * 카메라 프레임의 OCR 텍스트·바코드를 누적하고, 주기적으로 서버 매칭(POST /match)을
 * 호출해 결과를 3갈래로 전달한다:
 *  - matched            → onMatched (자동 확정)
 *  - needs_confirmation → onNeedsConfirmation (사용자 확인 필요)
 *  - no_match 지속      → 단계적 안내 (스캔 중 → 인식이 잘 안돼요 → 직접 선택 유도)
 *
 * 사용자가 "아니요"로 거절한 후보는 잠시 다시 제안하지 않으며(쿨다운),
 * 요청 실패 직후에는 재시도를 쉬어 API 연타를 방지한다.
 */
class ChemicalScanner(
    private val scope: CoroutineScope,
    private val listener: Listener,
    /** 측정 로그에 남길 화면 구분 — "checkin" | "checkout" */
    private val mode: String = "checkin"
) {
    enum class Phase { SCANNING, WEAK, FAILED, OFFLINE }

    interface Listener {
        /** 상태 안내 — chipText: 카메라 상단 칩용 짧은 문구, guideText: 결과 카드용 안내문 */
        fun onScanStatus(phase: Phase, chipText: String, guideText: String)
        fun onMatched(result: MatchResult, ocrText: String, barcode: String?)
        fun onNeedsConfirmation(result: MatchResult, ocrText: String, barcode: String?)
    }

    companion object {
        private const val WINDOW_SIZE = 5              // 누적할 최근 프레임 텍스트 수
        private const val MATCH_INTERVAL_MS = 900L     // /match 호출 최소 간격
        private const val BARCODE_TTL_MS = 4000L       // 마지막 바코드를 유효하게 보는 시간
        private const val WEAK_AFTER_MS = 4000L        // "인식이 잘 안돼요" 안내 시점
        private const val FAILED_AFTER_MS = 9000L      // "직접 선택" 유도 시점
        private const val DECLINE_COOLDOWN_MS = 12000L // 거절한 후보 재제안 금지 시간
        private const val LLM_AFTER_MS = 4000L         // 이 시간 이상 매칭 실패 시 LLM 폴백
        private const val LLM_AFTER_DECLINE_MS = 500L  // 후보를 거절한 뒤에는 거의 즉시 LLM 에 묻는다
        private const val LLM_RETRY_MS = 6000L         // LLM 재시도(통신 실패·no_match 후) 최소 간격
        private const val LLM_MAX_ATTEMPTS = 2         // 한 병당 LLM 이 후보를 낸 응답 최대 횟수
        private const val LLM_MAX_CALLS = 6            // 한 병당 LLM 호출 상한 (실패·no_match 포함)
        private const val LLM_MIN_TEXT = 6             // LLM 호출에 필요한 최소 누적 글자 수
    }

    /** LLM 폴백에 라벨 사진을 첨부하기 위한 최신 프레임 JPEG(base64) 공급자 */
    var frameImageProvider: (() -> String?)? = null

    /** 실험 모드에서 서버가 보관할 최신 프레임 공급자 (같은 프레임은 한 번만 보낸다) */
    var latestFrameProvider: (() -> String?)? = null

    /** 현재 스캔 세션 id — 병 하나를 스캔하기 시작해 결정이 날 때까지 유지, resume() 마다 새로 발급 */
    var sessionId: String = newSessionId()
        private set
    private var sessionStarted = false
    private var lastSentFrame: String? = null

    private val textWindow = ArrayDeque<String>()
    private var lastBarcode: String? = null
    private var lastBarcodeAt = 0L
    private var lastMatchAt = 0L
    private var struggleStartAt = 0L   // 신호는 있는데 매칭이 안 되기 시작한 시각
    private var frozen = false
    private var matching = false
    private var offline = false
    private var lastPhase: Phase? = null
    private val declined = mutableMapOf<String, Long>()
    private var llmAnswers = 0         // 이번 스캔 세션에서 LLM 이 후보를 낸 응답 수
    private var llmCalls = 0           // 이번 스캔 세션의 LLM 호출 수 (실패·no_match 포함)
    private var llmInFlight = false
    // 사전 후보 모달이 떠 있는 동안 도착한 LLM 후보 — 버리지 않고 거절 직후 제안한다.
    // (라벨 상용구 오탐 모달이 먼저 떠서 LLM 의 정답이 버려지고, 거절 뒤 재호출은 카메라가
    //  라벨 이름을 벗어난 텍스트로 가서 엉뚱한 답이 온 사례)
    private var heldLlm: Triple<MatchResult, String, String?>? = null
    private var lastLlmAt = 0L
    private var declinedInSession = 0  // 이번 병에서 사용자가 거절한 횟수

    /** 최근 프레임들의 OCR 텍스트 (서버 매칭·학습 요청용) */
    val aggregatedText: String
        get() = textWindow.joinToString("\n")

    /** 아직 유효한(최근 감지된) 바코드 */
    val activeBarcode: String?
        get() = lastBarcode?.takeIf { SystemClock.elapsedRealtime() - lastBarcodeAt <= BARCODE_TTL_MS }

    /** 카메라 분석기에서 프레임마다 호출한다. (메인 스레드) */
    fun onFrame(text: String?, barcode: String?) {
        if (frozen) return
        val now = SystemClock.elapsedRealtime()

        if (!barcode.isNullOrBlank()) {
            lastBarcode = barcode
            lastBarcodeAt = now
        }
        if (!text.isNullOrBlank()) {
            textWindow.addLast(text)
            while (textWindow.size > WINDOW_SIZE) textWindow.removeFirst()
        }

        val hasSignal = textWindow.isNotEmpty() || activeBarcode != null
        if (!hasSignal) {
            // 아무 글자도 안 보일 때는 재촉하지 않는다
            struggleStartAt = 0L
            setPhase(if (offline) Phase.OFFLINE else Phase.SCANNING)
            return
        }

        if (struggleStartAt == 0L) struggleStartAt = now
        if (!sessionStarted) {
            // 글자/바코드가 처음 보인 순간 = 측정상 스캔 시작
            sessionStarted = true
            ScanExperiment.event(sessionId, "session_start", mode)
        }
        val struggling = now - struggleStartAt
        setPhase(
            when {
                offline -> Phase.OFFLINE
                struggling > FAILED_AFTER_MS -> Phase.FAILED
                struggling > WEAK_AFTER_MS -> Phase.WEAK
                else -> Phase.SCANNING
            }
        )

        maybeAskLlm(now, struggling)

        if (matching || now - lastMatchAt < MATCH_INTERVAL_MS) return
        matching = true
        lastMatchAt = now
        val ocr = aggregatedText
        val code = activeBarcode
        val frame = experimentFrame()
        scope.launch {
            try {
                val result = NetworkClient.api.matchChemical(
                    MatchRequest(
                        ocr_text = ocr, barcode = code,
                        session_id = sessionId, label_id = ScanExperiment.activeLabelId, frame_b64 = frame
                    )
                )
                offline = false
                if (frozen) return@launch
                val name = result.chemical_name
                if (name != null && !isDeclined(name)) {
                    when (result.status) {
                        "matched" -> {
                            frozen = true
                            listener.onMatched(result, ocr, code)
                        }
                        "needs_confirmation" -> {
                            frozen = true
                            listener.onNeedsConfirmation(result, ocr, code)
                        }
                    }
                }
                // no_match(또는 거절 쿨다운)이면 계속 스캔 — 단계적 안내 타이머가 흐른다
            } catch (e: Exception) {
                offline = true
                setPhase(Phase.OFFLINE)
            } finally {
                matching = false
            }
        }
    }

    /** 사전 매칭이 LLM_AFTER_MS 이상 계속 실패하거나, 사전의 후보를 사용자가 거절한 직후에는
     *  LLM 폴백을 호출한다 (병당 최대 LLM_MAX_ATTEMPTS 회). 사전 매칭은 그대로 병행되며,
     *  그 사이 사전이 먼저 맞히면 LLM 결과는 버린다. 사용자가 확인하면 라벨 문구가 별칭으로
     *  학습되어 다음부터는 사전에서 즉시 잡힌다.
     *  실측: 영단어 하나로 틀린 후보가 0.9초마다 뜨는 라벨에서는 거절할 때마다 타이머가 다시
     *  돌아 4초 조건이 영영 충족되지 않았다 — 거절 뒤에는 0.5초만 기다린다. */
    private fun maybeAskLlm(now: Long, struggling: Long) {
        if (llmAnswers >= LLM_MAX_ATTEMPTS || llmCalls >= LLM_MAX_CALLS ||
            llmInFlight || frozen || offline) return
        val waitMs = if (declinedInSession > 0) LLM_AFTER_DECLINE_MS else LLM_AFTER_MS
        if (struggling < waitMs) return
        if (now - lastLlmAt < LLM_RETRY_MS) return
        val ocr = aggregatedText
        if (ocr.replace(Regex("\\s"), "").length < LLM_MIN_TEXT) return

        llmInFlight = true
        llmCalls++
        lastLlmAt = now
        val code = activeBarcode
        val image = frameImageProvider?.invoke()  // 라벨 사진 — 비전으로 오독 교정
        scope.launch {
            try {
                val result = NetworkClient.api.matchChemicalLlm(
                    MatchRequest(
                        ocr_text = ocr, barcode = code, image_b64 = image,
                        session_id = sessionId, label_id = ScanExperiment.activeLabelId
                    )
                )
                val name = result.chemical_name
                val usable = result.status == "needs_confirmation" && name != null
                // 실패·no_match 는 LLM_MAX_ATTEMPTS 에 세지 않는다 — 서버가 429/503 을 no_match 로
                // 돌려주면 두 번 만에 그 병에 더 묻지 않아 정답 기회를 잃었다. 무한 재호출은
                // LLM_MAX_CALLS 와 LLM_RETRY_MS 간격이 막는다.
                if (!usable) return@launch
                llmAnswers++
                if (isDeclined(name!!)) return@launch
                if (frozen) {
                    heldLlm = Triple(result, ocr, code)
                    return@launch
                }
                frozen = true
                listener.onNeedsConfirmation(result, ocr, code)
            } catch (e: Exception) {
                // 통신 실패 — LLM_RETRY_MS 뒤에 다시 시도한다
            } finally {
                llmInFlight = false
            }
        }
    }

    /** 사용자가 "아니요"한 후보를 잠시 다시 제안하지 않는다. */
    fun declineCandidate(name: String) {
        declined[name] = SystemClock.elapsedRealtime()
        declinedInSession++
        if (heldLlm?.first?.chemical_name == name) heldLlm = null  // 같은 이름이면 다시 제안하지 않는다
        ScanExperiment.event(sessionId, "decision", mode, name = name, source = "decline")
    }

    /** 실험 모드일 때만, 아직 보내지 않은 최신 프레임을 돌려준다 */
    private fun experimentFrame(): String? {
        if (!ScanExperiment.enabled) return null
        val frame = latestFrameProvider?.invoke() ?: return null
        if (frame === lastSentFrame) return null
        lastSentFrame = frame
        return frame
    }

    private fun newSessionId(): String =
        "s" + java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 16)

    private fun isDeclined(name: String): Boolean {
        val at = declined[name] ?: return false
        return SystemClock.elapsedRealtime() - at < DECLINE_COOLDOWN_MS
    }

    /** 확정 처리나 다이얼로그 표시 동안 스캔을 멈춘다. */
    fun freeze() {
        frozen = true
    }

    /** 스캔 재개. cooldownMs 동안은 매칭 호출을 쉬어 실패 직후 연타를 방지한다.
     *  newSession=false 는 같은 병을 계속 스캔하는 경우(후보 거절 뒤)로, 측정 세션 id 와
     *  "인식이 안 되기 시작한 시각"을 유지해 LLM 폴백 타이머가 처음부터 다시 돌지 않게 한다. */
    fun resume(cooldownMs: Long = 0L, newSession: Boolean = true) {
        textWindow.clear()
        lastBarcode = null
        if (newSession) struggleStartAt = 0L
        lastMatchAt = SystemClock.elapsedRealtime() - MATCH_INTERVAL_MS + cooldownMs
        lastPhase = null
        frozen = false
        if (newSession) {
            llmAnswers = 0       // 새 병 스캔에는 LLM 기회를 다시 준다 (거절 쿨다운은 별도 유지)
            llmCalls = 0
            lastLlmAt = 0L       // 직전 병의 LLM 재시도 간격도 새 병에는 적용하지 않는다
            declinedInSession = 0
            sessionId = newSessionId()  // 다음 병은 새 측정 세션
            sessionStarted = false
            lastSentFrame = null
        }
        setPhase(Phase.SCANNING)
        // 사전 후보 모달이 떠 있는 동안 보관해 둔 LLM 후보 — 같은 병을 계속 스캔하는 경우(후보 거절 뒤)
        // 바로 제안한다. 새 병이면 버린다. 다이얼로그 닫기 처리가 끝난 뒤 뜨도록 다음 루프로 미룬다.
        val held = heldLlm
        heldLlm = null
        if (!newSession && held != null && !isDeclined(held.first.chemical_name ?: "")) {
            frozen = true
            scope.launch { listener.onNeedsConfirmation(held.first, held.second, held.third) }
        }
    }

    private fun setPhase(phase: Phase) {
        if (phase == lastPhase) return
        lastPhase = phase
        val (chip, guide) = when (phase) {
            Phase.SCANNING -> "스캔 중" to "카메라로 시약통 라벨을 스캔해 주세요"
            Phase.WEAK -> "인식이 잘 안돼요" to
                    "인식이 잘 안됩니다 — 라벨이 프레임에 가득 차게,\n흔들림 없이 비춰주세요"
            Phase.FAILED -> "인식 실패" to
                    "인식이 계속 실패하고 있어요.\n아래 버튼으로 시약을 직접 선택할 수 있습니다"
            Phase.OFFLINE -> "서버 연결 안 됨" to
                    "서버에 연결할 수 없습니다 — 네트워크 상태를 확인해 주세요"
        }
        listener.onScanStatus(phase, chip, guide)
    }
}
