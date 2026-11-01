package com.example.cameramessage

/**
 * 화면 표시용 무게(kg). 로드셀 영점 오차로 빈 칸이 -0.05kg 처럼 음수로 측정돼도 0 으로 보인다.
 * 서버의 반입·반출 판정은 실측값을 그대로 쓰므로, 저장값이 아니라 표시할 때만 자른다.
 * maxOf(Math.max) 를 쓴다: coerceAtLeast(0.0) 는 -0.0 을 그대로 둬 "-0.0" 으로 보일 수 있다.
 */
fun displayKg(kg: Double): Double = maxOf(0.0, kg)
