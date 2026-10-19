from __future__ import annotations

import argparse
import json
import sys
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
from csi_pipeline.stage1.baseline import build_normalized_baseline, preprocess_session_segment, segment_amp  # noqa: E402
from csi_pipeline.stage1.calibration import ratio_rows  # noqa: E402
from csi_pipeline.stage1.scoring import feature_rows_for_range  # noqa: E402


TARGETS = ["room4_2026-05-20", "room4_2026-05-27"]
PREPROCESSING_TYPE_STAGE1 = "gain_mul_corrected"
PREPROCESSING_TYPE_STAGE2 = "packet_mean_normalized"
SEGMENT = "full_buffer"
WINDOW_SIZE = 128
STRIDE = 64
BASELINE_SECONDS = 20.0
CALIBRATION_RATIO = 0.20
STAGE1_FEATURE_VERSION = "stage1_feature_v1"
STAGE2_FEATURE_VERSION = "stage2_feature_v1"
LABEL_TO_ID = {"static_person": 0, "moving_person": 1}
ID_TO_LABEL = {0: "static_person", 1: "moving_person"}


class TestClientApi:
    def __init__(self):
        from fastapi.testclient import TestClient
        from api.server import create_app

        self.client = TestClient(create_app())

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = self.client.post(path, json=payload)
        return response.status_code, response.json()

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        response = self.client.get(path)
        return response.status_code, response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Stage 2 static/moving API on Stage 1 occupied windows.")
    parser.add_argument("--target-env", choices=TARGETS + ["all"], required=True)
    parser.add_argument("--api-mode", choices=["testclient"], default="testclient")
    parser.add_argument("--dataset-root", default=str(WORKSPACE_ROOT / "dataset"))
    parser.add_argument("--output-root", default=str(MODEL_SERVER_ROOT / "reports" / "stage2_api_replay"))
    args = parser.parse_args()

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    targets = TARGETS if args.target_env == "all" else [args.target_env]
    results = []
    for target_env in targets:
        result = replay_target(TestClientApi(), Path(args.dataset_root), target_env, output_root)
        results.append(result)
        print(json.dumps(compact(result), indent=2, ensure_ascii=False))
    write_summary(output_root)
    return 0


def replay_target(client: TestClientApi, dataset_root: Path, target_env: str, output_root: Path) -> dict[str, Any]:
    env = load_env(dataset_root, target_env)
    s01 = session_by_id(env, "S01")
    s04 = session_by_id(env, "S04")
    if s01["label"] != "empty" or s04["label"] != "empty":
        raise RuntimeError(f"{target_env}: S01/S04 must be empty")

    baseline_rows, _, sec_per_row = duration_to_rows(BASELINE_SECONDS, s01["timestamp"], len(segment_amp(s01, SEGMENT)))
    cal_rows = ratio_rows(s04, SEGMENT, CALIBRATION_RATIO, WINDOW_SIZE)
    s01_baseline, baseline_windows = build_normalized_baseline(
        [(s01, baseline_rows)],
        SEGMENT,
        PREPROCESSING_TYPE_STAGE1,
        WINDOW_SIZE,
        STRIDE,
    )
    merged_baseline, merged_windows = build_normalized_baseline(
        [(s01, baseline_rows), (s04, cal_rows)],
        SEGMENT,
        PREPROCESSING_TYPE_STAGE1,
        WINDOW_SIZE,
        STRIDE,
    )
    calibration_df = pd.DataFrame(
        feature_rows_for_range(
            s04,
            SEGMENT,
            s01_baseline,
            PREPROCESSING_TYPE_STAGE1,
            0,
            cal_rows,
            WINDOW_SIZE,
            STRIDE,
            "S04_empty_calibration_20pct",
            True,
        )
    )
    eval_df = build_eval_with_stage2(env, merged_baseline, cal_rows)

    room_id = f"{target_env}__stage2_guided".replace("/", "_")
    baseline_id = f"baseline_{room_id}_v001"
    device_group_id = f"device_group_{target_env}"
    debug_log = output_root / "api_replay_debug_log.jsonl"

    call(client, "POST", "/api/rooms", {"room_id": room_id, "room_name": room_id, "device_group_id": device_group_id}, debug_log, target_env, 200)
    baseline_start = call(
        client,
        "POST",
        f"/api/rooms/{room_id}/baseline/start",
        {"duration_sec": 300, "window_duration_sec": 5, "mode": "empty", "requested_by": "stage2_replay"},
        debug_log,
        target_env,
        200,
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
            "num_windows": int(baseline_windows),
            "preprocess_version": "preprocess_v1",
            "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
            "feature_version": STAGE1_FEATURE_VERSION,
            "baseline_profile": {"owner": "preprocessor", "summary": {"merged_baseline_windows": int(merged_windows)}},
        },
        debug_log,
        target_env,
        200,
    )
    calibration_start = call(
        client,
        "POST",
        f"/api/rooms/{room_id}/calibration/start",
        {"baseline_id": baseline_id, "method": "guided_baseline_merge", "expected_windows": len(calibration_df), "window_duration_sec": 5},
        debug_log,
        target_env,
        200,
    )
    for idx, row in calibration_df.iterrows():
        call(
            client,
            "POST",
            f"/api/rooms/{room_id}/calibration/window",
            {
                "calibration_session_id": calibration_start["calibration_session_id"],
                "window_id": f"{target_env}_stage2_calib_{idx:05d}",
                "baseline_id": baseline_id,
                "feature_version": STAGE1_FEATURE_VERSION,
                "features": stage1_features(row),
            },
            debug_log,
            target_env,
            200,
        )
    calibration_finish = call(
        client,
        "POST",
        f"/api/rooms/{room_id}/calibration/finish",
        {"calibration_session_id": calibration_start["calibration_session_id"], "method": "guided_baseline_merge", "quantile": 0.95},
        debug_log,
        target_env,
        200,
    )
    calibration_id = calibration_finish["calibration_id"]

    rows = []
    for idx, row in eval_df.reset_index(drop=True).iterrows():
        response = call(
            client,
            "POST",
            f"/api/rooms/{room_id}/window",
            {
                "window_id": f"{target_env}_stage2_test_{idx:05d}",
                "baseline_id": baseline_id,
                "calibration_id": calibration_id,
                "device_group_id": device_group_id,
                "window_start_time": "2026-06-14T06:10:00+09:00",
                "window_end_time": "2026-06-14T06:10:05+09:00",
                "window_duration_sec": 5,
                "data_type": "stage1_feature_vector",
                "feature_version": STAGE1_FEATURE_VERSION,
                "features": stage1_features(row),
                "stage2_input": {
                    "input_type": "window_matrix",
                    "feature_version": STAGE2_FEATURE_VERSION,
                    "shape": [WINDOW_SIZE, 192],
                    "values": row["stage2_matrix"],
                },
            },
            debug_log,
            target_env,
            200,
        )
        rows.append(
            {
                "session_id": row["session_id"],
                "original_label": row["original_label"],
                "stage1_label": response["stage1"]["label"],
                "stage2_label": response["stage2"].get("label"),
                "stage2_status": response["stage2"].get("status"),
            }
        )
    latest = call(client, "GET", f"/api/rooms/{room_id}/result/latest", {}, debug_log, target_env, 200)
    metrics = compute_metrics(pd.DataFrame(rows))
    metrics.update(
        {
            "target_env": target_env,
            "mode": "guided_baseline_merge",
            "stage2_mode": "static_moving_cnn",
            "stage2_artifact_id": "stage2_static_moving_v001",
            "stage2_input_type": "window_matrix",
            "stage2_input_shape": [WINDOW_SIZE, 192],
            "effective_threshold": float(calibration_finish["effective_threshold"]),
            "calibration_windows_excluded_from_test": True,
            "latest_result_verified": latest.get("latest_result", {}).get("window_id") == f"{target_env}_stage2_test_{len(rows)-1:05d}",
        }
    )
    write_json(output_root / f"{target_env}_metrics.json", metrics)
    return metrics


def build_eval_with_stage2(env: dict[str, Any], baseline: dict[str, Any], cal_rows: int) -> pd.DataFrame:
    rows = []
    for session in env["sessions"]:
        if session["label"] not in {"empty", "static_person", "moving_person"} or session["session_id"] == "S01":
            continue
        start_row = cal_rows if session["session_id"] == "S04" else 0
        for row in feature_rows_for_range(
            session,
            SEGMENT,
            baseline,
            PREPROCESSING_TYPE_STAGE1,
            start_row,
            len(segment_amp(session, SEGMENT)),
            WINDOW_SIZE,
            STRIDE,
            f"{session['session_id']}_eval",
            session["session_id"] == "S04",
        ):
            matrix = preprocess_session_segment(session, SEGMENT, PREPROCESSING_TYPE_STAGE2, row["start_frame"], row["end_frame"])
            row["stage2_matrix"] = np.nan_to_num(matrix.astype(float), nan=0.0, posinf=0.0, neginf=0.0).tolist()
            rows.append(row)
    return pd.DataFrame(rows)


def stage1_features(row: pd.Series) -> dict[str, float]:
    return {
        "static_score": float(row["static_score"]),
        "motion_score": float(row["motion_score"]),
        "shape_score": float(row["shape_score"]),
        "robust_max_shape_score": float(row["final_score"]),
    }


def compute_metrics(df: pd.DataFrame) -> dict[str, Any]:
    true_stage1 = (df["original_label"] != "empty").astype(int).to_numpy()
    pred_stage1 = (df["stage1_label"] == "occupied").astype(int).to_numpy()
    empty_mask = true_stage1 == 0
    occupied_mask = true_stage1 == 1
    stage1_empty_recall = float((pred_stage1[empty_mask] == 0).mean()) if empty_mask.any() else None
    stage1_occupied_recall = float((pred_stage1[occupied_mask] == 1).mean()) if occupied_mask.any() else None
    stage2_eval = df[(df["original_label"].isin(LABEL_TO_ID)) & (df["stage1_label"] == "occupied")].copy()
    y_true = np.asarray([LABEL_TO_ID[label] for label in stage2_eval["original_label"]], dtype=int)
    y_pred = np.asarray([LABEL_TO_ID.get(label, -1) for label in stage2_eval["stage2_label"]], dtype=int)
    valid = y_pred >= 0
    y_true = y_true[valid]
    y_pred = y_pred[valid]
    cm = confusion(y_true, y_pred)
    stage1_missed_static = int(((df["original_label"] == "static_person") & (df["stage1_label"] != "occupied")).sum())
    stage1_missed_moving = int(((df["original_label"] == "moving_person") & (df["stage1_label"] != "occupied")).sum())
    return {
        "stage1_empty_recall": stage1_empty_recall,
        "stage1_occupied_recall": stage1_occupied_recall,
        "stage2_conditional_accuracy": accuracy(y_true, y_pred),
        "stage2_conditional_macro_f1": macro_f1(y_true, y_pred),
        "stage2_static_recall": recall_for(y_true, y_pred, 0),
        "stage2_moving_recall": recall_for(y_true, y_pred, 1),
        "end_to_end_static_recall": end_to_end_recall(df, "static_person"),
        "end_to_end_moving_recall": end_to_end_recall(df, "moving_person"),
        "stage1_missed_static_count": stage1_missed_static,
        "stage1_missed_moving_count": stage1_missed_moving,
        "stage2_confusion_matrix": cm,
        "pipeline_confusion_summary": {
            "stage1_empty_skipped": int(((df["stage1_label"] == "empty") & (df["stage2_status"] == "skipped")).sum()),
            "stage1_occupied_stage2_ok": int(((df["stage1_label"] == "occupied") & (df["stage2_status"] == "ok")).sum()),
            "empty_false_alarms_to_stage2": int(((df["original_label"] == "empty") & (df["stage1_label"] == "occupied")).sum()),
        },
    }


def confusion(y_true: np.ndarray, y_pred: np.ndarray) -> list[list[int]]:
    return [[int(((y_true == i) & (y_pred == j)).sum()) for j in [0, 1]] for i in [0, 1]]


def accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float((y_true == y_pred).mean()) if len(y_true) else 0.0


def recall_for(y_true: np.ndarray, y_pred: np.ndarray, label: int) -> float:
    mask = y_true == label
    return float((y_pred[mask] == label).mean()) if mask.any() else 0.0


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    values = []
    for label in [0, 1]:
        tp = int(((y_true == label) & (y_pred == label)).sum())
        fp = int(((y_true != label) & (y_pred == label)).sum())
        fn = int(((y_true == label) & (y_pred != label)).sum())
        p = tp / max(1, tp + fp)
        r = tp / max(1, tp + fn)
        values.append(0.0 if p + r == 0 else 2 * p * r / (p + r))
    return float(sum(values) / 2.0)


def end_to_end_recall(df: pd.DataFrame, label: str) -> float:
    subset = df[df["original_label"] == label]
    if subset.empty:
        return 0.0
    return float(((subset["stage1_label"] == "occupied") & (subset["stage2_label"] == label)).mean())


def call(client: TestClientApi, method: str, path: str, payload: dict[str, Any], debug_log: Path, target_env: str, expect: int) -> dict[str, Any]:
    status, body = client.post(path, payload) if method == "POST" else client.get(path)
    with debug_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "target_env": target_env, "method": method, "path": path, "status": status}, ensure_ascii=False) + "\n")
    if status != expect:
        raise RuntimeError(f"{method} {path} expected {expect}, got {status}: {body}")
    return body


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_env": metrics["target_env"],
        "stage1_occupied_recall": metrics["stage1_occupied_recall"],
        "stage2_conditional_macro_f1": metrics["stage2_conditional_macro_f1"],
        "stage2_static_recall": metrics["stage2_static_recall"],
        "stage2_moving_recall": metrics["stage2_moving_recall"],
        "end_to_end_static_recall": metrics["end_to_end_static_recall"],
        "end_to_end_moving_recall": metrics["end_to_end_moving_recall"],
    }


def write_summary(output_root: Path) -> None:
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(output_root.glob("room4_*_metrics.json"))]
    write_json(output_root / "summary.json", {"generated_at": datetime.now(timezone.utc).isoformat(), "results": rows})
    lines = [
        "# Stage 2 API Replay Summary",
        "",
        "| target_env | stage1_empty_recall | stage1_occupied_recall | stage2_macro_f1 | stage2_static_recall | stage2_moving_recall | e2e_static | e2e_moving |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['target_env']} | {row['stage1_empty_recall']:.6f} | {row['stage1_occupied_recall']:.6f} | "
            f"{row['stage2_conditional_macro_f1']:.6f} | {row['stage2_static_recall']:.6f} | {row['stage2_moving_recall']:.6f} | "
            f"{row['end_to_end_static_recall']:.6f} | {row['end_to_end_moving_recall']:.6f} |"
        )
    lines.extend(
        [
            "",
            "Stage 2 is evaluated only on ground-truth occupied windows that Stage 1 passed as occupied. End-to-end recall includes Stage 1 misses.",
        ]
    )
    (output_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
