# Pipeline Design Verification Report

Generated: 2026-06-13T22:42:33.358330+00:00

## Evidence Policy

This report is based on Python source, configs, training/evaluation scripts, and generated artifacts. README files were treated as secondary notes only. When README text conflicts with code, the code path is treated as authoritative.

## Stage 1 Definition From Code

- Source files: `csi-pipeline/csi_pipeline/stage1/baseline.py`, `scoring.py`, `threshold.py`, `calibration.py`, `train.py`, `evaluate.py`.
- Stage 1 is a baseline-relative threshold detector, not a learned logistic regression or CNN.
- CSI parsing expects raw length 384 as interleaved I/Q and converts it to 192 amplitudes.
- The active refactored detector computes baseline-relative features and `robust_max_shape_score = max(static_score, motion_score, shape_score)`.
- `threshold.py::choose_threshold` selects a threshold over score candidates using training labels and the `balanced_macro` policy.
- `calibration.py::build_stage1_test_table` contains S01 initial empty baseline and guided S04 empty recalibration policies, and excludes the S04 recalibration prefix from evaluation.

## Stage 1 Score, Threshold, Baseline, Calibration Checks

1. Score calculation: `csi_pipeline.stage1.scoring.baseline_plus_shape_features` computes static, motion, and shape features; `robust_max_shape_score` is the final score.
2. Threshold decision: `csi_pipeline.stage1.threshold.choose_threshold` selects the threshold from training scores by maximizing macro F1 under `balanced_macro`.
3. Baseline sessions: `train_table_stage1` uses each env calibration session, preferring S01 empty, with the first 20 seconds by default.
4. Calibration sessions/ratio: `calibration.py` supports `guided 10%`, `guided 20%`, `guided 30%`, using an S04 empty prefix for guided recalibration.
5. Calibration exclusion: guided methods evaluate S04 from `s04_rows` onward, so calibration windows are excluded from metrics.

## Stage 2 Definition From Code

- Source files: `csi-pipeline/csi_pipeline/stage2/dataset.py`, `model.py`, `train.py`.
- Stage 2 is occupied-only static/moving classification.
- `dataset.py::build_stage2_arrays` filters labels to `static_person` and `moving_person`; empty is excluded.
- Label mapping is `static_person: 0`, `moving_person: 1`.
- `TinyCnn` exports a 2-class CNN checkpoint (`n_classes=2`).

## Current model_server Difference

- Previous config loaded `stage1_v001` with `stage1_mode: checkpoint`.
- `model_server/src/model_adapter/stage1_adapter.py` previously required `model.pkl` and applied learned logistic-regression weights.
- That does not match the verified Stage 1 threshold detector design.
- The server has been updated to support `stage1_mode: threshold` with `stage1_threshold_config.json` and no required `model.pkl`.
- Stage 2 server mode is set to `disabled`; it returns skipped for Stage 1 empty windows and unknown for occupied windows until a static/moving CNN is connected.

## README Conflicts

- `model_server/README.md` describes exporting and loading `stage1_v001/model.pkl` with `stage1_mode: checkpoint`.
- The verified `csi_pipeline` Stage 1 code defines a threshold detector. Therefore the README is stale for final pipeline design and only describes the legacy server integration artifact.

## stage1_v001 Decision

`stage1_v001` should not continue as the final Stage 1 artifact. It is marked legacy because it is a logistic-regression server integration proof-of-concept. It is preserved at `model_server/artifacts/models/stage1/stage1_v001`.

## New Artifacts

- Stage 1 threshold artifact: `model_server/artifacts/models/stage1/stage1_threshold_v001`
- Stage 1 manifest entries: 110
- Stage 2 manifest entries: 87

## Global Stage 1 Metrics

| split | threshold | macro_f1 | empty_recall | occupied_recall | static_recall | moving_recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 2.506834 | 0.750031 | 0.458799 | 0.979427 | 0.983100 | 0.974660 |
| validation | 2.506834 | 0.770115 | 0.463483 | 0.998946 | 1.000000 | 0.997561 |
| test | 2.506834 | 0.855526 | 0.667732 | 0.982122 | 0.968288 | 1.000000 |

## Target-Room Calibrated Stage 1 Metrics

Protocol: S01 empty baseline, S04 first 20% empty score quantile threshold, calibration windows excluded from test.

| target_env | selected_threshold | macro_f1 | empty_recall | occupied_recall | static_recall | moving_recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| room4_2026-05-20 | 1.394755 | 0.753737 | 0.977032 | 0.584022 | 0.859504 | 0.308540 |
| room4_2026-05-27 | 8.804143 | 0.904545 | 0.994872 | 0.844080 | 0.986111 | 0.698337 |

## Code-Path Guided Baseline Comparison

Protocol: S01 baseline plus S04 first 20% guided baseline merge, global threshold retained, calibration windows excluded from test. This matches the direction implemented in `csi_pipeline.stage1.calibration.build_stage1_test_table` more closely than pure empty-quantile thresholding.

| target_env | selected_threshold | macro_f1 | empty_recall | occupied_recall | static_recall | moving_recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| room4_2026-05-20 | 2.506834 | 0.975765 | 1.000000 | 0.957300 | 0.925620 | 0.988981 |
| room4_2026-05-27 | 2.506834 | 0.975595 | 0.982906 | 0.971864 | 0.995370 | 0.947743 |

## Required Work To Return To Original Design

- Keep `stage1_v001` only as legacy.
- Use `stage1_threshold_v001` as the Stage 1 artifact.
- Keep baseline creation outside `model_server`; the server must receive preprocessed windows or calibration-profile inputs.
- Connect a true Stage 2 static/moving CNN before enabling `stage2_mode: static_moving_cnn`.
- Do not wire risk-level categories into Stage 2. Risk scoring remains a later pipeline stage.

## Files Not To Delete

- `model_server/artifacts/models/stage1/stage1_v001/model.pkl`
- `model_server/artifacts/models/stage1/stage1_v001/*.json`
- `dataset/**`

## Legacy Files

- `model_server/artifacts/models/stage1/stage1_v001`
- `model_server/scripts/train_stage1_model.py`

## Newly Required Artifact Files

- `stage1_threshold_config.json`
- `feature_config.yaml`
- `label_mapping.json`
- `metrics_global.json`
- `metrics_loro_calibrated.json`
- `calibration_policy.json`
- `metadata.json`
