# CSI Pipeline Modules

This package separates the Wi-Fi CSI workflow into three responsibilities:

- `preprocessing/`: CSI parsing, amplitude conversion, gain correction, normalization
- `training/`: Stage-1 threshold detector fitting and Stage-2 CNN training
- `evaluation/`: Stage-1 occupancy metrics and Stage-2 static/moving metrics

This package no longer calls `../model/scripts` or `../model/src/csi_model`.

## Stage-1

Stage-1 is not a learned neural model. It is a calibration-aware, baseline-relative threshold detector:

1. Convert raw I/Q CSI packets to amplitude.
2. Apply the same preprocessing to the empty baseline and current window.
3. Compare the current window to the empty baseline.
4. Compute `robust_max_shape = max(static_score, motion_score, shape_score)`.
5. Predict occupied when the score is greater than or equal to the fitted threshold.

The default baseline is S01 first 20 seconds plus the S04 guided 20 percent empty prefix. The S04 prefix is excluded from evaluation.

### Score Formula

For a preprocessed window `W` and an empty baseline `B`:

```text
z_abs = abs(median(W, time) - B.median) / B.robust_scale
static_score = percentile(z_abs, 90)
motion_score = max(
  mean(abs(diff(W, time))) / B.diff_abs_p95_scale,
  mean(std(W, time)) / B.temporal_std_p95_scale
)
shape_score = max(0, 1 - corr(zscore(mean(W, time)), B.baseline_shape))
robust_max_shape = max(static_score, motion_score, shape_score)
pred = occupied if robust_max_shape >= threshold else empty
```

### Threshold Policy

`balanced_macro` searches quantile candidates from training scores and selects:

```text
minimize (-macro_f1, empty_false_alarm_rate, occupied_miss_rate)
```

## Stage-2

Stage-2 is an occupied-only static/moving candidate. It uses a small 1D CNN checkpoint and predicts:

- `static_person -> 0`
- `moving_person -> 1`

The default Stage-2 preprocessing is `packet_mean_normalized`. Stage-2 remains a candidate artifact until more rooms/dates validate it.

## Preprocessing

Implemented preprocessing names:

- `raw`
- `packet_mean_normalized`
- `window_mean_normalized`
- `packet_zscore_normalized`
- `window_zscore_normalized`
- `l2_normalized`
- `gain_div_corrected`
- `gain_mul_corrected`
- `gain_div_packet_mean`
- `gain_mul_packet_mean`
- `gain_div_l2`
- `gain_mul_l2`

`gain_mul_corrected` is the current Stage-1 service candidate for room4_2026-05-27, but the correction direction is not globally proven for every room/date.

## Workflow

1. Preprocess raw CSI into amplitude windows.
2. Train Stage-1 baseline-relative threshold detector.
3. Train Stage-2 occupied-only static/moving CNN candidate.
4. Evaluate Stage-1 empty/occupied performance.
5. Evaluate Stage-2 oracle/cascaded static/moving performance.

## Outputs

Stage-1 training/evaluation produces:

- baseline-relative feature table
- fitted threshold detector config
- threshold and train-threshold metrics
- empty/occupied evaluation metrics

Stage-2 training/evaluation produces:

- occupied-only static/moving arrays
- CNN checkpoint
- static/moving metrics
- confusion matrix

## Cautions

- Do not use raw amplitude alone for decisions.
- Baseline and current windows must be compared in the same preprocessing space.
- room4 is excluded from room4-excluded training protocols.
- `gain_mul_corrected` is the current candidate, not a universal conclusion.
- Existing scripts are retained outside this package for audit and reproduction.
