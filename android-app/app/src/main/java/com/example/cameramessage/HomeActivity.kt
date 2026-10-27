package com.example.cameramessage

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.example.cameramessage.databinding.ActivityHomeBinding
import com.example.cameramessage.databinding.ItemActivityBinding
import kotlinx.coroutines.DelicateCoroutinesApi
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * app-home (design-spec §3.3)
 * 인사말 + 신규 선반 감지 배너(미등록 shelves 폴링) + 반입/반출 액션 카드 + 최근 활동(GET /api/logs).
 */
class HomeActivity : AppCompatActivity() {

    private lateinit var binding: ActivityHomeBinding
    private var pollJob: Job? = null
    private lateinit var exceptionHelper: ExceptionDialogHelper

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityHomeBinding.inflate(layoutInflater)
        setContentView(binding.root)

        exceptionHelper = ExceptionDialogHelper(this)

        NavBar.setup(binding.bottomNav, this, NavBar.TAB_HOME)
        setupUserGreeting()
        setupNavigations()
        setupActivitiesList()
    }

    override fun onResume() {
        super.onResume()
        startPolling()
        exceptionHelper.startPollingAlerts()
    }

    override fun onPause() {
        super.onPause()
        stopPolling()
        exceptionHelper.stopPollingAlerts()
    }

    private fun setupUserGreeting() {
        val prefs = getSharedPreferences("smart_shelf", Context.MODE_PRIVATE)
        val nickname = prefs.getString("nickname", "연구원") ?: "연구원"
        binding.greetTitle.text = "안녕하세요, ${nickname}님"

        val dateText = SimpleDateFormat("M월 d일 EEEE", Locale.KOREAN).format(Date())
        binding.greetSub.text = "$dateText · 중앙연구소 3층 시약실"
        binding.homeAvatarText.text = nickname.take(1)
    }

    private fun setupNavigations() {
        binding.btnActionIn.setOnClickListener {
            startActivity(Intent(this, CheckinActivity::class.java))
        }
        binding.btnActionOut.setOnClickListener {
            startActivity(Intent(this, CheckoutActivity::class.java))
        }
        binding.btnHomeBell.setOnClickListener {
            Toast.makeText(this, "새 알림이 없습니다.", Toast.LENGTH_SHORT).show()
        }
        binding.btnViewAll.setOnClickListener {
            Toast.makeText(this, "전체 기록은 웹 대시보드에서 확인할 수 있습니다.", Toast.LENGTH_SHORT).show()
        }
        // 아바타 탭 → 로그아웃
        binding.homeAvatar.setOnClickListener {
            AppModal.show(
                this, AppModal.Tone.PRIMARY, R.drawable.ic_user,
                "로그아웃",
                "현재 프로필에서 로그아웃하고\n프로필 선택 화면으로 돌아갈까요?",
                "취소", "로그아웃",
                onPrimary = {
                    val prefs = getSharedPreferences("smart_shelf", Context.MODE_PRIVATE)
                    notifyAppSessionLeave(prefs.getString("username", null))
                    prefs.edit().clear().apply()
                    startActivity(Intent(this, LoginActivity::class.java))
                    finish()
                }
            )
        }
    }

    /**
     * 서버에 앱 세션 해제를 알린다 (선반 전력 모드, docs/power-modes.md).
     * 로그아웃 UX 를 네트워크에 묶지 않는 fire-and-forget — 액티비티가 곧
     * 종료되므로 lifecycleScope 대신 프로세스 스코프에서 보낸다. 전송이
     * 실패해도 서버 쪽 세션 TTL(30분)이 안전망이 된다.
     */
    @OptIn(DelicateCoroutinesApi::class)
    private fun notifyAppSessionLeave(username: String?) {
        if (username.isNullOrEmpty()) return
        GlobalScope.launch {
            try {
                NetworkClient.api.appSessionLeave(AppSessionRequest(username))
            } catch (_: Exception) {
                // 서버 TTL 이 처리한다
            }
        }
    }

    private fun setupActivitiesList() {
        binding.activityRecyclerView.layoutManager = LinearLayoutManager(this)
        binding.activityRecyclerView.adapter = ActivitiesAdapter()
    }

    private fun startPolling() {
        pollJob?.cancel()
        pollJob = lifecycleScope.launch {
            while (true) {
                try {
                    // 1. 미등록 기기 → 배너
                    val shelves = NetworkClient.api.getShelves()
                    val unregistered = shelves.filter { it.status == "unregistered" }
                    if (unregistered.isNotEmpty()) {
                        val device = unregistered.first()
                        binding.bannerDeviceText.text =
                            "Device ID : ${device.id} · 터치하여 선반 등록 화면으로 이동"
                        binding.unregisteredBanner.visibility = View.VISIBLE
                        val goShelf = View.OnClickListener {
                            startActivity(Intent(this@HomeActivity, ShelfManageActivity::class.java))
                        }
                        binding.unregisteredBanner.setOnClickListener(goShelf)
                        binding.btnBannerGo.setOnClickListener(goShelf)
                    } else {
                        binding.unregisteredBanner.visibility = View.GONE
                    }

                    // 2. 최근 활동
                    val logs = NetworkClient.api.getLogs()
                    (binding.activityRecyclerView.adapter as? ActivitiesAdapter)
                        ?.updateData(logs.take(6))
                } catch (e: Exception) {
                    // 백그라운드 네트워크 오류는 무시
                }
                delay(3000)
            }
        }
    }

    private fun stopPolling() {
        pollJob?.cancel()
    }

    private class ActivitiesAdapter : RecyclerView.Adapter<ActivitiesAdapter.ViewHolder>() {

        private var items = listOf<TransactionLog>()

        fun updateData(newItems: List<TransactionLog>) {
            items = newItems
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
            val itemBinding = ItemActivityBinding.inflate(
                LayoutInflater.from(parent.context), parent, false
            )
            return ViewHolder(itemBinding)
        }

        override fun onBindViewHolder(holder: ViewHolder, position: Int) {
            val item = items[position]
            val context = holder.b.root.context
            holder.b.activityChemicalName.text = item.chemical_name
            holder.b.activityOperatorInfo.text =
                "${item.operator_name} · ${relativeTime(item.timestamp)}"

            val isOut = item.action == "반출"
            val toneColor = ContextCompat.getColor(
                context, if (isOut) R.color.danger else R.color.success
            )
            val softColor = ContextCompat.getColor(
                context, if (isOut) R.color.danger_soft else R.color.success_soft
            )

            holder.b.activityIconWrap.backgroundTintList = ColorStateList.valueOf(softColor)
            holder.b.activityIcon.setImageResource(
                if (isOut) R.drawable.ic_arrow_up_right else R.drawable.ic_arrow_down_left
            )
            holder.b.activityIcon.imageTintList = ColorStateList.valueOf(toneColor)

            holder.b.activityBadge.text = item.action
            holder.b.activityBadge.backgroundTintList = ColorStateList.valueOf(softColor)
            holder.b.activityBadge.setTextColor(toneColor)
        }

        override fun getItemCount(): Int = items.size

        /** "방금 전 / N분 전 / N시간 전 / M월 d일" 상대 시각 표기 */
        private fun relativeTime(timestamp: String): String {
            val normalized = timestamp.substringBefore('.').replace(' ', 'T')
            val date = try {
                val sdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)
                sdf.timeZone = java.util.TimeZone.getTimeZone("UTC")
                sdf.parse(normalized)
            } catch (e: Exception) {
                null
            } ?: return timestamp.take(16).replace('T', ' ')

            val diffMin = (System.currentTimeMillis() - date.time) / 60000L
            return when {
                diffMin < 1 -> "방금 전"
                diffMin < 60 -> "${diffMin}분 전"
                diffMin < 60 * 24 -> "${diffMin / 60}시간 전"
                else -> SimpleDateFormat("M월 d일", Locale.KOREAN).format(date)
            }
        }

        class ViewHolder(val b: ItemActivityBinding) : RecyclerView.ViewHolder(b.root)
    }
}
