# Cleanup Candidates For Internal Staging

Scope: staging-friendly repository cleanup after the model server passed full API integration checks.

This report intentionally favors `.gitignore` and candidate classification over deletion. No source code, scripts, configs, docs, reports, outputs, tests, notebooks, or model artifacts were deleted.

## Automatic Cleanup Performed

Only obvious cache files/directories were removed:

- `.pytest_cache/`
- `src/**/__pycache__/`
- `scripts/__pycache__/`
- `tests/__pycache__/`
- `*.pyc` files contained in those cache directories

`.venv/` was not touched; it is ignored by `.gitignore`.

## Reference Check Summary

- `scripts/check_full_api_flow.py` is referenced by `docs/staging_deployment.md`.
- `scripts/run_model_server.py` is referenced by `README.md` and `docs/staging_deployment.md`.
- `scripts/replay_stage1_api.py` and `scripts/replay_stage2_api.py` are preserved for regression/replay validation.
- `motion_input` and compatibility `stage2_input` are referenced by docs, tests, full API check, replay scripts, adapter/server code, and reports.
- `risk_assessment` is referenced by server code, tests, docs, OpenAPI, and full API check.
- `stage1_threshold_v001` is referenced by `configs/model_server.yaml`, code defaults, tests, docs, replay scripts, reports, and artifacts.
- `stage2_static_moving_v001` is referenced by `configs/model_server.yaml`, server/adapter code, tests, docs, replay scripts, reports, and artifacts.
- Public compact `stage3` naming scan found only test/check code that prevents such naming; no public endpoint or file uses `stage3`.

## Candidate Table

| Path | Category | Reason | Referenced by code/config/docs/tests? | Risk | Recommendation |
| --- | --- | --- | --- | --- | --- |
| `.gitignore` | keep | Staging-friendly ignore rules for local/generated files. | yes | low | keep |
| `.venv/` | ignore | Local virtual environment, reproducible from `requirements.txt`. | no | low | ignore via `.gitignore`; do not delete in this task |
| `src/**` | keep | Runtime API, model adapters, state store, risk assessment, shared types. | yes | high | keep |
| `tests/**` | keep | Protects current API contract, risk behavior, and history cap. | yes | high | keep |
| `configs/model_server.yaml` | keep | Runtime config for model paths and state store. | yes | high | keep |
| `configs/risk_assessment.yaml` | keep | Runtime risk policy config. | yes | high | keep |
| `docs/api.md` | keep | Public API contract documentation. | yes | high | keep |
| `docs/openapi.json` | keep | Generated public API schema required by staging contract. | yes | high | keep |
| `docs/risk_assessment.md` | keep | Risk Assessment behavior documentation. | yes | high | keep |
| `docs/staging_deployment.md` | keep | Internal staging runbook. | yes | high | keep |
| `docs/pipeline_design_verification_report.md` | keep | Documents Stage 1 artifact decision and legacy `stage1_v001` status. | yes | medium | keep for provenance |
| `README.md` | keep | Repository overview and run instructions. | yes | medium | keep |
| `requirements.txt` | keep | Reproducible dependency install input. | yes | high | keep |
| `scripts/check_full_api_flow.py` | keep | Required staging integration validation. | yes | high | keep |
| `scripts/run_model_server.py` | keep | Required staging server entrypoint. | yes | high | keep |
| `scripts/replay_stage1_api.py` | keep | Regression/replay validation for Stage 1 API behavior. | yes | medium | keep |
| `scripts/replay_stage2_api.py` | keep | Regression/replay validation for Stage 2 API behavior. | yes | medium | keep |
| `scripts/test_room_setup_flow.py` | keep | Manual/API smoke validation helper for room setup flow. | unknown | medium | keep for now |
| `scripts/test_send_window.py` | needs_approval | Older manual single-window sender; may overlap with newer full API check. | unknown | medium | needs approval before deletion |
| `scripts/train_stage1_model.py` | needs_approval | Training script is not needed for staging runtime but may preserve reproducibility. | unknown | medium | needs approval before deletion |
| `scripts/build_stage_pipeline_artifacts.py` | keep | Reproducibility/provenance script for exported artifacts and reports. | yes | medium | keep for now |
| `reports/full_api_check/summary.md` | keep | Required current full API validation report. | yes | high | keep |
| `reports/full_api_check/summary.json` | keep | Required machine-readable full API validation report. | yes | high | keep |
| `reports/stage2_api_replay/summary.md` | keep | Required Stage 2 replay validation summary. | yes | high | keep |
| `reports/stage2_api_replay/summary.json` | needs_approval | Useful replay detail, but not explicitly required by staging policy. | yes | medium | needs approval before deletion |
| `reports/stage2_api_replay/*_metrics.json` | needs_approval | Replay metrics are reproducibility artifacts; not runtime inputs. | yes | medium | needs approval before deletion |
| `reports/stage2_api_replay/api_replay_debug_log.jsonl` | needs_approval | Replay debug log; useful for audit but not runtime input. | yes | medium | needs approval before deletion |
| `reports/stage1_api_replay/summary.md` | needs_approval | Stage 1 replay summary; useful validation/provenance but not required by current staging policy. | yes | medium | needs approval before deletion |
| `reports/stage1_api_replay/summary.json` | needs_approval | Stage 1 replay machine-readable summary; not runtime input. | yes | medium | needs approval before deletion |
| `reports/stage1_api_replay/*/*_metrics.json` | needs_approval | Per-room replay metrics; reproducibility artifact but not runtime input. | yes | medium | needs approval before deletion |
| `reports/stage1_api_replay/api_replay_debug_log.jsonl` | needs_approval | Very large replay debug log, about 1.03 GB; not runtime input but may be valuable audit data. | yes | medium | strong candidate for later deletion or external archival, approval required |
| `reports/cleanup/cleanup_candidates.md` | keep | This cleanup classification report. | yes | low | keep |
| `outputs/`, `outputs_*/` | ignore | Generated/local outputs if created in future. | no | low | ignore via `.gitignore` |
| `tmp/`, `scratch/`, `local_runs/`, `run_outputs/`, `logs/` | ignore | Local run artifacts if created in future. | no | low | ignore via `.gitignore` |
| `*.log`, `*.tmp`, `*.bak`, `*~`, `.DS_Store` | ignore | Local/editor/OS temporary files. | no | low | ignore via `.gitignore` |
| `*.sqlite`, `*.db` | ignore | Local database files are not part of current staging runtime. | no | low | ignore via `.gitignore` |
| `artifacts/models/stage1/stage1_threshold_v001/` | keep | Required default Stage 1 threshold artifact. | yes | high | keep; do not delete |
| `artifacts/models/stage2/stage2_static_moving_v001/` | keep | Required default Stage 2 static/moving artifact. | yes | high | keep; do not delete |
| `artifacts/models/stage1/stage1_v001/` | keep | Legacy Stage 1 logistic-regression artifact preserved for provenance/comparison. | yes | medium | keep; do not delete in this task |

## Artifact Directory Assessment

| Artifact directory | Referenced by config? | Referenced by docs? | Referenced by code/tests? | Likely role | Judgment |
| --- | --- | --- | --- | --- | --- |
| `artifacts/models/stage1/stage1_threshold_v001/` | yes | yes | yes | Current default Stage 1 threshold detector | Required; unsafe to delete |
| `artifacts/models/stage2/stage2_static_moving_v001/` | yes | yes | yes | Current default Stage 2 occupied-only static/moving CNN | Required; unsafe to delete |
| `artifacts/models/stage1/stage1_v001/` | no | yes | yes, indirectly via scripts/docs | Legacy logistic-regression proof-of-concept artifact | Keep for provenance; deletion requires explicit approval |

## `.gitignore` Policy Added

The repository now ignores:

- Python caches: `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
- Local envs: `.env`, `.venv/`, `venv/`
- Coverage/build-ish outputs: `.coverage`, `htmlcov/`
- Local/generated outputs: `outputs/`, `outputs_*/`, `tmp/`, `scratch/`, `local_runs/`, `run_outputs/`, `logs/`
- Local databases and temporary files: `*.sqlite`, `*.db`, `*.log`, `*.tmp`, `*.bak`, `*~`, `.DS_Store`

The repository does not broadly ignore `reports/` or `artifacts/`, so required validation reports and model artifacts remain visible.
