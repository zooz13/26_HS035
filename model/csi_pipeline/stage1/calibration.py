"""Stage-1 calibration table construction and baseline recalibration policies."""

from __future__ import annotations

import pandas as pd

from csi_pipeline.data import duration_to_rows, session_by_id
from csi_pipeline.stage1.baseline import build_normalized_baseline, segment_amp
from csi_pipeline.stage1.scoring import feature_rows_for_range


STAGE1_METHODS = ["S01-only", "guided 10%", "guided 20%", "guided 30%", "S04-only 20s ablation", "oracle full"]


def calibration_rows_seconds(session: dict, segment: str, seconds: float, window_size: int) -> int:
    amp = segment_amp(session, segment)
    rows, _, _ = duration_to_rows(seconds, session["timestamp"], len(amp))
    rows = max(rows, min(window_size, len(amp)))
    rows = min(rows, len(amp))
    return int(rows)


def ratio_rows(session: dict, segment: str, ratio: float, window_size: int) -> int:
    n_rows = len(segment_amp(session, segment))
    if n_rows <= window_size:
        return n_rows
    return int(max(window_size, min(n_rows - window_size, int(round(n_rows * ratio)))))


def merge_recalibration_baseline(initial_parts: list[tuple[dict, int]], recalibration_parts: list[tuple[dict, int]], segment: str, preprocessing_type: str, window_size: int, stride: int) -> tuple[dict, int]:
    return build_normalized_baseline(initial_parts + recalibration_parts, segment, preprocessing_type, window_size, stride)


def build_stage1_test_table(
    target_env: dict,
    preprocessing_type: str,
    method: str,
    segment: str = "full_buffer",
    s01_calibration_seconds: float = 20.0,
    s04_calibration_seconds: float = 20.0,
    window_size: int = 128,
    stride: int = 64,
) -> tuple[pd.DataFrame, int, str]:
    s01 = session_by_id(target_env, "S01")
    s04 = session_by_id(target_env, "S04")
    s07 = session_by_id(target_env, "S07")
    s01_rows = calibration_rows_seconds(s01, segment, s01_calibration_seconds, window_size)
    notes = []
    if method == "S01-only":
        baseline, cal_windows = build_normalized_baseline([(s01, s01_rows)], segment, preprocessing_type, window_size, stride)
        specs = [("S02", 0, "S02_static_before"), ("S03", 0, "S03_moving_before"), ("S04", 0, "S04_eval_full"), ("S05", 0, "S05_static_after"), ("S06", 0, "S06_moving_after"), ("S07", 0, "S07_final_empty")]
        notes.append("initial S01 first 20s calibration only")
    elif method.startswith("guided"):
        pct = float(method.split()[1].replace("%", "")) / 100.0
        s04_rows = ratio_rows(s04, segment, pct, window_size)
        baseline, cal_windows = merge_recalibration_baseline([(s01, s01_rows)], [(s04, s04_rows)], segment, preprocessing_type, window_size, stride)
        specs = [("S02", 0, "S02_static_before"), ("S03", 0, "S03_moving_before"), ("S04", s04_rows, "S04_eval_remainder"), ("S05", 0, "S05_static_after"), ("S06", 0, "S06_moving_after"), ("S07", 0, "S07_final_empty")]
        notes.append(f"S04 first {int(pct * 100)}% used for guided recalibration and excluded from evaluation")
    elif method == "S04-only 20s ablation":
        s04_rows = calibration_rows_seconds(s04, segment, s04_calibration_seconds, window_size)
        baseline, cal_windows = build_normalized_baseline([(s04, s04_rows)], segment, preprocessing_type, window_size, stride)
        specs = [("S01", 0, "S01_eval_full"), ("S02", 0, "S02_static_before"), ("S03", 0, "S03_moving_before"), ("S04", s04_rows, "S04_eval_remainder"), ("S05", 0, "S05_static_after"), ("S06", 0, "S06_moving_after"), ("S07", 0, "S07_final_empty")]
        notes.append("diagnostic ablation only; uses later S04 empty at service start")
    elif method == "oracle full":
        baseline, cal_windows = build_normalized_baseline([(s01, len(segment_amp(s01, segment))), (s04, len(segment_amp(s04, segment))), (s07, len(segment_amp(s07, segment)))], segment, preprocessing_type, window_size, stride)
        specs = [("S02", 0, "S02_static_before"), ("S03", 0, "S03_moving_before"), ("S05", 0, "S05_static_after"), ("S06", 0, "S06_moving_after")]
        notes.append("upper-bound only; uses future empty S07 and full S04")
    else:
        raise ValueError(method)
    rows = []
    for sid, start_row, source in specs:
        session = session_by_id(target_env, sid)
        rows.extend(feature_rows_for_range(session, segment, baseline, preprocessing_type, start_row, len(segment_amp(session, segment)), window_size, stride, source, method != "S01-only"))
    return pd.DataFrame(rows), cal_windows, "; ".join(notes)
