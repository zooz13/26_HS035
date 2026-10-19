# ESP32 Wi-Fi CSI UART Gateway 프로젝트 인수인계

## 1. 프로젝트 목표

ESP32가 출력하는 Wi-Fi CSI 데이터를 Gateway가 UART로 수신하고, rawlog는 S3에 보관하며 전처리된 realtime payload는 Model Server로 전송한다.

```text
ESP32
-> UART
-> Windows 노트북 / Raspberry Pi Gateway
   |- rawlog JSONL.gz -> S3
   `- Gateway 전처리 -> HTTP POST -> Model Server -> 추론/대시보드
```

- AWS 리전: `us-west-2`
- AWS IoT Core, MQTT, Basic Ingest는 사용하지 않는다.
- Gateway 코드는 Windows와 Raspberry Pi에서 동일하게 실행 가능해야 한다.

## 2. 역할 분리

### ESP32

- Wi-Fi CSI 수집
- `CSI_DATA` UART 출력
- 복잡한 전처리, feature extraction, inference는 수행하지 않음
- 실제 테스트 UART: `COM10`, `921600 baud`

### Gateway

- UART 수신 및 재연결
- CSI 파싱 및 검증
- raw I/Q를 amplitude `[192]`로 변환
- `packet_mean_normalized` 정규화
- baseline 기반 Stage-1 feature 계산
- Stage-2 `motion_input [128,192]` 생성
- 5초마다 Model Server로 HTTP 전송
- 60초 rawlog 생성 및 S3 업로드
- S3/realtime 실패 spool
- stdout JSON logging

### Model Server

- raw CSI를 직접 전처리하지 않음
- Gateway가 보낸 `features`와 `motion_input`으로 추론
- room/setup 상태 및 최근 추론 이력 관리
- 위험도 판단과 대시보드 API 제공

## 3. UART와 파서 계약

실제 ESP32 펌웨어 출력은 comma CSV 형식이며 마지막 CSI 배열이 따옴표로 감싸져 있다.

Canonical frame 필드:

```text
id, mac, rssi, rate, sig_mode, mcs, bandwidth, smoothing,
not_sounding, aggregation, stbc, fec_coding, sgi, noise_floor,
ampdu_cnt, channel, secondary_channel, local_timestamp, ant,
sig_len, rx_format, len, first_word_invalid, data
```

Gateway 공통 필드:

```text
schema_version, type, gateway_id, device_id, link_id, gateway_ts
```

검증 정책:

- `len`과 실제 `data` 길이가 다르면 reject
- CSI 값은 `-128..127`
- parse error로 프로그램을 종료하지 않음
- rawlog에는 정상 frame과 UART 원문을 함께 보존
- decode 실패 시 raw bytes hex 보존
- 최대 line 초과 시 앞 512 bytes만 보존하고 다음 newline까지 폐기

## 4. 전처리 계약

- CSI 길이: `384`
- I/Q 순서: `imag0, real0, imag1, real1, ...`
- amplitude: `sqrt(real^2 + imag^2)`
- packet amplitude shape: `[192]`
- 5초 window에서 최신 128 packet 사용
- 128 packet 미만이면 payload 생성/전송/spool 없음
- padding 사용 안 함
- NaN/inf는 `0.0`
- normalization: `packet_mean_normalized`
- Stage-2 input: `[128,192]`

Realtime payload 필수 구조:

```text
window_id
baseline_id
calibration_id
device_group_id
window_start_time
window_end_time
window_duration_sec
data_type=stage1_feature_vector
feature_version=stage1_feature_v1
features:
  static_score
  motion_score
  shape_score
  robust_max_shape_score
motion_input:
  input_type=window_matrix
  feature_version=stage2_feature_v1
  shape=[128,192]
  values
```

전송 endpoint:

```text
POST /api/rooms/{room_id}/window
```

## 5. Window 및 파일 정책

- 모든 timestamp와 S3 key는 UTC
- realtime window: UTC 절대 5초 경계
- rawlog window: UTC 절대 60초 경계
- 정상 frame은 realtime과 rawlog 양쪽에 포함
- parse error는 rawlog에만 포함
- parse-error-only window는 rawlog만 생성
- gzip level `6`, gzip `mtime=0`
- 파일 충돌 시 `_001`, `_002` suffix

Rawlog S3 구조:

```text
rawlog/gateway_id=.../device_id=.../link_id=.../
year=YYYY/month=MM/day=DD/hour=HH/HHMMSS.jsonl.gz
```

Rawlog는 `.jsonl.gz`와 `.metadata.json`을 함께 업로드한다. Metadata의 `size_bytes`와 `sha256`은 압축 파일 기준이다.

Preprocessed log는 최종 realtime payload만 `.json.gz`로 저장하며 sidecar metadata는 없다. S3 prefix는 `preprocessed/`이다.

## 6. S3 및 spool

S3 bucket:

```text
project6-52-oregon-csi-project
```

테스트 당시 `S3_UPLOAD_ENABLED=false`였으므로 실제 S3 업로드가 아니라 dry-run이었다.

Rawlog 업로드 순서:

1. `.jsonl.gz`
2. `.metadata.json`

부분 성공 상태는 manifest에 기록한다. 실패 파일은 `data/spool/pending/<spool_id>/`로 이동한다.

Realtime HTTP 실패는 `data/realtime_spool/`에 저장한다. baseline 없음, 128 packet 미만, feature 조건 미충족, dry-run은 spool 대상이 아니다.

## 7. Baseline과 setup

CLI 모드:

```text
room-create
baseline-collect
calibration-collect
realtime
baseline-finish
calibration-finish
transport-test-bootstrap
```

Baseline 정책:

- 사람이 없는 방에서 명시적으로 수집
- 기본 수집 시간 300초
- sliding window `128`, stride `64`
- 실제 사용 sliding window 개수가 `num_windows`
- 결과 파일: `data/baseline/baseline_state.json`

Setup 상태:

```text
data/setup/{room_id}.setup.json
```

setup/baseline이 없어도 Gateway는 종료하지 않고 UART, parsing, rawlog, 설정된 S3 업로드를 계속한다.

## 8. EC2 대시보드 baseline 버튼 연동

대시보드 버튼은 다음 API를 호출한다.

```text
POST /api/rooms/{room_id}/baseline/start
```

구현된 흐름:

```text
대시보드 버튼 클릭
-> Model Server가 baseline_command 저장
-> Gateway가 setup/status를 주기적으로 poll
-> 기존 UART 스트림에서 baseline 수집
-> realtime inference 일시 중단
-> rawlog/S3는 계속
-> baseline_state 생성
-> baseline/finish 전송
-> realtime 재개
```

중복 클릭은 `baseline_collection_in_progress`로 무시한다.

필요 환경변수:

```env
MODEL_SERVER_URL=http://34.209.101.170:8000
REALTIME_SEND_ENABLED=true
TRANSPORT_TEST_ENABLED=false
DASHBOARD_CONTROL_ENABLED=true
REMOTE_SETUP_POLL_SEC=2
BASELINE_COLLECTION_SEC=300
```

## 9. Model Server Git 상태

원본 저장소:

```text
https://github.com/team-ige-wae-andwae/model_server
```

작업 브랜치와 커밋:

```text
codex/dashboard-baseline-command
f59e840 Add dashboard baseline command flow
```

서버 변경 내용:

- baseline 명령 저장
- `setup/status`에 `baseline_command` 반환
- Gateway command status endpoint
- 중복 baseline 시작 방지
- command `pending/collecting/completed/failed` 상태
- API 계약 테스트 및 문서

처음에는 `main`에 병합하지 않고 작업 브랜치만 push했다. 이후 사용자가 직접 배포하기로 했고 실제 Gateway에서 `baseline_collection_in_progress`가 확인됐으므로 변경 서버가 배포된 것으로 보인다.

## 10. EC2 현황

```text
EC2: project6-52-oregon-csi-project-ec2
Security Group: project6-52-oregon-csi-project-sg
Model Server / Dashboard: http://34.209.101.170:8000
```

Health 응답 확인:

```json
{"status":"ok","server":"running"}
```

현재 8000 포트는 개발용 공개 HTTP다. 운영 전에 Security Group IP 제한, HTTPS, 인증이 필요하다.

## 11. 실제 연동 확인 결과

Transport test에서 확인한 항목:

- ESP32 CSI 수신
- 5초 payload 생성
- EC2 `/api/rooms/room_101/window` POST
- HTTP 200
- 대시보드 occupied/static/moving 결과 표시

Transport test의 Stage-1 score `3.0`은 고정 테스트 값이었다.

실제 baseline 버튼 테스트에서 다음 로그를 확인했다.

```text
realtime_window_skipped
reason=baseline_collection_in_progress
gateway_mode=baseline_collecting
```

따라서 대시보드 버튼 -> 서버 명령 -> Gateway baseline 수집 전환까지 정상 연결됐다.

300초 완료 시 기대 로그는 다음과 같다.

```text
dashboard_baseline_completed
```

이 완료 로그와 이후 실제 score 전송 재개 여부는 대화에서 최종 확인하지 못했다.

## 12. UART 오류 현황

측정 시점:

```text
valid_frames: 10,419
parse_errors: 452
오류율: 약 4.2%
유효 FPS: 69.6
baseline collected packets: 9,588
```

정상 frame이 약 96%라 기능 테스트는 가능하지만 실제 baseline 품질 기준으로 오류가 많은 편이다.

오류 형태:

- CSI literal 손상
- length mismatch
- int8 범위 초과
- CSV quote 손상

가능성이 높은 원인:

- `921600 baud`에 가까운 출력량
- USB-UART buffer overflow
- parse error WARNING 출력이 UART read를 지연시키는 피드백
- 정상 frame마다 로그를 출력하지 않고 오류만 출력하므로 화면상 더 심각해 보임

권장 개선:

- 다른 serial monitor 종료
- USB 허브 없이 직접 연결
- ESP32 출력률을 `30~40 FPS`로 제한
- parse error 로그 rate-limit
- 30 FPS도 5초에 약 150 packet이므로 128 packet 조건 충족

## 13. 로컬 상태 대시보드

정적 대시보드:

```text
dashboard/index.html
dashboard/status.json
```

실행:

```powershell
python -m http.server 8080 --directory dashboard
```

접속:

```text
http://localhost:8080
```

Status JSON 포함 항목:

- Gateway/UART 상태
- FPS와 parse error
- CSI 품질
- baseline 상태
- baseline 수집 경과 시간과 packet 수
- realtime/S3 상태
- 최근 파일

## 14. 테스트 결과

```text
Gateway: 112 passed
Model Server: 20 passed
git diff --check: passed
```

## 15. 위험도 기록 초기화 기능

현재 Model Server는 `room_result_history`에 방별 추론 결과를 보관한다. 위험도 API는 이벤트 시각 기준 최근 60초만 골라 사용한다.

위험도 endpoint:

```text
POST /api/rooms/{room_id}/risk-assessment
```

제안된 초기화 endpoint:

```text
POST /api/rooms/{room_id}/result-history/reset
```

초기화 대상:

- 해당 방의 `room_result_history`

유지 대상:

- room setup
- baseline/calibration
- 다른 방 기록
- Gateway 연결과 realtime inference
- 권장안에서는 latest result 유지

예상 응답:

```json
{
  "room_id":"room_101",
  "cleared_count":12,
  "reset_at":"2026-06-20T10:00:00Z",
  "status":"cleared"
}
```

이 기능은 아직 구현하지 않았다. 구현 전에 다음을 결정해야 한다.

- history 초기화 시 대시보드의 latest result도 함께 지울지
- 권장안처럼 history만 지우고 latest result는 유지할지

초기화 직후 위험도를 실행하면 기록 부족으로 `unknown/insufficient_input`이 나오는 것이 정상이다. 초기화 후 촬영 조건에 맞는 window를 충분히 쌓은 다음 재난 이벤트를 실행해야 한다.

## 16. 사용자 작업 규칙

향후 작업 시 반드시 준수한다.

- 필요한 데이터 구조나 의미를 임의로 결정하지 말고 질문한다.
- 파일 생성/수정 전에 변경 범위와 설계를 설명하고 승인을 받는다.
- 구현 후 변경 파일, 코드 구조, 주요 동작을 설명한다.
- 실제 credential, bucket, endpoint를 코드에 하드코딩하지 않는다.
- `.env.example`은 placeholder와 안전한 dry-run을 유지한다.
- 사용자의 기존 변경을 임의로 되돌리지 않는다.
- 테스트 실행 여부와 결과를 명확히 보고한다.

새 대화를 시작할 때 권장 지시:

```text
PROJECT_CONTEXT.md와 README.md를 먼저 읽고 이전 작업을 이어가.
현재 파일과 Git 상태를 확인하고, 수정 전 변경 범위와 설계를 보고해.
필요한 정보는 임의로 가정하지 말고 질문해.
```

## 17. 다음 우선순위

1. baseline 300초 완료 및 `dashboard_baseline_completed` 확인
2. `baseline_state.json` 생성 확인
3. transport test가 꺼진 실제 Stage-1 score 확인
4. UART parse error 비율 개선
5. 위험도 최근 기록 초기화 API 정책 확정
6. Model Server API와 대시보드 초기화 버튼 구현
7. 빈 방에서 실제 운영용 baseline 재수집
8. S3 실제 credential/bucket 설정 후 업로드 검증
