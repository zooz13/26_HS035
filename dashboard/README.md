# Wi-Fi CSI 위험도 판단 대시보드

순수 HTML, CSS, JavaScript ES Module 기반 대시보드입니다. 빌드 도구 없이 정적 파일로 실행할 수 있고, 서버 API 정의서 반영 후 **WebSocket이 아니라 Model Server HTTP API polling 방식**으로 통신합니다.

## 폴더 구조

```txt
csi_dashboard/
├─ index.html
├─ css/
│  └─ styles.css
├─ js/
│  ├─ app.js
│  ├─ config.js
│  ├─ api/
│  │  ├─ ApiClient.js
│  │  ├─ messages.js
│  │  └─ normalizers.js
│  ├─ socket/SocketClient.js        # 이전 버전 호환 파일, 현재 app.js에서는 사용하지 않음
│  ├─ store/AppState.js
│  ├─ components/
│  │  ├─ RiskOverview.js
│  │  ├─ RoomRegistration.js
│  │  ├─ SystemStatus.js
│  │  ├─ LatestResult.js
│  │  ├─ AlertPanel.js
│  │  ├─ EventLog.js
│  │  └─ BaselinePanel.js
│  ├─ mock/mockMessages.js
│  └─ utils/
│     ├─ dom.js
│     └─ time.js
└─ docs/server-api-integration.md
```

## 실행 방법

브라우저 보안 정책 때문에 ES Module은 `file://`로 직접 여는 것보다 로컬 정적 서버로 실행하는 것을 권장합니다.

```bash
cd csi_dashboard
python -m http.server 5500
```

브라우저에서 접속:

```txt
http://localhost:5500
```

서버 없이 화면만 확인:

```txt
http://localhost:5500?mock=1
```

## Model Server 주소 변경

`js/config.js`에서 기본값을 수정합니다. 화면의 **방 정보 등록** 폼에서 등록하면 런타임 기준 `ROOM_ID`, `ROOM_NAME`, `DEVICE_GROUP_ID`가 입력값으로 갱신되고, 이후 상태/결과/baseline API가 해당 room 기준으로 호출됩니다.

```js
export const CONFIG = {
  API_BASE_URL: 'http://localhost:8000',
  ROOM_ID: 'room_101',
  ROOM_NAME: '실험실 101',
  DEVICE_GROUP_ID: 'esp_group_101',
};
```

## 반영된 API 방식

현재 staging Model Server는 Dashboard로 push하지 않습니다. Dashboard가 HTTP로 상태와 결과를 polling합니다. 재난 이벤트 발생 버튼은 `POST /api/rooms/{room_id}/risk-assessment`를 호출해 서버의 잔류 위험도 평가 결과를 표시합니다.

| 화면 | 호출 API |
| --- | --- |
| 방 정보 등록 | `POST /api/rooms` |
| UC-D1 현재 시스템 상태 | `GET /api/health`, `GET /api/model/status`, `GET /api/rooms/{room_id}/setup/status`, `GET /api/rooms/{room_id}/result/latest` |
| UC-D2 최신 모델 분류 결과 | `GET /api/rooms/{room_id}/result/latest` |
| UC-D3 재난 이벤트 알람 | `POST /api/rooms/{room_id}/risk-assessment` |
| UC-D4 이벤트 로그 조회 | `GET /api/events` |
| UC-D5 baseline 설정 | `POST /api/rooms/{room_id}/baseline/start` |

## 방 정보 등록 요청

대시보드의 `방 정보 등록` 버튼은 아래 요청을 보냅니다.

```http
POST /api/rooms
Content-Type: application/json
```

```json
{
  "room_id": "room_101",
  "room_name": "실험실 101",
  "device_group_id": "esp_group_101"
}
```

등록 성공 후 대시보드는 다음 요청들을 새 `room_id` 기준으로 다시 조회합니다.

```txt
GET /api/rooms/{room_id}/setup/status
GET /api/rooms/{room_id}/result/latest
GET /api/events
```

## baseline 시작 요청

대시보드 버튼은 아래 요청을 보냅니다.

```http
POST /api/rooms/room_101/baseline/start
Content-Type: application/json
```

```json
{
  "duration_sec": 300,
  "window_duration_sec": 5,
  "mode": "empty",
  "requested_by": "dashboard"
}
```

주의: 이 요청은 하드웨어를 직접 제어하지 않고, 서버의 room 상태를 `baseline_collecting`으로 전환하는 용도입니다. 실제 empty CSI 수집과 `/baseline/finish` 호출은 하드웨어/전처리기가 담당합니다.

## 최신 결과 응답

서버의 room-scoped latest result 응답 예시는 다음과 같습니다.

```json
{
  "room_id": "room_101",
  "window_id": "win_000123",
  "stage1": {
    "label": "occupied",
    "score": 2.91,
    "threshold": 2.506834,
    "artifact_id": "stage1_threshold_v001"
  },
  "stage2": {
    "label": "static_person",
    "confidence": 0.87,
    "status": "ok",
    "artifact_id": "stage2_static_moving_v001"
  },
  "final_status": "occupied_static_person",
  "timestamp": "2026-06-14T06:10:05+09:00"
}
```

대시보드는 이를 내부 표시용 데이터로 정규화해서 다음처럼 보여줍니다.

- Stage 1: `empty` / `occupied` / `unknown`
- Stage 2: `static_person` / `moving_person` / `skipped` / `disabled`
- 최종 판단: `empty` / `occupied_detected` / `occupied_static_person` / `occupied_moving_person`

## 인위적 재난 이벤트 발생

`인위적 재난 이벤트 발생` 버튼은 서버의 `POST /api/rooms/{room_id}/risk-assessment` API를 호출합니다. 대시보드는 재난 이벤트 발생 시각을 보내고, 서버가 최근 room prediction history를 기준으로 계산한 위험도를 표시합니다.

```http
POST /api/rooms/room_101/risk-assessment
Content-Type: application/json
```

```json
{
  "event_timestamp": "2026-06-14T06:10:35+09:00"
}
```

| 서버 응답 필드 | 화면 반영 |
| --- | --- |
| `risk_level` | 원형 위험도 카드 색상과 알람 등급 |
| `residual_presence` | 실내 잔류 판단 메시지 |
| `confidence` | 위험도 카드의 신뢰도 |
| `reason` | 판단 사유 |
| `occupancy_summary`, `motion_summary` | 이벤트 로그와 상세 표시용 summary |

버튼을 누르면 서버 응답의 `risk_level`, `residual_presence`에 따라 1번 위험도 분류 원형 카드 색상이 바뀌고, 같은 내용이 화면 이벤트 로그에도 1건 추가됩니다.

## 주요 변경점

- 전체 UI를 `1. 위험도 분류` 상단 원형 카드 + `2. 방 정보 등록` + `3~6` 카드 구조로 변경했습니다.
- 방 정보 등록 폼을 추가하고 `POST /api/rooms` 호출을 연결했습니다.
- 등록 성공 시 현재 대시보드 기준 room 정보를 갱신하고, setup/status와 latest result를 새 room 기준으로 다시 조회합니다.
- 재난 이벤트 발생 전에는 위험도 분류를 `정상`으로 표시하고, 인위적 재난 이벤트 발생 이후 서버 위험도 평가 결과에 따라 원형 카드 색상을 변경합니다.
- WebSocket 연결 로직을 사용하지 않고 `fetch()` 기반 `ApiClient.js`로 변경했습니다.
- Stage 2는 `static_person` / `moving_person` / `skipped` / `disabled` 중심으로 표시합니다.
- baseline 버튼은 서버 상태를 `baseline_collecting`으로 전환하는 요청입니다. 실제 empty CSI 수집과 `baseline/finish`는 하드웨어/전처리기 담당입니다.
- `인위적 재난 이벤트 발생` 버튼은 `POST /api/rooms/{room_id}/risk-assessment`를 호출하고 서버 응답으로 위험도를 표시합니다.

## 화면 표시 정책

- 1번 위험도 분류는 재난 이벤트 발생 전 `정상` 상태로 표시됩니다.
- 2번 방 정보 등록에서 `room_id`, `room_name`, `device_group_id`를 입력하고 서버에 등록합니다.
- 등록된 room_id는 이후 현재 시스템 상태, baseline 설정, 최신 모델 분류 결과 API 호출 기준으로 사용됩니다.
- 인위적 재난 이벤트 발생 후 `danger`, `warning`, `normal`, `unknown` 등에 따라 원형 카드 색상이 변경됩니다.
- `위험 표시 초기화` 버튼은 현재 화면의 위험도 평가 표시를 초기화하여 다시 정상 대기 상태로 돌립니다.


## Calibration 재시작 동작

Baseline과 calibration은 분리된 단계입니다.

- `baseline 수집 시작 요청`: 빈 공간 기준 profile 수집 상태를 시작합니다.
- `calibration 시작 요청`: 생성된 baseline ID를 기준으로 empty calibration window 수집 상태를 시작합니다.
- 기존 `calibration_id`가 있어도 `baseline_id`가 존재하고 room 상태가 `baseline_ready`, `ready`, `running`이면 `calibration 재시작 요청` 버튼을 다시 누를 수 있습니다.
- 재시작 시 서버는 같은 baseline 기준으로 새 CalibrationProfile 버전을 생성하는 방식이 권장됩니다. 예: `calib_room_101_v001` → `calib_room_101_v002`.
- calibration 수집 중이거나 baseline 수집 중에는 중복 요청 방지를 위해 버튼이 비활성화됩니다.
