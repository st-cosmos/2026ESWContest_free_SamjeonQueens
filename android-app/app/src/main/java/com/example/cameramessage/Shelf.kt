package com.example.cameramessage

data class ShelfDevice(
    val id: String,
    val name: String?,
    val parent_shelf: String?,
    val row: Int?,
    val col: Int?,
    val weight: Double,
    val prev_weight: Double,
    val battery: Int,
    val status: String,
    val led_on: Boolean,
    val led_message: String,
    val updated_time: String,
    // 방금 리셋 버튼이 눌려 다시 잡힌 기기 — 미등록 목록에서 블링크로 식별
    val recently_reset: Boolean = false
)

data class ShelfConfig(
    val id: String,
    val name: String,
    val rows: Int,
    val cols: Int
)

data class ShelfRegister(
    val name: String,
    val parent_shelf: String,
    val row: Int,
    val col: Int
)

data class ShelfLedUpdate(
    val led_on: Boolean,
    val led_message: String
)
