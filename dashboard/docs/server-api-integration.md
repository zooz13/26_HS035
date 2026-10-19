# Dashboard - Model Server HTTP API 연동 문서

이 대시보드는 서버 API 정의서 기준으로 WebSocket 대신 HTTP polling을 사용합니다.

## 1. 기본 설정

```js
// js/config.js
export const CONFIG = {
  API_BASE_URL: 'http://localhost:8000',
  ROOM_ID: 'room_101',
  ROOM_NAME: '실험실 101',
  DEVICE_GROUP_ID: 'esp_group_101',
};
```

운영 배포 시에는 다음처럼 변경합니다.

```js
export const CONFIG = {
  API_BASE_URL: 'https://api.example.com',
  ROOM_ID: 'room_101',
  ROOM_NAME: '실험실 101',
  DEVICE_GROUP_ID: 'esp_group_101',
};
```

## 2. Dashboard 호출 API

| 기능 | Method | Path | 사용 파일 |
| --- | --- | --- | --- |
| 서버 health check | GET | `/api/health` | `js/api/ApiClient.js` |
| 모델 상태 확인 | GET | `/api/model/status` | `js/api/ApiClient.js` |
| 방 등록 | POST | `/api/rooms` | `js/api/ApiClient.js` |
| room setup 상태 | GET | `/api/rooms/{room_id}/setup/status` | `js/api/ApiClient.js` |
| baseline 시작 | POST | `/api/rooms/{room_id}/baseline/start` | `js/api/ApiClient.js` |
| calibration 시작 | POST | `/api/rooms/{room_id}/calibration/start` | `js/api/ApiClient.js` |
| 최신 추론 결과 | GET | `/api/rooms/{room_id}/result/latest` | `js/api/ApiClient.js` |
| 인위적 재난 이벤트 | POST | `/api/rooms/{room_id}/risk-assessment` | `js/api/ApiClient.js` |
| 이벤트 로그 | GET | `/api/events` | `js/api/ApiClient.js` |

## 3. 방 정보 등록 요청

대시보드의 `방 정보 등록` 폼은 아래 요청을 보냅니다.

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

등록 성공 후 대시보드는 입력한 `room_id`를 현재 API 기준 room으로 저장합니다. 이후 `setup/status`, `result/latest`, `baseline/start`는 이 room 기준으로 호출됩니다.

## 4. baseline 시작 요청

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

## 5. 최신 결과 응답

서버의 `GET /api/rooms/{room_id}/result/latest`는 결과를 **`latest_result` 키로 한 겹 감싸서** 반환합니다. 실제 추론 결과는 최상위가 아니라 `latest_result` 안에 들어 있으며, 아직 window가 처리되지 않았으면 `latest_result`는 `null`입니다.

```json
{
  "room_id": "room_101",
  "latest_result": {
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
}
```

`ApiClient.getLatestResult()`가 이 응답에서 `latest_result`를 벗겨내 normalizer에 전달합니다(`latest_result`가 `null`이면 `null` 반환). 따라서 normalizer와 화면 컴포넌트는 감싸지지 않은 결과 객체만 다룹니다.

대시보드는 이를 내부 표시용 데이터로 정규화해서 다음처럼 보여줍니다.

- Stage 1: `empty` / `occupied` / `unknown`
- Stage 2: `static_person` / `moving_person` / `skipped` / `disabled`
- 최종 판단: `empty` / `occupied_detected` / `occupied_static_person` / `occupied_moving_person`

## 6. 인위적 재난 이벤트 발생

현재 화면의 `인위적 재난 이벤트 발생` 버튼은 Model Server의 `POST /api/rooms/{room_id}/risk-assessment` API를 호출합니다.
대시보드는 재난 이벤트 발생 시각을 `event_timestamp`로 보내고, 서버가 반환한 위험도 평가 결과를 화면에 표시합니다.

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

버튼 클릭 시 서버 응답의 `risk_level`, `residual_presence`, summary 필드를 기준으로 위험도 분류 카드와 화면 이벤트 로그가 갱신됩니다.

## 7. 주의 사항

- 현재 staging 서버는 Dashboard로 push하지 않습니다.
- 따라서 Dashboard는 `GET /api/rooms/{room_id}/result/latest`를 주기적으로 polling합니다.
- Stage 2는 위험도 분류기가 아니라 `static_person` / `moving_person` classifier입니다.
- 현재 UI의 인위적 재난 이벤트는 `POST /api/rooms/{room_id}/risk-assessment` 응답을 바탕으로 `risk_level`과 `residual_presence`를 표시합니다.
