plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.example.cameramessage"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.example.cameramessage"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.recyclerview:recyclerview:1.3.2")

    // CameraX — 카메라 미리보기 + 프레임 분석
    // 1.4.x 부터 네이티브 라이브러리(libimage_processing_util_jni.so)가 16KB 페이지 정렬 —
    // 1.3.4 는 4KB 정렬이라 Android 16KB 호환성 경고의 유일한 실제 원인이었다.
    // 1.5.x 는 compileSdk 35, 1.6.x 는 compileSdk 36 이 필요하므로 1.4 계열의 마지막 버전을 쓴다.
    val cameraxVersion = "1.4.2"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")

    // ML Kit 문자 인식(OCR) — 한국어, 온디바이스(인터넷 불필요)
    implementation("com.google.mlkit:text-recognition-korean:16.0.1")

    // ML Kit 바코드/QR 인식 — 병에 이미 붙어 있는 코드를 기회적으로 활용
    implementation("com.google.mlkit:barcode-scanning:17.3.0")

    // 서버 통신 — 코루틴 + Retrofit(REST) + OkHttp(WebSocket, Retrofit 의 전이 의존성으로 포함)
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
}
