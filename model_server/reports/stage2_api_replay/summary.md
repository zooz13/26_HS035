# Stage 2 API Replay Summary

## Validation Decision

- Stage2 integration: passed
- Stage2 model quality: conditionally passed

The server/API integration is considered complete for the current milestone: Stage 2 is connected as an occupied-only `static_person` / `moving_person` classifier, and `/api/rooms/{room_id}/window` calls Stage 2 only after Stage 1 returns `occupied`.

The model quality is only conditionally passed. The API path works, but the current checkpoint has known limitations listed below.

## Evaluation Scope

Stage 2 conditional metrics are computed only on ground-truth occupied windows that Stage 1 also passed as `occupied`. Empty windows are not Stage 2 inputs. End-to-end recall includes Stage 1 misses, so it measures the full Stage 1 plus Stage 2 pipeline rather than the Stage 2 classifier alone.

| target_env | stage1_empty_recall | stage1_occupied_recall | stage2_macro_f1 | stage2_static_recall | stage2_moving_recall | e2e_static | e2e_moving |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| room4_2026-05-20 | 1.000000 | 0.957300 | 0.807098 | 0.928571 | 0.696379 | 0.859504 | 0.688705 |
| room4_2026-05-27 | 0.982906 | 0.971864 | 0.937269 | 0.895349 | 0.982456 | 0.891204 | 0.931116 |

## Known Limitations

1. `room4_2026-05-20` moving recall is low: Stage 2 conditional moving recall is `0.696379`, and end-to-end moving recall is `0.688705`.
2. The `room4_2026-05-20` moving degradation is not primarily a Stage 1 miss issue. Stage 1 occupied recall for that target is `0.957300`; the low moving result is a conditional Stage 2 static/moving classifier quality issue.
3. The exported checkpoint metadata records that the source checkpoint train envs include `room5_2026-05-08`. This is a checkpoint provenance limitation for the current artifact.

## Frozen State

No additional training or checkpoint change is planned as part of this validation freeze. The current artifact is fixed for API/server integration validation, with the model-quality limitations above carried forward explicitly.
