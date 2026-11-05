package com.example.cameramessage

import java.util.Locale

/**
 * 화면 표시용 무게(kg). 로드셀 영점 오차로 빈 칸이 -0.05kg 처럼 음수로 측정돼도 0 으로 보인다.
 * 서버의 반입·반출 판정은 실측값을 그대로 쓰므로, 저장값이 아니라 표시할 때만 자른다.
 * maxOf(Math.max) 를 쓴다: coerceAtLeast(0.0) 는 -0.0 을 그대로 둬 "-0.0" 으로 보일 수 있다.
 */
fun displayKg(kg: Double): Double = maxOf(0.0, kg)

/**
 * 화면에 쓰는 무게 문자열. 웹(weight.ts formatKg)과 같게 소수점 둘째 자리까지 고정 ("0.50").
 * 기기 언어 설정이 소수점을 쉼표로 쓰더라도 점으로 보이도록 Locale.US 로 포맷한다.
 */
fun formatKg(kg: Double): String = String.format(Locale.US, "%.2f", displayKg(kg))
