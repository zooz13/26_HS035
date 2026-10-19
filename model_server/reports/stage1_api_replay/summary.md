# Stage 1 API Replay Summary

## Purpose

Replay real room4 CSI-derived Stage 1 feature payloads through the model_server room/baseline/calibration/window APIs and compare API-response metrics to offline guided calibration results.

## Stage 1 Artifact

- Artifact: `stage1_threshold_v001`
- Global threshold: `2.5068338100224326`

## Mode Descriptions

- `no_calibration`: S01 baseline only, global threshold retained.
- `empty_quantile_threshold`: S01 baseline, S04 first 20% empty scores set the threshold.
- `guided_baseline_merge`: S01 baseline merged with S04 first 20%, global threshold retained.

## Metrics

| target_env | mode | threshold | macro_f1 | empty_recall | occupied_recall | static_recall | moving_recall | leakage | exclusion |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| room4_2026-05-20 | empty_quantile_threshold | 1.394756 | 0.753737 | 0.977032 | 0.584022 | 0.859504 | 0.308540 | False | True |
| room4_2026-05-20 | guided_baseline_merge | 2.506834 | 0.975765 | 1.000000 | 0.957300 | 0.925620 | 0.988981 | False | True |
| room4_2026-05-20 | no_calibration | 2.506834 | 0.304629 | 1.000000 | 0.000000 | 0.000000 | 0.000000 | False | True |
| room4_2026-05-27 | empty_quantile_threshold | 8.804144 | 0.904545 | 0.994872 | 0.844080 | 0.986111 | 0.698337 | False | True |
| room4_2026-05-27 | guided_baseline_merge | 2.506834 | 0.975595 | 0.982906 | 0.971864 | 0.995370 | 0.947743 | False | True |
| room4_2026-05-27 | no_calibration | 2.506834 | 0.902178 | 0.779487 | 0.998828 | 1.000000 | 0.997625 | False | True |

## Offline Guided Delta

### room4_2026-05-20
- `macro_f1` delta: `-0.000000412`
- `empty_recall` delta: `+0.000000000`
- `occupied_recall` delta: `+0.000000275`
- `static_person_recall` delta: `-0.000000165`
- `moving_person_recall` delta: `-0.000000284`
- API replay matches the offline guided metric within numerical precision.

### room4_2026-05-27
- `macro_f1` delta: `-0.000000389`
- `empty_recall` delta: `-0.000000017`
- `occupied_recall` delta: `+0.000000009`
- `static_person_recall` delta: `+0.000000370`
- `moving_person_recall` delta: `+0.000000468`
- API replay matches the offline guided metric within numerical precision.

## Validation Notes

- Metrics are computed from `/api/rooms/{room_id}/window` responses, not directly from offline labels.
- S01 and S04 labels are loaded from `scenario.md` through `csi_pipeline.data.load_env` and must be `empty`.
- Calibration windows are excluded by starting S04 evaluation after the calibration prefix.
- Negative `/window` before ready is checked for HTTP 400.
- Latest result is checked against the final replayed window.

## Next Work

- Connect Stage 2 static/moving CNN and replay Stage 1-pass windows through Stage 2.
- Persist RoomState/BaselineProfile/CalibrationProfile outside the in-memory store when moving beyond local validation.
