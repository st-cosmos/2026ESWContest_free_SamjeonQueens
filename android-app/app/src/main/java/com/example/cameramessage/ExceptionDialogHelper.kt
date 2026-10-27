package com.example.cameramessage

import android.app.Activity
import android.content.Intent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * app-exception-modals (design-spec §3.11)
 * GET /api/chemicals/alerts 로 예외 모달을 Modal/App 스타일로 표시한다.
 *  1) 반출 스캔 미완료 (danger / clipboard-list)
 *  2) 인접 보관 위험 (danger / shield-alert)
 * 유통기한 경과·임박 경고는 여기서 폴링 알림으로 띄우지 않고, 반출 완료 시점
 * (CheckoutActivity.showExpiryDisposalModal)과 재고 카드 길게 누르기
 * (InventoryActivity)의 폐기 등록 플로우로 처리한다.
 *
 * 서버는 선반 무게가 바뀔 때마다 WebSocket(weight_update)을 즉시 브로드캐스트한다.
 * 그 신호를 받으면 5초 폴링을 기다리지 않고 바로 alerts 를 재조회해, 무단 반출
 * 감지(반출 스캔 미완료 알림)가 실제 무게 변화 시점과 최대한 가깝게 뜨도록 한다.
 * 폴링은 WebSocket 유실을 대비한 보조 수단으로 유지한다.
 */
class ExceptionDialogHelper(private val activity: Activity) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var pollJob: Job? = null

    // 같은 알림을 반복해서 띄우지 않기 위한 기록
    private val shownAlertIds = mutableSetOf<String>()

    // 무게가 바뀌었다는 신호를 받으면(반입/반출 모두) 즉시 alerts 를 다시 확인한다
    private val wsListener: (String) -> Unit = { message ->
        if (message.contains("\"type\":\"weight_update\"")) {
            scope.launch { checkAlertsNow() }
        }
    }

    fun startPollingAlerts() {
        pollJob?.cancel()
        AppWebSocketManager.connect(NetworkClient.BASE_URL)
        AppWebSocketManager.addListener(wsListener)

        pollJob = scope.launch {
            checkAlertsNow()  // 화면 진입 즉시 1회 확인 — 기존엔 5초를 기다린 뒤 첫 확인이라 느렸음
            while (true) {
                delay(5000)
                checkAlertsNow()
            }
        }
    }

    fun stopPollingAlerts() {
        pollJob?.cancel()
        AppWebSocketManager.removeListener(wsListener)
    }

    private suspend fun checkAlertsNow() {
        try {
            val alerts = NetworkClient.api.getAlerts()
            handleAlerts(alerts)
        } catch (e: Exception) {
            // 네트워크 오류 무시
        }
    }

    private fun handleAlerts(alerts: ChemicalAlerts) {
        // 1. 반출 스캔 미완료
        if (alerts.unscanned_checkouts.isNotEmpty()) {
            val names = alerts.unscanned_checkouts.map { it.chemical_name }
            val alertId = "unscanned_" + names.sorted().joinToString("_")
            if (shownAlertIds.add(alertId)) {
                showUnscannedCheckoutDialog(names)
            }
        }

        // 2. 인접 보관 위험
        if (alerts.co_storage_warnings.isNotEmpty()) {
            val co = alerts.co_storage_warnings.first()
            val alertId = "costorage_${co.chemical_1_id}_${co.chemical_2_id}"
            if (shownAlertIds.add(alertId)) {
                showCoStorageWarningDialog(co)
            }
        }
    }

    private fun showUnscannedCheckoutDialog(chemicalNames: List<String>) {
        if (activity is CheckoutActivity) {
            return
        }
        AppModal.show(
            activity, AppModal.Tone.DANGER, R.drawable.ic_clipboard_list,
            "반출 스캔 미완료 알림",
            "선반에서 ${chemicalNames.size}개 시약의 회수가 감지되었지만 " +
                    "반출 스캔이 완료되지 않았습니다.\n${chemicalNames.joinToString(" · ")}",
            "나중에 하기", "지금 스캔하기",
            onPrimary = {
                activity.startActivity(Intent(activity, CheckoutActivity::class.java))
            }
        )
    }

    private fun showCoStorageWarningDialog(warning: CoStorageWarning) {
        val safeLoc = warning.recommended_safe_shelf_desc ?: "선반 C · 분리 보관 구역"
        val reasonText = warning.reason ?: "반응 및 발열/가스 발생 위험"
        AppModal.show(
            activity, AppModal.Tone.DANGER, R.drawable.ic_shield_alert,
            "🚨 함께 두면 위험한 시약",
            "${warning.message}\n\n💡 ${reasonText}\n\n👉 추천 이동 위치: ${safeLoc}",
            "닫기", "안전 위치 안내",
            onPrimary = {
                AppModal.show(
                    activity, AppModal.Tone.SUCCESS, R.drawable.ic_check,
                    "추천 안전 위치 안내",
                    "[${warning.chemical_1_name}]을(를)\n${safeLoc}(으)로 이동하여 배치해 주세요.",
                    null, "확인"
                )
            }
        )
    }
}
