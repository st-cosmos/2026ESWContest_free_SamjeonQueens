package com.example.cameramessage

import android.Manifest
import android.app.AlertDialog
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Size
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.example.cameramessage.databinding.ActivityScanBenchBinding
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.korean.KoreanTextRecognizerOptions
import kotlinx.coroutines.launch
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException

/**
 * 스캔 정확도 측정 화면 (docs/scan-accuracy-experiment.md).
 *
 * 반입/반출 절차(무게 감지·세션) 없이 실제 인식 파이프라인(CameraX → ML Kit → ChemicalScanner →
 * 서버 매칭)만 돌려 병 하나의 결정(자동 확정·확인·LLM 확인·직접 선택·미결)을 서버 측정 로그에 남긴다.
 * 라벨 번호는 결정마다 자동으로 1 올라가므로 정답표 순서대로 '다음 병 스캔'만 누르면 된다.
 * 진입: 반입/반출 화면 상태 칩 길게 누르기 → "측정 화면 열기".
 */
class ScanBenchActivity : AppCompatActivity(), ChemicalScanner.Listener {

    private lateinit var binding: ActivityScanBenchBinding
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private val recognizer = TextRecognition.getClient(KoreanTextRecognizerOptions.Builder().build())
    private val barcodeScanner = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder()
            .setBarcodeFormats(
                Barcode.FORMAT_QR_CODE, Barcode.FORMAT_DATA_MATRIX,
                Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8,
                Barcode.FORMAT_CODE_128, Barcode.FORMAT_CODE_39,
                Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E
            )
            .build()
    )
    private lateinit var scanner: ChemicalScanner
    private val frameCache = ScanFrameCache()

    /** 스캔 세션이 열려 있는가 — '다음 병 스캔'과 결정 사이에만 true */
    private var scanning = false
    private var sessionStartedAt = 0L
    private val counts = mutableMapOf("auto" to 0, "confirm" to 0, "llm" to 0, "manual" to 0, "skip" to 0)

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startCamera()
            else Toast.makeText(this, "카메라 권한이 필요합니다.", Toast.LENGTH_SHORT).show()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityScanBenchBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ScanExperiment.load(this)
        ScanExperiment.setEnabled(this, true)   // 측정 화면에서는 항상 라벨 번호·프레임 전송
        binding.labelInput.setText(ScanExperiment.labelId ?: "1")

        scanner = ChemicalScanner(lifecycleScope, this, mode = "bench")
        scanner.frameImageProvider = { frameCache.bestOrLatest() }
        scanner.latestFrameProvider = { frameCache.latestBase64 }
        scanner.freeze()

        binding.btnLabelPrev.setOnClickListener { shiftLabel(-1) }
        binding.btnLabelNext.setOnClickListener { shiftLabel(+1) }
        binding.btnStart.setOnClickListener { startSession() }
        binding.btnManual.setOnClickListener { openManualSelect() }
        binding.btnSkip.setOnClickListener { finishSession(null, "skip", null) }
        updateButtons()

        if (hasCameraPermission()) startCamera()
        else requestPermissionLauncher.launch(Manifest.permission.CAMERA)
    }

    override fun onDestroy() {
        super.onDestroy()
        cameraExecutor.shutdown()
    }

    // ---------- 라벨 번호 ----------

    private fun currentLabel(): String = binding.labelInput.text.toString().trim()

    /** 앞의 숫자만 바꾼다 — "12-glare" → "13-glare" */
    private fun shiftLabel(delta: Int) {
        val cur = currentLabel()
        val m = Regex("^(\\d+)(.*)$").find(cur)
        val next = if (m != null) "${m.groupValues[1].toInt() + delta}${m.groupValues[2]}" else cur
        binding.labelInput.setText(next)
        ScanExperiment.setLabel(this, next)
    }

    // ---------- 세션 ----------

    private fun startSession() {
        ScanExperiment.setLabel(this, currentLabel())
        frameCache.clear()
        scanning = true
        sessionStartedAt = System.currentTimeMillis()
        binding.resultText.text = "라벨 #${currentLabel()} 스캔 중…"
        scanner.resume()
        updateButtons()
    }

    /** 결정 기록 → 결과 표시 → 라벨 번호 자동 증가. 다음 병은 버튼으로 시작한다. */
    private fun finishSession(name: String?, source: String, result: MatchResult?) {
        if (!scanning) return
        scanning = false
        scanner.freeze()
        ScanExperiment.event(
            scanner.sessionId, "decision", "bench",
            name = name, source = source, method = result?.method, confidence = result?.confidence
        )
        counts[source] = (counts[source] ?: 0) + 1
        val secs = (System.currentTimeMillis() - sessionStartedAt) / 1000.0
        val how = when (source) {
            "auto" -> "자동 확정"; "confirm" -> "확인"; "llm" -> "LLM 확인"
            "manual" -> "직접 선택"; else -> "미결"
        }
        binding.resultText.text = "라벨 #${currentLabel()} → ${name ?: "-"}  ($how, ${"%.1f".format(secs)}s)"
        binding.summaryText.text = "세션 ${counts.values.sum()} · 자동 ${counts["auto"]} · 확인 ${counts["confirm"]} · " +
                "LLM ${counts["llm"]} · 직접 ${counts["manual"]} · 미결 ${counts["skip"]}"
        shiftLabel(+1)
        updateButtons()
    }

    private fun updateButtons() {
        binding.btnStart.isEnabled = !scanning
        binding.btnManual.isEnabled = scanning
        binding.btnSkip.isEnabled = scanning
        if (!scanning) binding.scanStatusText.text = "대기"
    }

    // ---------- ChemicalScanner.Listener ----------

    override fun onScanStatus(phase: ChemicalScanner.Phase, chipText: String, guideText: String) {
        if (scanning) binding.scanStatusText.text = "#${currentLabel()} · $chipText"
    }

    override fun onMatched(result: MatchResult, ocrText: String, barcode: String?) {
        finishSession(result.chemical_name, "auto", result)
    }

    override fun onNeedsConfirmation(result: MatchResult, ocrText: String, barcode: String?) {
        val name = result.chemical_name ?: run { scanner.resume(); return }
        val percent = (result.confidence * 100).toInt()
        val message = if (result.method == "llm") "AI 분석 결과: $name" else "인식 결과: $name\n(일치율 ${percent}%)"
        AppModal.show(
            this, AppModal.Tone.PRIMARY, R.drawable.ic_flask_conical,
            "이 시약이 맞나요?", message, "아니요", "맞아요",
            onSecondary = {
                scanner.declineCandidate(name)     // decline 이벤트는 scanner 가 보낸다
                scanner.resume(800)
            },
            onPrimary = {
                // 운영 앱과 동일하게 확인 결과를 학습한다 (두 번째 스캔의 즉시 인식 효과 측정)
                lifecycleScope.launch {
                    try {
                        NetworkClient.api.confirmMatch(
                            MatchConfirmRequest(
                                name, barcode.takeIf { result.method != "barcode" },
                                result.matched_token, session_id = scanner.sessionId
                            )
                        )
                    } catch (e: Exception) {
                        // 학습 실패는 무시
                    }
                }
                finishSession(name, if (result.method == "llm") "llm" else "confirm", result)
            }
        )
    }

    private fun openManualSelect() {
        if (!scanning) return
        scanner.freeze()
        lifecycleScope.launch {
            val names = try { NetworkClient.api.getKnownNames() } catch (e: Exception) { emptyList() }
            if (names.isEmpty()) {
                Toast.makeText(this@ScanBenchActivity, "시약 목록을 불러오지 못했습니다.", Toast.LENGTH_SHORT).show()
                scanner.resume(500)
                return@launch
            }
            AlertDialog.Builder(this@ScanBenchActivity)
                .setTitle("직접 선택")
                .setItems(names.toTypedArray()) { _, which -> finishSession(names[which], "manual", null) }
                .setNegativeButton("취소") { _, _ -> scanner.resume() }
                .setOnCancelListener { scanner.resume() }
                .show()
        }
    }

    // ---------- 카메라 (CheckinActivity 와 동일 설정) ----------

    private fun hasCameraPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()
            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(binding.cameraPreview.surfaceProvider)
            }
            val analysis = ImageAnalysis.Builder()
                .setResolutionSelector(
                    ResolutionSelector.Builder()
                        .setResolutionStrategy(
                            ResolutionStrategy(Size(1920, 1080), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)
                        )
                        .build()
                )
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(cameraExecutor, ScanAnalyzer())
            try {
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(this, CameraSelector.DEFAULT_FRONT_CAMERA, preview, analysis)
            } catch (e: Exception) {
                Toast.makeText(this, "카메라 구동 실패: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private inner class ScanAnalyzer : ImageAnalysis.Analyzer {
        @ExperimentalGetImage
        override fun analyze(imageProxy: ImageProxy) {
            val mediaImage = imageProxy.image
            if (mediaImage == null || !scanning) {
                imageProxy.close()
                return
            }
            val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
            val textTask = recognizer.process(image)
            val barcodeTask = barcodeScanner.process(image)
            Tasks.whenAllComplete(textTask, barcodeTask).addOnCompleteListener {
                val text = if (textTask.isSuccessful) textTask.result?.text?.trim().orEmpty() else ""
                val barcode = if (barcodeTask.isSuccessful) {
                    barcodeTask.result?.firstOrNull { !it.rawValue.isNullOrBlank() }?.rawValue
                } else null
                var closesLater = false
                if (scanning) {
                    try {
                        cameraExecutor.execute {
                            frameCache.offer(imageProxy, text.length)
                            imageProxy.close()
                        }
                        closesLater = true
                    } catch (e: RejectedExecutionException) {
                        // 화면 이탈 직후 — 캡처 생략
                    }
                    scanner.onFrame(text, barcode)
                }
                if (!closesLater) imageProxy.close()
            }
        }
    }
}
