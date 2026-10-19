"""Stage-2 occupied-window dataset and array builders."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from csi_pipeline.data import SESSION_MEANING, TRAIN_ENVS, default_dataset_root, load_env
from csi_pipeline.stage1.baseline import preprocess_session_segment, segment_amp


def build_stage2_arrays(envs: list[dict], preprocessing_type: str, segment: str = "full_buffer", window_size: int = 128, stride: int = 64, only_target_occupied: bool = False) -> tuple[np.ndarray, pd.DataFrame]:
    xs = []
    rows = []
    label_map = {"static_person": 0, "moving_person": 1}
    for env in envs:
        for session in env["sessions"]:
            if session["label"] not in label_map:
                continue
            if only_target_occupied and session["session_id"] not in {"S02", "S03", "S05", "S06"}:
                continue
            n_rows = len(segment_amp(session, segment))
            for start in range(0, n_rows - window_size + 1, stride) if n_rows >= window_size else []:
                xs.append(preprocess_session_segment(session, segment, preprocessing_type, start, start + window_size))
                rows.append(
                    {
                        "env_id": session["env_id"],
                        "session_id": session["session_id"],
                        "source_session": f"{session['session_id']}_{SESSION_MEANING.get(session['session_id'], session['label'])}",
                        "label": session["label"],
                        "class_id": label_map[session["label"]],
                        "window_start": start,
                        "window_end": start + window_size,
                    }
                )
    if not xs:
        return np.empty((0, window_size, 192), dtype=np.float32), pd.DataFrame()
    return np.nan_to_num(np.stack(xs).astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0), pd.DataFrame(rows)


def build_stage2_training_arrays(
    dataset_root: str | Path | None = None,
    preprocessing_type: str = "packet_mean_normalized",
    segment: str = "full_buffer",
    window_size: int = 128,
    stride: int = 64,
):
    root = default_dataset_root() if dataset_root is None else Path(dataset_root)
    train_envs = [load_env(root, env_id) for env_id in TRAIN_ENVS]
    return build_stage2_arrays(train_envs, preprocessing_type, segment, window_size, stride)
