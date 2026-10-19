from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Window:
    data: np.ndarray
    start: int
    end: int
    timestamp: float | None = None
    session_id: str | None = None
    label: str | None = None


def window_starts(n_rows: int, window_size: int, stride: int) -> list[int]:
    if n_rows < window_size:
        return []
    return list(range(0, n_rows - window_size + 1, stride))


def build_windows(
    packets: np.ndarray,
    window_size: int = 128,
    stride: int = 64,
    timestamps: np.ndarray | None = None,
    session_id: str | None = None,
    label: str | None = None,
) -> list[Window]:
    out: list[Window] = []
    for start in window_starts(len(packets), window_size, stride):
        end = start + window_size
        ts = None if timestamps is None or len(timestamps) <= start else float(timestamps[start])
        out.append(Window(packets[start:end], start, end, ts, session_id, label))
    return out

