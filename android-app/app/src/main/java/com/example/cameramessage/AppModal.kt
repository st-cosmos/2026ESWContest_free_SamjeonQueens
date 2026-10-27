package com.example.cameramessage

import android.app.Activity
import android.app.AlertDialog
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import androidx.annotation.DrawableRes
import androidx.core.content.ContextCompat
import com.example.cameramessage.databinding.DialogAppModalBinding

/**
 * Modal/App (design-spec §1.4) 공통 모달.
 * 톤(primary/success/warning/danger)에 따라 IconWrap 배경(soft), 아이콘, Primary 버튼 색을 지정한다.
 */
object AppModal {

    enum class Tone(val colorRes: Int, val softColorRes: Int) {
        PRIMARY(R.color.primary, R.color.primary_soft),
        SUCCESS(R.color.success, R.color.success_soft),
        WARNING(R.color.warning, R.color.warning_soft),
        DANGER(R.color.danger, R.color.danger_soft)
    }

    /** 추천 위치 안내용 선반 시각화 데이터 (행·열은 1부터 시작) */
    data class ShelfVisual(
        val shelfName: String,
        val rows: Int,
        val cols: Int,
        val occupied: Set<Pair<Int, Int>>,
        val recommended: Pair<Int, Int>
    )

    fun show(
        activity: Activity,
        tone: Tone,
        @DrawableRes iconRes: Int,
        title: String,
        message: String,
        secondaryText: String?,
        primaryText: String,
        cancelable: Boolean = false,
        autoDismissMs: Long? = null,
        dateBadge: String? = null,        // 지정 시 메시지 아래에 톤 색 pill 배지로 강조 표시
        shelfVisual: ShelfVisual? = null, // 지정 시 추천 위치 선반 그리드 시각화 표시
        inputTextHint: String? = null,    // 지정 시 Input/App 스타일 입력 필드 표시
        inputType: Int? = null,
        inputErrorText: String? = null,   // onInputSubmit 이 false 를 반환하면 표시할 오류 문구
        datePickerDefault: String? = null, // 지정 시(YYYY-MM-DD) 달력 UI 표시, 결과는 onInputSubmit 으로 전달
        onSecondary: (() -> Unit)? = null,
        onPrimary: (() -> Unit)? = null,
        onInputSubmit: ((String) -> Boolean)? = null  // true 반환 시에만 모달을 닫는다
    ): AlertDialog? {
        if (activity.isFinishing || activity.isDestroyed) return null

        val binding = DialogAppModalBinding.inflate(LayoutInflater.from(activity))
        val toneColor = ContextCompat.getColor(activity, tone.colorRes)
        val softColor = ContextCompat.getColor(activity, tone.softColorRes)

        binding.modalIconWrap.backgroundTintList = ColorStateList.valueOf(softColor)
        binding.modalIcon.setImageResource(iconRes)
        binding.modalIcon.imageTintList = ColorStateList.valueOf(toneColor)
        binding.modalTitle.text = title
        binding.modalMessage.text = message
        binding.btnModalPrimary.text = primaryText
        binding.btnModalPrimary.backgroundTintList = ColorStateList.valueOf(toneColor)

        if (secondaryText == null) {
            // 버튼 1개 변형: Secondary 숨김 → Primary 전체 폭
            binding.btnModalSecondary.visibility = View.GONE
        } else {
            binding.btnModalSecondary.text = secondaryText
        }

        if (dateBadge != null) {
            binding.modalDatePill.visibility = View.VISIBLE
            binding.modalDatePill.backgroundTintList = ColorStateList.valueOf(softColor)
            binding.modalDateIcon.imageTintList = ColorStateList.valueOf(toneColor)
            binding.modalDateText.setTextColor(toneColor)
            binding.modalDateText.text = dateBadge
        }

        if (shelfVisual != null) {
            renderShelfVisual(activity, binding, shelfVisual)
        }

        if (inputTextHint != null) {
            binding.modalInput.visibility = View.VISIBLE
            binding.modalInput.hint = inputTextHint
            if (inputType != null) {
                binding.modalInput.inputType = inputType
            }
        }

        if (datePickerDefault != null) {
            binding.modalDatePicker.visibility = View.VISIBLE
            Regex("(\\d{4})-(\\d{2})-(\\d{2})").find(datePickerDefault)?.let { m ->
                binding.modalDatePicker.updateDate(
                    m.groupValues[1].toInt(),
                    m.groupValues[2].toInt() - 1,  // DatePicker 의 month 는 0부터
                    m.groupValues[3].toInt()
                )
            }
        }

        val dialog = AlertDialog.Builder(activity)
            .setView(binding.root)
            .setCancelable(cancelable)
            .create()
        dialog.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))

        // 자동 닫힘 타이머 — 버튼을 누르면 취소된다
        var autoRunnable: Runnable? = null
        if (autoDismissMs != null) {
            autoRunnable = Runnable {
                if (dialog.isShowing && !activity.isFinishing && !activity.isDestroyed) {
                    dialog.dismiss()
                    onPrimary?.invoke()
                }
            }
            binding.root.postDelayed(autoRunnable, autoDismissMs)
        }

        binding.btnModalSecondary.setOnClickListener {
            autoRunnable?.let { r -> binding.root.removeCallbacks(r) }
            dialog.dismiss()
            onSecondary?.invoke()
        }
        binding.btnModalPrimary.setOnClickListener {
            // 입력 모달: 검증 통과(true)일 때만 닫는다
            if (onInputSubmit != null) {
                val value = if (datePickerDefault != null) {
                    // 달력 모달: 선택된 날짜를 YYYY-MM-DD 로 전달
                    val p = binding.modalDatePicker
                    String.format("%04d-%02d-%02d", p.year, p.month + 1, p.dayOfMonth)
                } else {
                    binding.modalInput.text.toString().trim()
                }
                val ok = onInputSubmit(value)
                if (!ok) {
                    binding.modalInput.error = inputErrorText ?: "입력 형식을 확인해 주세요"
                    return@setOnClickListener
                }
            }
            autoRunnable?.let { r -> binding.root.removeCallbacks(r) }
            dialog.dismiss()
            onPrimary?.invoke()
        }

        if (inputTextHint != null) {
            binding.modalInput.requestFocus()
            dialog.window?.setSoftInputMode(
                android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_VISIBLE
            )
        }

        dialog.show()
        // parent 없이 inflate 하면 루트의 layout_width 가 무시되므로 창 크기를 직접 지정
        dialog.window?.setLayout(activity.dp(360), ViewGroup.LayoutParams.WRAP_CONTENT)
        return dialog
    }

    /** 선반 미니 그리드를 동적으로 그린다 — 추천 칸(강조)·사용 중(플라스크)·빈 칸 */
    private fun renderShelfVisual(
        activity: Activity,
        binding: DialogAppModalBinding,
        visual: ShelfVisual
    ) {
        binding.modalShelfVisual.visibility = View.VISIBLE
        binding.modalShelfName.text = visual.shelfName
        binding.modalShelfCoord.text = "${visual.recommended.first}행 ${visual.recommended.second}열"

        binding.modalShelfGrid.removeAllViews()
        val cellHeight = activity.dp(42)
        val gap = activity.dp(6)
        val iconSize = activity.dp(15)

        for (r in 1..visual.rows) {
            val rowLayout = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, cellHeight
                ).apply { if (r > 1) topMargin = gap }
            }
            for (c in 1..visual.cols) {
                val isRecommended = (r to c) == visual.recommended
                val isOccupied = (r to c) in visual.occupied

                val cell = FrameLayout(activity).apply {
                    layoutParams = LinearLayout.LayoutParams(
                        0, ViewGroup.LayoutParams.MATCH_PARENT, 1f
                    ).apply { if (c > 1) marginStart = gap }
                    setBackgroundResource(
                        when {
                            isRecommended -> R.drawable.bg_cell_recommend
                            isOccupied -> R.drawable.bg_cell_occupied
                            else -> R.drawable.bg_cell_free
                        }
                    )
                }
                val icon = when {
                    isRecommended -> R.drawable.ic_map_pin to R.color.success
                    isOccupied -> R.drawable.ic_flask_conical to R.color.text_muted
                    else -> null
                }
                if (icon != null) {
                    cell.addView(ImageView(activity).apply {
                        layoutParams = FrameLayout.LayoutParams(iconSize, iconSize, Gravity.CENTER)
                        setImageResource(icon.first)
                        imageTintList = ColorStateList.valueOf(
                            ContextCompat.getColor(activity, icon.second)
                        )
                    })
                }
                rowLayout.addView(cell)
            }
            binding.modalShelfGrid.addView(rowLayout)
        }
    }
}
