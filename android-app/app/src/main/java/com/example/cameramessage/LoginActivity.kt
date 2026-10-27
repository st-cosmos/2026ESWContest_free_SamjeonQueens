package com.example.cameramessage

import android.animation.ObjectAnimator
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.example.cameramessage.databinding.ActivityLoginBinding
import com.example.cameramessage.databinding.ItemProfileBinding
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * app-login-profiles → app-login (design-spec §3.1, §3.2)
 * 프로필 목록에서 선택 → PIN 카드(4도트 + 키패드) → POST /api/users/login-pin 검증.
 * 4자리 PIN(시드값 "1234")을 모두 입력하면 로그인을 시도하고,
 * 실패하면 도트 흔들림 + 에러 색으로 표시한다.
 */
class LoginActivity : AppCompatActivity() {

    private lateinit var binding: ActivityLoginBinding
    private var selectedUser: UserData? = null
    private val pinBuffer = StringBuilder()
    private var loginJob: Job? = null
    private var errorShowing = false
    private lateinit var dots: List<View>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(binding.root)

        dots = listOf(binding.pinDot1, binding.pinDot2, binding.pinDot3, binding.pinDot4)

        setupProfiles()
        setupKeypad()

        binding.btnBackToProfiles.setOnClickListener { showProfiles() }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (binding.pinSection.visibility == View.VISIBLE) showProfiles() else finish()
            }
        })

        fetchProfiles()
    }

    private fun fetchProfiles() {
        lifecycleScope.launch {
            try {
                val users = NetworkClient.api.getUsers()
                (binding.profilesRecyclerView.adapter as? ProfilesAdapter)?.updateData(users)
            } catch (e: Exception) {
                Toast.makeText(this@LoginActivity, "네트워크 연결 실패: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun setupProfiles() {
        binding.profilesRecyclerView.layoutManager = LinearLayoutManager(this)
        binding.profilesRecyclerView.adapter = ProfilesAdapter { user -> showPinSection(user) }
    }

    private fun showPinSection(user: UserData) {
        selectedUser = user
        pinBuffer.clear()
        errorShowing = false
        updatePinDots()
        binding.pinTitle.text = "${user.nickname} 님의 PIN 입력"
        binding.selectedProfileInitial.text = user.nickname.take(1)
        binding.selectedProfileName.text = user.nickname
        binding.selectedProfileRole.text = user.role
        binding.loginSubtitle.text = "프로필을 선택하고 PIN 번호를 입력하세요"
        binding.profilesSection.visibility = View.GONE
        binding.pinSection.visibility = View.VISIBLE
    }

    private fun showProfiles() {
        selectedUser = null
        pinBuffer.clear()
        loginJob?.cancel()
        binding.loginSubtitle.text = "프로필을 선택하여 로그인을 진행해 주세요"
        binding.pinSection.visibility = View.GONE
        binding.profilesSection.visibility = View.VISIBLE
    }

    private fun setupKeypad() {
        val numberKeys = listOf(
            binding.btn0, binding.btn1, binding.btn2, binding.btn3, binding.btn4,
            binding.btn5, binding.btn6, binding.btn7, binding.btn8, binding.btn9
        )
        numberKeys.forEach { key ->
            key.setOnClickListener {
                if (errorShowing || pinBuffer.length >= 4) return@setOnClickListener
                pinBuffer.append(key.text)
                updatePinDots()
                if (pinBuffer.length >= 4) attemptLogin()
            }
        }

        binding.btnDelete.setOnClickListener {
            if (errorShowing) return@setOnClickListener
            if (pinBuffer.isNotEmpty()) {
                pinBuffer.deleteCharAt(pinBuffer.length - 1)
                updatePinDots()
            }
        }

        binding.btnFinger.setOnClickListener {
            Toast.makeText(this, "지문 인식은 준비 중입니다. PIN을 입력해 주세요.", Toast.LENGTH_SHORT).show()
        }
    }

    private fun updatePinDots(errorState: Boolean = false) {
        dots.forEachIndexed { index, dot ->
            dot.setBackgroundResource(
                when {
                    errorState -> R.drawable.pin_dot_error
                    index < pinBuffer.length -> R.drawable.pin_dot_filled
                    else -> R.drawable.pin_dot_empty
                }
            )
        }
    }

    private fun attemptLogin() {
        val user = selectedUser ?: return
        val pin = pinBuffer.toString()
        loginJob?.cancel()
        loginJob = lifecycleScope.launch {
            try {
                NetworkClient.api.loginPin(UserPinLoginRequest(username = user.username, pin = pin))
                onLoginSuccess(user)
            } catch (e: Exception) {
                showPinError()
            }
        }
    }

    private fun onLoginSuccess(user: UserData) {
        val prefs = getSharedPreferences("smart_shelf", Context.MODE_PRIVATE)
        prefs.edit()
            .putString("username", user.username)
            .putString("nickname", user.nickname)
            .putString("role", user.role)
            .apply()

        Toast.makeText(this, "${user.nickname}님 환영합니다.", Toast.LENGTH_SHORT).show()
        startActivity(Intent(this, HomeActivity::class.java))
        finish()
    }

    private fun showPinError() {
        errorShowing = true
        updatePinDots(errorState = true)

        // 도트 흔들림 애니메이션
        ObjectAnimator.ofFloat(
            binding.pinDotsRow, "translationX",
            0f, -14f, 14f, -10f, 10f, -5f, 5f, 0f
        ).setDuration(420L).start()

        lifecycleScope.launch {
            delay(700)
            pinBuffer.clear()
            errorShowing = false
            updatePinDots()
        }
    }

    /** 프로필 목록 어댑터. 첫 번째 프로필(최근 사용)만 아바타를 primary 로 강조한다. */
    private class ProfilesAdapter(private val onClick: (UserData) -> Unit) :
        RecyclerView.Adapter<ProfilesAdapter.ViewHolder>() {

        private var items = listOf<UserData>()

        fun updateData(newItems: List<UserData>) {
            items = newItems
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
            val itemBinding = ItemProfileBinding.inflate(
                LayoutInflater.from(parent.context), parent, false
            )
            return ViewHolder(itemBinding)
        }

        override fun onBindViewHolder(holder: ViewHolder, position: Int) {
            val item = items[position]
            val context = holder.b.root.context
            holder.b.profileNickname.text = item.nickname
            holder.b.profileRole.text = item.role
            holder.b.profileInitial.text = item.nickname.take(1)

            val highlighted = position == 0
            holder.b.profileAvatar.backgroundTintList = ColorStateList.valueOf(
                ContextCompat.getColor(context, if (highlighted) R.color.primary else R.color.primary_soft)
            )
            holder.b.profileInitial.setTextColor(
                ContextCompat.getColor(context, if (highlighted) R.color.white else R.color.primary)
            )
            holder.b.root.setOnClickListener { onClick(item) }
        }

        override fun getItemCount(): Int = items.size

        class ViewHolder(val b: ItemProfileBinding) : RecyclerView.ViewHolder(b.root)
    }
}
