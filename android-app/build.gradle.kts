// 프로젝트 전체(루트)에 적용되는 빌드 설정 파일입니다.
// 여기서는 사용할 Gradle 플러그인과 버전만 선언하고, 실제 적용(apply)은
// 각 모듈(app)에서 합니다.
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
