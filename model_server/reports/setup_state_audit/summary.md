# Room Setup State Audit

Scope: code-only audit of room setup state/status behavior before debugging Gateway integration assumptions.

No source code, tests, docs, API behavior, risk rules, or model artifacts were changed.

## Files Checked

- `src/model_server/room_manager.py`
- `src/model_server/calibration_manager.py`
- `src/api/server.py`
- `src/common/types.py`
- `src/storage/state_store.py`
- `scripts/check_full_api_flow.py`
- `tests/test_model_server_contract.py`
- `docs/api.md`

## Status Strings

There is no central enum. `RoomState.status` is typed as a plain `str` in `src/common/types.py`.

Observed status strings in code:

- `uncalibrated`
- `baseline_collecting`
- `baseline_ready`
- `calibration_collecting`
- `ready`
- `running`
- `error`

`error` appears in the setup-status message map and docs as a reserved state, but no checked code path sets it.

## A. State Transition Table

| API/action | previous status | next status | baseline_id set? | calibration_id set? | realtime allowed? |
| --- | --- | --- | --- | --- | --- |
| `POST /api/rooms` | none | `uncalibrated` | no | no | no |
| `POST /api/rooms/{room_id}/baseline/start` | any existing room status; no status gate | `baseline_collecting` | unchanged | unchanged | no |
| `POST /api/rooms/{room_id}/baseline/finish` | any existing room status; no status gate | `baseline_ready` | yes, set to request `baseline_id` | not created; existing value is preserved if one already existed | no |
| `POST /api/rooms/{room_id}/calibration/start` | `baseline_ready`, `ready`, or `running` only | `calibration_collecting` | unchanged; must match request `baseline_id` | unchanged; may remain `None` in first setup or preserve previous calibration during recalibration | no |
| `POST /api/rooms/{room_id}/calibration/window` | no room status change | unchanged | unchanged | unchanged | no unless room was already `ready`/`running`, but active calibration status should normally keep room at `calibration_collecting` |
| `POST /api/rooms/{room_id}/calibration/finish` | usually `calibration_collecting`; code validates session, not room status directly | `ready` | unchanged from session baseline | yes, set to generated `calib_{room_id}_vNNN` | yes |
| first successful `POST /api/rooms/{room_id}/window` | `ready` | `running` | unchanged | unchanged | yes |
| later successful `POST /api/rooms/{room_id}/window` | `running` | `running` | unchanged | unchanged | yes |

## B. Realtime Gating Table

`POST /api/rooms/{room_id}/window` calls `infer_room_window()` in `src/api/server.py`.

| status | `/api/rooms/{room_id}/window` allowed? | reason |
| --- | --- | --- |
| `uncalibrated` | no | `infer_room_window()` requires `room.status in {"ready", "running"}` |
| `baseline_collecting` | no | same status gate |
| `baseline_ready` | no | same status gate; baseline alone is not enough |
| `calibration_collecting` | no | same status gate |
| `ready` | yes | passes status gate, then validates device group, baseline id, calibration id, baseline profile, calibration profile, data type, feature version |
| `running` | yes | same as `ready`; successful inference keeps room in `running` |
| `error` | no | same status gate |
| unknown string | no | same status gate |

Additional `/window` requirements after the status gate:

- `device_group_id` must match the room.
- `baseline_id` must match the room.
- `calibration_id` must match the room.
- `data_type` must be `stage1_feature_vector`.
- The baseline profile must exist in `StateStore`.
- The request `feature_version` must match the registered baseline profile.
- The calibration profile must exist in `StateStore`.

If the room status is `ready`/`running` but the calibration profile cannot be found, the server raises HTTP 400 with `calibration profile not found`.

## Endpoint Details

### `POST /api/rooms`

`RoomManager.create_room()` creates a `RoomState` with:

- `status = "uncalibrated"`
- `baseline_id = None`
- `calibration_id = None`

### `POST /api/rooms/{room_id}/baseline/start`

`RoomManager.start_baseline()` sets:

- `status = "baseline_collecting"`
- `updated_at = now`

It does not set `baseline_id` or `calibration_id`.

### `POST /api/rooms/{room_id}/baseline/finish`

`RoomManager.finish_baseline()` creates and stores a `BaselineProfile`, then sets:

- `status = "baseline_ready"`
- `baseline_id = profile.baseline_id`
- `updated_at = now`

It does not create a calibration session or calibration profile. It does not set a new `calibration_id`.

The API response from `src/api/server.py` includes:

- `room_id`
- `baseline_id`
- `status = "baseline_ready"`
- `message`

It does not include `calibration_id`.

Normal first-time setup result: `calibration_id` remains `None`.

Edge case: because `finish_baseline()` uses `room.copy(update={...})`, any pre-existing `calibration_id` on the room would be preserved unless a separate flow clears it.

### `POST /api/rooms/{room_id}/calibration/start`

`CalibrationManager.start_calibration()` requires:

- `room.baseline_id == request.baseline_id`
- `room.status in {"baseline_ready", "ready", "running"}`
- supported method: `guided_baseline_merge`, `empty_quantile_threshold`, or `no_calibration`

It creates and stores a `CalibrationSession`, then sets room:

- `status = "calibration_collecting"`
- `updated_at = now`

It does not overwrite `baseline_id`.
It does not overwrite or clear an existing `calibration_id`.

### `POST /api/rooms/{room_id}/calibration/finish`

`CalibrationManager.finish_calibration()` validates the active session, enough windows, and method match. It then creates a `CalibrationProfile`, stores it in `StateStore.calibration_profiles`, marks the session `ready`, and sets room:

- `status = "ready"`
- `calibration_id = profile.calibration_id`
- `updated_at = now`

This is the first normal setup step that makes a newly created room realtime-ready.

### `GET /api/rooms/{room_id}/setup/status`

`RoomManager.setup_status()` returns:

- `room_id`
- `status`
- `baseline_id`
- `calibration_id`
- `stage1_artifact_id`
- `stage2_artifact_id`
- `stage2_mode`
- `message`

Gateway can use this response to decide realtime readiness if it treats only `status == "ready"` or `status == "running"` as realtime-capable and confirms `baseline_id` and `calibration_id` are present.

`baseline_ready` means baseline profile exists but calibration is still required. It is not realtime-ready under current `/window` gating.

## Existing Validation Assumptions

`scripts/check_full_api_flow.py` uses `setup_room()` to run:

1. `POST /api/rooms`
2. `baseline/start`
3. `baseline/finish`
4. `calibration/start`
5. `calibration/window`
6. `calibration/finish`
7. `setup/status`

It asserts `setup_status["status"] == "ready"` only after `calibration/finish`.

`tests/test_model_server_contract.py` also prepares room inference by calling `_prepare_ready_room()`, which performs baseline finish plus calibration start/window/finish before calling `/api/rooms/{room_id}/window`.

No checked test currently asserts that `/window` fails immediately after `baseline/finish` only. The code path would fail at the status gate with `room must be ready or running before inference`.

## Docs Alignment

`docs/api.md` matches the normal code flow:

- Room state transitions list `uncalibrated -> baseline_collecting -> baseline_ready -> calibration_collecting -> ready -> running`.
- Example scenario calls `calibration/finish`, then confirms `setup/status` is `ready`, then posts realtime `window`.
- `baseline/finish` is documented as registering a baseline profile, not as making the room ready for inference.

No docs/code conflict was found for the normal setup flow.

## Findings

Conclusion: case 2 applies.

`calibration/finish` 이후에만 `ready`가 된다.

The current server pipeline is calibration-mandatory for normal room-scoped realtime inference. `baseline/finish` sets `baseline_ready`, stores `baseline_id`, and stores a `BaselineProfile`, but it does not create or store `calibration_id`, and `/api/rooms/{room_id}/window` does not allow `baseline_ready`.

This means:

- If Gateway expects realtime to start immediately after `baseline/finish`, that expectation conflicts with the current server pipeline.
- If Gateway sees `baseline_ready` and interprets it as realtime-ready, this is a status semantics mismatch.
- If Gateway only starts realtime after `setup/status.status == "ready"` and a non-null `calibration_id`, it matches current server behavior.

This does not look like a server runtime gating bug in the normal flow. It looks like a communication/interpretation mismatch unless the intended operating policy is explicitly "baseline-only realtime without calibration." If that policy is desired, it would require a future API/behavior change, not just a Gateway fix.
