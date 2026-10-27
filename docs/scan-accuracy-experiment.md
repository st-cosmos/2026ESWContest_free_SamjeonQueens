# 스캔 정확도 측정 절차

시약 인식(OCR·바코드 → 서버 매칭)의 정확도를 수치로 내기 위한 측정 장치와 절차.
측정 데이터(로그·프레임)는 `web/data/` 에 쌓이며 git 에 올리지 않는다.

## 구성

| 구성 | 위치 | 역할 |
|---|---|---|
| 이벤트 로거 | `web/services/scan_log.py` | `/match`, `/match/llm`, `/match/confirm`, `/match/event` 를 세션(session_id) 단위로 `web/data/scan_logs/scan-YYYYMMDD.jsonl` 에 기록 |
| 프레임 보관 | 같은 모듈 | 앱이 실험 모드에서 보내는 라벨 프레임을 `web/data/scan_logs/frames/<session>/` 에 JPEG 로 저장 |
| 앱 측정 화면 | `ScanBenchActivity.kt` | 반입/반출 절차 없이 "다음 병 스캔" → 결정 → 자동으로 라벨 번호 +1. 결정(자동·확인·LLM·직접 선택·미결)만 기록한다. 진입: 반입/반출 화면 상태 칩 길게 누르기 → "측정 화면 열기" |
| 앱 실험 모드 | `ScanExperiment.kt` | 상태 칩을 길게 누르면 켜고 끔. 켜진 동안 라벨 번호가 모든 요청에 실리고 프레임이 보관된다. 측정 화면에서는 항상 켜진다 |
| 재고 등록 | `web/tools/register_inventory.py` | 앱 반입(선반 무게 감지) 없이 정답표의 이름·CAS 를 DB 에 등록. `--reset-learned` 로 학습된 별칭·바코드를 지워 시드 상태에서 다시 시작 |
| 집계 | `web/tools/scan_eval.py` | 로그 → 초회 성공률, 자동 확정 정확도·오확정률, 최종 정답률, 인식 소요 시간, 오제안 수 |
| 오프라인 재생 | `web/tools/scan_replay.py` | 보관한 OCR 텍스트 열을 현재 `matching.py` 에 다시 넣어 알고리즘 변경 전후를 같은 자료로 비교 |

세션은 앱이 병 하나를 스캔하기 시작해(`session_start`) 자동 확정·확인·LLM 확인·수동 선택(`decision`) 중 하나로
끝날 때까지다. 거절("아니요")도 `decision(source=decline)` 으로 남는다.

## 촬영 절차

1. 서버를 켠다 (`SCAN_LOG=1` 기본). 재고는 앱 반입 대신 스크립트로 넣는다.
   ```bash
   cd web
   uv run python tools/register_inventory.py --manifest <경로>/labels_manifest.csv --reset-learned
   ```
   등록 없이 "사전만 있는 상태"를 재고 싶으면 이 단계를 건너뛴다 (사전에 없는 시약은 no_match → LLM/직접 선택으로만 끝난다).
2. 앱 반입 화면에서 상태 칩을 길게 눌러 "측정 화면 열기". 라벨 번호를 정답표의 첫 번호에 맞춘다.
3. "다음 병 스캔"을 누르고 병을 비춘다. 결정(자동 확정 / 확인 모달 / 직접 선택 / 미결 처리)이 나면 결과가 표시되고 번호가 +1 된다. 정답표 순서대로 병을 바꿔 가며 반복한다. 확인 모달의 "맞아요"는 운영 앱과 똑같이 학습되므로, 같은 병을 한 번 더 찍으면 "학습 후" 조건이 된다.
4. 조건(거리·조명·병 방향·마모 라벨)을 바꿔 반복한다. 조건 구분은 라벨 번호 뒤에 붙인다 (예: `12-glare`). 집계는 앞의 숫자만 본다. v2 측정 전에는 `--reset-learned` 로 학습을 지워 출발점을 맞춘다.

## 집계

```bash
cd web
uv run python tools/scan_eval.py data/scan_logs/*.jsonl --manifest <경로>/labels_manifest.csv --csv result_v1.csv
```

알고리즘을 고친 뒤에는 재촬영 없이 재생으로 비교한다.

```bash
uv run python tools/scan_replay.py data/scan_logs/*.jsonl --manifest <경로>/labels_manifest.csv --inventory inventory.txt --learn --csv result_v2.csv
```

재생은 로그의 OCR 텍스트·바코드를 요청 순서대로 다시 매칭하고, 확인 모달은 "정답이면 맞아요, 아니면 아니요"로
답한 것으로 가정한다. LLM 응답은 재호출하지 않고 로그에 남은 결과를 그 시점에 그대로 쓴다.

## 지표 정의

- 초회 성공률: 서버의 첫 제안이 정답이고 거절 없이 자동 확정 또는 확인으로 끝난 세션 비율.
- 자동 확정 정확도 / 오확정률: 자동 확정(≥0.9)으로 끝난 세션 중 정답·오답 비율. 오확정은 사용자 확인 없이 틀린 이름이 기록되는 가장 치명적인 실패.
- 최종 정답률: 어떤 경로로든 최종 결정이 정답인 병 / 전체 병. 미결(결정 없이 끝남)은 실패로 센다. 수동 선택·LLM 사용 비율을 함께 본다.
- 인식 소요 시간: 세션 시작 → 최종 결정. 정답으로 끝난 세션만 평균·중앙값·p90.
- 정답 판정은 기본 느슨 모드("에탄올" ↔ "에탄올 95%" 는 정답). `--strict` 로 완전 일치만 인정.
