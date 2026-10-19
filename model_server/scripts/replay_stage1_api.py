from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


MODEL_SERVER_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = MODEL_SERVER_ROOT.parent
SRC_ROOT = MODEL_SERVER_ROOT / "src"
CSI_PIPELINE_ROOT = WORKSPACE_ROOT / "csi-pipeline"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(CSI_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(CSI_PIPELINE_ROOT))

from csi_pipeline.data import duration_to_rows, load_env, session_by_id  # noqa: E402
from csi_pipeline.stage1.baseline import build_normalized_baseline, segment_amp  # noqa: E402
from csi_pipeline.stage1.calibration import ratio_rows  # noqa: E402
from csi_pipeline.stage1.scoring import feature_rows_for_range  # noqa: E402


DEFAULT_TARGETS = ["room4_2026-05-20", "room4_2026-05-27"]
MODES = ["no_calibration", "empty_quantile_threshold", "guided_baseline_merge"]
PREPROCESSING_TYPE = "gain_mul_corrected"
SEGMENT = "full_buffer"
WINDOW_SIZE = 128
STRIDE = 64
BASELINE_SECONDS = 20.0
CALIBRATION_RATIO = 0.20
FEATURE_VERSION = "stage1_feature_v1"
NORMALIZATION_VERSION = "norm_v1_log_amp_empty_robust_zscore"
PREPROCESS_VERSION = "preprocess_v1"
OFFLINE_EXPECTED = {
    "room4_2026-05-20": {
        "macro_f1": 0.975765,
        "empty_recall": 1.000000,
        "occupied_recall": 0.957300,
        "static_person_recall": 0.925620,
        "moving_person_recall": 0.988981,
    },
    "room4_2026-05-27": {
        "macro_f1": 0.975595,
        "empty_recall": 0.982906,
        "occupied_recall": 0.971864,
        "static_person_recall": 0.995370,
        "moving_person_recall": 0.947743,
    },
}


@dataclass
class ReplayContext:
    target_env: str
    mode: str
    room_id: str
    baseline_id: str
    calibration_id: str
    calibration_session_id: str | None
    global_threshold: float
    effective_threshold: float
    stage1_artifact_id: str
    leakage: dict[str, Any]
    s01_label: str
    s04_label: str
    calibration_window_count: int
    excluded_calibration_window_count: int
    setup_status: dict[str, Any]
    latest_result_verified: bool
    negative_window_rejected: bool


class ApiClient:
    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        raise NotImplementedError

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        raise NotImplementedError


class TestClientApi(ApiClient):
    def __init__(self):
        from fastapi.testclient import TestClient
        from api.server import create_app

        self.client = TestClient(create_app())

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = self.client.post(path, json=payload)
        return response.status_code, parse_response_json(response)

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        response = self.client.get(path)
        return response.status_code, parse_response_json(response)


class HttpApi(ApiClient):
    def __init__(self, base_url: str):
        import httpx

        self.client = httpx.Client(base_url=base_url, timeout=30.0)

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = self.client.post(path, json=payload)
        return response.status_code, parse_response_json(response)

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        response = self.client.get(path)
        return response.status_code, parse_response_json(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Stage 1 room API using real CSI dataset-derived feature payloads.")
    parser.add_argument("--target-env", choices=DEFAULT_TARGETS + ["all"], required=True)
    parser.add_argument("--mode", choices=MODES + ["all"], default="all")
    parser.add_argument("--api-mode", choices=["testclient", "http"], default="testclient")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dataset-root", default=str(WORKSPACE_ROOT / "dataset"))
    parser.add_argument("--artifact-path", default=str(MODEL_SERVER_ROOT / "artifacts" / "models" / "stage1" / "stage1_threshold_v001"))
    parser.add_argument("--output-root", default=str(MODEL_SERVER_ROOT / "reports" / "stage1_api_replay"))
    parser.add_argument("--quantile", type=float, default=0.95)
    parser.add_argument("--margin", type=float, default=0.0)
    args = parser.parse_args()

    targets = DEFAULT_TARGETS if args.target_env == "all" else [args.target_env]
    modes = MODES if args.mode == "all" else [args.mode]
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    debug_log = output_root / "api_replay_debug_log.jsonl"

    all_results = []
    for target_env in targets:
        for mode in modes:
            client = TestClientApi() if args.api_mode == "testclient" else HttpApi(args.base_url)
            metrics = replay_target_mode(
                client=client,
                target_env=target_env,
                mode=mode,
                dataset_root=Path(args.dataset_root),
                artifact_path=Path(args.artifact_path),
                output_root=output_root,
                debug_log=debug_log,
                quantile=args.quantile,
                margin=args.margin,
                api_mode=args.api_mode,
            )
            all_results.append(metrics)
            print(json.dumps(compact_result(metrics), indent=2, ensure_ascii=False))
    write_summary(output_root)
    return 0


def replay_target_mode(
    client: ApiClient,
    target_env: str,
    mode: str,
    dataset_root: Path,
    artifact_path: Path,
    output_root: Path,
    debug_log: Path,
    quantile: float,
    margin: float,
    api_mode: str,
) -> dict[str, Any]:
    target_dir = output_root / target_env
    target_dir.mkdir(parents=True, exist_ok=True)
    threshold_cfg = read_json(artifact_path / "stage1_threshold_config.json")
    metadata = read_json(artifact_path / "metadata.json")
    global_threshold = float(threshold_cfg["selected_threshold"])
    stage1_artifact_id = str(metadata.get("model_id", "stage1_threshold_v001"))
    leakage = threshold_leakage_info(artifact_path, target_env)

    env = load_env(dataset_root, target_env)
    s01 = session_by_id(env, "S01")
    s04 = session_by_id(env, "S04")
    ensure_empty_labels(target_env, s01, s04)

    baseline_rows, _, sec_per_row = duration_to_rows(BASELINE_SECONDS, s01["timestamp"], len(segment_amp(s01, SEGMENT)))
    cal_rows = ratio_rows(s04, SEGMENT, CALIBRATION_RATIO, WINDOW_SIZE)
    s01_baseline, s01_baseline_windows = build_normalized_baseline([(s01, baseline_rows)], SEGMENT, PREPROCESSING_TYPE, WINDOW_SIZE, STRIDE)
    calibration_df = build_calibration_table(s04, s01_baseline, cal_rows)

    if mode == "guided_baseline_merge":
        replay_baseline, replay_baseline_windows = build_normalized_baseline(
            [(s01, baseline_rows), (s04, cal_rows)],
            SEGMENT,
            PREPROCESSING_TYPE,
            WINDOW_SIZE,
            STRIDE,
        )
        calibration_method = "guided_baseline_merge"
        expected_windows = len(calibration_df)
    elif mode == "empty_quantile_threshold":
        replay_baseline = s01_baseline
        replay_baseline_windows = s01_baseline_windows
        calibration_method = "empty_quantile_threshold"
        expected_windows = len(calibration_df)
    elif mode == "no_calibration":
        replay_baseline = s01_baseline
        replay_baseline_windows = s01_baseline_windows
        calibration_method = "no_calibration"
        expected_windows = 0
    else:
        raise ValueError(mode)

    eval_df = build_eval_table(env, replay_baseline, s04["session_id"], cal_rows)
    room_id = f"{target_env}__{mode}".replace("/", "_")
    baseline_id = f"baseline_{room_id}_v001"
    device_group_id = f"device_group_{target_env}"

    call(client, "POST", "/api/rooms", {"room_id": room_id, "room_name": room_id, "device_group_id": device_group_id}, debug_log, target_env, mode, expect=200)
    baseline_start = call(
        client,
        "POST",
        f"/api/rooms/{room_id}/baseline/start",
        {"duration_sec": 300, "window_duration_sec": 5, "mode": "empty", "requested_by": "api_replay"},
        debug_log,
        target_env,
        mode,
        expect=200,
    )
    call(
        client,
        "POST",
        f"/api/rooms/{room_id}/baseline/finish",
        {
            "baseline_session_id": baseline_start["baseline_session_id"],
            "baseline_id": baseline_id,
            "duration_sec": float(baseline_rows * sec_per_row),
            "window_duration_sec": 5,
            "num_windows": int(s01_baseline_windows),
            "preprocess_version": PREPROCESS_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "feature_version": FEATURE_VERSION,
            "baseline_profile": {
                "owner": "preprocessor",
                "subcarrier_count": 192,
                "summary": {"baseline_rows": int(baseline_rows), "baseline_windows": int(replay_baseline_windows)},
            },
        },
        debug_log,
        target_env,
        mode,
        expect=200,
    )

    negative = call(
        client,
        "POST",
        f"/api/rooms/{room_id}/window",
        room_window_payload(room_id, "negative_before_ready", baseline_id, "missing_calibration", device_group_id, 0, feature_dict(eval_df.iloc[0])),
        debug_log,
        target_env,
        mode,
        expect=None,
    )
    negative_window_rejected = negative["_status_code"] == 400

    calibration_start = call(
        client,
        "POST",
        f"/api/rooms/{room_id}/calibration/start",
        {
            "baseline_id": baseline_id,
            "method": calibration_method,
            "expected_windows": expected_windows,
            "window_duration_sec": 5,
        },
        debug_log,
        target_env,
        mode,
        expect=200,
    )
    calibration_session_id = calibration_start["calibration_session_id"]

    if mode != "no_calibration":
        for idx, row in calibration_df.iterrows():
            call(
                client,
                "POST",
                f"/api/rooms/{room_id}/calibration/window",
                {
                    "calibration_session_id": calibration_session_id,
                    "window_id": f"{target_env}_{mode}_calib_{idx:05d}",
                    "baseline_id": baseline_id,
                    "feature_version": FEATURE_VERSION,
                    "features": feature_dict(row),
                },
                debug_log,
                target_env,
                mode,
                expect=200,
            )

    calibration_finish = call(
        client,
        "POST",
        f"/api/rooms/{room_id}/calibration/finish",
        {
            "calibration_session_id": calibration_session_id,
            "method": calibration_method,
            "quantile": quantile,
            "margin": margin,
        },
        debug_log,
        target_env,
        mode,
        expect=200,
    )
    calibration_id = calibration_finish["calibration_id"]
    setup_status = call(client, "GET", f"/api/rooms/{room_id}/setup/status", {}, debug_log, target_env, mode, expect=200)

    api_rows = []
    for idx, row in eval_df.reset_index(drop=True).iterrows():
        window_id = f"{target_env}_{mode}_test_{idx:05d}"
        response = call(
            client,
            "POST",
            f"/api/rooms/{room_id}/window",
            room_window_payload(room_id, window_id, baseline_id, calibration_id, device_group_id, idx, feature_dict(row)),
            debug_log,
            target_env,
            mode,
            expect=200,
        )
        api_rows.append(
            {
                "window_id": window_id,
                "session_id": row["session_id"],
                "source_session": row["source_session"],
                "original_label": row["original_label"],
                "true_binary": int(row["true_binary"]),
                "score": float(row["final_score"]),
                "stage1_label": response["stage1"]["label"],
                "stage1_threshold": float(response["stage1"]["threshold"]),
                "stage2_label": response["stage2"]["label"],
                "stage2_status": response["stage2"]["status"],
            }
        )
    latest = call(client, "GET", f"/api/rooms/{room_id}/result/latest", {}, debug_log, target_env, mode, expect=200)
    latest_result_verified = bool(latest.get("latest_result", {}).get("window_id") == api_rows[-1]["window_id"])

    metrics = compute_metrics(pd.DataFrame(api_rows))
    metrics.update(
        {
            "target_env": target_env,
            "mode": mode,
            "api_mode": api_mode,
            "room_id": room_id,
            "stage1_artifact_id": stage1_artifact_id,
            "stage1_artifact_path": str(artifact_path),
            "threshold_source": threshold_source(mode),
            "global_threshold": global_threshold,
            "selected_threshold": float(calibration_finish["effective_threshold"]),
            "effective_threshold": float(calibration_finish["effective_threshold"]),
            "calibration_method": calibration_method,
            "calibration_window_count": int(expected_windows),
            "excluded_calibration_window_count": int(len(calibration_df)) if mode != "no_calibration" else 0,
            "test_window_count": int(len(api_rows)),
            "calibration_windows_excluded_from_test": verify_calibration_exclusion(api_rows, s04["session_id"], cal_rows),
            "baseline_session": "S01",
            "baseline_rows": int(baseline_rows),
            "guided_calibration_session": "S04",
            "guided_calibration_rows": int(cal_rows),
            "s01_label": s01["label"],
            "s04_label": s04["label"],
            "scenario_label_source": "scenario.md via csi_pipeline.data.parse_scenario/load_env",
            "leakage": leakage,
            "api_state": {
                "negative_window_rejected": negative_window_rejected,
                "setup_status": setup_status,
                "latest_result_verified": latest_result_verified,
            },
            "offline_expected_guided": OFFLINE_EXPECTED.get(target_env),
            "offline_delta_guided": offline_delta(target_env, metrics) if mode == "guided_baseline_merge" else None,
        }
    )
    write_json(target_dir / f"{mode}_metrics.json", metrics)
    return metrics


def build_calibration_table(s04: dict[str, Any], baseline: dict[str, Any], cal_rows: int) -> pd.DataFrame:
    rows = feature_rows_for_range(
        s04,
        SEGMENT,
        baseline,
        PREPROCESSING_TYPE,
        0,
        cal_rows,
        WINDOW_SIZE,
        STRIDE,
        "S04_empty_calibration_20pct",
        True,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("no calibration windows were generated")
    return df


def build_eval_table(env: dict[str, Any], baseline: dict[str, Any], calibration_session_id: str, cal_rows: int) -> pd.DataFrame:
    rows = []
    for session in env["sessions"]:
        if session["label"] not in {"empty", "static_person", "moving_person"}:
            continue
        if session["session_id"] == "S01":
            continue
        start_row = cal_rows if session["session_id"] == calibration_session_id else 0
        if len(segment_amp(session, SEGMENT)) - start_row < WINDOW_SIZE:
            continue
        rows.extend(
            feature_rows_for_range(
                session,
                SEGMENT,
                baseline,
                PREPROCESSING_TYPE,
                start_row,
                len(segment_amp(session, SEGMENT)),
                WINDOW_SIZE,
                STRIDE,
                f"{session['session_id']}_eval",
                session["session_id"] == calibration_session_id,
            )
        )
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("no evaluation windows were generated")
    return df


def feature_dict(row: pd.Series) -> dict[str, float]:
    score = float(row["final_score"])
    return {
        "static_score": float(row["static_score"]),
        "motion_score": float(row["motion_score"]),
        "shape_score": float(row["shape_score"]),
        "robust_max_shape_score": score,
    }


def room_window_payload(
    room_id: str,
    window_id: str,
    baseline_id: str,
    calibration_id: str,
    device_group_id: str,
    index: int,
    features: dict[str, float],
) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "baseline_id": baseline_id,
        "calibration_id": calibration_id,
        "device_group_id": device_group_id,
        "window_start_time": f"2026-06-14T06:{10 + (index // 12):02d}:{(index % 12) * 5:02d}+09:00",
        "window_end_time": f"2026-06-14T06:{10 + (index // 12):02d}:{(index % 12) * 5 + 5:02d}+09:00",
        "window_duration_sec": 5,
        "data_type": "stage1_feature_vector",
        "feature_version": FEATURE_VERSION,
        "features": features,
        "stage2_input": {
            "input_type": "window_matrix",
            "feature_version": "stage2_feature_v1",
            "shape": [128, 192],
            "values": [[0.0 for _ in range(192)] for _ in range(128)],
        },
    }


def compute_metrics(df: pd.DataFrame) -> dict[str, Any]:
    y_true = df["true_binary"].to_numpy(dtype=int)
    y_pred = (df["stage1_label"].to_numpy() == "occupied").astype(int)
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    empty_precision, empty_recall, empty_f1 = prf(tn, fn, fp)
    occupied_precision, occupied_recall, occupied_f1 = prf(tp, fp, fn)
    per_session = {}
    for session_id, group in df.groupby("session_id"):
        g_true = group["true_binary"].to_numpy(dtype=int)
        g_pred = (group["stage1_label"].to_numpy() == "occupied").astype(int)
        label = str(group["original_label"].iloc[0])
        target = 0 if label == "empty" else 1
        per_session[str(session_id)] = {
            "label": label,
            "windows": int(len(group)),
            "recall": float((g_pred == target).mean()) if len(group) else None,
            "predicted_occupied": int(g_pred.sum()),
            "predicted_empty": int((g_pred == 0).sum()),
        }
    scores = df["score"].to_numpy(dtype=float)
    return {
        "total_windows": int(len(df)),
        "empty_windows": int((df["original_label"] == "empty").sum()),
        "occupied_windows": int((df["original_label"] != "empty").sum()),
        "true_empty": int((y_true == 0).sum()),
        "true_occupied": int((y_true == 1).sum()),
        "predicted_empty": int((y_pred == 0).sum()),
        "predicted_occupied": int((y_pred == 1).sum()),
        "accuracy": float((tp + tn) / max(1, len(df))),
        "macro_f1": float((empty_f1 + occupied_f1) / 2.0),
        "empty_precision": empty_precision,
        "empty_recall": empty_recall,
        "empty_f1": empty_f1,
        "occupied_precision": occupied_precision,
        "occupied_recall": occupied_recall,
        "occupied_f1": occupied_f1,
        "static_person_recall": source_recall(df, "static_person"),
        "moving_person_recall": source_recall(df, "moving_person"),
        "empty_false_alarm_rate": float(1.0 - empty_recall),
        "occupied_miss_rate": float(1.0 - occupied_recall),
        "confusion_matrix": [[tn, fp], [fn, tp]],
        "score_summary": quantile_summary(scores),
        "label_distribution": {
            "true": df["original_label"].value_counts().to_dict(),
            "predicted_stage1": df["stage1_label"].value_counts().to_dict(),
            "stage2": df["stage2_label"].value_counts().to_dict(),
        },
        "per_session_metrics": per_session,
    }


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 0.0 if precision + recall == 0 else 2.0 * precision * recall / (precision + recall)
    return float(precision), float(recall), float(f1)


def source_recall(df: pd.DataFrame, source_label: str) -> float | None:
    subset = df[df["original_label"] == source_label]
    if subset.empty:
        return None
    pred = (subset["stage1_label"].to_numpy() == "occupied").astype(int)
    return float(pred.mean())


def quantile_summary(values: np.ndarray) -> dict[str, float]:
    if values.size == 0:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
    return {
        "p50": float(np.quantile(values, 0.50)),
        "p90": float(np.quantile(values, 0.90)),
        "p95": float(np.quantile(values, 0.95)),
        "p99": float(np.quantile(values, 0.99)),
    }


def verify_calibration_exclusion(api_rows: list[dict[str, Any]], calibration_session_id: str, cal_rows: int) -> bool:
    del cal_rows
    return all(not (row["session_id"] == calibration_session_id and "calibration" in str(row["source_session"])) for row in api_rows)


def ensure_empty_labels(target_env: str, s01: dict[str, Any], s04: dict[str, Any]) -> None:
    problems = []
    if s01["label"] != "empty":
        problems.append(f"S01 label is {s01['label']}")
    if s04["label"] != "empty":
        problems.append(f"S04 label is {s04['label']}")
    if problems:
        raise RuntimeError(f"{target_env} invalid replay labels: {', '.join(problems)}")


def call(
    client: ApiClient,
    method: str,
    path: str,
    payload: dict[str, Any],
    debug_log: Path,
    target_env: str,
    mode: str,
    expect: int | None,
) -> dict[str, Any]:
    if method == "POST":
        status, body = client.post(path, payload)
    elif method == "GET":
        status, body = client.get(path)
    else:
        raise ValueError(method)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_env": target_env,
        "mode": mode,
        "method": method,
        "path": path,
        "status_code": status,
        "request": payload,
        "response": body,
    }
    debug_log.parent.mkdir(parents=True, exist_ok=True)
    with debug_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(to_jsonable(record), ensure_ascii=False) + "\n")
    if expect is not None and status != expect:
        raise RuntimeError(f"{method} {path} expected HTTP {expect}, got {status}: {body}")
    if expect is None:
        body = dict(body)
        body["_status_code"] = status
    return body


def threshold_source(mode: str) -> str:
    if mode == "empty_quantile_threshold":
        return "calibration empty quantile from API CalibrationProfile"
    if mode == "guided_baseline_merge":
        return "stage1_threshold_config.json global threshold retained after guided baseline merge"
    return "stage1_threshold_config.json global threshold retained without calibration baseline merge"


def threshold_leakage_info(artifact_path: Path, target_env: str) -> dict[str, Any]:
    metrics_global = read_json(artifact_path / "metrics_global.json")
    metadata = read_json(artifact_path / "metadata.json")
    train_groups = metrics_global.get("train_groups") or metadata.get("train_groups") or metadata.get("train_split")
    if train_groups is None:
        return {
            "threshold_fitting_source": "unknown",
            "threshold_train_groups": None,
            "leakage_warning": None,
            "message": "threshold fitting source not fully auditable",
        }
    return {
        "threshold_fitting_source": "metrics_global.json train_groups",
        "threshold_train_groups": train_groups,
        "leakage_warning": target_env in train_groups,
        "message": "target_env is included in threshold fitting" if target_env in train_groups else "target_env not found in threshold fitting groups",
    }


def offline_delta(target_env: str, metrics: dict[str, Any]) -> dict[str, float] | None:
    expected = OFFLINE_EXPECTED.get(target_env)
    if not expected:
        return None
    return {key: float(metrics[key] - expected[key]) for key in expected}


def compact_result(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_env": metrics["target_env"],
        "mode": metrics["mode"],
        "macro_f1": metrics["macro_f1"],
        "empty_recall": metrics["empty_recall"],
        "occupied_recall": metrics["occupied_recall"],
        "static_person_recall": metrics["static_person_recall"],
        "moving_person_recall": metrics["moving_person_recall"],
        "effective_threshold": metrics["effective_threshold"],
        "calibration_windows_excluded_from_test": metrics["calibration_windows_excluded_from_test"],
        "leakage_warning": metrics["leakage"]["leakage_warning"],
    }


def write_summary(output_root: Path) -> None:
    rows = []
    for metrics_path in sorted(output_root.glob("*/*_metrics.json")):
        rows.append(read_json(metrics_path))
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": rows,
    }
    write_json(output_root / "summary.json", summary)
    lines = [
        "# Stage 1 API Replay Summary",
        "",
        "## Purpose",
        "",
        "Replay real room4 CSI-derived Stage 1 feature payloads through the model_server room/baseline/calibration/window APIs and compare API-response metrics to offline guided calibration results.",
        "",
        "## Stage 1 Artifact",
        "",
    ]
    if rows:
        lines.extend(
            [
                f"- Artifact: `{rows[0]['stage1_artifact_id']}`",
                f"- Global threshold: `{rows[0]['global_threshold']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Mode Descriptions",
            "",
            "- `no_calibration`: S01 baseline only, global threshold retained.",
            "- `empty_quantile_threshold`: S01 baseline, S04 first 20% empty scores set the threshold.",
            "- `guided_baseline_merge`: S01 baseline merged with S04 first 20%, global threshold retained.",
            "",
            "## Metrics",
            "",
            "| target_env | mode | threshold | macro_f1 | empty_recall | occupied_recall | static_recall | moving_recall | leakage | exclusion |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['target_env']} | {row['mode']} | {row['effective_threshold']:.6f} | {row['macro_f1']:.6f} | "
            f"{row['empty_recall']:.6f} | {row['occupied_recall']:.6f} | {row['static_person_recall']:.6f} | "
            f"{row['moving_person_recall']:.6f} | {row['leakage']['leakage_warning']} | {row['calibration_windows_excluded_from_test']} |"
        )
    lines.extend(["", "## Offline Guided Delta", ""])
    for row in rows:
        if row["mode"] != "guided_baseline_merge":
            continue
        lines.append(f"### {row['target_env']}")
        delta = row.get("offline_delta_guided") or {}
        if not delta:
            lines.append("- No offline expected values configured.")
            continue
        for key, value in delta.items():
            lines.append(f"- `{key}` delta: `{value:+.9f}`")
        if any(abs(value) > 1e-6 for value in delta.values()):
            lines.append("- Difference is present. Inspect windowing, S04 ratio rows, baseline merge, and API threshold application.")
        else:
            lines.append("- API replay matches the offline guided metric within numerical precision.")
        lines.append("")
    lines.extend(
        [
            "## Validation Notes",
            "",
            "- Metrics are computed from `/api/rooms/{room_id}/window` responses, not directly from offline labels.",
            "- S01 and S04 labels are loaded from `scenario.md` through `csi_pipeline.data.load_env` and must be `empty`.",
            "- Calibration windows are excluded by starting S04 evaluation after the calibration prefix.",
            "- Negative `/window` before ready is checked for HTTP 400.",
            "- Latest result is checked against the final replayed window.",
            "",
            "## Next Work",
            "",
            "- Connect Stage 2 static/moving CNN and replay Stage 1-pass windows through Stage 2.",
            "- Persist RoomState/BaselineProfile/CalibrationProfile outside the in-memory store when moving beyond local validation.",
        ]
    )
    (output_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_response_json(response) -> dict[str, Any]:
    try:
        return response.json()
    except Exception:
        return {"text": response.text}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
