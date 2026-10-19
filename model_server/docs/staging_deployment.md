# Internal Staging Deployment

This document prepares `model_server` for an internal staging server before dashboard and hardware integration.

This staging run does not retrain models, modify model artifacts, implement false-alarm evaluation, add database persistence, or add authentication/HTTPS.

## 1. Install Dependencies

From the `model_server` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On Linux:

```bash
python -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

## 2. Config And Paths

Default config files:

- `configs/model_server.yaml`
- `configs/risk_assessment.yaml`

Model artifact paths in `configs/model_server.yaml` are repo-relative by default:

```yaml
model:
  stage1_artifact_path: artifacts/models/stage1/stage1_threshold_v001
  stage2_artifact_path: artifacts/models/stage2/stage2_static_moving_v001
```

The server resolves relative artifact paths from the `model_server` repo root. Windows absolute paths are not required. If staging uses a different artifact location, set the YAML value to either a staging-local relative path under `model_server` or an explicit absolute path managed by the staging host.

Current in-memory state config:

```yaml
state_store:
  backend: memory
  max_room_result_history: 1000
```

`max_room_result_history` bounds stored room results per room. When the cap is exceeded, oldest results are dropped first.

## 3. Start The Server

For local-only staging:

```powershell
.\.venv\Scripts\python.exe scripts\run_model_server.py --host 127.0.0.1 --port 8000
```

For an internal staging network, bind to the staging interface chosen by infra:

```powershell
.\.venv\Scripts\python.exe scripts\run_model_server.py --host 0.0.0.0 --port 8000
```

Use INFO log level in the process supervisor. The server logs compact integration events such as room creation, baseline completion, calibration completion, room-window labels, and risk-assessment results. It does not log full CSI payloads or full motion matrices.

## 4. Initial Room Setup Flow

For initial staging setup:

1. `POST /api/rooms`
2. `POST /api/rooms/{room_id}/baseline/start`
3. Gateway or Hardware/Preprocessor collects empty-room CSI.
4. `POST /api/rooms/{room_id}/baseline/finish`
5. Model Server creates a default calibration profile using the global Model Pipeline Stage 1 threshold.
6. Room status becomes `ready`.
7. Gateway stores the returned `baseline_id` and `calibration_id`.
8. Gateway can start realtime `POST /api/rooms/{room_id}/window`.

Threshold recalibration is optional and can be performed later with:

1. `POST /api/rooms/{room_id}/calibration/start`
2. repeated `POST /api/rooms/{room_id}/calibration/window`
3. `POST /api/rooms/{room_id}/calibration/finish`

After optional recalibration finishes, Gateway should replace the stored `calibration_id` with the new value returned by `calibration/finish`.

## 5. Health Check

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Expected response:

```json
{
  "status": "ok",
  "server": "running"
}
```

## 6. Full API Integration Check

Run this from `model_server`:

```powershell
.\.venv\Scripts\python.exe scripts\check_full_api_flow.py
```

Expected output after staging preparation:

```json
{
  "status": "staging_ready",
  "pass": 9,
  "warn": 0,
  "fail": 0,
  "report": "reports\\full_api_check\\summary.md"
}
```

If `FAIL` is nonzero, do not proceed with dashboard/hardware integration until the failing scenario is fixed. A `WARN` result means the API flow ran but a staging caveat was detected and should be reviewed.

## 7. Validation Commands

```powershell
.\.venv\Scripts\python.exe -m compileall src scripts tests
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe scripts\check_full_api_flow.py
```

## 8. Known Staging Caveats

- State is process-local memory. Room setup state and result history are lost when the process restarts.
- Room result history is bounded, not durable. Default cap is 1000 recent results per room.
- Authentication and HTTPS are intentionally out of scope for this internal staging task.
- False-alarm/disaster-event validation is intentionally out of scope.
- Dashboard should send `motion_input` for occupied-window motion classification. `stage2_input` is accepted only as a deprecated compatibility alias.
