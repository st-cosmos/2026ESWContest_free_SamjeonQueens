package com.example.cameramessage

data class UserData(
    val username: String,
    val nickname: String,
    val role: String,
    val created_at: String
)

// 안드로이드 앱 로그인: 4자리 PIN으로 인증
data class UserPinLoginRequest(
    val username: String,
    val pin: String
)

// 앱 세션 (선반 전력 모드, docs/power-modes.md)
// 로그인 등록은 서버가 login-pin 성공 시 알아서 하고, 앱은 로그아웃 때 leave 만 보낸다.
data class AppSessionRequest(
    val username: String
)

data class AppSessionState(
    val mode: String,       // "active" | "idle"
    val users: Int,
    val ttl_s: Double?
)
