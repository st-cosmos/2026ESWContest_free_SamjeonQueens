package com.example.cameramessage

import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.SystemClock
import android.util.Base64
import androidx.camera.core.ImageProxy
import java.io.ByteArrayOutputStream

/**
 * 스캔 프레임을 JPEG(base64)로 보관한다.
 * LLM 비전 매칭(/match/llm)과 용량 추정(/estimate-capacity)에 라벨 '사진'을
 * 첨부하기 위한 것 — ML Kit OCR 이 깨뜨린 글자를 서버의 비전 모델이 원본
 * 이미지에서 직접 읽을 수 있게 한다.
 *
 * 두 장을 관리한다:
 *  - best   : 이번 스캔에서 OCR 글자가 가장 많이 읽힌 프레임 (라벨이 제일 잘 보임)
 *  - latest : 가장 최근 프레임 (병 전체 형태 — 외형 기반 용량 추정용)
 */
class ScanFrameCache {

    companion object {
        private const val MIN_INTERVAL_MS = 1200L  // latest 갱신 주기 (변환 부하 제한)
        private const val MAX_DIM = 1280           // 전송 크기 절약용 축소 상한 (px)
        private const val JPEG_QUALITY = 72
    }

    @Volatile private var lastAt = 0L
    @Volatile private var bestTextLen = 0

    /** 가장 최근에 캡처된 프레임 (JPEG base64, 없으면 null) */
    @Volatile var latestBase64: String? = null
        private set

    /** 이번 스캔 세션에서 글자가 가장 많이 읽힌 프레임 */
    @Volatile var bestBase64: String? = null
        private set

    /** 서버 전송용 사진 묶음: best 우선 + latest (중복 제거) */
    fun images(): List<String> = listOfNotNull(bestBase64, latestBase64).distinct()

    /** 단일 사진이 필요한 곳(LLM 매칭)용 — 라벨이 제일 잘 보인 프레임 우선 */
    fun bestOrLatest(): String? = bestBase64 ?: latestBase64

    /**
     * 분석 스레드에서 imageProxy.close() 전에 호출한다.
     * 글자가 보인 프레임만 저장하며, best 갱신은 주기 제한 없이 즉시 반영한다.
     */
    fun offer(imageProxy: ImageProxy, textLen: Int) {
        if (textLen <= 0) return
        val now = SystemClock.elapsedRealtime()
        val isBest = textLen > bestTextLen
        val intervalOk = now - lastAt >= MIN_INTERVAL_MS
        if (!isBest && !intervalOk) return
        val encoded = encode(imageProxy) ?: return
        if (isBest) {
            bestTextLen = textLen
            bestBase64 = encoded
        }
        if (intervalOk) {
            lastAt = now
            latestBase64 = encoded
        }
    }

    /** 새 병 스캔 시작 시 호출 — 이전 병의 best 프레임이 섞이지 않게 한다. */
    fun clear() {
        latestBase64 = null
        bestBase64 = null
        bestTextLen = 0
    }

    private fun encode(imageProxy: ImageProxy): String? {
        return try {
            var bmp = imageProxy.toBitmap()
            val deg = imageProxy.imageInfo.rotationDegrees
            val scale = MAX_DIM.toFloat() / maxOf(bmp.width, bmp.height)
            val m = Matrix()
            if (scale < 1f) m.postScale(scale, scale)
            if (deg != 0) m.postRotate(deg.toFloat())
            if (!m.isIdentity) {
                bmp = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
            }
            val out = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
            Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        } catch (e: Exception) {
            null  // 캡처 실패는 무시 — 텍스트 매칭 경로는 그대로 동작한다
        }
    }
}
