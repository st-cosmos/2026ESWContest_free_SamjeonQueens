package com.example.cameramessage

import android.app.Activity
import android.content.Intent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * app-exception-modals (design-spec §3.11) / app-exception-modals-2
 * GET /api/chemicals/alerts 로 예외 모달을 Modal/App 스타일로 표시한다.
 *  1) 반출 스캔 미완료 (danger / clipboard-list) — 반출 화면을 제외한 모든 화면
 *  2) 인접 보관 위험 (danger / shield-alert)
 *  3) 반입 스캔 미완료 (warning / 반출중 시약 목록 포함) — 반입 화면을 제외한 모든 화면.
 *     '지금 스캔하기'는 반입 화면에 병이 놓인 칸을 넘겨, 스캔만으로 그 적재를 확정하게 한다.
 * 유통기한 경과·임박 경고는 여기서 폴링 알림으로 띄우지 않고, 반출 완료 시점
 * (CheckoutActivity.showExpiryDisposalModal)과 재고 편집 모드(InventoryActivity)의
 * 폐기 플로우로 처리한다.
 *
 * 서버는 선반 무게가 바뀔 때마다 WebSocket(weight_update)을 즉시 브로드캐스트한다.
 * 그 신호를 받으면 폴링을 기다리지 않고 바로 alerts 를 재조회해, 무단 반입(유예 없음)이
 * 실제 무게 변화 시점과 최대한 가깝게 뜨도록 한다. 무단 반출은 서버가 유예 시간(병을
 * 먼저 들고 스캔하는 정상 순서 허용) 뒤에야 목록에 넣으므로 폴링 주기에 잡힌다.
 * 폴링은 WebSocket 유실 대비 보조 수단이기도 하다.
 *
 * 같은 경보를 띄웠는지는 프로세스 전역으로 기억한다 — 홈에서 닫은 경보가 재고 관리로
 * 넘어갈 때 또 뜨지 않고, 반대로 새 회수(발생 시각이 다른 경보)는 어느 화면에 있든
 * 즉시 한 번 뜬다. 예전에는 화면마다 따로 기억해서 같은 시약 이름의 새 회수가
 * 현재 화면에서는 묻히고 다른 화면으로 넘어가야 보이는 문제가 있었다.
 */
class ExceptionDialogHelper(
    private val activity: Activity,
    // 반입 화면은 안착 직후 혼재 경고를 직접(이동 선택지와 함께) 띄우므로 여기서는 띄우지 않는다
    private val showCoStorage: Boolean = true
) {

    companion object {
        private const val POLL_MS = 3000L
        private val shownAlertIds = mutableSetOf<String>()

        private fun coStorageId(w: CoStorageWarning) = "costorage_${w.chemical_1_id}_${w.chemical_2_id}"

        /** 다른 화면(반입)이 이 혼재 경고를 이미 사용자에게 보여 줬다 — 홈 등에서 또 띄우지 않는다 */
        fun markCoStorageHandled(w: CoStorageWarning) {
            shownAlertIds.add(coStorageId(w))
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var pollJob: Job? = null

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
            while (true) {
                // 끊긴 WebSocket 은 매 주기 다시 붙인다 (연결돼 있으면 no-op)
                AppWebSocketManager.connect(NetworkClient.BASE_URL)
                checkAlertsNow()
                delay(POLL_MS)
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
        // 1. 반출 스캔 미완료 — 반출 화면에서는 띄우지 않되, 본 것으로 처리하지도 않는다
        //    (반출 화면을 나가면 다른 화면에서 떠야 하므로)
        if (activity !is CheckoutActivity) {
            val fresh = alerts.unscanned_checkouts.filter {
                shownAlertIds.add("unscanned_${it.chemical_id}:${it.since ?: "-"}")
            }
            if (fresh.isNotEmpty()) {
                showUnscannedCheckoutDialog(fresh.map { it.chemical_name })
            }
        }

        // 2. 인접 보관 위험
        if (showCoStorage && alerts.co_storage_warnings.isNotEmpty()) {
            val co = alerts.co_storage_warnings.first()
            if (shownAlertIds.add(coStorageId(co))) {
                showCoStorageWarningDialog(co)
            }
        }

        // 3. 반입 스캔 미완료 — 반입 화면에서는 띄우지 않되, 본 것으로 처리하지도 않는다.
        //    반입 화면에서는 사용자가 이미 스캔 중이거나 지정 위치·안전 위치로 병을 옮기는
        //    중이라, 그 과정의 안착이 서버에 잠깐 미청구 증가로 잡혀도 알림 대상이 아니다.
        //    화면을 나갔는데도 남아 있으면 그때 다른 화면에서 뜬다.
        //    한 번에 하나씩 (여러 칸이면 다음 폴링에서 이어서 뜬다)
        if (activity !is CheckinActivity) {
            val fresh = alerts.unscanned_checkins.orEmpty().firstOrNull {
                !shownAlertIds.contains("checkin_${it.shelf_id}:${it.since ?: "-"}")
            }
            if (fresh != null) {
                shownAlertIds.add("checkin_${fresh.shelf_id}:${fresh.since ?: "-"}")
                scope.launch { showUnscannedCheckinDialog(fresh) }
            }
        }
    }

    /** 반입 스캔 미완료: 어느 칸에 얼마가 놓였는지 + 현재 반출중인 시약 목록 + 지금 스캔하기 */
    private suspend fun showUnscannedCheckinDialog(checkin: UnscannedCheckin) {
        val items = loadCheckedOutItems()
        val grams = (checkin.delta_kg * 1000).toInt()
        AppModal.show(
            activity, AppModal.Tone.WARNING, R.drawable.ic_package_plus,
            "반입 스캔 미완료 알림",
            "${checkin.shelf_desc}에 반입 스캔 없이 시약이 놓였습니다 (+${grams}g).\n" +
                    "반출했던 시약을 돌려놓으셨다면 지금 반입 스캔으로 기록을 맞춰 주세요.",
            "나중에 하기", "지금 스캔하기",
            listTitle = "현재 반출중인 시약",
            listItems = items,
            listHint = if (items.isEmpty())
                "반출중인 시약이 없습니다 — 새 시약이면 반입 스캔으로 등록해 주세요."
            else
                "목록은 참고용입니다 — 스캔하면 라벨로 시약을 확인하고 기존 기록에 복귀 처리합니다.",
            onPrimary = {
                val intent = Intent(activity, CheckinActivity::class.java)
                    .putExtra(CheckinActivity.EXTRA_CLAIM_SHELF_ID, checkin.shelf_id)
                activity.startActivity(intent)
                // 홈은 항상 스택 하단에 두고, 그 외 화면은 탭 이동과 같이 닫는다 (NavBar 규칙)
                if (activity !is HomeActivity && activity !is CheckinActivity) activity.finish()
            }
        )
    }

    /** 반출중 시약 → 목록 행 (이름 / 반출자·시점 / 원래 보관 위치). 조회 실패 시 빈 목록 */
    private suspend fun loadCheckedOutItems(): List<AppModal.ListItem> {
        return try {
            val chemicals = NetworkClient.api.getChemicals().filter { it.current_status == "반출중" }
            if (chemicals.isEmpty()) return emptyList()
            val nicknames = try {
                NetworkClient.api.getUsers().associate { it.username to it.nickname }
            } catch (_: Exception) {
                emptyMap()
            }
            val shelfLetter = try {
                NetworkClient.api.getShelves().associate { it.id to it.parent_shelf }
            } catch (_: Exception) {
                emptyMap()
            }
            chemicals
                .sortedByDescending { it.time_out ?: "" }
                .map { chem ->
                    val holder = nicknames[chem.holder_username] ?: chem.holder_username ?: "알 수 없음"
                    val letter = chem.shelf_id?.let { shelfLetter[it] }
                    val loc = if (chem.shelf_row != null && chem.shelf_col != null)
                        "${letter ?: "?"} · ${chem.shelf_row}행 ${chem.shelf_col}열" else null
                    AppModal.ListItem(
                        title = chem.name,
                        subtitle = "$holder · ${relativeTime(chem.time_out)} 반출",
                        trailing = loc
                    )
                }
        } catch (_: Exception) {
            emptyList()
        }
    }

    /** "yyyy-MM-dd HH:mm:ss"(서버 로컬 시각) → 방금 전 / N분 전 / N시간 전 / N일 전 */
    private fun relativeTime(timestamp: String?): String {
        if (timestamp.isNullOrBlank()) return "시점 미상"
        val parsed = try {
            SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                .parse(timestamp.substringBefore('.').replace('T', ' '))
        } catch (_: Exception) {
            null
        } ?: return timestamp.take(16)
        val diffMin = (System.currentTimeMillis() - parsed.time) / 60000L
        return when {
            diffMin < 1 -> "방금 전"
            diffMin < 60 -> "${diffMin}분 전"
            diffMin < 60 * 24 -> "${diffMin / 60}시간 전"
            else -> "${diffMin / (60 * 24)}일 전"
        }
    }

    private fun showUnscannedCheckoutDialog(chemicalNames: List<String>) {
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
