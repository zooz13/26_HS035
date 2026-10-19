# CSI Pipeline Refactor V1

## 1. Created Structure

Created `model/csi_pipeline/` around the intended workflow modules: `preprocessing`, `training`, and `evaluation`.

## 2. Stage-1 Behavior

Stage-1 is a baseline-relative threshold detector, not a CNN. It computes baseline-relative features and uses `robust_max_shape = max(static_score, motion_score, shape_score)`. The default threshold policy is `balanced_macro`.

## 3. Stage-2 Behavior

Stage-2 is an occupied-only static/moving candidate. It keeps the existing `TinyCnn` model shape and default `packet_mean_normalized` preprocessing.

## 4. Preprocessing Modules

The refactor exposes raw, packet/window mean, packet/window z-score, L2, and gain div/mul variants. Real-time use should prefer preprocessing that only depends on the current packet/window and available gain metadata.

## 5. Training/Evaluation Artifacts

Training/evaluation helpers build Stage-1 feature tables, fit threshold detectors, build Stage-2 arrays, train CNN candidates, and compute metrics.

## 6. Existing Result Reproduction

Existing validated result files are preserved under `model/outputs/room4_2026-05-27_full_validation_stage2_v1/`. This refactor adds wrappers and summary files under `model/outputs/csi_pipeline_refactor_v1/` for side-by-side audit.

## 7. Reproduction Failure Causes

If reproduced metrics differ, check windowing, preprocessing location, threshold selection, baseline merge method, seed, and train/validation split first.

## 8. Next Work

Run the training/evaluation entrypoints end-to-end and compare CSV values against the existing validated output.
