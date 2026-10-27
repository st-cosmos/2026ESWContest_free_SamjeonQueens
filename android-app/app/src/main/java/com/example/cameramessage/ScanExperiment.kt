package com.example.cameramessage

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.text.InputType
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * 스캔 정확도 측정 지원 (docs/scan-accuracy-experiment.md).
 *
 * - 스캔 이벤트(세션 시작·사용자 결정·거절)를 서버 측정 로그(/match/event)로 보낸다.
 *   인식·반입·반출 동작에는 영향이 없고, 실패해도 무시한다.
 * - 실험 모드: 반입/반출 화면의 상태 칩을 길게 눌러 켠다. 켜져 있으면 라벨 번호가 모든
 *   요청에 실려 정답표와 자동으로 맞춰지고, 서버가 재평가용 프레임을 보관한다.
 */
object ScanExperiment {
    private const val PREFS = "smart_shelf"
    private const val KEY_ENABLED = "exp_enabled"
    private const val KEY_LABEL = "exp_label"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @Volatile var enabled: Boolean = false
        private set
    @Volatile var labelId: String? = null
        private set

    fun load(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        enabled = prefs.getBoolean(KEY_ENABLED, false)
        labelId = prefs.getString(KEY_LABEL, null)?.takeIf { it.isNotBlank() }
    }

    fun setEnabled(context: Context, on: Boolean) {
        enabled = on
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, on).apply()
    }

    fun setLabel(context: Context, label: String?) {
        labelId = label?.trim()?.takeIf { it.isNotBlank() }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_LABEL, labelId).apply()
    }

    /** 요청에 실을 라벨 번호 — 실험 모드가 켜진 동안만 */
    val activeLabelId: String?
        get() = labelId.takeIf { enabled }

    /** 상태 칩 앞에 붙일 표시 (실험 모드일 때만) */
    fun badge(): String? = if (enabled) "실험 #${labelId ?: "-"}" else null

    /** 측정 이벤트 전송 (session_start / decision) */
    fun event(
        sessionId: String?, event: String, mode: String,
        name: String? = null, source: String? = null,
        method: String? = null, confidence: Double? = null
    ) {
        if (sessionId == null) return
        val req = ScanEventRequest(
            session_id = sessionId, event = event, label_id = activeLabelId,
            chemical_name = name, source = source, method = method,
            confidence = confidence, mode = mode
        )
        scope.launch {
            try {
                NetworkClient.api.scanEvent(req)
            } catch (e: Exception) {
                // 측정 로그 실패는 무시
            }
        }
    }

    /** 실험 모드 on/off + 라벨 번호 입력 다이얼로그 */
    fun showDialog(activity: Activity, onChanged: () -> Unit) {
        val density = activity.resources.displayMetrics.density
        val pad = (20 * density).toInt()
        val layout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad / 2, pad, 0)
        }
        val toggle = Switch(activity).apply {
            text = "실험 모드 (프레임 보관·라벨 번호 전송)"
            isChecked = enabled
        }
        val hintView = TextView(activity).apply {
            text = "라벨 번호 — 정답표(labels_manifest.csv)의 파일 번호. 조건 구분은 뒤에 붙임 (예: 12-glare)"
            textSize = 12f
            setPadding(0, pad / 2, 0, 0)
        }
        val input = EditText(activity).apply {
            inputType = InputType.TYPE_CLASS_TEXT
            setText(labelId ?: "")
            hint = "예: 12"
        }
        layout.addView(toggle)
        layout.addView(hintView)
        layout.addView(input)

        AlertDialog.Builder(activity)
            .setTitle("스캔 정확도 측정")
            .setView(layout)
            .setNeutralButton("측정 화면 열기") { _, _ ->
                activity.startActivity(Intent(activity, ScanBenchActivity::class.java))
            }
            .setNegativeButton("취소", null)
            .setPositiveButton("적용") { _, _ ->
                enabled = toggle.isChecked
                labelId = input.text.toString().trim().takeIf { it.isNotBlank() }
                activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                    .putBoolean(KEY_ENABLED, enabled)
                    .putString(KEY_LABEL, labelId)
                    .apply()
                onChanged()
            }
            .show()
    }
}
