# CSI Model Server

FastAPI skeleton for the CSI model server contract.

This repository contains only the model-server contract, dummy inference pipeline,
memory state store, API server, scripts, configs, and contract tests.

## API Documentation

Room setup and real-time inference APIs are documented in
[`docs/api.md`](docs/api.md).
Residual presence risk assessment is documented in
[`docs/risk_assessment.md`](docs/risk_assessment.md).

The current default Stage 1 server artifact is `stage1_threshold_v001`, a
threshold detector. The older `stage1_v001/model.pkl` checkpoint is preserved as
a legacy server-integration artifact, not the final Stage 1 pipeline.
Stage 2 is connected through `stage2_static_moving_v001`, an occupied-only
static/moving CNN that expects a `[128, 192]` `window_matrix` as `stage2_input`
on room-scoped `/window` requests.

## Current Validation Status

- Stage2 integration: passed
- Stage2 model quality: conditionally passed

Known limitations:

- `room4_2026-05-20` moving recall is degraded in Stage 2 API replay.
- The degradation is a conditional Stage 2 static/moving classifier issue, not mainly a Stage 1 miss issue.
- The current checkpoint provenance includes `room5_2026-05-08` in `train_envs`.

See [`reports/stage2_api_replay/summary.md`](reports/stage2_api_replay/summary.md).

## Install

From this directory:

```powershell
& "C:\Users\zoo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If `python` is correctly installed on PATH, this also works:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If `python` only prints `Python`, Windows is launching the Store alias instead of
a real interpreter. Use the full Python path above or disable the Python app
execution alias in Windows settings.

## Install Without Venv

```powershell
python -m pip install -r requirements.txt
```

## Run Server

```powershell
& .\.venv\Scripts\python.exe scripts\run_model_server.py
```

The server defaults to `http://127.0.0.1:8000`.

## Legacy Train Stage 1

The legacy training wrapper reads `..\dataset` and writes artifacts under
`artifacts\models\stage1\stage1_v001`.

```powershell
& "C:\Users\zoo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" scripts\train_stage1_model.py
```

The exported checkpoint is `artifacts\models\stage1\stage1_v001\model.pkl`.
This path is kept for comparison only. The default server config uses
`artifacts\models\stage1\stage1_threshold_v001` with `stage1_mode: threshold`.
Stage 2 is configured separately with `stage2_mode: static_moving_cnn` and
`stage2_artifact_path`.

## Send Sample Window

Open a second terminal in this directory:

```powershell
& .\.venv\Scripts\python.exe scripts\test_send_window.py
```

## Test

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_model_server_contract.py
```
