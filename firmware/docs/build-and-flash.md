# 스마트 선반 노드 — 빌드 / 업로드 가이드

대상 하드웨어: **E73-2G4M08S1C (Nordic nRF52840)** + **CS1237** 로드셀 ADC
소프트웨어 스택: **nRF Connect SDK v3.4.0 (Zephyr 4.4) + OpenThread + CoAP**

> 4장(최초 플래싱)은 2026-08-07 에 실제 하드웨어에서 끝까지 수행하며 검증한 절차입니다.
> 함정이 여러 개 있으니 순서를 바꾸지 마세요.

---

## 1. 부트로더 질문에 대한 답

> "USB C타입으로 업로드 할 수 있으면 좋겠고, 부트로더도 업로드 해야 할까?"

| 항목 | 사실 |
|---|---|
| nRF52840 의 공장 ROM | **USB 부트로더 없음.** ST/SAMD 계열과 달리 nRF52 는 ROM DFU 가 없습니다. |
| Ebyte E73 모듈 출하 상태 | 부트로더 없음. 다만 **공장 테스트 펌웨어가 들어 있는 개체가 있습니다** (실제로 그랬습니다 — 4.3 참고). |
| 결론 | **최초 1회는 SWD 로 구워야 합니다.** USB-C 만으로는 첫 write 가 불가능합니다. |

그래서 이 프로젝트는 최초 SWD 플래싱 때 MCUboot(USB CDC 시리얼 리커버리)를 애플리케이션과 함께 굽고, 그 이후부터 USB-C 로 업데이트하는 구성입니다 (`sysbuild.conf`, `sysbuild/mcuboot.conf`).

### 1.1 전원 모드와 REGOUT0

USB 5V 로 모듈에 전원을 넣으면 nRF52840 은 **고전압(VDDH) 모드**로 동작합니다. 이때 VDD 는 내부 레귤레이터 REG0 가 만드는데, **공장 기본값이 1.8V** 입니다.

1.8V 로는 안 됩니다:

* CS1237 이 2.7V 이상을 요구해서 로드셀 읽기가 아예 동작하지 않습니다.
* SWD 통신 마진이 거의 없어집니다 (4.2 참고).

그래서 `UICR.REGOUT0` 을 3.3V(값 `5`)로 바꿔야 합니다. **이건 플래시 영역이라 칩 이레이즈로 지워집니다. 반드시 이레이즈 이후에 쓰세요** — 순서가 반대면 조용히 날아갑니다.

| 주소 | 레지스터 | 값 |
|---|---|---|
| `0x10001304` | `UICR.REGOUT0` | `0x00000005` = 3.3V (0=1.8V … 4=3.0V, 5=3.3V) |

적용은 리셋이 아니라 **전원 재인가**로 됩니다.

### 1.2 USB-C 배선 요구사항

* `D+`, `D-` 가 모듈의 `D+`/`D-` 핀에 연결되어야 합니다.
* **`VBUS` 가 모듈의 `VBUS` 핀에 연결되어야 하고, 4.35 ~ 5.5V 여야 합니다.**

  nRF52840 의 USB 는 VDDH 가 아니라 **VBUS 핀 전용 레귤레이터**로 켜집니다. VBUS 가 4.35V 미만이면 `USBREGSTATUS.VBUSDETECT` 가 서지 않아 `usb_enable()` 이 기다리는 `USBDETECTED` 이벤트가 오지 않습니다. 그러면 D+ 풀업이 안 걸려서 **PC 가 장치 착탈 자체를 감지하지 못합니다** (장치 관리자에 실패 장치조차 안 뜸).

  > **현 보드의 미해결 이슈:** VBUS 에서 3.88V 가 측정됩니다(규격 미달). 계측 오차 가능성도 남아 있어 검증 중입니다. 10장 참고.

---

## 2. 개발 환경

이 저장소에는 툴체인이 없습니다. nRF Connect SDK 를 먼저 설치하세요 (VS Code 의 nRF Connect extension pack 또는 nRF Connect for Desktop 의 Toolchain Manager).

검증된 조합: **NCS v3.4.0** / 툴체인 해시 `dcbdc366a1` / west 1.5.0 / SEGGER J-Link V9.24a

### 2.1 `west --version` 이 Python 에러를 낼 때

```
Module use of python312.dll conflicts with this version of Python
```

`west.exe` 의 shebang 이 상대 경로 `#!python.exe` 라서 PATH 에서 먼저 찾은 Python 을 씁니다. 시스템 Python 이 앞에 있으면 `PYTHONPATH` 가 가리키는 NCS 3.12 용 확장 모듈을 엉뚱한 인터프리터가 로드하면서 터집니다.

PowerShell 프로필(`Documents\PowerShell\Microsoft.PowerShell_profile.ps1`)에 아래를 넣어 두었습니다. NCS 환경이 주입된 셸에서만 동작하고, 툴체인 해시는 `PYTHONPATH` 에서 추출하므로 업데이트에도 살아남습니다.

```powershell
if ($env:PYTHONPATH -match 'ncs\\toolchains\\([0-9a-f]+)') {
    $ncsToolchainBin = "C:\ncs\toolchains\$($Matches[1])\opt\bin"
    if (Test-Path $ncsToolchainBin) {
        $ncsToolchainScripts = Join-Path $ncsToolchainBin 'Scripts'
        $rest = ($env:PATH -split ';' | Where-Object {
            $_ -and $_ -ne $ncsToolchainBin -and $_ -ne $ncsToolchainScripts
        }) -join ';'
        $env:PATH = "$ncsToolchainBin;$ncsToolchainScripts;$rest"
    }
}
```

### 2.2 `nrfjprog` 은 없습니다

NCS 3.x 툴체인에 `nrfjprog` 이 포함되지 않습니다. 인터넷의 옛 문서에 나오는 `nrfjprog --program / --recover / --memwr` 는 전부 **`nrfutil device` 로 대체**되었습니다.

### 2.3 `nrfutil` 경로 함정

이 PC 에는 `nrfutil` 이 두 벌 있고, PATH 에 잡히는 쪽에는 서브커맨드가 하나도 없습니다.

| 위치 | 버전 | `device` 커맨드 |
|---|---|---|
| `%USERPROFILE%\.nrfutil\bin\nrfutil.exe` | 8.2.0 | ❌ (PATH 에 잡히는 건 이쪽) |
| `C:\ncs\toolchains\dcbdc366a1\nrfutil\bin\nrfutil.exe` | 8.1.1 | ✅ 2.19.0 설치됨 |

`nrfutil` 은 `NRFUTIL_HOME` 아래에 서브커맨드를 설치하므로, 실행 파일과 home 을 같이 지정해야 합니다.

```powershell
$env:NRFUTIL_HOME = "C:\ncs\toolchains\dcbdc366a1\nrfutil\home"
Set-Alias nrfutil "C:\ncs\toolchains\dcbdc366a1\nrfutil\bin\nrfutil.exe"
nrfutil device list
```

`nrfutil install device` 로 PATH 쪽에 새로 설치해도 되지만, 이미 설치된 걸 쓰는 게 빠릅니다.

> **`pkg` / `dfu` (nrf5sdk-tools) 는 반대쪽에 있습니다** (노트북, 2026-08-08). 툴체인 home 은 `locked` 파일로 잠겨 있어 커맨드 추가 설치가 안 되므로, `nrf5sdk-tools` 는 `%USERPROFILE%\.nrfutil` 쪽에 설치했습니다. PowerShell 프로필이 `NRFUTIL_HOME` 을 툴체인 경로로 잡아두기 때문에, `pkg`/`dfu` 를 쓸 때는 `$env:NRFUTIL_HOME = "$env:USERPROFILE\.nrfutil"` 로 바꿔야 찾습니다. `device` 는 툴체인 home, `pkg`/`dfu` 는 사용자 home — 헷갈리기 쉬움.

### 2.4 Windows 빌드는 짧은 경로에서

Zephyr 빌드는 중간 산출물 경로가 깊어서, 빌드 디렉터리가 긴 경로(예: `AppData\Local\Temp\...` 아래)에 있으면 260자 제한에 걸립니다. 증상이 특이한데, **오브젝트 파일을 방금 컴파일해 놓고 아카이브 단계에서 `No such file or directory`** 가 납니다. 빌드는 항상 `C:\Users\<user>\workspace\...` 수준의 짧은 경로에서 하세요.

PowerShell 에서 `west build ... -- -D<이미지>_EXTRA_CONF_FILE=boards/foo.conf` 처럼 `-D` 인자에 `.conf`/`.overlay` 가 들어가면 **PowerShell 이 확장자 앞에서 인자를 쪼개는** 경우가 있습니다. `-D` 인자 전체를 따옴표로 감싸면 됩니다.

---

## 3. 빌드

```powershell
cd C:\Users\seadmisk\workspace\smart-chemical-shelf\firmware
west build -b nrf52840dk/nrf52840 --sysbuild -p always .
```

**왜 `nrf52840dk` 보드인가?** E73 모듈용 보드 정의가 Zephyr 에 없습니다. DK 보드 파일의 SoC 부분만 쓰고 핀 배치는 전부 `boards/nrf52840dk_nrf52840.overlay` 가 덮어씁니다. 양산 단계에서는 별도 보드 정의를 만드는 게 깔끔합니다.

### 3.1 산출물 — `merged.hex` 는 생성되지 않습니다

이 구성에서는 통합 hex 가 나오지 않습니다. 이미지가 두 개입니다.
(애플리케이션 이미지 디렉터리 이름은 sysbuild 가 앱 폴더 이름을 그대로 쓰므로 `firmware` 입니다.)

| 파일 | 용도 |
|---|---|
| `build/mcuboot/zephyr/zephyr.hex` | 부트로더 (SWD) |
| `build/firmware/zephyr/zephyr.signed.hex` | 서명된 애플리케이션 (SWD) |
| `build/firmware/zephyr/zephyr.signed.bin` | USB DFU 로 올리는 파일 |
| `build/dfu_application.zip` | DFU 패키지 |

`west flash` 는 sysbuild 이미지를 알아서 순서대로 굽습니다. 수동으로 구울 때만 두 hex 를 직접 지정하면 됩니다.

### 3.2 MCUboot 에 오버레이를 얹는 법 — 디렉터리 아님, 파일

MCUboot 용 devicetree 오버레이는 **`sysbuild/mcuboot.overlay` 파일**이어야 합니다. `sysbuild/mcuboot/` **디렉터리**를 만들면 안 됩니다.

```
zephyr/share/sysbuild/cmake/modules/sysbuild_extensions.cmake:267
  if(EXISTS ${sysbuild_image_name_conf_dir})     # <app>/sysbuild/mcuboot
    set(${ZBUILD_APPLICATION}_APPLICATION_CONFIG_DIR ...)
```

디렉터리가 존재하면 sysbuild 가 그것을 MCUboot 의 **애플리케이션 설정 디렉터리 전체**로 지정합니다. 그러면 MCUboot 자신의 `boot/zephyr/prj.conf` 대신 거기서 `prj.conf` 를 찾고, 없으면 이렇게 실패합니다:

```
No prj.conf file(s) was found in the .../sysbuild/mcuboot folder(s)
```

Kconfig 조각은 `sysbuild/mcuboot.conf`, DTS 오버레이는 `sysbuild/mcuboot.overlay` — 둘 다 파일입니다.

### 3.3 MCUboot 콘솔은 꺼야 합니다

`sysbuild/mcuboot.conf` 에 `CONFIG_UART_CONSOLE=n` 이 들어 있습니다. 빼면 빌드가 이렇게 실패합니다:

```
serial_adapter.c:37: #error Zephyr UART console must be disabled if CDC ACM is enabled ...
```

부트로더에는 CDC ACM 인스턴스가 하나뿐이고 그건 mcumgr 가 씁니다. 같은 인스턴스에 콘솔을 올리면 로그가 리커버리 프로토콜에 섞이므로 MCUboot 가 빌드를 거부합니다. 로그는 애플리케이션에서 나옵니다.

---

## 4. 최초 플래싱 (SWD, 1회)

### 4.1 배선

J-Link 20핀 헤더 기준:

| 핀 | 신호 | 연결 |
|---|---|---|
| 1 | VTref | **모듈 VDD** ← 필수 |
| 4, 8, 10 | GND | GND (여러 개 연결할수록 안정적) |
| 7 | SWDIO | SWDIO |
| 9 | SWCLK | SWCLK |
| 15 | nRESET | 선택 (모듈에 리셋 핀이 없으면 생략) |

10핀 Cortex 커넥터면 1=VTref, 2=SWDIO, 3=GND, 4=SWCLK, 5=GND 입니다.

**VTref 를 빼먹으면 `Target voltage too low` 로 즉시 거부됩니다.** VTref 는 J-Link 가 전압을 *감지*하는 핀이지 *공급*하는 핀이 아닙니다. 보드 전원은 USB-C 로 따로 넣어야 합니다.

> **클론 프로브 주의:** 저가 클론 중에는 VTref 감지 회로가 없이 고정값(예: 3.338V)을 지어내 보고하는 개체가 있습니다. 타깃을 떼고 `JLink.exe` 를 실행했을 때 VTref 가 0V 가 아니면 그런 개체입니다. 이 경우 레벨 시프팅이 없어 항상 내부 3.3V 로 구동하므로, 1.8V 타깃에는 쓰면 안 됩니다.
>
> 검증에 쓴 프로브: HW V9.60, S/N 69658020, `J-Link_http://debugging.shop`. 클론이지만 VTref 를 정상적으로 읽습니다. **펌웨어 업데이트 프롬프트는 무조건 거절하세요** — 클론은 벽돌이 됩니다.

### 4.2 SWD 속도 — 기본 4000 kHz 로는 안 됩니다

VDD 가 1.8V 인 동안에는 마진이 없어 통신이 깨집니다. 증상은 같은 레지스터를 읽을 때마다 값이 달라지는 것입니다:

```
Found SW-DP with ID 0x00001477    ← 깨짐
Found SW-DP with ID 0x2BA01477    ← 정상 (nRF52840 정답값)
DPIDR: 0xFFA01477                 ← 깨짐
DAP: Could not power-up system power domain.
```

`Failed to power up DAP` 는 결과지 원인이 아닙니다. 실측 결과:

| VDD | 동작하는 속도 |
|---|---|
| 1.8V | **100 kHz** (그 이상은 불안정) |
| 3.3V | **1000 kHz** (4000 은 실패했습니다) |

### 4.3 공장 펌웨어가 슬립에 들어가는 문제

모듈에 공장 테스트 펌웨어가 들어 있으면, 부팅 몇 초 뒤 슬립에 들어가면서 **디버그 도메인 전원이 내려갑니다.** 그러면 속도를 아무리 낮춰도 붙지 않습니다.

```
J-Link> h
WARNING: CPU could not be halted
```
```
nrfutil: Setting the debug port SELECT register failed while powering up sys and debug regions
```

리셋 핀이 없으면 hold-reset 으로 잡을 수 없으므로, **전원 인가 직후의 짧은 창을 노려야 합니다.** USB-C 를 뽑은 상태에서 아래를 실행하고, 루프가 도는 중에 USB-C 를 꽂습니다.

```powershell
$ok = $false
for ($i = 1; $i -le 200 -and -not $ok; $i++) {
    nrfutil device recover --swd-clock-frequency 100 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $ok = $true; Write-Host "성공 (시도 $i)" -ForegroundColor Green }
}
if (-not $ok) { Write-Host "실패 - 다시 시도하세요" -ForegroundColor Red }
```

`recover` 는 CTRL-AP 를 통한 ERASEALL 이라 **CPU 를 halt 할 필요가 없습니다.** 성공하면 플래시가 비어 잠들 펌웨어 자체가 없어지므로, 이후 작업은 타이밍을 신경 쓰지 않아도 됩니다.

**다만 이 루프는 재현성이 매우 낮습니다.** 실측에서 1500회 이상 시도해 성공한 것은 초반 한 번뿐이었습니다. 4.4 의 외부 전원 부트스트랩이 훨씬 확실합니다.

#### 영구 해결책 — 리셋 핀 활성화

nRF52840 의 RST 핀(P0.18)은 **`UICR.PSELRESET` 이 프로그램되어야만** 하드웨어 리셋으로 동작합니다. UICR 이 지워진 상태에서는 J-Link 의 15번 핀도, 보드의 리셋 버튼(S1)도 아무 효과가 없습니다.

한 번이라도 접근에 성공하면 반드시 켜두세요.

```powershell
nrfutil device pinreset-enable
```

이게 켜져 있으면 J-Link 가 CPU 를 리셋에 붙잡아둔 채 접속할 수 있어서, 펌웨어가 슬립해도 언제든 들어갈 수 있습니다. **`recover` 로 UICR 을 지울 때마다 다시 켜야 합니다.**

#### 실제로 통하는 조합

두 보드 모두에서 **첫 시도에 성공**한 조합입니다. 다른 조합은 수백 번씩 실패했습니다.

1. **클론 J-Link 의 VTref 점퍼를 `2-3`(프로브가 3.3V 공급) 위치에 둡니다.** VDD 1.8V 에서는 마진이 없어 리셋으로 창을 열어도 그 안에서 통신이 깨집니다. 3.3V 를 받으면 SWD 가 4000 kHz 에서도 안정적입니다.
2. **리셋을 1~2초 간격으로 툭툭 눌렀다 뗍니다.** 계속 누르고 있으면 안 됩니다 — 리셋을 유지하면 SWD 가 응답하지 않습니다. 뗄 때마다 MCUboot 가 5초 창을 엽니다.
3. 그 상태로 `nrfutil device recover` 를 반복 실행합니다.

주의할 점:

* 점퍼가 `2-3` 이면 **프로브가 보드 전원을 대주고 있으므로 USB-C 를 뽑아도 칩이 리셋되지 않습니다.** 이 위치에서는 리셋 버튼이 유일한 수단입니다.
* 반대로 점퍼가 `1-2`(감지 전용)면 USB-C 를 뽑았다 꽂는 것이 진짜 전원 사이클이 되지만, VDD 가 1.8V 라 SWD 가 불안정합니다.
* **리셋 버튼이 없는 보드는 훨씬 어렵습니다.** 실측에서 리셋 버튼을 납땜하지 않은 보드는 전원 사이클 방식으로 수백 회 시도해도 들어가지 못했고, 버튼이 있는 보드는 첫 시도에 성공했습니다. 시제품에는 리셋 버튼을 꼭 실장하세요.

> **부팅이 빨라지면 창이 좁아집니다.** `CONFIG_NET_CONFIG_INIT_TIMEOUT=0` 을 적용하기 전에는 `main()` 까지 30초가 걸려 진입이 쉬웠습니다. 지금은 31 ms 만에 부팅을 마치고 슬립에 들어갑니다. MCUboot 의 5초 시리얼 리커버리 창이 사실상 유일한 진입점입니다.

### 4.4 전체 순서

```powershell
$env:NRFUTIL_HOME = "C:\ncs\toolchains\dcbdc366a1\nrfutil\home"
Set-Alias nrfutil "C:\ncs\toolchains\dcbdc366a1\nrfutil\bin\nrfutil.exe"
```

> **가장 확실한 방법은 외부 전원으로 부트스트랩하는 것입니다.** 아래 순서는 그 방식이며,
> 실측에서 유일하게 재현성 있게 성공한 경로입니다. 배경은 4.4.1 을 보세요.

**0) 외부 3.0~3.3V 를 J5 1번(VDD) / 6번(GND) 에 인가하고 보드 USB-C 는 분리합니다.**

**1) 복구** — 공장 펌웨어 제거 + AP-Protect 해제.

```powershell
nrfutil device recover
```

**2) REGOUT0 = 3.3V** — `recover` 가 UICR 을 지우므로 반드시 그 **다음**입니다.

```powershell
nrfutil device write --address 0x10001304 --value 0x00000005
```

`--direct` 없이 쓰면 NVMC 의 WEN/REN 설정을 알아서 해줍니다. J-Link Commander 로 손수 할 필요가 없습니다.

**3) 리셋 핀 활성화** — 이걸 빼먹으면 이후 내내 고생합니다 (4.3 참고).

```powershell
nrfutil device pinreset-enable
```

**4) 확인**

```powershell
nrfutil device read --address 0x10001304   # 00000005  REGOUT0 = 3.3V
nrfutil device read --address 0x10001200   # 00000012  PSELRESET[0] = P0.18
nrfutil device read --address 0x10001204   # 00000012  PSELRESET[1] = P0.18
```

**5) 플래싱** — 외부 전원이 물려 있는 지금이 가장 안정적입니다. `west flash` 대신 4.5 의 `nrfutil device program` 을 쓰세요.

**6) 전원 전환** — 외부 전원 제거 → USB-C 를 PC 에 연결 → **모듈 VDD 가 3.3V 인지 확인**합니다.

### 4.4.1 왜 외부 전원이 필요한가

USB 5V 로만 켜면 순환에 갇힙니다.

```
USB 5V → VDDH → 내부 REG0 → VDD = 1.8V (REGOUT0 기본값)
       → 1.8V 에서는 레벨 추종 없는 클론 프로브의 SWD 마진이 거의 없음
       → REGOUT0 를 3.3V 로 못 씀
       → VDD 는 계속 1.8V
```

VDD 에 직접 3.0~3.3V 를 넣으면 normal voltage 모드가 되어 **REG0 를 아예 쓰지 않습니다.** REGOUT0 값과 무관하게 전압이 안정되고, SWD 가 4000 kHz 에서 붙습니다. 한 번만 하면 되고, 이후에는 USB 전원만으로 VDD 3.3V 가 나옵니다.

**반드시 USB-C 를 분리하세요.** 둘 다 물리면 외부 전원과 내부 REG0 가 충돌합니다.

전원은 AA 2개(3.0V), CR2032(3.0V), 실험실 전원 어느 것이든 됩니다. nRF52840 VDD 정격은 1.7~3.6V 입니다.

### 4.4.2 J5 헤더 핀 배치

보드에 전용 SWD 헤더 J5(2x3)가 있습니다. 모듈 핀에 직접 물리는 것보다 훨씬 안정적입니다.

| J5 핀 | 신호 | 모듈 | J-Link |
|---|---|---|---|
| **1** | E73_3V3 (VDD) | — | **1번 VTref** (+ 외부 전원 +) |
| 2 | (미연결) | — | — |
| **3** | E73_SWC | U1/39 | **9번 SWCLK** |
| **4** | RESET | U1/26 | **15번 nRESET** |
| **5** | E73_SWD | U1/37 | **7번 SWDIO** |
| **6** | GND | — | **4/8/10번 GND** (+ 외부 전원 −) |

GND 는 2개 이상 연결하세요. 하나만 쓰면 리턴 경로가 부족해 DPIDR 이 불안정해집니다.

### 4.5 `west flash` 가 안 될 때 — nrfutil 로 직접

`nrfutil device program` 의 **기본 `chip_erase_mode` 는 `ERASE_ALL` 이고 이건 UICR 까지 지웁니다.** 반드시 옵션을 붙이세요.

```powershell
$opt = "chip_erase_mode=ERASE_RANGES_TOUCHED_BY_FIRMWARE,verify=VERIFY_READ"

nrfutil device program --firmware build\mcuboot\zephyr\zephyr.hex `
    --options $opt --swd-clock-frequency 1000

nrfutil device program --firmware build\firmware\zephyr\zephyr.signed.hex `
    --options "$opt,reset=RESET_SYSTEM" --swd-clock-frequency 1000
```

| 옵션 | 이유 |
|---|---|
| `chip_erase_mode=ERASE_RANGES_TOUCHED_BY_FIRMWARE` | 기본값 `ERASE_ALL` 은 UICR 을 지웁니다 |
| `reset=RESET_SYSTEM` | 기본값이 `RESET_NONE` 이라 안 붙이면 굽고도 안 뜁니다 |
| `verify=VERIFY_READ` | 기본값 `VERIFY_NONE`. 마진이 빠듯하므로 검증하는 게 낫습니다 |

### 4.6 J-Link Commander 를 직접 쓸 경우

`nrfutil` 이 프로브를 못 잡을 때만 쓰세요. UICR 쓰기는 `nrfutil device write` 쪽이 훨씬 안정적입니다.

```
& 'C:\Program Files\SEGGER\JLink_V924a\JLink.exe'
```
```
J-Link> connect
Device> nRF52840_xxAA
TIF>    S
Speed>  100

J-Link> h                        ← 반드시 halt. 안 하면 램 접근이 실패합니다
J-Link> exec DisableFlashDL      ← 플래시 로더 우회 (램 256KB 백업 생략)
J-Link> mem32 0x10001304,1
```

`h` 없이 플래시 영역에 접근하면 이렇게 실패합니다:

```
Failed to preserve target RAM @ 0x20000000-0x2003FFFF
```

J-Link 가 램에 플래시 로더를 올리기 전에 램 전체를 백업하는데, 100 kHz 에서는 시간이 오래 걸리는 데다 코어가 lockup 상태면 접근 자체가 안 됩니다. `exec DisableFlashDL` 로 로더를 끄면 이 단계가 통째로 없어집니다.

NVMC 는 플래시 쓰기 중(약 338 µs) 바쁘므로, 연속 쓰기 사이에 `NVMC.READY`(`0x4001E400`)가 `1` 인지 확인하세요. 무시하면 `Failed to write memory` 가 나고 AP 가 엉킵니다. 엉키면 전원 사이클이 가장 빠른 복구입니다.

---

## 5. 이후 업데이트 (USB-C 만으로)

**실측으로 검증된 절차입니다.** SWD 없이 USB-C 만으로 완료됩니다.

### 5.1 mcumgr 설치 (최초 1회)

```powershell
go install github.com/apache/mynewt-mcumgr-cli/mcumgr@latest
# -> %USERPROFILE%\go\bin\mcumgr.exe
```

### 5.2 업로드

MCUboot 는 부팅 후 **5초 동안** CDC ACM 포트에서 mcumgr 접속을 기다립니다 (`CONFIG_BOOT_SERIAL_WAIT_FOR_DFU_TIMEOUT`). 그 창 안에 업로드를 시작해야 합니다. 일단 시작되면 5초가 지나도 계속됩니다.

```powershell
$mc = "$env:USERPROFILE\go\bin\mcumgr.exe"
$cs = "dev=COM7,baud=115200,mtu=512"

# 리셋 (J-Link 가 없으면 보드의 S1 버튼을 누르세요)
nrfutil device reset --serial-number 69658020
Start-Sleep -Milliseconds 1300

& $mc --conntype serial --connstring $cs image upload build\firmware\zephyr\zephyr.signed.bin
& $mc --conntype serial --connstring $cs reset
```

* **connstring 형식은 `dev=COM7,...` 입니다.** `"COM7,baud=..."` 처럼 `dev=` 를 빼면 동작하지 않습니다.
* 포트 번호는 장치 관리자에서 확인하세요. MCUboot 는 `Smart Shelf Bootloader`, 애플리케이션은 `Smart Shelf Node` 로 열거됩니다.
* **업로드에 약 5분 걸립니다** (347 KiB, 약 1.1 KiB/s). 정상입니다.
* 시리얼 리커버리는 primary slot 에 직접 씁니다. `image test` / `image confirm` 이 필요 없습니다.

> `nrfutil device program` 으로는 안 됩니다 — 이 장치를 MCUboot DFU 타깃(`mcuBoot` trait)으로 인식하지 못합니다.

포트가 잡히는지는 이렇게 확인합니다.

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.DeviceID -match "VID_2FE3" }
# USB Composite Device    USB\VID_2FE3&PID_0100\E84427A7729B1E17
# USB 직렬 장치(COM7)      USB\VID_2FE3&PID_0100&MI_00\...
```

**VID 는 `0x2FE3`(Zephyr Project) 입니다. Nordic 의 `0x1915` 가 아닙니다.** `CONFIG_USB_DEVICE_VID` 를 설정하지 않으면 Zephyr 기본값이 쓰입니다. 이걸 몰라서 장치가 멀쩡히 열거되는데도 "안 잡힌다"고 한참 헤맸습니다.

애플리케이션은 부팅 후 **약 30초 뒤**에 USB 를 올립니다 (`CONFIG_NET_CONFIG_INIT_TIMEOUT` 기본 30초 — 10.2 참고). 포트가 바로 안 보여도 기다리세요.

> 대기 시간 5초가 짧으면 `sysbuild/mcuboot.conf` 의 타임아웃을 늘리거나, 버튼 GPIO 진입 방식(`CONFIG_BOOT_SERIAL_ENTRANCE_GPIO`)으로 바꾸면 됩니다.

애플리케이션이 정상 동작 중이면 같은 USB-C 포트가 **로그 콘솔 + Zephyr 셸**로 열립니다. `ot state`, `ot ipaddr`, `log` 등을 쓸 수 있습니다.

---

## 6. Thread 네트워크 참여

`prj.conf` 에 정적 크리덴셜이 들어 있습니다. **게이트웨이(보더 라우터)와 반드시 일치**해야 합니다.

```
CONFIG_OPENTHREAD_NETWORK_NAME="SmartShelf"
CONFIG_OPENTHREAD_CHANNEL=15
CONFIG_OPENTHREAD_PANID=43981          # 0xABCD
CONFIG_OPENTHREAD_XPANID="11:11:11:11:22:22:22:22"
CONFIG_OPENTHREAD_NETWORKKEY="00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff"
```

게이트웨이 값을 확인하려면 OTBR 에서:

```
ot-ctl dataset active -x
```

셸로 확인:

```
ot state          # child / router 가 나와야 정상. detached = 네트워크 탐색 중
ot ifconfig       # up 이어야 함
ot channel        # 15
ot panid          # 0xabcd
ot networkname    # SmartShelf
ot ipaddr         # mesh-local / link-local 주소
```

> **크리덴셜이 무시되면 `CONFIG_OPENTHREAD_MANUAL_START` 를 확인하세요.** NCS 기본값이 `y` 라서, 그 상태로는 애플리케이션이 `openthread_start()` 를 직접 부르지 않는 한 스택이 올라오지 않고 Kconfig 값도 커밋되지 않습니다. `prj.conf` 에서 `n` 으로 명시해 두었습니다 (10.3 참고).
>
> `.config` 에는 값이 정상으로 보이므로 빌드만 봐서는 알 수 없습니다. 반드시 장치에서 `ot` 셸로 실제 값을 읽어 확인하세요.

커미셔너로 붙이려면 위 블록을 지우고 `CONFIG_OPENTHREAD_JOINER=y` 를 쓰세요.

역할은 전력 모드에 따라 바뀝니다 (`../../docs/power-modes.md`): **IDLE 에서는 SED**(`CONFIG_OPENTHREAD_MTD_SED=y`, 데이터 폴 1 초)로 잠들고, 게이트웨이가 `PUT shelf/mode "active"` 를 밀어넣으면 `shelf_mode.c` 가 `otThreadSetLinkMode()` 로 **MED (수신 상시 ON)** 로 전환합니다. 예전의 "700 ms LED 폴링 때문에 SED 불가" 제약은 LED 를 게이트웨이 푸시로 바꾸면서 해소됐습니다. 잠든 노드로 가는 푸시는 부모(파이)가 큐에 뒀다가 다음 데이터 폴에 실어 배달합니다.

---

## 7. 게이트웨이가 구현해야 하는 CoAP 인터페이스

> 이 인터페이스의 구현(라즈베리파이 + RCP 동글 + Python 브리지)은 **[../../gateway](../../gateway/README.md)** 에 있습니다.
> 이 장은 노드 쪽 규격의 기준 문서로 유지합니다.

노드는 게이트웨이 주소를 설정받지 않습니다. 처음에는 **realm-local 멀티캐스트 `ff03::1`** 로 요청을 보내고, 폴/질의에 응답했거나 푸시를 보낸 상대의 유니캐스트 주소를 기억해 이후에는 그쪽으로만 보냅니다. 3회 연속 무응답이면 다시 멀티캐스트 탐색으로 돌아갑니다.

이 멀티캐스트는 ACTIVE(수신 상시 ON) 상태의 다른 선반 노드도 듣습니다. 이웃 노드가 게이트웨이로 오인되지 않도록 노드는 **자신이 제공하지 않는 요청에는 응답하지 않고**(4.04 를 보내지 않음), 탐색 중에는 **2.xx 응답만** 게이트웨이의 답으로 인정합니다. 따라서 게이트웨이는 7.2·7.3 에 반드시 2.05 로 답해야 합니다.

노드는 **:5683 소켓 하나로 클라이언트와 서버를 겸합니다** — 게이트웨이도 5683 에서 서버 리소스(7.1~7.3)를 제공하고, 노드로 푸시(7.4)를 보낼 수 있어야 합니다.

### 7.1 무게 리포트 (노드 → 게이트웨이)

```
POST coap://<gw>/shelf/weight        (NON-confirmable)
Content: {"id":"f4ce36a1b2c3d4e5","seq":12,"raw":842317,"mg":8423,"mv":4915,"md":"a"}
```

* `id` — 노드 하드웨어 ID (16자리 hex)
* `seq` — 리포트 일련번호
* `raw` — CS1237 원시 카운트 (부호 있는 24비트)
* `mg` — `SHELF_CAL_OFFSET` / `SHELF_CAL_COUNTS_PER_KG` 로 환산한 밀리그램
* `mv` — VDDH 전압 (SAADC VDDH/5 탭). 배터리 구동이면 배터리 전압, USB 연결 중이면 Q5 가 배터리를 분리하므로 VBUS-다이오드 값(~4.9 V). **4.4 V 초과 = USB 전원으로 해석**하면 됩니다. 음수는 측정 실패.
* `md` — 노드가 실제 적용 중인 전력 모드 (`"a"`=active / `"i"`=idle). 게이트웨이는 세션 상태와 어긋나면 7.4 의 모드 푸시로 교정합니다.

`SHELF_ADC_DELTA_THRESHOLD` 이상 변했을 때만 전송하고, 변화가 없어도 모드별 하트비트(`SHELF_HEARTBEAT_IDLE_S` 30 초 / `SHELF_HEARTBEAT_ACTIVE_S` 10 초)마다 한 번은 보냅니다. 측정 주기는 IDLE 2 초(레일 게이트, 40 Hz) / ACTIVE 0.2 초(레일 상시 ON, 640 Hz)입니다. 변화 보고에는 안정화 필터가 걸립니다 — 연속 측정이 `SHELF_SETTLE_THRESHOLD_G`(기본 10 g) 이내로 일치할 때만 보고하므로, 병을 내려놓는 도중의 과도값(0 → 300 g → 500 g)이 서버로 새지 않습니다.

### 7.2 LED 백업 폴 (노드 → 게이트웨이, ACTIVE 에서 5 초 주기)

```
GET coap://<gw>/shelf/led?id=f4ce36a1b2c3d4e5     (CON)
→ 2.05 Content, payload: "1"
```

응답 페이로드는 아래 형태를 모두 받아들입니다: `0` / `1`, `on` / `off`, `{"led":1}`, `{"led":true}`.

빠른 경로는 7.4 의 LED 푸시이고, 이 폴은 유실된 푸시의 복구와 게이트웨이 생존 확인을 겸합니다. ACTIVE 에서 게이트웨이 접촉이 `SHELF_ACTIVE_FAILSAFE_S`(60 초) 동안 없으면 노드는 스스로 IDLE 로 내려갑니다 (배터리 보호). IDLE 에서는 LED 를 끄고 폴도 하지 않습니다.

### 7.3 전력 모드 질의 (노드 → 게이트웨이, 부팅 시 1회)

```
GET coap://<gw>/shelf/mode?id=f4ce36a1b2c3d4e5    (CON)
→ 2.05 Content, payload: "active" 또는 "idle"
```

노드는 IDLE 로 부팅한 뒤 이 질의로 현재 세션 상태를 동기화합니다. 응답이 없으면 2/4/8…60 초 백오프로 재시도하고, 먼저 도착한 모드 푸시(7.4)가 있으면 그만둡니다.

### 7.4 게이트웨이 → 노드 푸시 (CON, piggyback 2.04)

```
PUT coap://<node>/shelf/mode   payload: "active" | "idle"
PUT coap://<node>/shelf/led    payload: "1" | "0"
```

노드는 2.04 Changed 로 ACK 합니다. IDLE(SED) 노드로 가는 푸시는 Thread 부모가 큐에 보관했다가 다음 데이터 폴(1 초)에 배달하므로, CoAP 재전송(2 초 × 수 회)이면 충분합니다. 모드 푸시는 멱등이고, IDLE 상태의 노드는 `led "1"` 푸시를 무시합니다 (아무도 안 보는 LED 를 켜 두지 않기 위해).

---

## 8. 로드셀 캘리브레이션

**캘리브레이션은 셸에서 하고 결과는 NVS 에 저장됩니다.** 재부팅과 펌웨어 업데이트를 넘어 유지되므로 값을 바꾸려고 다시 빌드할 필요가 없습니다. Kconfig 의 `SHELF_CAL_*` 는 저장된 값이 없을 때만 쓰이는 공장 기본값입니다.

USB-C 콘솔에서:

```
shelf show            # 현재 적용 중인 값
shelf raw             # 1회 측정 (raw 카운트 + mg)

# 1) 선반을 비우고
shelf tare            # 현재 값을 영점으로 저장

# 2) 무게를 아는 추를 올리고 (예: 1 kg = 1000 g)
shelf cal 1000        # counts_per_kg 계산 후 저장

shelf raw             # 검증
shelf reset           # 공장 기본값으로 되돌리기
```

부팅 시 적용된 값이 로그에 찍힙니다:

```
<inf> shelf_cal: calibration: offset=1140545 counts_per_kg=100000
```

저장 위치는 `storage_partition`(48 KB)의 NVS 이며 키는 `shelf/cal/offset`, `shelf/cal/cpkg` 입니다. `CONFIG_SETTINGS` / `CONFIG_SETTINGS_NVS` / `CONFIG_NVS` 는 OpenThread 가 데이터셋 저장용으로 이미 켜두기 때문에 추가 설정이 필요 없습니다.

> 셸 명령과 측정 스레드가 같은 레일과 비트뱅 버스를 쓰므로 뮤텍스로 직렬화됩니다 (`shelf_read_raw()`, `main.c`).

`CONFIG_SHELF_ADC_DELTA_THRESHOLD` 는 **비어 있는 상태에서 관찰되는 raw 노이즈의 peak-to-peak 보다 크게** 잡으세요. 그렇지 않으면 노드가 계속 전송합니다. 기본값 500 카운트는 출발점일 뿐입니다.

CS1237 은 `CS1237_SPEED_40 | CS1237_PGA_128 | CS1237_CH_A` (40 Hz, 게인 128) 로 설정합니다. 노이즈가 심하면 `src/main.c` 의 `loadcell.config` 를 `CS1237_SPEED_10` 으로 낮추고 `SHELF_ADC_AVG_SAMPLES` 를 늘리세요.

---

## 9. 핀 배치

`boards/nrf52840dk_nrf52840.overlay` 에 정의되어 있습니다.

| 기능 | 핀 | 비고 |
|---|---|---|
회로도(`pcb-smart-shelf`)로 전부 검증했습니다.

| 기능 | 핀 | 넷 / 회로 |
|---|---|---|
| LED | P0.06 (U1/14) | `PIN_LED` → R8(100R) → Q1(AO3400A, N채널 로우사이드) 게이트. R9(100k) 풀다운. LED 는 S1 버튼 내장. **`GPIO_ACTIVE_HIGH`** |
| CS1237 DRDY/DOUT | P0.08 (U1/16) | `HX_DOUT` → J3/4. 양방향 |
| CS1237 SCLK | P1.09 (U1/17) | `HX_SCK` → J3/3 |
| 로드셀 전원 스위치 | P0.12 (U1/20) | `HX_PWC` → R10(1k) → Q2(AO3401A, **P채널 하이사이드**) 게이트. R11(100k) 로 소스 풀업 |

**전원 스위치는 `GPIO_ACTIVE_LOW` 입니다.** Q2 는 P채널이라 게이트를 LOW 로 당겨야 켜집니다 (소스 = `E73_3V3`, 드레인 = `+3V3_C` → J3).

`GPIO_ACTIVE_HIGH` 로 두면 로직이 정확히 반전됩니다 — `cs1237_init()` 이 레일을 계속 켜두고 `cs1237_power_up()` 이 오히려 꺼버립니다. 증상은 로드셀 미연결 시 `-116` 타임아웃, 연결 시 벌크 커패시턴스로 겨우 도는 브라운아웃 상태의 **raw = 0** 입니다. 두 증상 모두 실제로 겪었습니다.

측정하지 않는 동안 DOUT/SCLK 는 `GPIO_DISCONNECTED` 로 두어 전원이 꺼진 CS1237 로 전류가 역주입되지 않게 합니다.

---

## 10. 상태

### 10.1 남은 항목

1. ~~게이트웨이(보더 라우터) 없음~~ → **[../../gateway](../../gateway/README.md)** 로 구현·이관. 라즈베리파이에서 ot-daemon 으로 같은 크리덴셜의 Thread 네트워크를 세우면 노드가 `child` 로 붙는 것까지 실측 확인됨(노드 2대, RSSI -63/-71). RCP 동글 플래싱(클론 동글 함정 포함)·ot-daemon 빌드·데이터셋 구성·CoAP↔웹서버 브리지는 전부 그쪽 README 에 있습니다.
2. **"P12" 의 실제 핀 번호** — P0.12 로 가정. 다르면 오버레이 한 줄 수정.
3. **CS1237 레지스터 프레임 (46 클럭 시퀀스)** — `src/cs1237.c` 상단 주석의 클럭 배분과 커맨드 값(read `0x56` / write `0x65`)을 데이터시트로 대조하세요. 데이터 읽기(27 클럭)만 쓰면 무관합니다.
4. **LED 극성 / 로드 스위치 극성**
5. `usb_enable()` deprecated 경고 — 레거시 USB device 스택. 동작에는 문제없지만 언젠가 `CONFIG_USB_DEVICE_STACK_NEXT` 로 이전 필요.
6. **TP4056 충전 전류 0.8A** — `R13 = 1.5kΩ` (PROG). USB 2.0 포트는 열거 전 100mA / 열거 후 500mA 만 허용하므로, 배터리를 연결한 채 PC 에 꽂으면 포트 전류 제한에 걸립니다. 실사용 전에 R13 을 2.4kΩ(500mA) 또는 3kΩ(400mA)로 올리는 것을 검토하세요.
7. **임계값 재검토 (낮출 여지)** — 2500 카운트는 조립 직후의 드리프트 기준입니다. 재캘리브레이션(counts_per_kg=240770) 기준으로 약 10.4 g. 기구가 하루 이상 안착된 뒤 빈 선반 raw 를 다시 5분 이상 관찰하면 더 낮출 수 있을지 판단 가능.
8. **하중 경로 기구 개선** — 3D 출력물 판의 stick-slip 이 드리프트의 주범 (10.5 캘리브레이션 항목 참고). 판이 로드셀 하중 버튼에만 접촉하도록 다른 부위에 확실한 틈을 만들고, 로드셀 체결부의 플라스틱 직접 접촉을 금속 와셔/플레이트로 대체하고, 예압 부위 출력물을 두껍게 (PLA 크리프 주의, PETG/ABS 고려).

### 10.2 해결됨 — VDD 가 3.0V 가 아니라 1.8V 로 나오던 문제

**증상:** `UICR.REGOUT0` 를 3.3V(값 5)로 써도, 3.0V(값 4)로 써도 VDD 가 1.8V(기본값)로 나왔습니다. USB 전원이든 배터리든 동일. VDD 1.8V 에서는 USB 가 열거되지 않아 보드가 자립하지 못했습니다.

**원인: REG0 의 고전압 DC/DC 가 켜지는데 이 보드에는 인덕터가 없습니다.**

```c
/* zephyr/soc/nordic/nrf52/soc.c:39 */
#if NRF_POWER_HAS_DCDCEN_VDDH && (defined(CONFIG_SOC_DCDC_NRF52X_HV) || \
	DT_NODE_HAS_STATUS_OKAY(DT_INST(0, nordic_nrf52x_regulator_hv)))
	nrf_power_dcdcen_vddh_set(NRF_POWER, true);
#endif
```

nRF52840 DK 보드 정의가 `reg0`(고전압 레귤레이터 노드)를 `status = "okay"` 로 켜둡니다. DK 는 DCCH 에 인덕터가 실장돼 있기 때문입니다. **이 보드는 모듈의 `DCH` 핀이 미연결이라 인덕터가 없습니다.** 인덕터 없이 DC/DC 를 켜면 REG0 가 레귤레이션을 못 하고, VDD 가 1.8V 기본값으로 주저앉으면서 `REGOUT0` 가 무시되는 것처럼 보입니다.

**해결:** 오버레이 두 곳(앱 + MCUboot)에서 노드를 비활성화합니다.

```dts
&reg0 {
	status = "disabled";
};
```

그러면 REG0 가 LDO 모드로 남고 `REGOUT0` 가 정상 적용됩니다.

#### 진단을 어렵게 만든 것들

* **빈 칩에서는 3.0V 가 나왔습니다.** 펌웨어가 없으니 DC/DC 도 켜지지 않아서입니다. "값을 쓰면 되는데 펌웨어를 구우면 안 된다"는 혼란스러운 대비가 여기서 나왔습니다.
* **클론 J-Link 의 VTref 점퍼가 3.3V 를 공급하고 있었습니다.** 3핀 헤더가 `1-2`(타깃에서 감지) / `2-3`(프로브 내부 3.3V 출력)를 선택하는데, `2-3` 위치에서는 프로브가 **VDD 레일에 전원을 대줍니다**. 그래서 프로브를 연결한 동안에는 모든 것이 정상 동작했고(USB·셸·로드셀·mcumgr DFU 전부), 떼면 죽었습니다. 이 사실을 모른 채 "REGOUT0 가 적용됐다"고 여러 번 오판했습니다.
* `REGOUT0` 는 **POR 에서만 래치**됩니다. 소프트 리셋(`RESET_SYSTEM`)이나 핀 리셋으로는 다시 읽지 않으므로, 값을 쓴 뒤에는 전원을 완전히 끊었다 넣어야 확인됩니다.

> **교훈:** 전원 관련 디버깅에서는 프로브가 타깃에 전원을 공급하고 있는지부터 확인하세요. 클론 J-Link 는 이 점퍼가 흔합니다.

### 10.2.1 해결됨 — USB 가 열거되지 않던 문제

증상은 "PC 가 장치를 전혀 감지하지 못함"이었고, 원인은 **세 개가 겹친 것 + 확인 방법의 오류 두 개**였습니다.

#### 실제 원인

1. **LFCLK 소스가 XTAL** — 이 보드에는 32.768 kHz 크리스탈이 없습니다(모듈 XL1/XL2 미연결, 회로도 확인). DK 보드 정의의 기본값을 그대로 상속받아 `LFCLKSTARTED` 를 영원히 기다리며 부팅 중에 멈췄습니다. → `prj.conf` / `sysbuild/mcuboot.conf` 에서 RC 로 변경.
2. **MCUboot 가 slot0(0xC000)에 링크됨** — `sysbuild/mcuboot.overlay` 가 MCUboot 자신의 `app.overlay` 를 대체하면서 `zephyr,code-partition = &boot_partition` 이 사라졌습니다. 앱을 구우면 부트로더를 덮어썼습니다. → 3.2 참고.
3. **boot_partition 이 48K 로 부족** — USB 시리얼 리커버리를 포함한 MCUboot 는 약 60K 입니다. → `partitions.dtsi` 로 80K 확대.

#### 확인 방법의 오류 (여기서 시간을 가장 많이 썼습니다)

* **오실로스코프 오독** — USB VBUS 를 3.88V 로 표시했습니다. 멀티미터로는 5V 였습니다. 이 잘못된 값 때문에 "VBUS 규격 미달"이라는 존재하지 않는 문제를 오래 쫓았습니다. **전원 전압은 멀티미터로 확인하세요.**
* **USB VID 를 잘못 알고 있었음** — Nordic 의 `0x1915` 로 필터링했지만 실제로는 Zephyr 기본값 `0x2FE3` 입니다. 장치가 정상 열거된 뒤에도 한동안 "안 잡힌다"고 판단했습니다.

#### 참고 — USB 배선은 처음부터 정상이었습니다

| 경로 | 연결 |
|---|---|
| VBUS | J6 A4/B4/A9/B9 → `VBUS` 넷 → **U1/27 (VBS)** 직결 |
| D+ | J6 A6/B6 → U4/3 → U4/4 → **U1/31 (D+)** |
| D− | J6 A7/B7 → U4/1 → U4/6 → **U1/29 (D−)** |
| CC | J6 A5/B5 → R1/R2 5.1kΩ → GND |

전원 구조 (VDDH 모드가 설계 의도임이 확인됨):

```
VBUS ──D1(SS34)──> +BAT_FIN ──> U1/23 (VDH)
       Q5(AO3401A): G=VBUS, S=+BAT_FIN, D=+BATT
       USB 연결 시 배터리 분리, 미연결 시 배터리가 +BAT_FIN 공급
U1/19 (VCC) = E73_3V3  ← VDDH 모드에서는 내부 REG0 의 출력
```

### 10.3 해결됨 — Thread 가 시작되지 않던 문제

**증상:** `ot state` 가 `disabled`, `ot ifconfig` 가 `down`, `ot dataset active` 는 `NotFound`. 그리고 `prj.conf` 의 크리덴셜이 전부 무시되고 OpenThread 내장 기본값이 쓰였습니다.

| 항목 | prj.conf | 장치 (수정 전) |
|---|---|---|
| channel | 15 | 11 |
| panid | 43981 (0xABCD) | 0xffff |
| network name | SmartShelf | OpenThread |
| extpanid | 11:11:11:11:22:22:22:22 | dead00beef00cafe |

**원인: `CONFIG_OPENTHREAD_MANUAL_START` 이 NCS 기본값으로 `y`** 였습니다. 이 모드에서는 애플리케이션이 `openthread_start()` 를 직접 호출해야 하는데, 이 앱은 `NET_EVENT_L4_CONNECTED` 만 기다리므로 스택이 영영 올라오지 않았습니다. Kconfig 크리덴셜도 L2 가 시작될 때 데이터셋으로 커밋되는 것이라 같이 무시됐습니다.

빌드 산출물(`.config`)에는 값이 정상적으로 들어 있었기 때문에, **Kconfig 만 봐서는 발견할 수 없었습니다.** 장치에서 `ot` 셸로 실제 값을 읽어보고 나서야 드러났습니다.

`prj.conf` 에 `CONFIG_OPENTHREAD_MANUAL_START=n` 을 명시해 해결했습니다.

### 10.4 해결됨 — 부팅이 30초 걸리던 문제

`main()` 의 첫 로그가 `00:00:30` 에 찍혔고, `usb_enable()` 이 거기 있어서 USB 콘솔도 30초 뒤에야 올라왔습니다.

원인은 `CONFIG_NET_CONFIG_INIT_TIMEOUT` 기본값 30초입니다. `NET_CONFIG_AUTO_INIT=y` + `NEED_IPV6=y` 라 `net_config_init()` 이 IPv6 주소를 기다리며 **부팅을 블로킹**했고, 보더 라우터가 없으니 매번 30초를 다 썼습니다.

`prj.conf` 에 `CONFIG_NET_CONFIG_INIT_TIMEOUT=0` 을 넣어 해결했습니다. `init.c:420` 에서 `timeout == 0` 이면 대기 루프를 아예 돌지 않습니다. 애플리케이션 스레드가 이미 `NET_EVENT_L4_CONNECTED` 를 직접 기다리므로 여기서 막을 이유가 없습니다.

수정 후 `main()` 은 `00:00:00.031` 에 시작하고 USB 는 약 1초 만에 열거됩니다.

### 10.5 해결됨 — 기타

* ~~실제 무게 캘리브레이션 미완료~~ → 2026-08-08 515 g 기준으로 수행. **최종 counts_per_kg = 240770** (NVS 저장, 검증 측정 515.08 g). **NVS 값이 mcumgr 펌웨어 업데이트를 넘어 유지되는 것도 실증됨.**
  * **주의 — 첫 캘리브레이션(counts_per_kg=144852)은 무효였습니다.** 3D 출력물 선반 판이 로드셀에 고르게 닿지 않아 **하중의 약 40% 가 구조물로 새는 상태**에서 잡힌 값이었음 (같은 515 g 이 span 74599 → 접촉 수리 후 123997 카운트). 기구를 손보면 스팬이 통째로 바뀌므로 **기구 변경 후에는 반드시 재캘리브레이션**할 것.
  * 하중 경로가 3D 출력물이라 **stick-slip 히스테리시스**가 있습니다: 만진 뒤에는 기준선이 분당 수 g 씩 걷고(빈 선반에서 3분에 32 g 까지 관찰), 판을 2~3회 지그시 눌러 "운동"시키면 ±4 g 수준으로 자리 잡음. 제하 직후에는 약 -13 g 언더슛 후 서서히 복귀. **운용 수칙: 선반을 만지면 → 판을 몇 번 눌러 자리 잡게 → `shelf tare`.** 근본 대책은 판이 로드셀 하중 버튼에만 닿게 틈을 확보하고 체결부에 금속 와셔를 넣는 것 (10.1 참고).
* ~~`SHELF_ADC_DELTA_THRESHOLD` 재조정~~ → 빈 선반에서 5분간 58회 측정: 연속 측정 간 차이는 50~300 카운트지만 **수 분에 걸친 저주파 드리프트가 2119 카운트 p-p** (조립/조작 직후, stddev 566). 데스크톱에서 본 80 p-p 는 연속 4회의 순간 노이즈만 본 것. 기본값 500 으로는 드리프트만으로 가짜 리포트가 나가므로 **`prj.conf` 에서 2500 카운트(≈17 g)로 상향**. 5분 하트비트가 비교 기준점을 재설정하므로 드리프트 누적으로 넘길 일은 거의 없음.
* ~~컴파일 검증 안 됨~~ → NCS v3.4.0 빌드 통과. 앱 313808 B / 450410 B (69.7%), MCUboot 60980 B / 80 KB (74.4%)
* ~~`net_mgmt` 핸들러 시그니처~~ → Zephyr 4.4 에서 이벤트 인자가 `uint32_t` → `uint64_t` 로 변경. 고치지 않으면 64비트 상수가 잘려 `NET_EVENT_L4_DISCONNECTED` 를 영영 수신하지 못합니다 (`src/main.c:61`)
* ~~USB-C 의 D+/D- 배선 미확인~~ → 회로도에서 정상 확인
* ~~모듈의 32.768 kHz 크리스탈 유무~~ → **없음**. RC 오실레이터로 설정 완료
* ~~`qspi_nor: JEDEC id [ff ff ff]` 에러~~ → DK 의 MX25R64 외장 플래시가 이 보드에는 없습니다. 오버레이에서 `&qspi` / `&mx25r64` 비활성화

### 10.3 해결된 항목

* ~~USB-C 의 D+/D- 가 모듈까지 배선되어 있는지~~ → 회로도에서 확인. J6 → U4(USBLC6-2SC6) → U1/29·31 로 정상 연결 (10.1 참고)
* ~~컴파일 검증 안 됨~~ → NCS v3.4.0 에서 빌드 통과 (FLASH 12.31%, RAM 12.70%)
* ~~`net_mgmt` 핸들러 시그니처~~ → Zephyr 4.4 에서 이벤트 인자가 `uint32_t` → `uint64_t` 로 변경. 고치지 않으면 64비트 상수가 잘려 `NET_EVENT_L4_DISCONNECTED` 를 영영 수신하지 못합니다 (`src/main.c:61`)
