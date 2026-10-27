package com.example.cameramessage

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/**
 * 서버 `/ws` 구독 — 프로세스 내 단일 연결을 여러 화면이 공유한다.
 * 연결이 끊기면 참조를 비워 두고, 다음 connect() 호출(화면 진입·세션 폴링 시작)에서
 * 다시 붙는다. 수신 메시지는 등록된 리스너 전체에 그대로 전달한다.
 */
object AppWebSocketManager {
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private val listeners = mutableListOf<(String) -> Unit>()

    fun addListener(listener: (String) -> Unit) {
        synchronized(listeners) {
            if (!listeners.contains(listener)) {
                listeners.add(listener)
            }
        }
    }

    fun removeListener(listener: (String) -> Unit) {
        synchronized(listeners) {
            listeners.remove(listener)
        }
    }

    fun connect(baseUrl: String) {
        if (webSocket != null) return

        val wsUrl = baseUrl.replace("http://", "ws://").replace("https://", "wss://") + "ws"
        val request = Request.Builder().url(wsUrl).build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                synchronized(listeners) {
                    listeners.forEach { it.invoke(text) }
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                this@AppWebSocketManager.webSocket = null
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                this@AppWebSocketManager.webSocket = null
            }
        })
    }

    fun disconnect() {
        webSocket?.close(1000, "Activity destroyed")
        webSocket = null
    }
}
