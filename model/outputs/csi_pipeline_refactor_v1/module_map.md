# Module Map

| Area | Module | Purpose |
| --- | --- | --- |
| shared | `csi_pipeline/data.py` | Dataset discovery, session loading, label metadata, and row-duration helpers used by both stages |
| preprocessing | `csi_pipeline/preprocessing/*.py` | CSI parsing, amplitude extraction, gain correction, normalization, preprocessing dispatch, and window indexing |
| stage1 | `csi_pipeline/stage1/baseline.py` | Stage-1 segment slicing, gain-aware preprocessing, calibration baseline statistics, and normalized baseline construction |
| stage1 | `csi_pipeline/stage1/calibration.py` | Stage-1 calibration row selection, guided/S04/oracle baseline merge policy, and test-table construction |
| stage1 | `csi_pipeline/stage1/scoring.py` | `baseline_plus_shape` feature calculation and `robust_max_shape` score calculation |
| stage1 | `csi_pipeline/stage1/threshold.py` | Binary metric calculation, threshold candidate generation, and `balanced_macro` threshold selection |
| stage1 | `csi_pipeline/stage1/train.py` | Stage-1 training-table construction and detector fitting |
| stage1 | `csi_pipeline/stage1/evaluate.py` | Stage-1 evaluation entry points kept as callable functions only |
| stage1 | `csi_pipeline/stage1/inference.py` | Stage-1 detector application and probability/prediction columns |
| stage2 | `csi_pipeline/stage2/dataset.py` | Stage-2 occupied-window array and metadata construction |
| stage2 | `csi_pipeline/stage2/model.py` | `TinyCnn` small CNN architecture |
| stage2 | `csi_pipeline/stage2/train.py` | Stage-2 CNN training loop, validation split, and `.pt` checkpoint export |
| stage2 | `csi_pipeline/stage2/evaluate.py` | Stage-2 static/moving metric calculation kept as callable functions only |
| stage2 | `csi_pipeline/stage2/inference.py` | Stage-2 CNN prediction helper |
| experiments | `csi_pipeline/experiments/reproduction.py` | Optional reproduction entry point composed from Stage-1 and Stage-2 modules; no code runs on import |
| configs | `csi_pipeline/configs/` | Configuration location reserved for pipeline settings |

Removed source modules:

| Old Module | New Location |
| --- | --- |
| `csi_pipeline/training/stage1.py` | Split across `csi_pipeline/stage1/*.py` |
| `csi_pipeline/evaluation/stage1.py` | `csi_pipeline/stage1/evaluate.py` |
| `csi_pipeline/training/stage2.py` | Split across `csi_pipeline/stage2/*.py` |
| `csi_pipeline/evaluation/stage2.py` | `csi_pipeline/stage2/evaluate.py` |
| `csi_pipeline/evaluation/reproduction.py` | `csi_pipeline/experiments/reproduction.py` |
