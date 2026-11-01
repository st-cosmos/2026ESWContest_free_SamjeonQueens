package com.example.cameramessage

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * 무단 반출 경보 감시 서비스 (포그라운드).
 *
 * 로그인 중에는 앱이 화면에 없어도(백그라운드·화면 꺼짐) 서버 WebSocket 을 유지하며
 * `unscanned_checkout`(반출 스캔 없는 회수) / `unscanned_checkin`(반입 스캔 없는 적재)
 * 이벤트를 받으면 기본 알림음·진동과 함께 헤드업 알림을 띄운다. (시계 알람처럼
 * 계속 울리지 않도록 알림 스트림의 기본 알림음을 한 번만 낸다)
 *
 * WebSocket 이 끊긴 사이의 이벤트는 GET /api/chemicals/alerts 의 unscanned_events 를
 * 주기 폴링해 보충한다. 두 경로 모두 서버가 확정한 같은 이벤트(event_id)를 전달하므로
 * 한 번만 알린다. HomeActivity 가 로그인 후 띄우고 로그아웃 때 내린다.
 */
class AlertMonitorService : Service() {

    companion object {
        private const val CHANNEL_MONITOR = "shelf_monitor"
        // 채널 설정(소리·진동)은 한 번 만들면 바뀌지 않으므로 소리를 바꿀 때 id 를 올린다
        private const val CHANNEL_ALERT = "unscanned_alert_v2"
        private const val CHANNEL_ALERT_LEGACY = "unscanned_checkout"  // v1: 알람 벨소리 — 삭제 대상
        private const val NOTIF_ID_MONITOR = 1001
        private const val POLL_MS = 15_000L
        private const val MAX_REMEMBERED = 200

        fun start(context: Context) {
            ContextCompat.startForegroundService(
                context, Intent(context, AlertMonitorService::class.java)
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, AlertMonitorService::class.java))
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val notifiedEventIds = LinkedHashSet<String>()
    private var nextNotifId = 2000

    private val wsListener: (String) -> Unit = { message ->
        when {
            message.contains("\"type\":\"unscanned_checkout\"") ->
                scope.launch { onUnscannedEvent(parseEvent(message)) }
            message.contains("\"type\":\"unscanned_checkin\"") ->
                scope.launch { onUnscannedCheckinEvent(parseCheckinEvent(message)) }
        }
    }

    override fun onCreate() {
        super.onCreate()
        createChannels()
        ServiceCompat.startForeground(
            this, NOTIF_ID_MONITOR, buildMonitorNotification(),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0
        )
        AppWebSocketManager.addListener(wsListener)
        scope.launch {
            while (isActive) {
                // 끊겨 있으면 재연결 (이미 연결돼 있으면 no-op)
                AppWebSocketManager.connect(NetworkClient.BASE_URL)
                pollEvents()
                delay(POLL_MS)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        AppWebSocketManager.removeListener(wsListener)
        scope.cancel()
        super.onDestroy()
    }

    /** WebSocket 유실 대비 — 서버가 보관 중인 최근 확정 이벤트를 받아 놓친 것만 알린다 */
    private suspend fun pollEvents() {
        try {
            val alerts = NetworkClient.api.getAlerts()
            alerts.unscanned_events.orEmpty().reversed().forEach { onUnscannedEvent(it) }
            alerts.unscanned_checkin_events.orEmpty().reversed().forEach { onUnscannedCheckinEvent(it) }
        } catch (_: Exception) {
            // 서버 미응답 — 다음 주기에 재시도
        }
    }

    private fun parseEvent(message: String): UnscannedEvent? = try {
        val o = JSONObject(message)
        UnscannedEvent(
            event_id = o.getString("event_id"),
            chemical_id = o.optString("chemical_id"),
            chemical_name = o.optString("chemical_name"),
            shelf_desc = o.optString("shelf_desc"),
            detected_at = o.optString("detected_at")
        )
    } catch (_: Exception) {
        null
    }

    private fun parseCheckinEvent(message: String): UnscannedCheckinEvent? = try {
        val o = JSONObject(message)
        UnscannedCheckinEvent(
            event_id = o.getString("event_id"),
            shelf_id = o.optString("shelf_id"),
            shelf_desc = o.optString("shelf_desc"),
            delta_kg = o.optDouble("delta_kg", 0.0),
            detected_at = o.optString("detected_at")
        )
    } catch (_: Exception) {
        null
    }

    /** 무단 반입: 반입 스캔 없이 선반 무게가 늘어난 뒤 유예 시간 안에 스캔이 없었다 */
    private fun onUnscannedCheckinEvent(event: UnscannedCheckinEvent?) {
        event ?: return
        if (!rememberEvent("checkin:${event.event_id}")) return
        val grams = (event.delta_kg * 1000).toInt()
        notify(
            title = "📦 반입 스캔 없이 시약이 놓였습니다",
            text = "${event.shelf_desc} · +${grams}g",
            bigText = "${event.shelf_desc}에 반입 스캔 없이 시약(+${grams}g)이 놓였습니다.\n" +
                    "반출했던 시약을 돌려놓으셨다면 앱에서 '지금 스캔하기'로 기록을 맞춰 주세요.\n" +
                    "감지 시각 ${event.detected_at}",
            iconRes = R.drawable.ic_flask_conical,
            requestCode = event.event_id.hashCode()
        )
    }

    /** 같은 이벤트를 두 경로(WebSocket·폴링)로 받아도 한 번만 알린다 */
    private fun rememberEvent(key: String): Boolean {
        if (!notifiedEventIds.add(key)) return false
        if (notifiedEventIds.size > MAX_REMEMBERED) notifiedEventIds.remove(notifiedEventIds.first())
        return true
    }

    private fun notify(title: String, text: String, bigText: String, iconRes: Int, requestCode: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return
        val open = Intent(this, HomeActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or
                    Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val tap = PendingIntent.getActivity(
            this, requestCode, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ALERT)
            .setSmallIcon(iconRes)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(bigText))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_EVENT)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setAutoCancel(true)
            .setContentIntent(tap)
            .build()
        NotificationManagerCompat.from(this).notify(nextNotifId++, notification)
    }

    /** 무단 반출: 반출 스캔 없이 병이 빠진 뒤 유예 시간 안에 스캔이 없었다 */
    private fun onUnscannedEvent(event: UnscannedEvent?) {
        event ?: return
        if (!rememberEvent("checkout:${event.event_id}")) return
        notify(
            title = "🚨 반출 스캔 없이 시약이 회수됐습니다",
            text = "${event.chemical_name} · ${event.shelf_desc}",
            bigText = "${event.chemical_name}이(가) ${event.shelf_desc}에서 회수됐지만 " +
                    "반출 스캔이 없습니다.\n감지 시각 ${event.detected_at}",
            iconRes = R.drawable.ic_shield_alert,
            requestCode = event.event_id.hashCode()
        )
    }

    private fun buildMonitorNotification(): Notification =
        NotificationCompat.Builder(this, CHANNEL_MONITOR)
            .setSmallIcon(R.drawable.ic_flask_conical)
            .setContentTitle("선반 감시 중")
            .setContentText("반출 스캔 없는 회수가 감지되면 경보를 보냅니다")
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(
                PendingIntent.getActivity(
                    this, 0, Intent(this, HomeActivity::class.java), PendingIntent.FLAG_IMMUTABLE
                )
            )
            .build()

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_MONITOR, "선반 감시", NotificationManager.IMPORTANCE_LOW).apply {
                description = "무단 반출 경보를 받기 위해 서버 연결을 유지합니다"
                setShowBadge(false)
            }
        )
        // v1 채널은 시계 알람 벨소리(알람 스트림)를 써서 알람처럼 울렸다 — 제거
        nm.deleteNotificationChannel(CHANNEL_ALERT_LEGACY)
        // 기본 알림음 + 진동으로 한 번만 울린다 (헤드업 표시는 IMPORTANCE_HIGH 로 유지)
        val sound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        val attrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_NOTIFICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ALERT, "무단 반입·반출 알림", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "스캔 없이 시약이 회수되거나 놓이면 알림음·진동으로 알립니다"
                setSound(sound, attrs)
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 300, 150, 300)
                enableLights(true)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            }
        )
    }
}
