"""Stage-1 detector application for empty/occupied inference."""

from __future__ import annotations

import numpy as np
import pandas as pd

from csi_pipeline.stage1.scoring import robust_max_shape_score


def apply_detector(df: pd.DataFrame, detector_cfg: dict) -> pd.DataFrame:
    out = df.copy()
    detector = detector_cfg["detector_name"]
    if detector != "robust_max_shape":
        raise ValueError(f"unsupported detector: {detector}")
    score = robust_max_shape_score(out)
    threshold = float(detector_cfg["threshold"])
    prob_occ = 1.0 / (1.0 + np.exp(-(score - threshold)))
    out["prob_occupied"] = prob_occ
    out["prob_empty"] = 1.0 - prob_occ
    out["final_score"] = score
    out["pred_binary"] = (score >= threshold).astype(int)
    out["pred_binary_label"] = np.where(out["pred_binary"].to_numpy(dtype=int) == 1, "occupied", "empty")
    return out
