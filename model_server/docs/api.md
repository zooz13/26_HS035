# Model Server Room Setup API

## API Responsibility Summary

This document describes APIs provided by the Model Server. Dashboard and Hardware/Preprocessor components call the Model Server over HTTP.

The current internal staging Model Server does not call Dashboard APIs. It only returns responses to incoming requests. Dashboard should poll latest results or call Risk Assessment when a disaster event occurs. Push callbacks or webhooks from Model Server to Dashboard are out of scope for the current internal staging version.

Prefer the room-scoped APIs for dashboard and hardware integration. The non-room-scoped window APIs remain available for legacy/debug use, but they are not recommended for dashboard integration.

| Caller | API | Role | Staging usage |
| --- | --- | --- | --- |
| Dashboard | `GET /api/health` | Check whether the Model Server is running | Required |
| Dashboard/Admin | `GET /api/model/status` | Check model/server status | Optional |
| Dashboard | `POST /api/rooms` | Register a room | Required for setup |
| Dashboard | `GET /api/rooms/{room_id}/setup/status` | Check room setup state | Required for setup |
| Dashboard | `POST /api/rooms/{room_id}/baseline/start` | Mark baseline collection as started | Required for setup |
| Hardware/Preprocessor | `POST /api/rooms/{room_id}/baseline/finish` | Register baseline profile and create default calibration profile | Required for setup |
| Dashboard | `POST /api/rooms/{room_id}/calibration/start` | Start optional threshold recalibration collection state | Optional recalibration |
| Hardware/Preprocessor | `POST /api/rooms/{room_id}/calibration/window` | Send empty recalibration feature window | Optional recalibration |
| Hardware/Preprocessor | `POST /api/rooms/{room_id}/calibration/finish` | Finish recalibration and create updated calibration profile | Optional recalibration |
| Hardware/Preprocessor | `POST /api/rooms/{room_id}/window` | Send real-time feature window and run room-scoped inference | Required |
| Dashboard | `GET /api/rooms/{room_id}/result/latest` | Read latest room inference result | Required |
| Dashboard | `POST /api/rooms/{room_id}/risk-assessment` | Evaluate residual presence risk after disaster event | Required for disaster flow |
| Admin/Debug | `GET /api/events` | Read internal event log for non-room-scoped inference events | Optional/debug |
| Legacy/Debug | `POST /api/window` | Run non-room-scoped Model Pipeline inference | Not recommended for dashboard integration |
| Legacy/Debug | `GET /api/result/latest` | Read latest non-room-scoped inference result | Not recommended for dashboard integration |

## Does the Model Server call Dashboard APIs?

No, not in the current internal staging version.

The Model Server only returns responses to requests. Dashboard is responsible for polling latest results, triggering Risk Assessment when a disaster event occurs, and displaying `risk_level` and `residual_presence`.

If push notifications are needed later, design a separate Dashboard webhook contract.

## Disaster Event Flow

1. Hardware/Preprocessor sends real-time windows to `POST /api/rooms/{room_id}/window`.
2. Model Server stores the latest room result and room result history.
3. Dashboard detects or receives a disaster event.
4. Dashboard calls `POST /api/rooms/{room_id}/risk-assessment`.
5. Model Server returns `residual_presence`, `risk_level`, `reason`, and summary fields.
6. Dashboard displays the result.

## 1. Overall Flow

The initial room setup flow is:

1. Register a room.
2. Start baseline collection.
3. Register a baseline profile produced by the hardware/preprocessor.
4. The server creates a default `CalibrationProfile` using the global Model Pipeline Stage 1 threshold.
5. The room becomes `ready`.
6. Send real-time feature windows for inference.
7. Read the latest room result.
8. When a disaster event is raised, request residual presence risk assessment from recent prediction history.

Optional threshold recalibration can be performed later:

1. Start calibration collection.
2. Send empty calibration window feature scores.
3. Finish calibration and create an updated `CalibrationProfile`.
4. Use the new `calibration_id` for subsequent real-time windows.

The model server does not read raw CSI CSV files and does not build the baseline from raw data. The hardware/preprocessor owns empty collection, baseline construction, feature extraction, and feature-vector delivery.

## 2. Dashboard APIs

### POST `/api/rooms`

Registers a room.

```json
{
  "room_id": "room_101",
  "room_name": "실험실 101",
  "device_group_id": "esp_group_101",
  "description": "optional"
}
```

### POST `/api/rooms/{room_id}/baseline/start`

Moves the room to `baseline_collecting` and persists a `baseline_command`.
An enabled Gateway polls `setup/status`, acknowledges this command, and starts
empty-room collection without opening a second UART connection.
The response includes a unique `command_id` and `baseline_session_id`; the latter
must be sent to `baseline/finish`.

```json
{
  "duration_sec": 300,
  "window_duration_sec": 5,
  "mode": "empty",
  "requested_by": "dashboard"
}
```

### GET `/api/rooms/{room_id}/setup/status`

Returns setup state, baseline id, calibration id, calibration method/status, global/effective thresholds, calibration source metadata, the latest baseline command, Model Pipeline Stage 1 artifact id, and Model Pipeline Stage 2 mode.

After `baseline/finish`, `status` is `ready`, `calibration_id` is populated, and `calibration_method` is `empty_quantile_threshold`.
The completed baseline command is retained in memory and exposed as `baseline_command`:

```json
{
  "room_id": "room_101",
  "status": "ready",
  "baseline_id": "baseline_room_101_v001",
  "calibration_id": "calib_room_101_baseline_default_v001",
  "calibration_method": "empty_quantile_threshold",
  "global_threshold": 2.506834,
  "effective_threshold": 0.43,
  "calibration_source_metadata": {
    "source": "baseline_finish_summary",
    "baseline_session_id": "base_20260614_001",
    "quantile": 0.95,
    "summary_key": "p95_score",
    "quantile_value": 0.43
  },
  "baseline_command": {
    "command_id": "baseline_cmd_...",
    "baseline_session_id": "base_20260614_001",
    "status": "completed"
  }
}
```

### POST `/api/rooms/{room_id}/calibration/start`

Starts optional empty-room threshold recalibration window collection.

```json
{
  "baseline_id": "baseline_room_101_v001",
  "method": "guided_baseline_merge",
  "expected_windows": 12,
  "window_duration_sec": 5
}
```

### GET `/api/rooms/{room_id}/result/latest`

Returns the latest room-scoped inference result inside an envelope. The actual result is nested under `latest_result`, which is `null` until a window has been processed. Clients must read the result from `latest_result`, not from the top level.

```json
{
  "room_id": "room_101",
  "latest_result": {
    "room_id": "room_101",
    "window_id": "win_000123",
    "stage1": { "label": "occupied", "score": 2.91, "threshold": 2.506834, "artifact_id": "stage1_threshold_v001" },
    "stage2": { "label": "moving_person", "confidence": 0.86, "status": "ok", "artifact_id": "stage2_static_moving_v001" },
    "final_status": "occupied_moving_person",
    "timestamp": "2026-06-14T06:10:05+09:00"
  }
}
```

When no window has been processed yet:

```json
{ "room_id": "room_101", "latest_result": null }
```

### POST `/api/rooms/{room_id}/risk-assessment`

Assesses residual presence risk for a disaster event. This endpoint is server-side rule logic over recent room predictions. It is not a new trained model and it does not run false-alarm validation.

The dashboard should call this endpoint when an external disaster event is raised. If `recent_predictions` is omitted, the model server uses the stored `/window` result history for that room.

```json
{
  "event_timestamp": "2026-06-14T06:10:35+09:00"
}
```

The dashboard or another upstream service may also pass explicit recent predictions. At least 6 recent windows are required by the current policy.

```json
{
  "event_timestamp": "2026-06-14T06:10:35+09:00",
  "recent_predictions": [
    {
      "window_id": "win_001",
      "timestamp": "2026-06-14T06:10:05+09:00",
      "occupancy_state": "occupied",
      "motion_state": "static_person",
      "window_duration_sec": 5
    },
    {
      "window_id": "win_002",
      "timestamp": "2026-06-14T06:10:10+09:00",
      "occupancy_state": "occupied",
      "motion_state": "static_person",
      "window_duration_sec": 5
    },
    {
      "window_id": "win_003",
      "timestamp": "2026-06-14T06:10:15+09:00",
      "occupancy_state": "occupied",
      "motion_state": "static_person",
      "window_duration_sec": 5
    },
    {
      "window_id": "win_004",
      "timestamp": "2026-06-14T06:10:20+09:00",
      "occupancy_state": "occupied",
      "motion_state": "static_person",
      "window_duration_sec": 5
    },
    {
      "window_id": "win_005",
      "timestamp": "2026-06-14T06:10:25+09:00",
      "occupancy_state": "occupied",
      "motion_state": "static_person",
      "window_duration_sec": 5
    },
    {
      "window_id": "win_006",
      "timestamp": "2026-06-14T06:10:30+09:00",
      "occupancy_state": "occupied",
      "motion_state": "static_person",
      "window_duration_sec": 5
    }
  ]
}
```

Example response:

```json
{
  "room_id": "room_101",
  "event_timestamp": "2026-06-14T06:10:35+09:00",
  "residual_presence": "high",
  "risk_level": "danger",
  "occupancy_summary": {
    "empty_ratio": 0.0,
    "occupied_ratio": 1.0,
    "recent_occupied": true
  },
  "motion_summary": {
    "static_ratio": 1.0,
    "moving_ratio": 0.0,
    "static_persistence_seconds": 30.0
  },
  "confidence": 0.9,
  "reason": "static_presence_persisted",
  "limitations": []
}
```

## 3. Hardware/Preprocessor APIs

### POST `/api/rooms/{room_id}/baseline/finish`

Registers a baseline profile after empty-room collection and preprocessing.
On success, the server creates a `CalibrationProfile` with the artifact's existing empty-quantile policy. With the current artifact this is `p95_score + 0.0`, so the Gateway-provided exact p95 summary becomes the effective threshold. Missing or non-finite required quantile input returns HTTP 400 rather than falling back to the global threshold. The room becomes `ready`, and the returned `calibration_id` can be used immediately for `/api/rooms/{room_id}/window`.

The idempotency key is `(room_id, baseline_session_id)`. The first successful finish stores the completed session result and returns `idempotent=false`. A retry for the same session returns the original baseline/calibration IDs with `idempotent=true` and does not create another profile. A finish for a session other than the current baseline command returns HTTP 409, including after the current command is completed.

```json
{
  "baseline_session_id": "base_20260614_001",
  "baseline_id": "baseline_room_101_v001",
  "duration_sec": 300,
  "window_duration_sec": 5,
  "num_windows": 60,
  "preprocess_version": "preprocess_v1",
  "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
  "feature_version": "stage1_feature_v1",
  "baseline_profile": {
    "owner": "preprocessor",
    "subcarrier_count": 192,
    "summary": {
      "median_score": 0.21,
      "p95_score": 0.43,
      "p99_score": 0.51
    }
  }
}
```

Example response:

```json
{
  "room_id": "room_101",
  "baseline_session_id": "base_20260614_001",
  "baseline_id": "baseline_room_101_v001",
  "calibration_id": "calib_room_101_baseline_default_v001",
  "calibration_method": "empty_quantile_threshold",
  "global_threshold": 2.506834,
  "effective_threshold": 0.43,
  "score_summary": {
    "p50": 0.21,
    "p95": 0.43,
    "p99": 0.51
  },
  "source_metadata": {
    "source": "baseline_finish_summary",
    "baseline_session_id": "base_20260614_001",
    "quantile": 0.95,
    "summary_key": "p95_score",
    "quantile_value": 0.43
  },
  "status": "ready",
  "idempotent": false,
  "message": "Baseline profile registered. Default calibration profile created. Room is ready for inference."
}
```

The same response IDs are returned on a retry, with `idempotent` changed to `true`.

### POST `/api/rooms/{room_id}/calibration/window`

Adds one empty feature window for optional threshold recalibration.

```json
{
  "calibration_session_id": "calib_20260614_001",
  "window_id": "calib_win_0001",
  "baseline_id": "baseline_room_101_v001",
  "feature_version": "stage1_feature_v1",
  "features": {
    "static_score": 0.14,
    "motion_score": 0.06,
    "shape_score": 0.22,
    "robust_max_shape_score": 0.22
  }
}
```

### POST `/api/rooms/{room_id}/calibration/finish`

Creates an updated `CalibrationProfile` for optional threshold recalibration. If fewer than `expected_windows` were received, the server returns HTTP 400. On success, `room.calibration_id` is updated to the new calibration id.

```json
{
  "calibration_session_id": "calib_20260614_001",
  "method": "guided_baseline_merge",
  "quantile": 0.95,
  "margin": 0.0
}
```

### POST `/api/rooms/{room_id}/window`

Runs room-scoped real-time inference from a Model Pipeline Stage 1 feature vector.

```json
{
  "window_id": "win_000123",
  "baseline_id": "baseline_room_101_v001",
  "calibration_id": "calib_room_101_v001",
  "device_group_id": "esp_group_101",
  "window_start_time": "2026-06-14T06:10:00+09:00",
  "window_end_time": "2026-06-14T06:10:05+09:00",
  "window_duration_sec": 5,
  "data_type": "stage1_feature_vector",
  "feature_version": "stage1_feature_v1",
  "features": {
    "static_score": 0.43,
    "motion_score": 0.12,
    "shape_score": 2.91,
    "robust_max_shape_score": 2.91
  }
}
```

## 4. Room State Transitions

```text
uncalibrated
  -> baseline_collecting
  -> ready
  -> running
```

`baseline_ready` remains a legacy/intermediate status string but is not part of the normal initial setup path after `baseline/finish`, because `baseline/finish` now creates a default calibration profile and moves the room to `ready`.

Optional recalibration transitions a room through:

```text
ready or running
  -> calibration_collecting
  -> ready
```

`error` is reserved for future persistent error handling.

## 5. BaselineProfile

```json
{
  "baseline_id": "baseline_room_101_v001",
  "room_id": "room_101",
  "baseline_session_id": "base_20260614_001",
  "duration_sec": 300,
  "window_duration_sec": 5,
  "preprocess_version": "preprocess_v1",
  "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
  "feature_version": "stage1_feature_v1",
  "owner": "preprocessor",
  "status": "ready",
  "summary": {
    "median_score": 0.21,
    "p95_score": 0.43,
    "p99_score": 0.51
  },
  "created_at": "..."
}
```

## 6. CalibrationProfile

```json
{
  "calibration_id": "calib_room_101_v001",
  "room_id": "room_101",
  "baseline_id": "baseline_room_101_v001",
  "stage1_artifact_id": "stage1_threshold_v001",
  "method": "guided_baseline_merge",
  "global_threshold": 2.506834,
  "effective_threshold": 2.506834,
  "num_windows": 12,
  "score_name": "robust_max_shape_score",
  "score_summary": {
    "p50": 0.41,
    "p90": 1.12,
    "p95": 1.48,
    "p99": 1.91
  },
  "margin": 0.0,
  "source_metadata": {
    "source": "calibration_windows",
    "calibration_session_id": "calib_20260614_001",
    "quantile": 0.95
  },
  "status": "ready",
  "created_at": "..."
}
```

`empty_quantile_threshold` is created automatically by `baseline/finish`. It reuses the artifact policy (`empty_quantile=0.95`, `margin=0.0`) and the exact precomputed `p95_score` from the baseline summary. No clamp/min/max is applied because the existing calibration algorithm has none.

`guided_baseline_merge` keeps the global Model Pipeline Stage 1 threshold and stores the calibration score distribution. `empty_quantile_threshold` is available as an option and sets `effective_threshold = quantile(calibration_scores) + margin`. `no_calibration` is reserved for replay/diagnostic flows that register a default CalibrationProfile with the global threshold and no calibration windows.

## 7. Real-Time Window Response

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
    "label": "unknown",
    "status": "disabled"
  },
  "final_status": "occupied_detected",
  "timestamp": "2026-06-14T06:10:05+09:00"
}
```

Model Pipeline Stage 1 uses `robust_max_shape_score` by default. If that key is missing, the server can derive the score from `max(static_score, motion_score, shape_score)`.

## 8. Error Codes

- `400`: invalid state transition, wrong baseline id, wrong calibration id, unsupported method, not enough calibration windows, or invalid feature payload.
- `404`: room not found for status/latest-result lookup.
- `422`: request schema validation error.

## 9. Example Scenario

1. Dashboard calls `POST /api/rooms`.
2. Dashboard calls `POST /api/rooms/room_101/baseline/start`.
3. Hardware/preprocessor collects empty CSI and posts `baseline/finish`.
4. Server creates a default calibration profile using the global Model Pipeline Stage 1 threshold.
5. Dashboard confirms `setup/status` is `ready` and stores `baseline_id` plus `calibration_id`.
6. Hardware/preprocessor posts `window` every 5 seconds.
7. Dashboard polls `result/latest`.
8. When a disaster event is raised, Dashboard calls `risk-assessment`.

## 10. Current Model Pipeline Stage 2 Limitations

Model Pipeline Stage 2 is an occupied-only `static_person` / `moving_person` classifier. It is not a risk classifier and it does not accept `empty` as a class.

When `stage2_mode` is `static_moving_cnn`, `/api/rooms/{room_id}/window` accepts optional `motion_input`. The older `stage2_input` key is still accepted as a deprecated compatibility alias.

```json
{
  "motion_input": {
    "input_type": "window_matrix",
    "feature_version": "stage2_feature_v1",
    "shape": [128, 192],
    "values": [[0.0]]
  }
}
```

The real `values` matrix must have shape `[128, 192]`. Model Pipeline Stage 1 uses the scalar feature payload first. If Model Pipeline Stage 1 returns `empty`, Model Pipeline Stage 2 is not called and the response is:

Model Pipeline Stage 2 input rules:

- Model Pipeline Stage 1 `empty`: Model Pipeline Stage 2 is skipped. Motion input is not required and is ignored if present.
- Model Pipeline Stage 1 `occupied` + Model Pipeline Stage 2 enabled + `motion_input` or compatibility `stage2_input` present: Model Pipeline Stage 2 CNN runs.
- Model Pipeline Stage 1 `occupied` + Model Pipeline Stage 2 enabled + motion input missing: the server returns HTTP 400.
- Model Pipeline Stage 1 `occupied` + Model Pipeline Stage 2 disabled: Model Pipeline Stage 2 returns `unknown/disabled`.

```json
{
  "stage2": {
    "label": null,
    "status": "skipped",
    "reason": "stage1_empty"
  },
  "final_status": "empty"
}
```

If Model Pipeline Stage 1 returns `occupied` and Model Pipeline Stage 2 is enabled, motion input is required. Missing input, shape mismatch, or feature-version mismatch returns HTTP 400. A successful Model Pipeline Stage 2 response is:

```json
{
  "stage2": {
    "label": "static_person",
    "confidence": 0.87,
    "status": "ok",
    "artifact_id": "stage2_static_moving_v001"
  },
  "final_status": "occupied_static_person"
}
```

If `stage2_mode` is `disabled`, occupied windows return `stage2.label = "unknown"` and `stage2.status = "disabled"`. Risk categories such as `risk_low`, `risk_medium`, and `risk_high` are not produced by this room setup flow.
