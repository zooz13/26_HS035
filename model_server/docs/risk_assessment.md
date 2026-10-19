# Residual Presence Risk Assessment

## Purpose

Residual Presence Risk Assessment evaluates recent model-server prediction history when the dashboard reports a disaster event. It returns a dashboard-facing `residual_presence` and `risk_level`.

This is server-side rule logic. It is not a new trained Stage 3 model.

Terminology:

- [Model Pipeline] Stage 1: Occupancy Detection
- [Model Pipeline] Stage 2: Motion State Classification
- [Server] Risk Assessment

## Non-Goals

- Does not evaluate whether the disaster alarm is false.
- Does not verify model false alarms without labels.
- Does not infer actual evacuation completion.
- Does not track position or exit direction.
- Does not retrain occupancy or motion classifiers.

False alarm validation requires separate labeled logs or manual real-time annotation.

## Inputs

Endpoint:

```text
POST /api/rooms/{room_id}/risk-assessment
```

Request:

```json
{
  "event_timestamp": "2026-06-14T06:10:05+09:00",
  "recent_predictions": [
    {
      "window_id": "win_001",
      "timestamp": "2026-06-14T06:10:00+09:00",
      "occupancy_state": "occupied",
      "motion_state": "static_person",
      "window_duration_sec": 5,
      "quality": 1.0
    }
  ]
}
```

If `recent_predictions` is omitted, the server uses stored prediction history for the room. Prediction records use:

- `occupancy_state`: `empty`, `occupied`, `unknown`
- `motion_state`: `static_person`, `moving_person`, `skipped`, `disabled`, `unknown`
- optional confidence and quality fields

## Outputs

```json
{
  "room_id": "room_101",
  "event_timestamp": "2026-06-14T06:10:05+09:00",
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
    "static_persistence_seconds": 60.0
  },
  "confidence": 0.9,
  "reason": "static_presence_persisted",
  "limitations": []
}
```

## Risk Classification Rules

### Stable Empty

If the recent buffer is mostly empty and no recent occupied window exists:

- `residual_presence`: `none`
- `risk_level`: `normal`
- `reason`: `stable_empty_before_event`

### Persistent Static Occupied

If occupied ratio is high and `static_person` persists long enough:

- `residual_presence`: `high`
- `risk_level`: `danger`
- `reason`: `static_presence_persisted`

### Moving Occupied

If occupied ratio is high and `moving_person` is dominant:

- `residual_presence`: `medium`
- `risk_level`: `warning`
- `reason`: `moving_presence_detected_but_exit_unknown`

Moving is not treated as low risk because the system has no position tracking or exit tracking.

### Unstable Or Recently Occupied

If predictions fluctuate, or occupied was detected recently but the latest state is empty:

- `residual_presence`: `low` or `medium`
- `risk_level`: `caution`
- `reason`: `unstable_or_recent_presence`

### Insufficient Input

If prediction history is missing, too short, malformed, or low quality:

- `residual_presence`: `unknown`
- `risk_level`: `unknown`
- `reason`: `insufficient_input`

## Known Limitations

- `moving_person` does not mean the person evacuated.
- `static_person` and `moving_person` may contain classifier errors.
- Current system has no position or exit tracking.
- Risk Assessment does not determine whether a disaster event is a false alarm.
- False alarm validation requires separate labeled logs or manual real-time annotation.

## Example API Responses

Stable empty:

```json
{
  "residual_presence": "none",
  "risk_level": "normal",
  "reason": "stable_empty_before_event"
}
```

Persistent static occupied:

```json
{
  "residual_presence": "high",
  "risk_level": "danger",
  "reason": "static_presence_persisted"
}
```

Moving occupied:

```json
{
  "residual_presence": "medium",
  "risk_level": "warning",
  "reason": "moving_presence_detected_but_exit_unknown",
  "limitations": ["moving_person does not confirm evacuation"]
}
```
