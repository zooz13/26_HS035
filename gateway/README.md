# ESP32 Wi-Fi CSI UART Gateway

Windows 노트북과 Raspberry Pi에서 실행할 수 있는 Python 기반 CSI
Gateway입니다. ESP32는 Wi-Fi CSI를 수집하여 UART로 `CSI_DATA`를
출력하고, Gateway는 이를 파싱하고 전처리하여 Model Server와 S3로
전달합니다.

## 저장소 범위

이 저장소에는 Gateway 코드만 포함합니다.

- UART 수신 및 연결 끊김 자동 복구
- ESP32 CSI 파싱 및 canonical record 변환
- Gateway에서 수행하는 CSI 전처리
- Model Server HTTP 전송
- rawlog 및 전처리 payload S3 업로드
- HTTP/S3 업로드 실패 시 로컬 spool 보관
- 정적 로컬 모니터링 대시보드
- 자동화 테스트

다음 항목은 이 저장소에 포함하지 않습니다.

- Model Server 및 학습된 모델 artifact
- ESP32 펌웨어
- 실제 CSI 수집 데이터
- AWS credential
- 로컬 `.env`

Model Server는 별도 저장소 또는 배포 파일을 통해 준비해야 합니다.

## 전체 구조

```text
ESP32 -> UART -> Gateway
                  |-> 5초 realtime window
                  |    -> I/Q [384]를 amplitude [192]로 변환
                  |    -> packet_mean_normalized
                  |    -> Stage-1 features
                  |    -> motion_input [128, 192]
                  |    -> HTTP Model Server
                  |
                  |-> S3
                       |-> 60초 rawlog JSONL.gz + metadata
                       |-> 전처리 완료 realtime payload JSON.gz
```

HTTP 추론과 S3 보관은 서로 독립적인 경로입니다. 한쪽에서 오류가
발생해도 다른 경로를 의도적으로 중단하지 않습니다.

S3는 실시간 추론 trigger가 아닙니다. 실시간 추론 요청은 다음 API로
전송합니다.

```text
POST {MODEL_SERVER_URL}/api/rooms/{ROOM_ID}/window
```

이 Gateway는 AWS IoT Core, MQTT, Basic Ingest, Lambda, EC2 worker를
사용하지 않습니다.

## 요구사항

- Python 3.11 이상
- UART로 연결된 ESP32 CSI 수신기
- Windows 또는 Raspberry Pi/Linux
- 선택 사항: 호환되는 Model Server
- 선택 사항: `us-west-2` 리전의 S3 bucket

## 빠른 시작

### Windows PowerShell

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### Linux / Raspberry Pi

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

`.env`에서 실제 serial port와 장치 식별자를 설정합니다. 기본
`.env.example`은 S3 업로드와 realtime HTTP 전송이 모두 비활성화된
안전한 dry-run 설정입니다.

설정 검사:

```bash
python -m app.main --check-config
```

Gateway 실행:

```bash
python -m app.main realtime
```

## CSI 전처리 계약

- 기존 25-field 탭 구분 형식과 ESP32-S3 펌웨어의 28-field CSV 형식을
  모두 지원합니다.
- CSV 형식의 `fft_gain`, `agc_gain`,
  `compensate_gain_x1000000`을 canonical frame과 rawlog에 보존합니다.
- CSV의 따옴표 처리된 `data` 필드를 CSI 정수 배열로 파싱합니다.
- raw `data`는 384개의 interleaved 값을 가져야 합니다.
- I/Q 순서는 `imag0, real0, imag1, real1, ...`입니다.
- amplitude는 `sqrt(real^2 + imag^2)`로 계산하며 shape은 `[192]`입니다.
- 5초 window에 128개보다 많은 packet이 있으면 최신 128개를 사용합니다.
- 유효 packet이 128개 미만이면 padding하지 않고 해당 window를
  건너뜁니다.
- Stage-2 `motion_input`의 shape은 항상 `[128, 192]`입니다.
- 기본 normalization은 `packet_mean_normalized`입니다.
- NaN과 무한대 값은 `0.0`으로 정리합니다.

Stage-1 feature를 계산하려면 유효한 `baseline_state.json`이 필요합니다.
setup 또는 baseline이 없거나 잘못되어도 UART 수신과 rawlog 저장은
계속됩니다. 128개 이상의 packet이 있으면 정규화된 `motion_input`까지
생성한 뒤 Stage-1 및 HTTP 전송만 건너뜁니다.

## 로컬 파일 구조

- `data/rawlogs/YYYY/MM/DD/HH/`: raw CSI 및 parse error JSONL.gz/metadata
- `data/preprocessed/YYYY/MM/DD/HH/`: 최종 realtime payload JSON.gz
- `data/realtime_dryrun/`: HTTP 비활성화 시 생성되는 전체 payload JSON
- `data/spool/`: 실패한 S3 업로드
- `data/realtime_spool/`: 실패한 Model Server HTTP payload
- `data/setup/{room_id}.setup.json`: 현재 baseline/calibration ID
- `data/setup/{room_id}/`: baseline 및 calibration setup payload
- `data/setup_spool/`: 재전송을 기다리는 setup finish payload

모든 window 경계, S3 partition, metadata timestamp, window ID는 UTC를
사용합니다.

## 환경 설정

`.env.example`을 `.env`로 복사한 뒤 실제 값을 설정합니다.

```powershell
Copy-Item .env.example .env
```

또는 Linux/Raspberry Pi에서:

```bash
cp .env.example .env
```

`.env`에는 실제 serial port, 장치/방 식별자, S3 bucket, Model Server
주소를 입력합니다. AWS 리전은 `us-west-2`로 고정됩니다.

`.env`는 Git에 커밋하면 안 됩니다. `.env.example`에는 placeholder만
남겨야 하며 다음 값을 실제 값으로 기록하지 않습니다.

- AWS access key
- AWS secret key
- AWS session token
- Model Server token
- 운영 S3 bucket 이름

Serial port 예시:

```env
# Windows
SERIAL_PORT=COM3

# Raspberry Pi/Linux
SERIAL_PORT=/dev/ttyUSB0
```

Gateway는 AWS credential을 사용하지 않습니다. S3 업로드를 활성화하면
Model Server의 `/api/s3/presign-upload`에서 매 객체마다 Presigned PUT
URL을 발급받고, 응답에 포함된 서명 헤더로 S3에 직접 PUT합니다.

### 로컬 dry-run

```env
S3_UPLOAD_ENABLED=false
REALTIME_SEND_ENABLED=false
DELETE_LOCAL_AFTER_UPLOAD=false
```

이 모드에서는 S3와 Model Server로 실제 전송하지 않고 로컬 산출물만
생성합니다.

### S3 업로드 활성화

```env
S3_BUCKET=your-csi-bucket
S3_UPLOAD_ENABLED=true
REALTIME_SEND_ENABLED=false
MODEL_SERVER_URL=http://127.0.0.1:8000
```

`S3_BUCKET`은 기존 rawlog metadata/spool 호환을 위해 유지되지만 Presign
요청에는 전달되지 않습니다. 실제 업로드 bucket과 region은 Model Server
설정이 결정합니다.

업로드 대상:

- `rawlog/.../*.jsonl.gz`
- `rawlog/.../*.metadata.json`
- `preprocessed/.../*.json.gz`

### Model Server 활성화

```env
MODEL_SERVER_URL=http://127.0.0.1:8000
REALTIME_SEND_ENABLED=true
```

## 연동 테스트 모드

빈 방 baseline이 준비되기 전에 ESP32, Gateway, Model Server 간 데이터
전송을 검증하기 위한 staging 전용 모드입니다.

- UART CSI와 `motion_input [128, 192]`는 실제 데이터입니다.
- packet mean normalization도 실제로 수행합니다.
- Stage-1 점수만 `TRANSPORT_TEST_STAGE1_SCORE` 고정값을 사용합니다.
- `baseline/finish` 시 서버의 global threshold를 사용하는 기본
  calibration profile을 생성합니다.
- 로컬 `baseline_state.json`은 사용하지 않습니다.

기본 고정 점수 `3.0`은 제공된 Model Server의 기본 threshold보다 높아
Stage-2 경로까지 확인할 수 있습니다.

이 모드의 `occupied` 결과는 실제 Stage-1 분석 결과가 아닙니다. 운영
점유 판단에 사용하면 안 됩니다.

별도로 준비한 Model Server를 실행합니다. 일반적인 Windows 실행 예:

```powershell
& .\.venv\Scripts\python.exe scripts\run_model_server.py
```

기본 주소:

```text
http://127.0.0.1:8000
```

API 문서:

```text
http://127.0.0.1:8000/docs
```

Gateway `.env`:

```env
MODEL_SERVER_URL=http://127.0.0.1:8000
REALTIME_SEND_ENABLED=true
TRANSPORT_TEST_ENABLED=true
TRANSPORT_TEST_STAGE1_SCORE=3.0
```

Model Server가 실행된 상태에서 테스트 room을 준비합니다.

```powershell
python -m app.main transport-test-bootstrap
```

ESP32를 연결하고 Gateway를 실행합니다.

```powershell
python -m app.main realtime
```

UTC 기준 5초 window에 유효 packet이 128개 이상이면 다음 주소로
payload를 전송합니다.

```text
POST http://127.0.0.1:8000/api/rooms/{ROOM_ID}/window
```

Gateway 전송 성공 로그:

```text
event="realtime_payload_sent"
status_code=200
```

Model Server 처리 성공 로그:

```text
room_window_processed
```

128 packet 미만인 window는 `realtime_window_skipped`로 기록하고
전송하지 않습니다.

실제 baseline/calibration을 사용할 때는 테스트 모드를 비활성화합니다.

```env
TRANSPORT_TEST_ENABLED=false
```

## Room 설정

baseline과 calibration은 사람이 없는 상태에서 명시적으로 실행해야
합니다. realtime 실행 중 자동으로 setup 모드로 전환하지 않습니다.

Room 생성:

```bash
python -m app.main room-create
```

기본 300초 빈 방 baseline 수집:

```bash
python -m app.main baseline-collect
```

빠른 staging 수집:

```bash
python -m app.main baseline-collect --duration-sec 60
```

baseline 생성 기준:

```text
normalization = packet_mean_normalized
window_size = 128
stride = 64
num_windows = count_windows(valid_packet_count, 128, 64)
```

현재 Model Server는 `baseline/finish` 성공 시 global threshold를 사용하는
기본 calibration profile을 생성합니다. 응답으로 받은 `calibration_id`를
다음 파일에 저장하고 room은 바로 `ready` 상태가 됩니다.

```text
data/setup/{room_id}.setup.json
```

Threshold 재보정은 선택 사항입니다. 기본 calibration을 교체하려면 빈
방 calibration window를 수집합니다.

```bash
python -m app.main calibration-collect
```

기본값은 12개 window입니다.

```bash
python -m app.main calibration-collect --expected-windows 20
```

각 calibration window는 UTC 5초 경계를 사용하며 유효 packet이 128개
이상이어야 합니다.

## Baseline 동작

Gateway는 `BASELINE_RELOAD_SEC` 주기로 `BASELINE_STATE_JSON`을
확인합니다. setup이 준비된 뒤 파일 mtime이 바뀌면 다시 로드합니다.

setup 또는 baseline이 없거나 잘못된 경우:

- UART 수신 계속
- CSI parsing 계속
- rawlog 저장 계속
- 활성화된 경우 rawlog S3 업로드 계속
- Stage-1 feature 생성 중단
- realtime payload/HTTP 전송 중단
- realtime spool 생성 안 함

`AMPLITUDE_NPY_ENABLED=false`가 기본값입니다. 활성화해도 명시적인
baseline/calibration session에서만 디버깅용 `.npy`를 저장하며 realtime
중에는 연속 저장하지 않습니다.

프로그램 시작 시 secret을 제외한 `config_summary` JSON log를
출력합니다.

## Spool 동작

- rawlog S3 실패: data와 metadata를 S3 spool에 보존
- preprocessed S3 실패: 단일 JSON.gz 파일을 S3 spool에 보존
- realtime HTTP 실패: `REALTIME_SEND_ENABLED=true`인 경우에만 realtime
  spool 생성
- baseline 없음, 짧은 window, feature 생성 실패, dry-run: realtime
  spool을 생성하지 않음
- setup start/window API 실패: fail-fast 처리하고 realtime spool에 넣지
  않음
- setup finish API 실패: payload를 `data/setup_spool/pending/`에 보존

저장된 finish payload 재전송:

```bash
python -m app.setup_cli baseline-finish --payload PATH_TO_PAYLOAD_JSON
python -m app.setup_cli calibration-finish --payload PATH_TO_PAYLOAD_JSON
```

pending setup payload 전체 재전송:

```bash
python -m app.setup_cli retry-pending
```

## 실행

UART, S3, Model Server에 연결하지 않고 설정을 검사합니다.

```bash
python -m app.main --check-config
```

baseline 파일이 없어도 rawlog-only 실행이 가능하므로 config 자체가
유효하면 종료 코드 `0`을 반환합니다.

Gateway 실행:

```bash
python -m app.main
```

명시적 실행:

```bash
python -m app.main realtime
```

setup이 없으면 rawlog-only 모드로 계속 실행하며 setup 파일이 생성되거나
수정되는지 주기적으로 확인합니다.

## 로컬 대시보드

대시보드는 raw CSI 배열, motion matrix, UART 원문, credential을 포함하지
않는 작은 status snapshot만 읽습니다. 내장 로컬 HTTP 서버를 활성화하면
같은 화면에서 RX UART를 거쳐 TX의 패킷 전송률과 송신 전력 상한을 변경할
수 있습니다.

기본 설정:

```env
DASHBOARD_STATUS_ENABLED=true
DASHBOARD_STATUS_PATH=./dashboard/status.json
DASHBOARD_UPDATE_SEC=1
DASHBOARD_HTTP_ENABLED=true
DASHBOARD_HTTP_HOST=127.0.0.1
DASHBOARD_HTTP_PORT=8080
DASHBOARD_DIR=./dashboard
```

Gateway를 실행하면 내장 대시보드 서버도 함께 시작됩니다.

```bash
python -m app.main realtime
```

브라우저 접속:

```text
http://localhost:8080
```

TX 펌웨어 제어는 다음 순서로 동작합니다.

```text
Dashboard POST -> Gateway -> RX UART -> RX ESP-NOW -> TX
TX applied ACK -> RX ESP-NOW -> RX UART -> Gateway status.json -> Dashboard
```

- 기본값은 `40 Hz`, `20 dBm`입니다.
- 전송률 범위는 `1..200 Hz`, 전력 상한 범위는 `2..20 dBm`입니다.
- 전력은 ESP-IDF 단위에 맞춰 `0.25 dBm` 간격만 허용합니다.
- `Apply & save`와 `Reset defaults`는 TX NVS에 값을 저장합니다.
- `Read current`는 TX가 실제 적용 중인 값을 ACK로 확인합니다.
- 한 번에 한 명령만 전송하며 3초 안에 ACK가 없으면 timeout으로 표시합니다.

`200 Hz`는 TX 설정의 허용 상한이지 UART까지 무손실을 보장하는 값이
아닙니다. CSI 행 길이와 `921600` baud의 직렬화 한계 때문에 높은 설정은
RX 또는 Gateway 구간에서 손실될 수 있으므로 기본 `40 Hz`에서 시작하고
대시보드의 실제 FPS와 parse error를 함께 확인하십시오.

UI의 `Ready`는 새 RX 제어 프로토콜이 감지됐다는 뜻이고, `Applied Rate`와
`Applied Power`는 TX ACK에서 받은 실제 값입니다. 단순히 UART 쓰기에
성공한 것만으로 적용 성공으로 처리하지 않습니다.

`DASHBOARD_HTTP_HOST=127.0.0.1`을 유지하면 현재 노트북에서만 접속할 수
있습니다. 인증 없이 `0.0.0.0`으로 바꾸면 같은 네트워크의 다른 사용자가
TX 설정을 변경할 수 있으므로 운영 환경에서는 사용하지 마십시오.
대시보드 status 저장 또는 HTTP 서버 오류는 UART 수집 데이터 자체의
형식을 변경하지 않습니다.

## 테스트

```bash
python -m pytest
```

## GitHub 업로드

업로드할 파일:

```text
app/
dashboard/
tests/
.env.example
.gitignore
pytest.ini
README.md
requirements.txt
```

로컬에만 유지할 파일:

```text
.env
data/
model_server-main/
dashboard/status.json
outputs/
work/
.venv/
__pycache__/
.pytest_cache/
```

첫 commit 전에 staged file을 확인합니다.

```bash
git add .
git status
```

`git status`에 다음 항목이 보이면 commit하면 안 됩니다.

- `.env`
- `data/`
- `model_server-main/`
- AWS credential
- 실제 CSI capture
- spool payload

## EC2 대시보드 Baseline 버튼 연동

개발용 Model Server가 baseline 명령을 `setup/status`에 보관하면 실행 중인
Gateway가 이를 주기적으로 확인할 수 있습니다. 다음 값을 `.env`에
설정하고 Gateway를 `realtime` 모드로 실행합니다.

```env
MODEL_SERVER_URL=http://<EC2_PUBLIC_IP>:8000
REALTIME_SEND_ENABLED=true
TRANSPORT_TEST_ENABLED=false
DASHBOARD_CONTROL_ENABLED=true
REMOTE_SETUP_POLL_SEC=2
BASELINE_COLLECTION_SEC=300
```

```bash
python -m app.main realtime
```

대시보드에서 baseline 수집을 시작하면 서버 명령의 `duration_sec`을 우선
사용하고, 값이 없을 때 `BASELINE_COLLECTION_SEC`을 사용합니다. 수집 중에는
UART 수신, 파싱, rawlog 생성 및 설정된 S3 업로드는 계속되지만 realtime
feature 생성과 `/api/rooms/{room_id}/window` 전송은 일시 중단됩니다. 같은
명령을 다시 눌러도 중복 수집은 시작하지 않습니다.

수집이 끝나면 Gateway가 `baseline_state.json`을 만들고 baseline finish를
서버로 전송합니다. 로컬 status JSON의 `gateway.mode`와
`baseline.collection_in_progress`에서 진행 상태를 확인할 수 있습니다.

> 경고: 현재 이 제어 경로는 인증 없는 개발용 HTTP를 전제로 합니다.
> 장시간 공개하거나 운영 환경에 사용하지 마십시오. 데모 또는 운영 전에
> EC2 Security Group의 8000 포트를 팀 IP로 제한하고 HTTPS/인증을 별도로
> 적용해야 합니다.
