from __future__ import annotations

import numpy as np

from csi_pipeline.preprocessing.csi_parser import iq_to_amplitude


def raw_to_amplitude(raw_iq: np.ndarray) -> np.ndarray:
    return iq_to_amplitude(raw_iq)


def select_valid_carriers(amplitude: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return np.asarray(amplitude)[:, np.asarray(mask, dtype=bool)]
