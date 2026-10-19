# Service Artifact Export

The package exports the Stage-2 CNN checkpoint directly from `csi_pipeline`.

Checkpoint path used by default reproduction:

```text
outputs/csi_pipeline_refactor_v1/stage2_small_cnn_packet_mean_normalized.pt
```

Checkpoint contents:

```text
state_dict
model_class = TinyCnn
n_features
n_classes
label_map
preprocessing_type
segment
window_size
stride
train_envs
seed
best_epoch
curve
device_used_for_export_training
train_time_sec
train_windows_total
num_train_windows
num_valid_windows
```

Export functions:

```text
csi_pipeline.training.stage2.save_stage2_checkpoint
csi_pipeline.training.stage2.train_and_save_stage2_checkpoint
csi_pipeline.evaluation.reproduction.run_end_to_end_room4_2026_05_27
```

Stage-1 artifacts are represented by the returned detector config:

```text
detector_name = robust_max_shape
feature_set = baseline_plus_shape
threshold
train_threshold_metrics
```

