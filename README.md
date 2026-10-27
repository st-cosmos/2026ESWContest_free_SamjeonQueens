# Chemical Memory — OCR 스캔 앱과 저전력 무게 측정 선반을 이용한 시약 관리 시스템

> **제24회 임베디드SW경진대회 · 자유공모 부문 · 팀 삼전퀸즈**
>
> - 개발완료보고서: `2026ESWContest_자유공모_삼전퀸즈_개발완료보고서.pdf`
> - 시연 동영상: https://youtu.be/jZAySR9MeEU
> - 소스코드: https://github.com/st-cosmos/2026ESWContest_free_SamjeonQueens (이 저장소)
>
> 올리고 내리는 행위가 곧 기록이 되는 선반 — 무게 감지 선반 모듈이 반입·반출·위치를 자동 기록하고, 앱의 OCR+바코드 스캔이 시약을 식별하며, LED가 위치를 안내합니다.

Chemical Memory 프로젝트입니다. E73-2G4M08S1C(nRF52840) 커스텀 보드 선반 노드가 **Thread 메시**로 게이트웨이(라즈베리파이)에 붙고, 게이트웨이가 웹 대시보드 서버와 통신하여 로드셀 무게 모니터링·배터리 잔량 보고·LED 제어를 수행합니다.

```
[선반 노드 xN: E73 + CS1237 로드셀] --Thread/CoAP--> [게이트웨이: 파이 + RCP 동글] --HTTP--> [web 서버] <-- 브라우저/안드로이드 앱
```

## 프로젝트 구성
- `android-app`: 안드로이드 애플리케이션 (PIN 로그인 = 선반 웨이크, OCR+바코드 스캔 반입·반출, 재고·선반 관리)
- `firmware`: 선반 노드 펌웨어 — E73-2G4M08S1C(nRF52840) + CS1237 로드셀, nRF Connect SDK(Zephyr) + OpenThread + CoAP. 이전 ESP8266(PlatformIO) 버전은 git 히스토리에 있습니다.
- `gateway`: Thread 게이트웨이 — 라즈베리파이 + nRF52840 RCP 동글(ot-daemon) + CoAP↔웹서버 브리지(`gateway.py`)
- `web`: 웹 대시보드 및 FastAPI 서버 소스 코드 (uv 패키지 관리 환경)
- `docs`: 컴포넌트를 가로지르는 설계 문서 (`power-modes.md` — 앱 세션 연동 전력 모드)
- `ui-design`: 웹·앱 화면 디자인 원본 (`design.pen`, pen.dev 파일) — 코드 주석의 `design.pen <프레임명>` 참조 대상

---

## 1. Web 대시보드 서버 실행

웹 서버는 REST API 제공 및 대시보드 프론트엔드 정적 파일 서빙을 동시에 담당합니다.
프로젝트는 [uv](https://docs.astral.sh/uv/)로 관리됩니다.

### 실행 방법
`web` 디렉토리로 이동한 뒤, 아래 명령어로 서버를 실행합니다.

```bash
# 1. web 폴더로 이동
cd web

# 2. 로컬 개발 서버 실행 (기본 localhost:8000)
uv run uvicorn main:app --reload

# 3. 외부 기기(게이트웨이·안드로이드 앱) 접속 허용을 위한 0.0.0.0 호스팅 실행 (중요)
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

> [!IMPORTANT]
> 게이트웨이(라즈베리파이)가 PC의 웹 서버에 접속하려면 반드시 `--host 0.0.0.0` 옵션을 지정하여 실행해야 하며, 게이트웨이와 PC가 **동일한 LAN**에 연결되어 있어야 합니다.
> Windows의 경우 방화벽에서 8000번 포트의 인바운드 허용이 필요할 수 있습니다.

실행 후 브라우저에서 `http://localhost:8000` 또는 `http://<PC의_IP_주소>:8000`으로 접속하여 실시간 대시보드를 확인할 수 있습니다.

---

## 2. Firmware (선반 노드, E73-2G4M08S1C) — [`firmware/`](firmware)

nRF Connect SDK v3.4.0 (Zephyr) + OpenThread + CoAP 기반이며, VS Code 의 nRF Connect 확장 또는 west CLI 로 빌드합니다.

```powershell
cd firmware
west build -b nrf52840dk/nrf52840 --sysbuild -p always .
```

- **최초 1회는 SWD 플래싱**이 필요합니다 (nRF52840 은 공장 USB 부트로더가 없음). `UICR.REGOUT0` 3.3V 설정 등 순서 함정이 많으니 반드시 [firmware/docs/build-and-flash.md](firmware/docs/build-and-flash.md) 를 따르세요.
- 이후 업데이트는 **USB-C 만으로** 가능합니다 (MCUboot 시리얼 리커버리 + mcumgr, 검증 완료).
- Wi-Fi 설정 같은 것은 없습니다. Thread 크리덴셜(`prj.conf`)이 게이트웨이의 네트워크와 일치하면 자동으로 붙습니다.
- 로드셀 영점/캘리브레이션은 USB-C 셸에서 `shelf tare` / `shelf cal <g>` 로 수행하고 NVS 에 영구 저장됩니다.
- 핀: LED P0.06 / CS1237 DOUT P0.08, SCLK P1.09 / 로드셀 전원 P0.12 / 배터리 전압 SAADC VDDH÷5 내부 탭

자세한 내용은 [firmware/README.md](firmware/README.md) 참고.

---

## 3. Gateway (Thread ↔ 웹 서버 브리지) — [`gateway/`](gateway)

라즈베리파이 + nRF52840 동글(RCP) 로 Thread 네트워크를 만들고, 노드의 CoAP 트래픽을 웹 서버 HTTP API 로 중개합니다.

1. 동글에 `ot-rcp` 펌웨어 굽기 → 파이에서 `ot-daemon` 실행 (`wpan0` 생성)
2. `ot-ctl` 로 Thread 네트워크 데이터셋 구성 (노드 크리덴셜과 일치, 최초 1회)
3. `gateway.py` 실행 — 무게 보고(g + 배터리 %)를 `POST /api/weight/{id}` 로 전달하고, 앱 로그인 상태(`GET /api/shelf-power`)에 따라 노드 전력 모드를 전환하며(`PUT shelf/mode` 푸시), LED 변화를 노드에 즉시 푸시합니다 (`PUT shelf/led`, 서버 하트비트 대행 겸용)

설치·검증 절차와 트러블슈팅은 [gateway/README.md](gateway/README.md) 참고.

---

## 4. Android Application (`android-app`)

CameraX 프레임을 ML Kit(한국어 OCR + 바코드)으로 읽어 시약병 라벨의 **원문 텍스트와 바코드를 그대로 서버로 보내고**, 매칭·판정은 전부 서버(`web/matching.py`)가 담당합니다. 앱에는 고정 키워드 리스트가 없어 새 시약도 코드 수정 없이 인식 대상에 들어옵니다.

### 인식 파이프라인
- **온디바이스 신호 추출**: CameraX ImageAnalysis 프레임마다 ML Kit KoreanTextRecognizer(OCR)와 BarcodeScanning(QR/DataMatrix/EAN/Code128 등)을 동시에 실행합니다. 기기에서는 이름을 판정하지 않고 신호만 모읍니다.
- **스캔 안정화** (`ChemicalScanner.kt`): OCR 텍스트를 5프레임 슬라이딩 윈도우로 누적하고, 바코드는 4초 TTL 유지, 서버 매칭 호출은 900ms 간격으로 제한합니다.
- **서버 매칭** (`POST /api/chemicals/match`): 바코드 > CAS 번호(체크섬 검증) > 별칭 정확일치 > 부분일치 > 퍼지 순으로 DB 등록 시약과 대조합니다. 확신도에 따라 자동 확정(≥0.9) / "이 시약이 맞나요?" 확인 모달(≥0.65) / 계속 스캔으로 나뉩니다.
- **학습 루프** (`POST /api/chemicals/match/confirm`): 사용자가 확인·수동 선택한 결과가 별칭/바코드 사전(`ChemicalAlias`, `BarcodeMap`)에 저장되어, 처음 보는 시약도 한 번 알려주면 다음부터 자동 인식됩니다.
- **반입/반출 확정**: 매칭 후 `scan-in`/`scan-out` 세션을 열고, 선반 로드셀의 무게 증감으로 실제 놓기/집기를 검증합니다.

### 연동 서버 설정
`NetworkClient.kt` 의 `BASE_URL`을 웹 서버 주소로 변경하여 빌드하십시오.
(예: 에뮬레이터 `http://10.0.2.2:8000/`, 실제 기기 `http://<PC-IP>:8000/`)

---

## 주요 기능 흐름

1. **전력 모드** ([docs/power-modes.md](docs/power-modes.md)): 앱 PIN 로그인 시 서버가 세션을 등록하고, 게이트웨이가 이를 감지해 모든 선반 노드를 깨웁니다(ACTIVE: 수신 상시 ON + 0.2초 측정). 로그아웃(또는 30분 TTL)하면 노드들은 IDLE 로 돌아가 슬립합니다(SED 1초 폴 + 60초 게이트 측정 + LED 소등) — 대기 평균 약 70 µA(10 Ω 션트 실측 기반), 1일 8회 사용 시 1회 충전으로 약 1년.
2. **LED 상태 동기화**: 웹 대시보드에서 LED 상태를 제어(`PUT /api/led/{id}`)하거나 안드로이드 앱에서 라벨 인식을 수행하면, 게이트웨이가 `GET /api/led/{id}` 폴링으로 변화를 감지해(서버 쪽 온라인 하트비트 겸용) ACTIVE 노드에 `PUT shelf/led` 로 즉시 푸시합니다 (노드에는 5초 백업 폴만 남음).
3. **무게 업로드**: 노드가 모드별 주기(IDLE 60초 게이트 측정 / ACTIVE 0.2초 상시 측정)로 CS1237 을 읽고, 직전 보고 대비 임계값 이상 변하고 연속 측정이 10g 이내로 안정됐을 때만 CoAP 로 게이트웨이에 보고합니다 (변화가 없어도 30초/10초 하트비트). 게이트웨이는 이를 `POST /api/weight/{id}` (그램 + 배터리 %) 로 전달하고, 웹 페이지는 무게 그래프·잔량 및 반입/반출 세션 판정에 사용합니다.
4. **배터리 잔량**: 노드가 보고마다 SAADC 내부 VDDH/5 탭으로 배터리 전압을 함께 보내고, 게이트웨이가 % 로 환산해 서버 `battery` 필드로 올립니다 (USB 전원 중에는 미보고로 마지막 값 유지).
