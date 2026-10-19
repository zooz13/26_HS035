# Stage-1 Score Pseudocode

Stage-1 is a baseline-relative threshold detector.

```text
input:
  W: current preprocessed CSI window, shape [time, subcarrier]
  B: empty baseline built in the same preprocessing space

baseline:
  B.median = median(empty_calibration_packets, axis=time)
  B.robust_scale = max(MAD * 1.4826, IQR / 1.349, global_floor)
  B.baseline_shape = zscore(mean(empty_calibration_packets, axis=time))
  B.diff_abs_p95_scale = p95(mean(abs(diff(empty_window)), axis=all_windows))
  B.temporal_std_p95_scale = p95(mean(std(empty_window)), axis=all_windows)

window features:
  med = median(W, axis=time)
  profile = mean(W, axis=time)
  z_abs = clip(abs(med - B.median) / B.robust_scale, 0, 20)

  static_score = percentile(z_abs, 90)

  diff_abs_mean = mean(abs(diff(W, axis=time)))
  temporal_std_mean = mean(std(W, axis=time))
  motion_score = max(
    diff_abs_mean / B.diff_abs_p95_scale,
    temporal_std_mean / B.temporal_std_p95_scale
  )

  window_shape = zscore(profile)
  shape_corr = corr(window_shape, B.baseline_shape)
  shape_score = max(0, 1 - shape_corr)

final score:
  robust_max_shape = max(static_score, motion_score, shape_score)

prediction:
  occupied if robust_max_shape >= threshold
  empty otherwise
```

The implementation lives in `csi_pipeline/training/stage1.py` as `baseline_plus_shape_features`, `robust_max_shape_score`, and `apply_detector`.

