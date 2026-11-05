package com.example.cameramessage

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.HapticFeedbackConstants
import android.view.LayoutInflater
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.example.cameramessage.databinding.ActivityInventoryBinding
import com.example.cameramessage.databinding.ItemInventoryRowBinding
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

// 잔량 % 환산: 병별 capacity_kg(라벨/사진 추정 + 실측 래칫)을 우선 쓰고,
// 미추정 병은 가득 = 520g 폴백 (web Inventory.tsx·서버 용량 추정 기본값과 동일 기준)
private const val CAPACITY_KG = 0.52
private const val DANGER_PCT = 25

/**
 * app-inventory / app-inventory-edit (design.pen)
 * 웹 재고 관리와 동일한 시약 리스트: 검색 · 필터 칩 · 상태 배지 · 잔량 바 · 위치 LED 점등.
 * 카드를 길게 누르면 편집 모드 — 여러 시약을 선택해 한 번에 폐기한다.
 */
class InventoryActivity : AppCompatActivity() {

    private enum class Filter(val label: String) {
        ALL("전체"), IN_STOCK("비치중"), OUT("반출중"), EXPIRY("유통기한"), CO_STORAGE("인접 보관")
    }

    private lateinit var binding: ActivityInventoryBinding
    private var pollJob: Job? = null
    private lateinit var exceptionHelper: ExceptionDialogHelper

    private var chemicals = listOf<ChemicalData>()
    private var devices = listOf<ShelfDevice>()
    private var alerts: ChemicalAlerts? = null
    private var nicknames = mapOf<String, String>()
    private var loaded = false

    private var filter = Filter.ALL
    private var query = ""
    private var currentUser = ""
    private val chipViews = linkedMapOf<Filter, TextView>()

    // 편집 모드: 5초 폴링으로 리스트가 다시 그려져도 선택이 유지되도록 id 로 보관
    private var editMode = false
    private val selectedIds = linkedSetOf<String>()
    private var disposing = false
    private val exitEditOnBack = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() = exitEditMode()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityInventoryBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences("smart_shelf", Context.MODE_PRIVATE)
        currentUser = prefs.getString("username", "kim.lab") ?: "kim.lab"

        exceptionHelper = ExceptionDialogHelper(this)
        NavBar.setup(binding.bottomNav, this, NavBar.TAB_INVENTORY)

        buildChips()
        onBackPressedDispatcher.addCallback(this, exitEditOnBack)
        binding.editDoneBtn.setOnClickListener { exitEditMode() }
        binding.disposeBtn.setOnClickListener { confirmDisposeSelected() }
        binding.searchInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) {
                query = s?.toString() ?: ""
                render()
            }
        })

        // 보유자 닉네임 표시용 (반출중 배지)
        lifecycleScope.launch {
            try {
                nicknames = NetworkClient.api.getUsers().associate { it.username to it.nickname }
                render()
            } catch (_: Exception) {
            }
        }
    }

    override fun onResume() {
        super.onResume()
        startPolling()
        exceptionHelper.startPollingAlerts()
    }

    override fun onPause() {
        super.onPause()
        pollJob?.cancel()
        exceptionHelper.stopPollingAlerts()
    }

    private fun startPolling() {
        pollJob?.cancel()
        pollJob = lifecycleScope.launch {
            while (isActive) {
                fetchData()
                delay(5000)
            }
        }
    }

    private suspend fun fetchData() {
        try {
            chemicals = NetworkClient.api.getChemicals()
            devices = NetworkClient.api.getShelves()
            alerts = NetworkClient.api.getAlerts()
            loaded = true
        } catch (_: Exception) {
            // 폴링 실패 무시 (다음 주기 재시도)
        }
        render()
    }

    // ---------- 렌더링 ----------

    private fun buildChips() {
        Filter.entries.forEach { f ->
            val chip = TextView(this).apply {
                textSize = 10f
                typeface = Typeface.DEFAULT_BOLD
                setPadding(dp(13), dp(6), dp(13), dp(6))
                text = f.label
                setOnClickListener {
                    filter = f
                    render()
                }
            }
            val lp = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { if (f != Filter.ALL) marginStart = dp(6) }
            binding.filterChips.addView(chip, lp)
            chipViews[f] = chip
        }
    }

    private fun render() {
        val expiredIds = alerts?.expired_chemicals
            ?.map { it.chemical_id }?.toSet() ?: emptySet()
        val coIds = alerts?.co_storage_warnings
            ?.flatMap { listOf(it.chemical_1_id, it.chemical_2_id) }?.toSet() ?: emptySet()
        val outCount = chemicals.count { it.current_status == "반출중" }
        val expiryCount = chemicals.count { it.id in expiredIds }
        val coCount = chemicals.count { it.id in coIds }

        binding.invSummary.text =
            if (!loaded) "데이터를 불러오는 중입니다..."
            else "보유 시약 ${chemicals.size}종 · 반출 ${outCount}건 · " +
                    "유통기한 ${expiryCount}건 · 인접 보관 ${coCount}건"
        binding.expiryPill.visibility = if (expiryCount > 0) View.VISIBLE else View.GONE
        binding.expiryPillText.text = "유통기한 $expiryCount"
        binding.coPill.visibility = if (coCount > 0) View.VISIBLE else View.GONE
        binding.coPillText.text = "인접 보관 $coCount"

        val counts = mapOf(
            Filter.ALL to chemicals.size,
            Filter.IN_STOCK to chemicals.size - outCount,
            Filter.OUT to outCount,
            Filter.EXPIRY to expiryCount,
            Filter.CO_STORAGE to coCount
        )
        chipViews.forEach { (f, chip) ->
            chip.text = if (loaded) "${f.label} ${counts[f]}" else f.label
            if (f == filter) {
                chip.setBackgroundResource(R.drawable.shape_pill)
                chip.backgroundTintList =
                    ColorStateList.valueOf(color(R.color.primary))
                chip.setTextColor(color(R.color.white))
            } else {
                chip.setBackgroundResource(R.drawable.bg_pill_outline)
                chip.backgroundTintList = null
                chip.setTextColor(color(R.color.text_body))
            }
        }

        val q = query.trim().lowercase()
        val filtered = chemicals.filter { chem ->
            val matchQ = q.isEmpty() ||
                    chem.name.lowercase().contains(q) ||
                    (chem.formula ?: "").lowercase().contains(q) ||
                    (chem.cas_no ?: "").lowercase().contains(q)
            val matchF = when (filter) {
                Filter.ALL -> true
                Filter.IN_STOCK -> chem.current_status != "반출중"
                Filter.OUT -> chem.current_status == "반출중"
                Filter.EXPIRY -> chem.id in expiredIds
                Filter.CO_STORAGE -> chem.id in coIds
            }
            matchQ && matchF
        }

        // 다른 기기에서 이미 폐기·삭제된 시약은 선택에서 뺀다
        if (loaded) selectedIds.retainAll(chemicals.map { it.id }.toSet())

        binding.chemListLayout.removeAllViews()
        binding.emptyText.visibility = if (filtered.isEmpty()) View.VISIBLE else View.GONE
        binding.emptyText.text =
            if (!loaded) "데이터를 불러오는 중입니다..." else "검색 결과가 없습니다."
        filtered.forEach { chem -> binding.chemListLayout.addView(buildRow(chem)) }
        renderEditBars()
    }

    private fun renderEditBars() {
        binding.editBar.visibility = if (editMode) View.VISIBLE else View.GONE
        val picked = chemicals.filter { it.id in selectedIds }
        binding.editCount.text = "${picked.size}개 선택됨"
        binding.disposeBar.visibility =
            if (editMode && picked.isNotEmpty()) View.VISIBLE else View.GONE
        binding.disposeTitle.text = "선택한 시약 ${picked.size}개"
        binding.disposeNames.text = picked.joinToString(" · ") { it.name }
        binding.disposeBtnText.text = "선택 폐기 (${picked.size})"
    }

    private fun buildRow(chem: ChemicalData): View {
        val b = ItemInventoryRowBinding.inflate(
            LayoutInflater.from(this), binding.chemListLayout, false
        )
        val isOut = chem.current_status == "반출중"
        val expired = alerts?.expired_chemicals?.find { it.chemical_id == chem.id }
        val co = alerts?.co_storage_warnings
            ?.find { it.chemical_1_id == chem.id || it.chemical_2_id == chem.id }

        b.invName.text = chem.name
        b.invSub.text = buildString {
            chem.formula?.takeIf { it.isNotBlank() }?.let { append(it).append(" · ") }
            append("CAS ").append(chem.cas_no ?: "-")
        }

        // 반출중이면 아이콘/LED 비활성 톤
        b.invIconWrap.backgroundTintList = ColorStateList.valueOf(
            color(if (isOut) R.color.bg_deep else R.color.primary_soft)
        )
        b.invIcon.imageTintList = ColorStateList.valueOf(
            color(if (isOut) R.color.text_muted else R.color.primary)
        )

        if (isOut) {
            val holder = nicknames[chem.holder_username] ?: chem.holder_username
            b.invBadge.text = if (holder.isNullOrBlank()) "반출중" else "반출중 · $holder"
            b.invBadge.backgroundTintList = ColorStateList.valueOf(color(R.color.danger_soft))
            b.invBadge.setTextColor(color(R.color.danger))
        }

        b.invLoc.text = locationText(chem)
        if (co != null) b.invCoWarn.visibility = View.VISIBLE

        b.invExpiry.text = chem.expiration_date ?: "-"
        if (expired != null) {
            // 기한 초과 = danger, 임박(days_over ≤ 0) = warning
            val c = color(if (expired.days_over > 0) R.color.danger else R.color.warning)
            b.invExpIcon.setImageResource(R.drawable.ic_clock_alert)
            b.invExpIcon.imageTintList = ColorStateList.valueOf(c)
            b.invExpiry.setTextColor(c)
        }

        val cap = chem.capacity_kg?.takeIf { it > 0 } ?: CAPACITY_KG
        val pct = ((chem.weight / cap) * 100).roundToInt().coerceIn(0, 100)
        val low = pct <= DANGER_PCT
        b.invFill.backgroundTintList = ColorStateList.valueOf(
            color(if (low) R.color.danger else R.color.primary)
        )
        b.invFill.layoutParams = b.invFill.layoutParams.apply {
            width = (dp(72) * pct / 100).coerceAtLeast(dp(5))
        }
        b.invPct.text = "$pct%"
        if (low) b.invPct.setTextColor(color(R.color.danger))

        if (editMode) {
            // 편집 중 LED 버튼은 숨긴다 — 선택하려다 LED가 켜지는 오조작 방지
            b.invLedBtn.visibility = View.GONE
            val selected = chem.id in selectedIds
            b.invCheck.visibility = View.VISIBLE
            if (selected) {
                b.invCheck.setBackgroundResource(R.drawable.shape_circle)
                b.invCheck.backgroundTintList = ColorStateList.valueOf(color(R.color.primary))
                b.invCheckIcon.visibility = View.VISIBLE
                b.root.setBackgroundResource(R.drawable.bg_inv_row_selected)
                b.root.backgroundTintList = null
            }
            b.root.setOnClickListener { toggleSelection(chem) }
        } else if (isOut) {
            b.invLedBtn.backgroundTintList = ColorStateList.valueOf(color(R.color.bg))
            b.invLedIcon.imageTintList = ColorStateList.valueOf(color(R.color.text_muted))
        } else {
            b.invLedBtn.setOnClickListener { lightLed(chem) }
        }
        b.root.setOnLongClickListener {
            it.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
            if (editMode) toggleSelection(chem) else enterEditMode(chem)
            true
        }
        return b.root
    }

    // ---------- 편집 모드 · 일괄 폐기 (design.pen app-inventory-edit / -edit-confirm) ----------

    /** 카드를 길게 누르면 편집 모드로 들어가며, 누른 카드는 선택된 상태로 시작한다. */
    private fun enterEditMode(first: ChemicalData) {
        editMode = true
        selectedIds.clear()
        selectedIds.add(first.id)
        exitEditOnBack.isEnabled = true
        render()
    }

    private fun exitEditMode() {
        editMode = false
        selectedIds.clear()
        exitEditOnBack.isEnabled = false
        render()
    }

    private fun toggleSelection(chem: ChemicalData) {
        if (!selectedIds.remove(chem.id)) selectedIds.add(chem.id)
        render()
    }

    private fun confirmDisposeSelected() {
        val picked = chemicals.filter { it.id in selectedIds }
        if (picked.isEmpty() || disposing) return
        AppModal.show(
            this, AppModal.Tone.DANGER, R.drawable.ic_trash_2,
            "시약 ${picked.size}개 폐기",
            "${picked.joinToString(" · ") { it.name }}\n" +
                    "선택한 ${picked.size}개 시약을 폐기 등록할까요?\n" +
                    "재고 목록에서 삭제되며 되돌릴 수 없습니다.",
            "취소", "${picked.size}개 폐기하기",
            onPrimary = { disposeSelected(picked) }
        )
    }

    /** 선택 시약을 하나씩 폐기 등록한다. 일부만 실패하면 실패한 시약만 선택으로 남겨
     *  편집 모드를 유지하고, 모두 성공하면 편집 모드를 끝낸다. */
    private fun disposeSelected(picked: List<ChemicalData>) {
        disposing = true
        lifecycleScope.launch {
            val failed = mutableListOf<ChemicalData>()
            var lastError: String? = null
            for (chem in picked) {
                try {
                    NetworkClient.api.disposeChemical(chem.id, DisposeRequest(username = currentUser))
                } catch (e: Exception) {
                    failed.add(chem)
                    lastError = httpErrorDetail(e)
                }
            }
            disposing = false
            val done = picked.size - failed.size
            if (failed.isEmpty()) {
                exitEditMode()
                AppModal.show(
                    this@InventoryActivity, AppModal.Tone.SUCCESS, R.drawable.ic_trash_2,
                    "폐기 등록 완료",
                    "시약 ${done}개가 폐기 등록되었습니다.\n재고 목록에서 삭제되었습니다.",
                    null, "확인"
                )
            } else {
                selectedIds.clear()
                failed.forEach { selectedIds.add(it.id) }
                Toast.makeText(
                    this@InventoryActivity,
                    "${done}개 폐기 완료, ${failed.size}개 실패" +
                            (lastError?.let { " — $it" } ?: ""),
                    Toast.LENGTH_LONG
                ).show()
            }
            fetchData()
        }
    }

    // ---------- 데이터 유틸 ----------

    private fun locationText(chem: ChemicalData): String {
        val row = chem.shelf_row
        val col = chem.shelf_col
        if (row == null || col == null) return "-"
        val letter = chem.shelf_id?.let { id -> devices.find { it.id == id }?.parent_shelf }
        return if (letter != null) "$letter · ${row}행 ${col}열" else "${row}행 ${col}열"
    }

    private fun lightLed(chem: ChemicalData) {
        lifecycleScope.launch {
            try {
                val res = NetworkClient.api.selectLed(SelectLedRequest(chem_id = chem.id))
                Toast.makeText(
                    this@InventoryActivity,
                    "${res.chemical_name} 위치 LED 점등 — 선반 ${res.parent_shelf} · ${res.row}행 ${res.col}열",
                    Toast.LENGTH_SHORT
                ).show()
            } catch (e: Exception) {
                Toast.makeText(
                    this@InventoryActivity,
                    httpErrorDetail(e) ?: "LED 점등에 실패했습니다.",
                    Toast.LENGTH_SHORT
                ).show()
            }
        }
    }

    private fun color(res: Int) = ContextCompat.getColor(this, res)
}
