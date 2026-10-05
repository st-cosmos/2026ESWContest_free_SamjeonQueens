package com.example.cameramessage

import android.Manifest
import android.app.AlertDialog
import android.content.Context
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.util.Size
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.Animation
import android.view.animation.LinearInterpolator
import android.view.animation.RotateAnimation
import android.view.animation.TranslateAnimation
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.Camera
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
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.example.cameramessage.databinding.ActivityCheckoutBinding
import com.example.cameramessage.databinding.DialogSearchResultBinding
import com.example.cameramessage.databinding.ItemChemicalSearchBinding
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.korean.KoreanTextRecognizerOptions
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.temporal.ChronoUnit
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException

/**
 * app-checkout / -search (design-spec §3.9, §3.10)
 * 검색바(시약 검색 → 결과 모달: 미니맵 LED 셀 + select-led) +
 * CameraX·ML Kit OCR/바코드 → ChemicalScanner(서버 매칭) → scan-out.
 *
 * 인식 확신이 낮으면 확인 모달, 계속 실패하면 "인식이 잘 안됩니다" 안내와
 * 비치중 시약 목록에서 직접 선택하는 폴백을 제공한다.
 */
class CheckoutActivity : AppCompatActivity(), ChemicalScanner.Listener {

    private lateinit var binding: ActivityCheckoutBinding
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

    private var camera: Camera? = null
    private var torchOn = false
    private var isScanned = false
    private var currentUser: String = ""
    private var chemicalsList = listOf<ChemicalData>()
    private var searchJob: Job? = null
    private var checkoutPollJob: Job? = null
    private var pendingName: String? = null
    private var pendingChemicalId: String? = null
    private val frameCache = ScanFrameCache()  // LLM 비전 매칭용 라벨 사진
    private lateinit var exceptionHelper: ExceptionDialogHelper

    // 세션 완료를 폴링 루프와 WebSocket 트리거가 동시에 감지해 완료 모달이
    // 두 번 뜨는 것을 막기 위한 가드. 서버는 완료 결과를 다음 세션 시작
    // 전까지 계속 돌려주므로, 클라이언트에서 1회만 처리하도록 막는다.
    private var sessionCompleted = false

    private val requestPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startCamera()
            else Toast.makeText(this, "카메라 권한이 필요합니다.", Toast.LENGTH_SHORT).show()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityCheckoutBinding.inflate(layoutInflater)
        setContentView(binding.root)

        exceptionHelper = ExceptionDialogHelper(this)
        scanner = ChemicalScanner(lifecycleScope, this)
        scanner.frameImageProvider = { frameCache.bestOrLatest() }

        val prefs = getSharedPreferences("smart_shelf", Context.MODE_PRIVATE)
        currentUser = prefs.getString("username", "kim.lab") ?: "kim.lab"

        binding.appHeader.headerTitle.text = "시약 반출"
        binding.appHeader.btnBell.visibility = View.GONE
        binding.appHeader.btnBack.setOnClickListener { finish() }

        NavBar.setup(binding.bottomNav, this, NavBar.TAB_CHECKOUT)

        binding.cameraCard.clipToOutline = true
        binding.btnFlash.setOnClickListener {
            torchOn = !torchOn
            camera?.cameraControl?.enableTorch(torchOn)
        }
        binding.btnManualSelect.setOnClickListener { openManualSelect() }

        startScanLineAnimation()
        setupSearch()

        if (hasCameraPermission()) startCamera()
        else requestPermissionLauncher.launch(Manifest.permission.CAMERA)

        fetchChemicals()
    }

    override fun onResume() {
        super.onResume()
        exceptionHelper.startPollingAlerts()
        // 회수 대기 중 화면을 떠났다 돌아온 경우(세션 취소됨) 대기 상태 초기화
        if (isScanned && checkoutPollJob?.isActive != true &&
            binding.scanResultActive.visibility == View.VISIBLE
        ) {
            resetScanState()
        }
    }

    override fun onPause() {
        super.onPause()
        exceptionHelper.stopPollingAlerts()
        // 회수 대기 중 화면을 떠나면 서버 세션을 취소해 안내 LED 를 끈다
        if (checkoutPollJob?.isActive == true) {
            checkoutPollJob?.cancel()
            CoroutineScope(Dispatchers.IO).launch {
                runCatching { NetworkClient.api.cancelCheckoutSession() }
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        cameraExecutor.shutdown()
    }

    private fun hasCameraPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()

            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(binding.cameraPreview.surfaceProvider)
            }

            // 기본 해상도(640x480)로는 라벨의 작은 글자가 뭉개져 OCR 인식률이 낮다.
            // KEEP_ONLY_LATEST 라 고해상도여도 프레임이 밀리지 않는다.
            val analysis = ImageAnalysis.Builder()
                .setResolutionSelector(
                    ResolutionSelector.Builder()
                        .setResolutionStrategy(
                            ResolutionStrategy(
                                Size(1920, 1080),
                                ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                            )
                        )
                        .build()
                )
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(cameraExecutor, ScanAnalyzer())

            // 태블릿 거치 방향상 사용자 쪽(전면) 카메라로 라벨을 스캔한다
            val selector = CameraSelector.DEFAULT_FRONT_CAMERA
            try {
                cameraProvider.unbindAll()
                camera = cameraProvider.bindToLifecycle(this, selector, preview, analysis)
            } catch (e: Exception) {
                Toast.makeText(this, "카메라 구동 실패: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun startScanLineAnimation() {
        val range = dp(80).toFloat()
        val anim = TranslateAnimation(0f, 0f, -range, range).apply {
            duration = 1600
            repeatMode = Animation.REVERSE
            repeatCount = Animation.INFINITE
            interpolator = LinearInterpolator()
        }
        binding.scanLine.startAnimation(anim)
    }

    private fun startLoaderAnimation() {
        val rotate = RotateAnimation(
            0f, 360f,
            Animation.RELATIVE_TO_SELF, 0.5f,
            Animation.RELATIVE_TO_SELF, 0.5f
        ).apply {
            duration = 900
            repeatCount = Animation.INFINITE
            interpolator = LinearInterpolator()
        }
        binding.statusIcon.startAnimation(rotate)
    }

    private fun color(res: Int): Int = ContextCompat.getColor(this, res)

    // ---------- 검색 ----------

    private fun setupSearch() {
        binding.searchResultsRecyclerView.layoutManager = LinearLayoutManager(this)
        binding.searchResultsRecyclerView.adapter = SearchResultsAdapter { chem ->
            binding.searchEditText.setText("")
            binding.searchResultsCard.visibility = View.GONE
            selectChemicalForLed(chem)
        }

        binding.searchEditText.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                searchJob?.cancel()
                searchJob = lifecycleScope.launch {
                    delay(300)
                    val query = s?.toString()?.trim() ?: ""
                    if (query.length >= 1) {
                        val filtered = chemicalsList.filter {
                            it.current_status == "비치중" && (
                                    it.name.contains(query, ignoreCase = true) ||
                                            (it.formula?.contains(query, ignoreCase = true) ?: false)
                                    )
                        }
                        (binding.searchResultsRecyclerView.adapter as? SearchResultsAdapter)
                            ?.updateData(filtered)
                        binding.searchResultsCard.visibility =
                            if (filtered.isNotEmpty()) View.VISIBLE else View.GONE
                    } else {
                        binding.searchResultsCard.visibility = View.GONE
                    }
                }
            }

            override fun afterTextChanged(s: Editable?) {}
        })
    }

    private fun fetchChemicals() {
        lifecycleScope.launch {
            try {
                chemicalsList = NetworkClient.api.getChemicals()
            } catch (e: Exception) {
            }
        }
    }

    private fun selectChemicalForLed(chem: ChemicalData) {
        lifecycleScope.launch {
            try {
                val response = NetworkClient.api.selectLed(SelectLedRequest(chem_id = chem.id))
                if (response.status == "success") {
                    val configs = try {
                        NetworkClient.api.getShelfConfigs()
                    } catch (e: Exception) {
                        emptyList()
                    }
                    showSearchResultDialog(chem, response, configs)
                }
            } catch (e: Exception) {
                Toast.makeText(this@CheckoutActivity, "LED 안내 요청 실패", Toast.LENGTH_SHORT).show()
            }
        }
    }

    /** 검색 결과 모달 (§3.10): 시약 정보 + 선반 미니맵 LED 셀 표시 */
    private fun showSearchResultDialog(
        chem: ChemicalData,
        response: SelectLedResponse,
        configs: List<ShelfConfig>
    ) {
        val b = DialogSearchResultBinding.inflate(LayoutInflater.from(this))

        val formula = chem.formula?.let { " ($it)" } ?: ""
        b.searchChemName.text = "${chem.name}$formula"
        b.searchChemMeta.text =
            "잔량 ${chem.weight} kg · 유통기한 ${chem.expiration_date ?: "-"}"
        b.searchLocationText.text =
            "선반 ${response.parent_shelf} · ${response.row}행 ${response.col}열"

        // 미니맵 그리드 (해당 위치 셀 = primary + flask 아이콘)
        val config = configs.find { it.id == response.parent_shelf }
        val rows = config?.rows ?: 3
        val cols = config?.cols ?: 4
        b.minimapGrid.removeAllViews()
        for (r in 1..rows) {
            val rowLayout = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
            }
            for (c in 1..cols) {
                val isTarget = r == response.row && c == response.col
                val cell = FrameLayout(this).apply {
                    setBackgroundResource(R.drawable.shape_rounded_8)
                    backgroundTintList = ColorStateList.valueOf(
                        ContextCompat.getColor(
                            this@CheckoutActivity,
                            if (isTarget) R.color.primary else R.color.surface
                        )
                    )
                    if (isTarget) elevation = dp(3).toFloat()
                    layoutParams = LinearLayout.LayoutParams(dp(64), dp(36)).apply {
                        setMargins(dp(3), dp(3), dp(3), dp(3))
                    }
                }
                if (isTarget) {
                    val icon = ImageView(this).apply {
                        setImageResource(R.drawable.ic_flask_conical)
                        imageTintList = ColorStateList.valueOf(Color.WHITE)
                        layoutParams = FrameLayout.LayoutParams(dp(16), dp(16)).apply {
                            gravity = android.view.Gravity.CENTER
                        }
                    }
                    cell.addView(icon)
                }
                rowLayout.addView(cell)
            }
            b.minimapGrid.addView(rowLayout)
        }

        val dialog = AlertDialog.Builder(this)
            .setView(b.root)
            .setCancelable(true)
            .create()
        dialog.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))

        b.btnCloseSearch.setOnClickListener { dialog.dismiss() }
        b.btnConfirmSearch.setOnClickListener { dialog.dismiss() }
        dialog.show()
        dialog.window?.setLayout(dp(390), ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    // ---------- 반출 스캔 ----------

    /** OCR + 바코드를 한 프레임에서 함께 분석해 ChemicalScanner 로 전달 */
    private inner class ScanAnalyzer : ImageAnalysis.Analyzer {
        @ExperimentalGetImage
        override fun analyze(imageProxy: ImageProxy) {
            val mediaImage = imageProxy.image
            if (mediaImage == null || isScanned) {
                imageProxy.close()
                return
            }

            val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
            val textTask = recognizer.process(image)
            val barcodeTask = barcodeScanner.process(image)
            // 완료 콜백은 메인 스레드(기본값)에서 받는다. 콜백 자체를 cameraExecutor 에
            // 올리면 화면 이탈(onDestroy→shutdown) 후 완료되는 태스크가 종료된 풀에
            // 제출되며 RejectedExecutionException 으로 앱이 죽는다.
            Tasks.whenAllComplete(textTask, barcodeTask).addOnCompleteListener {
                val text = if (textTask.isSuccessful) textTask.result?.text?.trim().orEmpty() else ""
                val barcode = if (barcodeTask.isSuccessful) {
                    barcodeTask.result?.firstOrNull { !it.rawValue.isNullOrBlank() }?.rawValue
                } else null
                // 프레임 JPEG 변환(offer)만 카메라 스레드로 — UI를 막지 않는다.
                // executor 가 이미 종료됐으면 캡처만 건너뛴다.
                var closesLater = false
                if (!isScanned) {
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

    // ---------- ChemicalScanner.Listener ----------

    override fun onScanStatus(phase: ChemicalScanner.Phase, chipText: String, guideText: String) {
        binding.scanStatusText.text = chipText
        if (binding.scanResultEmpty.visibility == View.VISIBLE) {
            binding.scanResultEmpty.text = guideText
            binding.btnManualSelect.visibility =
                if (phase == ChemicalScanner.Phase.WEAK || phase == ChemicalScanner.Phase.FAILED)
                    View.VISIBLE else View.GONE
        }
    }

    override fun onMatched(result: MatchResult, ocrText: String, barcode: String?) {
        isScanned = true
        val name = result.chemical_name ?: run { resumeScanning(); return }
        requestScanOut(name, ocrText, barcode.takeIf { result.method != "barcode" }, learnToken = null)
    }

    override fun onNeedsConfirmation(result: MatchResult, ocrText: String, barcode: String?) {
        isScanned = true
        val name = result.chemical_name ?: run { resumeScanning(); return }
        val percent = (result.confidence * 100).toInt()
        val message = if (result.method == "llm") {
            "AI 분석 결과: $name\n맞다면 다음부터는 즉시 인식돼요."
        } else {
            "인식 결과: $name\n(일치율 ${percent}%)"
        }
        AppModal.show(
            this, AppModal.Tone.PRIMARY, R.drawable.ic_flask_conical,
            "이 시약이 맞나요?",
            message,
            "아니요", "맞아요",
            onSecondary = {
                scanner.declineCandidate(name)
                Toast.makeText(
                    this, "다시 비춰주세요. 계속 안 되면 '직접 선택'을 이용하세요.", Toast.LENGTH_SHORT
                ).show()
                resumeScanning(800)
            },
            onPrimary = {
                requestScanOut(
                    name, ocrText,
                    barcode.takeIf { result.method != "barcode" },
                    learnToken = result.matched_token
                )
            }
        )
    }

    // ---------- 반출 요청 / 학습 ----------

    private fun requestScanOut(
        name: String,
        ocrText: String,
        barcode: String?,
        learnToken: String?,
        chemicalId: String? = null
    ) {
        lifecycleScope.launch {
            try {
                val response = NetworkClient.api.scanOut(
                    ScanOutRequest(
                        ocr_text = ocrText, username = currentUser,
                        chemical_name = name, chemical_id = chemicalId
                    )
                )
                when {
                    // 반출 세션 시작 → 선반 무게 감소로 확정될 때까지 대기
                    response.status == "pending" -> {
                        pendingName = name
                        pendingChemicalId = chemicalId
                        learnMatch(name, barcode, learnToken)
                        showWaitingState(name)
                        startCheckoutPolling()
                    }
                    // 즉시 확정 응답 — 이미 선반에서 회수된 병의 사후 스캔 등
                    response.status == "success" && response.chemical != null -> {
                        showCheckoutResult(
                            response.chemical, response.weight_verified, response.measured_delta
                        )
                        learnMatch(name, barcode, learnToken)
                    }
                    else -> resumeScanning(1500)
                }
            } catch (e: Exception) {
                Toast.makeText(
                    this@CheckoutActivity,
                    httpErrorDetail(e) ?: "반출 기록 실패 — 잠시 후 다시 시도합니다.",
                    Toast.LENGTH_SHORT
                ).show()
                resumeScanning(2500)
            }
        }
    }

    // ---------- 반출 세션: 무게 감소 확정 대기 ----------

    /** 회수 대기 상태 — LED가 켜진 칸에서 시약을 들면 무게 감소로 병이 특정·검증된다 */
    private fun showWaitingState(name: String) {
        binding.scanResultEmpty.visibility = View.GONE
        binding.btnManualSelect.visibility = View.GONE
        binding.scanResultActive.visibility = View.VISIBLE
        binding.resultBadge.visibility = View.VISIBLE
        binding.resultBadge.text = "회수 대기"
        binding.scanStatusText.text = "회수 감지 중"

        binding.scannedChemicalName.text = name
        val prefs = getSharedPreferences("smart_shelf", Context.MODE_PRIVATE)
        binding.scannedOperatorInfo.text = "반출자 : ${prefs.getString("nickname", "연구원")}"
        binding.scannedCheckoutDetails.text = "LED가 켜진 칸에서 시약을 들어주세요"

        binding.statusCircle.backgroundTintList = ColorStateList.valueOf(color(R.color.warning_soft))
        binding.statusIcon.setImageResource(R.drawable.ic_loader)
        binding.statusIcon.imageTintList = ColorStateList.valueOf(color(R.color.warning))
        binding.statusTitle.text = "회수 대기 중"
        binding.statusTitle.setTextColor(color(R.color.warning))
        binding.statusSub.text = "무게 감소 감지 대기"
        startLoaderAnimation()
    }

    private fun startCheckoutPolling() {
        checkoutPollJob?.cancel()
        sessionCompleted = false
        AppWebSocketManager.connect(NetworkClient.BASE_URL)

        val wsListener: (String) -> Unit = { _ ->
            lifecycleScope.launch {
                checkCheckoutSessionOnce()
            }
        }
        AppWebSocketManager.addListener(wsListener)

        checkoutPollJob = lifecycleScope.launch {
            try {
                while (true) {
                    val finished = checkCheckoutSessionOnce()
                    if (finished) break
                    delay(1000)
                }
            } finally {
                AppWebSocketManager.removeListener(wsListener)
            }
        }
    }

    private suspend fun checkCheckoutSessionOnce(): Boolean {
        try {
            val session = NetworkClient.api.getCheckoutSession()
            // 진행 중 경고 (예: 다른 시약이 들림) — 서버가 1회만 전달
            session.event?.message?.let { msg ->
                Toast.makeText(this@CheckoutActivity, msg, Toast.LENGTH_LONG).show()
            }
            if (!session.active) {
                // 폴링 루프와 WS 트리거가 동시에 완료를 감지해도 한 번만 처리한다.
                if (sessionCompleted) return true
                sessionCompleted = true

                val result = session.result
                when {
                    result != null -> handleCheckoutComplete(result)
                    session.timeout -> showTimeoutModal()
                    else -> resetScanState()  // 다른 경로로 취소됨
                }
                return true
            } else {
                binding.statusSub.text = "남은 시간 ${session.time_left.toInt()}초"
            }
        } catch (e: Exception) {
            // 폴링 중 네트워크 오류는 무시
        }
        return false
    }

    private suspend fun handleCheckoutComplete(result: CheckoutResultData) {
        // 완료 팝업은 showCheckoutResult 안에서 무게 검증 결과에 맞춰 표시된다
        showCheckoutResult(result.chemical, result.weight_verified, result.measured_delta)
    }

    private fun showTimeoutModal() {
        binding.statusIcon.clearAnimation()
        AppModal.show(
            this, AppModal.Tone.WARNING, R.drawable.ic_clock_alert,
            "회수가 감지되지 않았습니다",
            "선반에서 ${pendingName ?: "시약"}을(를) 든 것이 감지되지 않았습니다.\n" +
                    "무게 확인 없이 반출로 기록할까요?",
            "다시 시도", "기록하기",
            onSecondary = { resetScanState() },
            onPrimary = { forceScanOut() }
        )
    }

    private fun forceScanOut() {
        lifecycleScope.launch {
            try {
                val response = NetworkClient.api.scanOutForce(
                    ScanOutForceRequest(
                        username = currentUser,
                        chemical_name = pendingName,
                        chemical_id = pendingChemicalId
                    )
                )
                val chem = response.chemical
                if (response.status == "success" && chem != null) {
                    showCheckoutResult(chem, null, null)
                } else {
                    resetScanState()
                }
            } catch (e: Exception) {
                Toast.makeText(
                    this@CheckoutActivity,
                    httpErrorDetail(e) ?: "반출 기록 실패",
                    Toast.LENGTH_SHORT
                ).show()
                resetScanState()
            }
        }
    }

    /** 확인·선택된 매핑을 서버에 학습 (실패해도 동작에는 지장 없음) */
    private fun learnMatch(name: String, barcode: String?, token: String?) {
        if (barcode == null && token == null) return
        lifecycleScope.launch {
            try {
                NetworkClient.api.confirmMatch(MatchConfirmRequest(name, barcode, token))
            } catch (e: Exception) {
                // 학습 실패는 무시
            }
        }
    }

    // ---------- 직접 선택 폴백 ----------

    private fun openManualSelect() {
        isScanned = true
        scanner.freeze()
        val stocked = chemicalsList.filter { it.current_status == "비치중" }
        if (stocked.isEmpty()) {
            Toast.makeText(this, "비치 중인 시약이 없습니다.", Toast.LENGTH_SHORT).show()
            fetchChemicals()
            resumeScanning(500)
            return
        }
        val labels = stocked.map { chem ->
            "${chem.name} · ${chem.shelf_row ?: "-"}행 ${chem.shelf_col ?: "-"}열"
        }
        AlertDialog.Builder(this)
            .setTitle("반출할 시약 직접 선택")
            .setItems(labels.toTypedArray()) { _, which ->
                requestScanOut(
                    stocked[which].name, scanner.aggregatedText, scanner.activeBarcode, null,
                    chemicalId = stocked[which].id
                )
            }
            .setNegativeButton("취소") { _, _ -> resumeScanning() }
            .setOnCancelListener { resumeScanning() }
            .show()
    }

    private fun resumeScanning(cooldownMs: Long = 0L) {
        isScanned = false
        frameCache.clear()  // 이전 병의 best 프레임이 새 스캔에 섞이지 않게
        scanner.resume(cooldownMs)
    }

    // ---------- 결과 카드 ----------

    private suspend fun showCheckoutResult(
        chemical: ChemicalData,
        weightVerified: Boolean?,
        measuredDelta: Double?
    ) {
        val parentShelf = try {
            NetworkClient.api.getShelves().find { it.id == chemical.shelf_id }?.parent_shelf
        } catch (e: Exception) {
            null
        }
        val shelfDesc =
            "선반 ${parentShelf ?: "?"} · ${chemical.shelf_row ?: 0}행 ${chemical.shelf_col ?: 0}열"

        binding.scanResultEmpty.visibility = View.GONE
        binding.btnManualSelect.visibility = View.GONE
        binding.scanResultActive.visibility = View.VISIBLE
        binding.resultBadge.visibility = View.VISIBLE
        binding.resultBadge.text = "서버 기록 완료"
        binding.scanStatusText.text = "스캔 중"

        val formula = chemical.formula?.let { " ($it)" } ?: ""
        binding.scannedChemicalName.text = "${chemical.name}$formula"

        val prefs = getSharedPreferences("smart_shelf", Context.MODE_PRIVATE)
        val nickname = prefs.getString("nickname", "연구원")
        val timeString = chemical.time_out?.takeIf { it.length >= 16 }?.substring(11, 16) ?: ""
        binding.scannedOperatorInfo.text = "반출자 : $nickname · 오늘 $timeString"
        binding.scannedCheckoutDetails.text = "보관 위치였던 ${shelfDesc}이 비워졌습니다"

        // 상태 컬럼: 무게 검증 결과 표시
        binding.statusIcon.clearAnimation()
        binding.statusCircle.backgroundTintList = ColorStateList.valueOf(
            color(if (weightVerified == false) R.color.warning_soft else R.color.success_soft)
        )
        binding.statusIcon.setImageResource(
            if (weightVerified == false) R.drawable.ic_triangle_alert else R.drawable.ic_check
        )
        binding.statusIcon.imageTintList = ColorStateList.valueOf(
            color(if (weightVerified == false) R.color.warning else R.color.success)
        )
        binding.statusTitle.text = "반출 기록 완료"
        binding.statusTitle.setTextColor(
            color(if (weightVerified == false) R.color.warning else R.color.success)
        )
        binding.statusSub.text = when (weightVerified) {
            true -> "무게 검증 완료 (${measuredDelta ?: "-"}kg 감소)"
            false -> "무게 불일치 주의"
            else -> "무게 확인 없이 기록"
        }

        // 완료 팝업 — 3초 내 [확인]이 없으면 자동으로 닫히며 초기화된다 (하단 카드는 유지).
        // 무게 불일치 경고만은 놓치면 안 되므로 자동 닫힘 없이 확인을 요구한다.
        // 유통기한 경고(경과·30일 이내 임박) 시약은 완료 대신 폐기 등록 안내 모달을 띄운다.
        val expiry = expiryInfoOf(chemical)
        when {
            weightVerified == false -> {
                AppModal.show(
                    this, AppModal.Tone.WARNING, R.drawable.ic_triangle_alert,
                    "반출 완료 — 무게 불일치 주의",
                    "[${chemical.name}] 반출이 기록되었습니다.\n" +
                            "다만 기록된 병 무게 ${chemical.weight}kg 대비 " +
                            "실제 감소량이 ${measuredDelta ?: "-"}kg입니다.\n" +
                            "맞는 병을 가져갔는지 확인해 주세요.",
                    null, "확인",
                    onPrimary = {
                        if (expiry != null) showExpiryDisposalModal(chemical, expiry)
                        else resetScanState()
                    }
                )
            }
            expiry != null -> showExpiryDisposalModal(chemical, expiry)
            else -> {
                val verifiedNote =
                    if (weightVerified == true) "무게 검증 완료 (${measuredDelta ?: "-"}kg 감소)"
                    else "(무게 확인 없이 기록)"
                AppModal.show(
                    this, AppModal.Tone.SUCCESS, R.drawable.ic_check,
                    "반출 완료",
                    "[${chemical.name}]이(가) 반출 처리되었습니다.\n$verifiedNote",
                    null, "확인",
                    autoDismissMs = 3000L,
                    onPrimary = { resetScanState() }
                )
            }
        }
    }

    // ---------- 유통기한 경고 · 폐기 등록 (design.pen app-exception-modals §2·2-1·2-2) ----------

    /** 경과(expired=true, days=경과일) 또는 30일 이내 임박(expired=false, days=남은일). null이면 정상 */
    private data class ExpiryInfo(val expired: Boolean, val days: Long)

    private fun expiryInfoOf(chemical: ChemicalData): ExpiryInfo? {
        val date = chemical.expiration_date
            ?.let { runCatching { LocalDate.parse(it) }.getOrNull() } ?: return null
        val today = LocalDate.now()
        return when {
            date.isBefore(today) -> ExpiryInfo(true, ChronoUnit.DAYS.between(date, today))
            // 서버 alerts 와 동일 기준: 30일 이내 임박
            ChronoUnit.DAYS.between(today, date) <= 30 ->
                ExpiryInfo(false, ChronoUnit.DAYS.between(today, date))
            else -> null
        }
    }

    private fun showExpiryDisposalModal(chemical: ChemicalData, expiry: ExpiryInfo) {
        val tone = if (expiry.expired) AppModal.Tone.DANGER else AppModal.Tone.WARNING
        val title = if (expiry.expired) "유통기한 경과 시약" else "유통기한 임박 시약"
        val message = if (expiry.expired) {
            "'${chemical.name}' 반출이 완료되었습니다.\n" +
                    "유통기한(${chemical.expiration_date})이 ${expiry.days}일 경과한 시약입니다.\n" +
                    "사용을 중단하고 폐기 등록을 권장합니다."
        } else {
            "'${chemical.name}' 반출이 완료되었습니다.\n" +
                    "유통기한(${chemical.expiration_date})까지 ${expiry.days}일 남은 시약입니다.\n" +
                    "사용 계획이 없다면 폐기 등록을 권장합니다."
        }
        AppModal.show(
            this, tone, R.drawable.ic_clock_alert, title, message,
            "그래도 반출", "폐기 등록",
            onSecondary = { resetScanState() },
            onPrimary = { disposeChemical(chemical) }
        )
    }

    private fun disposeChemical(chemical: ChemicalData) {
        lifecycleScope.launch {
            try {
                NetworkClient.api.disposeChemical(chemical.id, DisposeRequest(username = currentUser))
                AppModal.show(
                    this@CheckoutActivity, AppModal.Tone.SUCCESS, R.drawable.ic_trash_2,
                    "폐기 등록 완료",
                    "'${chemical.name}'이(가) 폐기 등록되었습니다.\n재고 목록에서 삭제되었습니다.",
                    null, "확인",
                    onPrimary = { resetScanState() }
                )
            } catch (e: Exception) {
                Toast.makeText(
                    this@CheckoutActivity,
                    httpErrorDetail(e) ?: "폐기 등록에 실패했습니다.",
                    Toast.LENGTH_SHORT
                ).show()
                resetScanState()
            }
        }
    }

    private fun resetScanState() {
        checkoutPollJob?.cancel()
        pendingName = null
        pendingChemicalId = null
        binding.statusIcon.clearAnimation()
        binding.scanResultActive.visibility = View.GONE
        binding.resultBadge.visibility = View.GONE
        binding.scanResultEmpty.visibility = View.VISIBLE
        binding.btnManualSelect.visibility = View.GONE
        fetchChemicals()
        resumeScanning()
    }

    private class SearchResultsAdapter(private val onClick: (ChemicalData) -> Unit) :
        RecyclerView.Adapter<SearchResultsAdapter.ViewHolder>() {

        private var items = listOf<ChemicalData>()

        fun updateData(newItems: List<ChemicalData>) {
            items = newItems
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
            val itemBinding = ItemChemicalSearchBinding.inflate(
                LayoutInflater.from(parent.context), parent, false
            )
            return ViewHolder(itemBinding)
        }

        override fun onBindViewHolder(holder: ViewHolder, position: Int) {
            val item = items[position]
            val formula = item.formula?.let { " ($it)" } ?: ""
            holder.b.chemSearchName.text = "${item.name}$formula"
            holder.b.chemSearchDetails.text =
                "잔량 ${item.weight} kg · 유통기한 ${item.expiration_date ?: "-"}"
            holder.b.chemSearchLocation.text =
                "${item.shelf_row ?: "-"}행 ${item.shelf_col ?: "-"}열"
            holder.b.root.setOnClickListener { onClick(item) }
        }

        override fun getItemCount(): Int = items.size

        class ViewHolder(val b: ItemChemicalSearchBinding) : RecyclerView.ViewHolder(b.root)
    }
}
