from __future__ import annotations

import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd


def parse_csi_data(data_str: object) -> np.ndarray:
    if isinstance(data_str, np.ndarray):
        return data_str.astype(np.float32)
    if isinstance(data_str, (list, tuple)):
        return np.asarray(data_str, dtype=np.float32)
    if data_str is None or (isinstance(data_str, float) and np.isnan(data_str)):
        return np.array([], dtype=np.float32)
    text = str(data_str).strip()
    if text.startswith("[") and text.endswith("]"):
        fast = np.fromstring(text[1:-1], sep=",", dtype=np.float32)
        if fast.size:
            return fast
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, (list, tuple)):
            return np.asarray(parsed, dtype=np.float32)
    except Exception:
        pass
    return np.asarray([float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", text)], dtype=np.float32)


def load_raw_matrix(csv_path: str | Path) -> np.ndarray:
    df = pd.read_csv(csv_path)
    if "data" in df.columns:
        parsed = [parse_csi_data(value) for value in df["data"]]
        width = max((len(row) for row in parsed), default=0)
        matrix = np.full((len(parsed), width), np.nan, dtype=np.float32)
        for idx, row in enumerate(parsed):
            matrix[idx, : len(row)] = row
        return matrix
    numeric = df.select_dtypes(include=["number"]).to_numpy(dtype=np.float32)
    if numeric.shape[1] >= 384:
        return numeric[:, -384:]
    raise ValueError(f"could not detect CSI raw data in {csv_path}")


def raw_to_complex(raw: np.ndarray) -> np.ndarray:
    arr = np.asarray(raw, dtype=np.float32)
    if arr.shape[-1] % 2:
        raise ValueError(f"raw width must be even, got {arr.shape[-1]}")
    imag = arr[..., 0::2]
    real = arr[..., 1::2]
    return real + 1j * imag


def iq_to_amplitude(iq_array: np.ndarray) -> np.ndarray:
    arr = np.asarray(iq_array, dtype=np.float32)
    if arr.ndim == 1:
        if arr.size % 2:
            raise ValueError(f"I/Q length must be even, got {arr.size}")
        imag = arr[0::2]
        real = arr[1::2]
        return np.sqrt(real * real + imag * imag).astype(np.float32)
    if arr.shape[-1] % 2:
        raise ValueError(f"I/Q width must be even, got {arr.shape[-1]}")
    imag = arr[..., 0::2]
    real = arr[..., 1::2]
    return np.sqrt(real * real + imag * imag).astype(np.float32)


def build_amplitude_matrix(df: pd.DataFrame) -> np.ndarray:
    if "data" not in df.columns:
        numeric = df.select_dtypes(include=["number"]).to_numpy(dtype=np.float32)
        if numeric.shape[1] >= 384:
            return iq_to_amplitude(numeric[:, -384:])
        raise ValueError("DataFrame must contain a data column or at least 384 numeric CSI columns")
    parsed = [parse_csi_data(v) for v in df["data"]]
    width = max((len(x) for x in parsed), default=0)
    raw = np.full((len(parsed), width), np.nan, dtype=np.float32)
    for i, row in enumerate(parsed):
        raw[i, : len(row)] = row
    if raw.shape[1] != 384:
        raise ValueError(f"expected 384 raw values, got {raw.shape[1]}")
    return iq_to_amplitude(raw)


def detect_invalid_carriers(amplitude_matrix: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    amp = np.asarray(amplitude_matrix, dtype=np.float32)
    finite_ratio = np.isfinite(amp).mean(axis=0)
    mean_abs = np.nanmean(np.abs(amp), axis=0)
    return (finite_ratio < 0.95) | (mean_abs <= eps)


def valid_carrier_mask(amplitude_matrix: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    return ~detect_invalid_carriers(amplitude_matrix, eps=eps)


def load_amplitude_csv(path: str | Path) -> np.ndarray:
    raw = load_raw_matrix(path)
    if raw.shape[1] != 384:
        raise ValueError(f"expected 384 raw values, got {raw.shape[1]} in {path}")
    amp = np.abs(raw_to_complex(raw)).astype(np.float32)
    if amp.shape[1] != 192:
        raise ValueError(f"expected amplitude length 192, got {amp.shape[1]}")
    return amp
