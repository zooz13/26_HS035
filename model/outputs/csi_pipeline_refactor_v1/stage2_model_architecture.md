# Stage-2 Model Architecture

Stage-2 is an occupied-only static/moving classifier.

Input shape:

```text
[batch, window_size, n_features]
default = [batch, 128, 192]
```

The model transposes input to Conv1d layout:

```text
[batch, n_features, window_size]
```

Architecture:

```text
Conv1d(n_features -> 32, kernel_size=5, padding=2)
BatchNorm1d(32)
ReLU
MaxPool1d(2)
Conv1d(32 -> 64, kernel_size=5, padding=2)
BatchNorm1d(64)
ReLU
AdaptiveAvgPool1d(1)
Flatten
Dropout(0.2)
Linear(64 -> 2)
```

Labels:

```text
static_person -> 0
moving_person -> 1
```

The implementation lives in `csi_pipeline/training/stage2.py` as `TinyCnn`.

