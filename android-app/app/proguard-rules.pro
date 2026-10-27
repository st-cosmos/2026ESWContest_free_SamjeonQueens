# release 빌드의 코드 축소/난독화 규칙. 현재 isMinifyEnabled=false 라 비어 있다.
# minify 를 켤 때는 Gson 으로 역직렬화하는 data class(User.kt / Shelf.kt / Chemical.kt)의
# 필드 이름이 서버 JSON 키와 일치해야 하므로 해당 클래스의 keep 규칙이 필요하다.
