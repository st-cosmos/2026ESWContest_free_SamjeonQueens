package com.example.cameramessage

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.util.TypedValue
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.example.cameramessage.databinding.ComponentBottomNavBinding

/** dp → px 변환 (동적 뷰 생성용) */
fun Context.dp(value: Int): Int =
    TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics
    ).toInt()

/** FastAPI 오류 응답 {"detail": "..."} 에서 사용자용 메시지를 꺼낸다. */
fun httpErrorDetail(e: Exception): String? {
    val http = e as? retrofit2.HttpException ?: return null
    return try {
        val body = http.response()?.errorBody()?.string() ?: return null
        org.json.JSONObject(body).optString("detail").takeIf { it.isNotBlank() }
    } catch (ex: Exception) {
        null
    }
}

/**
 * Nav/BottomBar (design-spec §1.2) 활성 탭 스타일 지정 + 탭 이동 처리.
 * 홈이 항상 스택 하단에 있으므로, 홈 이외 화면 간 이동은 startActivity + finish 로 처리한다.
 */
object NavBar {
    const val TAB_HOME = 0
    const val TAB_CHECKIN = 1
    const val TAB_CHECKOUT = 2
    const val TAB_SHELF = 3
    const val TAB_INVENTORY = 4

    fun setup(nav: ComponentBottomNavBinding, activity: Activity, activeTab: Int) {
        val primary = ContextCompat.getColor(activity, R.color.primary)
        val primarySoft = ContextCompat.getColor(activity, R.color.primary_soft)
        val muted = ContextCompat.getColor(activity, R.color.text_muted)

        val tabs: List<Triple<LinearLayout, ImageView, TextView>> = listOf(
            Triple(nav.navHome, nav.navHomeIcon, nav.navHomeLabel),
            Triple(nav.navCheckin, nav.navCheckinIcon, nav.navCheckinLabel),
            Triple(nav.navCheckout, nav.navCheckoutIcon, nav.navCheckoutLabel),
            Triple(nav.navShelf, nav.navShelfIcon, nav.navShelfLabel),
            Triple(nav.navInventory, nav.navInventoryIcon, nav.navInventoryLabel)
        )

        tabs.forEachIndexed { index, (tab, icon, label) ->
            val active = index == activeTab
            if (active) {
                tab.setBackgroundResource(R.drawable.shape_pill)
                tab.backgroundTintList = ColorStateList.valueOf(primarySoft)
                icon.imageTintList = ColorStateList.valueOf(primary)
                label.setTextColor(primary)
            } else {
                tab.background = null
                icon.imageTintList = ColorStateList.valueOf(muted)
                label.setTextColor(muted)
            }

            tab.setOnClickListener {
                if (index == activeTab) return@setOnClickListener
                when (index) {
                    TAB_HOME -> if (activity !is HomeActivity) activity.finish()
                    TAB_CHECKIN -> navigate(activity, CheckinActivity::class.java)
                    TAB_CHECKOUT -> navigate(activity, CheckoutActivity::class.java)
                    TAB_SHELF -> navigate(activity, ShelfManageActivity::class.java)
                    TAB_INVENTORY -> navigate(activity, InventoryActivity::class.java)
                }
            }
        }
    }

    private fun navigate(activity: Activity, target: Class<*>) {
        activity.startActivity(Intent(activity, target))
        if (activity !is HomeActivity) activity.finish()
    }
}
