# CSI Pipeline

This is the standalone refactor folder for the Wi-Fi CSI workflow.

The code is intentionally split by workflow responsibility:

- `csi_pipeline/preprocessing/`: parse CSI, convert I/Q to amplitude, window packets, normalize, apply gain correction
- `csi_pipeline/training/`: train Stage-1 threshold detectors and Stage-2 static/moving CNN candidates
- `csi_pipeline/evaluation/`: evaluate Stage-1 empty/occupied and Stage-2 static/moving results

The historical validation code still lives under `../model/scripts` and `../model/src`. The internal `_legacy.py` bridge points there so the new modules can reproduce the validated behavior while this package is being separated.

