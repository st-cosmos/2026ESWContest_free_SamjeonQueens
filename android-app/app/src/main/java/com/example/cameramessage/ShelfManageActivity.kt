package com.example.cameramessage

import android.content.ClipData
import android.content.res.ColorStateList
import android.os.Bundle
import android.view.DragEvent
import android.view.Gravity
import android.view.View
import android.view.animation.Animation
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.example.cameramessage.databinding.ActivityShelfManageBinding
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * app-shelf-manage (design-spec §3.4)
 * 미등록 기기 칩(드래그 소스) → 그리드 빈 슬롯(드롭 타깃) → 기기 등록 카드 → register API.
 * 선반 카드/그리드는 configs(행×열) 기반으로 동적 생성. 행/열 추가, 새 선반 추가 지원.
 */
class ShelfManageActivity : AppCompatActivity() {

    private lateinit var binding: ActivityShelfManageBinding
    private var pollJob: Job? = null
    private lateinit var exceptionHelper: ExceptionDialogHelper

    private val expandedShelves = mutableSetOf<String>()
    private var expandedInit = false
    private var isDragging = false

    private var pendingDeviceId: String? = null
    private var pendingShelfId: String? = null
    private var pendingRow = 0
    private var pendingCol = 0

    private var lastConfigs = listOf<ShelfConfig>()
    private var lastShelves = listOf<ShelfDevice>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityShelfManageBinding.inflate(layoutInflater)
        setContentView(binding.root)

        exceptionHelper = ExceptionDialogHelper(this)
        NavBar.setup(binding.bottomNav, this, NavBar.TAB_SHELF)

        binding.btnAddShelf.setOnClickListener { addShelf() }
        binding.btnRegisterDevice.setOnClickListener { submitRegistration() }

        // 등록 카드가 열려 있으면 뒤로가기로 먼저 닫는다
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (binding.registerCard.visibility == View.VISIBLE) hideRegisterCard()
                else finish()
            }
        })
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

    private fun startPolling() {
        pollJob?.cancel()
        pollJob = lifecycleScope.launch {
            while (true) {
                refreshOnce()
                delay(3000)
            }
        }
    }

    private fun stopPolling() {
        pollJob?.cancel()
    }

    private suspend fun refreshOnce() {
        try {
            val shelves = NetworkClient.api.getShelves()
            val configs = NetworkClient.api.getShelfConfigs()
            lastShelves = shelves
            lastConfigs = configs

            // 드래그/등록 입력 중에는 리렌더하지 않는다 (포커스/드래그 유지)
            if (!isDragging && binding.registerCard.visibility != View.VISIBLE) {
                updateUnregisteredStrip(shelves)
                renderShelfCards(configs, shelves)
            }
        } catch (e: Exception) {
            // 네트워크 오류 무시
        }
    }

    private fun refreshNow() {
        lifecycleScope.launch { refreshOnce() }
    }

    // ---------- 미등록 기기 스트립 ----------

    // 리셋 블링크를 이미 보여준 기기 — 폴링 재렌더마다 다시 깜빡이지 않게 한다
    private val blinkShown = mutableSetOf<String>()

    private fun updateUnregisteredStrip(shelves: List<ShelfDevice>) {
        val unregistered = shelves.filter { it.status == "unregistered" }
        // 리셋 표시가 꺼진 기기는 기록을 지워, 다음 리셋 때 다시 깜빡일 수 있게 한다
        unregistered.filter { !it.recently_reset }.forEach { blinkShown.remove(it.id) }
        if (unregistered.isEmpty()) {
            binding.unregisteredStripCard.visibility = View.GONE
            return
        }

        binding.unregisteredStripCard.visibility = View.VISIBLE
        binding.unregCountText.text = "미등록 기기 ${unregistered.size}대"
        binding.unregisteredDevicesLayout.removeAllViews()

        // 방금 리셋된 기기를 맨 앞으로 — 블링크와 함께 바로 눈에 띄게
        unregistered.sortedByDescending { it.recently_reset }.forEach { device ->
            binding.unregisteredDevicesLayout.addView(buildDeviceChip(device))
        }
    }

    /** 드래그 소스가 되는 기기 칩 (§3.4-2)
     *  방금 리셋 버튼이 눌린 기기는 블링크로 강조해 어떤 기기인지 식별하게 한다. */
    private fun buildDeviceChip(device: ShelfDevice): View {
        val primary = color(R.color.primary)
        val chip = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundResource(R.drawable.shape_rounded_10)
            backgroundTintList = ColorStateList.valueOf(color(R.color.primary_soft))
            setPadding(dp(11), dp(8), dp(11), dp(8))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { marginEnd = dp(8) }
        }

        // 리셋 직후 1회만: 주황으로 3번 깜빡인 뒤 파란색으로 돌아온다
        if (device.recently_reset && blinkShown.add(device.id)) {
            chip.backgroundTintList = ColorStateList.valueOf(color(R.color.warning_soft))
            chip.startAnimation(
                android.view.animation.AlphaAnimation(1f, 0.3f).apply {
                    duration = 350
                    repeatMode = Animation.REVERSE
                    repeatCount = 5  // 왕복 3회
                    setAnimationListener(object : Animation.AnimationListener {
                        override fun onAnimationStart(a: Animation?) {}
                        override fun onAnimationRepeat(a: Animation?) {}
                        override fun onAnimationEnd(a: Animation?) {
                            chip.backgroundTintList =
                                ColorStateList.valueOf(color(R.color.primary_soft))
                        }
                    })
                }
            )
        }

        chip.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_grip_vertical)
            imageTintList = ColorStateList.valueOf(primary)
            layoutParams = LinearLayout.LayoutParams(dp(11), dp(11))
        })
        chip.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_hard_drive)
            imageTintList = ColorStateList.valueOf(primary)
            layoutParams = LinearLayout.LayoutParams(dp(12), dp(12)).apply { marginStart = dp(5) }
        })
        chip.addView(TextView(this).apply {
            text = device.id
            textSize = 10f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(primary)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { marginStart = dp(5) }
        })

        chip.setOnLongClickListener { v ->
            isDragging = true
            val clip = ClipData.newPlainText("deviceId", device.id)
            v.startDragAndDrop(clip, View.DragShadowBuilder(v), device.id, 0)
            true
        }
        chip.setOnClickListener {
            Toast.makeText(this, "칩을 길게 눌러 빈 슬롯으로 드래그하세요.", Toast.LENGTH_SHORT).show()
        }
        return chip
    }

    // ---------- 선반 카드 / 그리드 ----------

    private fun renderShelfCards(configs: List<ShelfConfig>, shelves: List<ShelfDevice>) {
        if (!expandedInit && configs.isNotEmpty()) {
            expandedShelves.addAll(configs.map { it.id })
            expandedInit = true
        }

        binding.shelvesContainer.removeAllViews()
        configs.sortedBy { it.id }.forEach { config ->
            binding.shelvesContainer.addView(buildShelfCard(config, shelves))
        }
    }

    private fun buildShelfCard(config: ShelfConfig, shelves: List<ShelfDevice>): View {
        val expanded = expandedShelves.contains(config.id)
        val deviceCount = shelves.count { it.parent_shelf == config.id && it.status == "registered" }

        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.shape_rounded_14)
            elevation = dp(2).toFloat()
            setPadding(dp(14), dp(12), dp(14), dp(12))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(12) }
        }

        // 헤더: 이름 + 크기 칩 + 접기/펼치기
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(TextView(this).apply {
            text = "선반 ${config.id}"
            textSize = 15f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(color(R.color.text_strong))
        })
        header.addView(TextView(this).apply {
            text = if (expanded) "${config.rows}행 × ${config.cols}열"
            else "${config.rows}행 × ${config.cols}열 · 기기 ${deviceCount}대"
            textSize = 9f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(color(R.color.text_body))
            setBackgroundResource(R.drawable.shape_pill)
            backgroundTintList = ColorStateList.valueOf(color(R.color.bg))
            setPadding(dp(8), dp(3), dp(8), dp(3))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { marginStart = dp(8) }
        })
        header.addView(View(this).apply {
            layoutParams = LinearLayout.LayoutParams(0, 1, 1f)
        })
        header.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_chevron_down)
            imageTintList = ColorStateList.valueOf(color(R.color.text_muted))
            rotation = if (expanded) 180f else 0f
            layoutParams = LinearLayout.LayoutParams(dp(16), dp(16))
        })
        header.setOnClickListener {
            if (expanded) expandedShelves.remove(config.id) else expandedShelves.add(config.id)
            renderShelfCards(lastConfigs, lastShelves)
        }
        card.addView(header)

        if (!expanded) return card

        // GridWrap: [슬롯 그리드] + [열 추가 버튼]
        val gridWrap = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(10) }
        }

        val gridColumn = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        for (r in 1..config.rows) {
            val rowLayout = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
            }
            for (c in 1..config.cols) {
                val device = shelves.find {
                    it.parent_shelf == config.id && it.row == r && it.col == c &&
                            it.status == "registered"
                }
                rowLayout.addView(buildCell(config, r, c, device))
            }
            gridColumn.addView(rowLayout)
        }
        // 행이나 열이 0이면 그리드가 완전히 접혀 열 추가 버튼까지 사라지므로
        // 빈 상태 안내를 셀 높이만큼 넣어 추가 버튼들이 항상 보이게 한다
        if (config.rows == 0 || config.cols == 0) {
            gridColumn.addView(TextView(this).apply {
                text = "빈 선반입니다\n행과 열을 추가해 수납칸을 만드세요"
                textSize = 10f
                gravity = Gravity.CENTER
                setTextColor(color(R.color.text_muted))
                setBackgroundResource(R.drawable.shape_rounded_8)
                backgroundTintList = ColorStateList.valueOf(color(R.color.bg))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, dp(78)
                ).apply { setMargins(dp(3), dp(3), dp(3), dp(3)) }
            })
        }
        gridWrap.addView(gridColumn)

        // 열 추가 버튼 (그리드 오른쪽 세로 버튼)
        gridWrap.addView(FrameLayout(this).apply {
            setBackgroundResource(R.drawable.shape_rounded_8)
            backgroundTintList = ColorStateList.valueOf(color(R.color.bg))
            layoutParams = LinearLayout.LayoutParams(dp(26), LinearLayout.LayoutParams.MATCH_PARENT)
                .apply { setMargins(dp(3), dp(3), 0, dp(3)) }
            addView(ImageView(this@ShelfManageActivity).apply {
                setImageResource(R.drawable.ic_plus)
                imageTintList = ColorStateList.valueOf(color(R.color.primary))
                layoutParams = FrameLayout.LayoutParams(dp(13), dp(13)).apply {
                    gravity = Gravity.CENTER
                }
            })
            setOnClickListener { updateConfig(config.id, config.rows, config.cols + 1) }
        })
        card.addView(gridWrap)

        // 행 추가 버튼
        card.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setBackgroundResource(R.drawable.shape_rounded_8)
            backgroundTintList = ColorStateList.valueOf(color(R.color.bg))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(30)
            ).apply { topMargin = dp(6) }
            addView(ImageView(this@ShelfManageActivity).apply {
                setImageResource(R.drawable.ic_plus)
                imageTintList = ColorStateList.valueOf(color(R.color.primary))
                layoutParams = LinearLayout.LayoutParams(dp(12), dp(12))
            })
            addView(TextView(this@ShelfManageActivity).apply {
                text = "행 추가"
                textSize = 10f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(color(R.color.primary))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { marginStart = dp(4) }
            })
            setOnClickListener { updateConfig(config.id, config.rows + 1, config.cols) }
        })

        return card
    }

    /** 그리드 셀 3상태: 기기 배치됨 / 빈 슬롯 / 드롭 타깃 (§3.4-3) */
    private fun buildCell(config: ShelfConfig, row: Int, col: Int, device: ShelfDevice?): View {
        val cellParams = LinearLayout.LayoutParams(0, dp(78), 1f).apply {
            setMargins(dp(3), dp(3), dp(3), dp(3))
        }

        if (device != null) {
            // (a) 기기 배치됨 — LED 점등 시 primary 강조
            val cell = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(8), dp(7), dp(8), dp(7))
                setBackgroundResource(
                    if (device.led_on) R.drawable.bg_cell_drop else R.drawable.shape_rounded_8
                )
                if (!device.led_on) {
                    backgroundTintList = ColorStateList.valueOf(color(R.color.bg))
                }
                layoutParams = cellParams
            }
            cell.addView(TextView(this).apply {
                text = device.name ?: device.id
                textSize = 10f
                maxLines = 1
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(color(R.color.text_strong))
            })
            cell.addView(TextView(this).apply {
                text = device.id
                textSize = 8f
                maxLines = 1
                setTextColor(color(R.color.text_muted))
            })

            val meta = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { topMargin = dp(5) }
            }
            meta.addView(ImageView(this).apply {
                setImageResource(R.drawable.ic_weight)
                imageTintList = ColorStateList.valueOf(color(R.color.text_muted))
                layoutParams = LinearLayout.LayoutParams(dp(9), dp(9))
            })
            meta.addView(TextView(this).apply {
                text = "${device.weight}kg"
                textSize = 8f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(color(R.color.text_body))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { marginStart = dp(2) }
            })
            meta.addView(ImageView(this).apply {
                setImageResource(R.drawable.ic_battery_full)
                imageTintList = ColorStateList.valueOf(color(R.color.success))
                layoutParams = LinearLayout.LayoutParams(dp(10), dp(10)).apply {
                    marginStart = dp(7)
                }
            })
            meta.addView(TextView(this).apply {
                text = "${device.battery}%"
                textSize = 8f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(color(R.color.text_body))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { marginStart = dp(2) }
            })
            cell.addView(meta)

            // 탭 → LED 토글 (기존 기능 유지)
            cell.setOnClickListener {
                lifecycleScope.launch {
                    try {
                        NetworkClient.api.setLedState(
                            device.id,
                            ShelfLedUpdate(
                                led_on = !device.led_on,
                                led_message = if (!device.led_on) "앱에서 수동 점등" else ""
                            )
                        )
                        refreshOnce()
                    } catch (e: Exception) {
                        Toast.makeText(this@ShelfManageActivity, "LED 제어 실패", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            return cell
        }

        // (b) 빈 슬롯 + (c) 드롭 타깃
        val cell = FrameLayout(this).apply {
            setBackgroundResource(R.drawable.bg_cell_empty)
            layoutParams = cellParams
        }
        val emptyLabel = TextView(this).apply {
            text = "빈 슬롯"
            textSize = 9f
            setTextColor(color(R.color.text_muted))
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
            ).apply { gravity = Gravity.CENTER }
        }
        val dropHint = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            visibility = View.GONE
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
            ).apply { gravity = Gravity.CENTER }
            addView(ImageView(this@ShelfManageActivity).apply {
                setImageResource(R.drawable.ic_circle_plus)
                imageTintList = ColorStateList.valueOf(color(R.color.primary))
                layoutParams = LinearLayout.LayoutParams(dp(16), dp(16))
            })
            addView(TextView(this@ShelfManageActivity).apply {
                text = "여기에 배치"
                textSize = 9f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(color(R.color.primary))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { topMargin = dp(2) }
            })
        }
        cell.addView(emptyLabel)
        cell.addView(dropHint)

        fun revertCellStyle() {
            cell.setBackgroundResource(R.drawable.bg_cell_empty)
            emptyLabel.visibility = View.VISIBLE
            dropHint.visibility = View.GONE
        }

        cell.setOnDragListener { _, event ->
            when (event.action) {
                DragEvent.ACTION_DRAG_STARTED -> event.localState is String
                DragEvent.ACTION_DRAG_ENTERED -> {
                    cell.setBackgroundResource(R.drawable.bg_cell_drop)
                    emptyLabel.visibility = View.GONE
                    dropHint.visibility = View.VISIBLE
                    true
                }
                DragEvent.ACTION_DRAG_EXITED -> {
                    revertCellStyle()
                    true
                }
                DragEvent.ACTION_DROP -> {
                    val deviceId = event.localState as? String
                    revertCellStyle()
                    if (deviceId != null) {
                        onDeviceDropped(deviceId, config, row, col)
                        true
                    } else false
                }
                DragEvent.ACTION_DRAG_ENDED -> {
                    isDragging = false
                    revertCellStyle()
                    true
                }
                else -> true
            }
        }
        return cell
    }

    // ---------- 기기 등록 카드 ----------

    private fun onDeviceDropped(deviceId: String, config: ShelfConfig, row: Int, col: Int) {
        pendingDeviceId = deviceId
        pendingShelfId = config.id
        pendingRow = row
        pendingCol = col

        binding.registerCardTitle.text = "기기 등록 — $deviceId"
        binding.regDeviceIdText.text = deviceId
        binding.regPositionText.text = "선반 ${config.id} · ${row}행 ${col}열"
        binding.regNameInput.setText("수납칸 ${config.id}${(row - 1) * config.cols + col}")
        binding.registerCard.visibility = View.VISIBLE
        binding.regNameInput.requestFocus()
        binding.scrollArea.post {
            binding.scrollArea.smoothScrollTo(0, binding.registerCard.top)
        }
    }

    private fun hideRegisterCard() {
        pendingDeviceId = null
        pendingShelfId = null
        binding.registerCard.visibility = View.GONE
        refreshNow()
    }

    private fun submitRegistration() {
        val deviceId = pendingDeviceId ?: return
        val shelfId = pendingShelfId ?: return
        val name = binding.regNameInput.text.toString().trim()
        if (name.isEmpty()) {
            Toast.makeText(this, "기기 식별 이름을 입력해 주세요.", Toast.LENGTH_SHORT).show()
            return
        }

        lifecycleScope.launch {
            try {
                NetworkClient.api.registerShelf(
                    deviceId,
                    ShelfRegister(name = name, parent_shelf = shelfId, row = pendingRow, col = pendingCol)
                )
                Toast.makeText(this@ShelfManageActivity, "기기가 등록되었습니다.", Toast.LENGTH_SHORT).show()
                hideRegisterCard()
            } catch (e: Exception) {
                Toast.makeText(this@ShelfManageActivity, "등록 실패: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    // ---------- 선반 구성 변경 ----------

    private fun updateConfig(configId: String, rows: Int, cols: Int) {
        lifecycleScope.launch {
            try {
                NetworkClient.api.updateShelfConfig(configId, mapOf("rows" to rows, "cols" to cols))
                refreshOnce()
            } catch (e: Exception) {
                Toast.makeText(this@ShelfManageActivity, "선반 구성 변경 실패", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun addShelf() {
        val nextId = if (lastConfigs.isEmpty()) {
            "A"
        } else {
            val maxChar = lastConfigs.mapNotNull { it.id.firstOrNull() }.maxOrNull() ?: 'A'
            (maxChar + 1).toString()
        }
        lifecycleScope.launch {
            try {
                // 1×1 최소 크기로 만든다 — 행/열은 사용자가 카드에서 직접 늘린다
                NetworkClient.api.updateShelfConfig(nextId, mapOf("rows" to 1, "cols" to 1))
                Toast.makeText(this@ShelfManageActivity, "선반 ${nextId}가 추가되었습니다.", Toast.LENGTH_SHORT).show()
                expandedShelves.add(nextId)
                refreshOnce()
            } catch (e: Exception) {
                Toast.makeText(this@ShelfManageActivity, "선반 추가 실패", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun color(res: Int): Int = ContextCompat.getColor(this, res)
}
