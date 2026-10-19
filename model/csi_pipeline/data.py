from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re

import numpy as np
import pandas as pd

from csi_pipeline.preprocessing.csi_parser import load_amplitude_csv


TRAIN_ENVS = [
    "room1_2026-04-30",
    "room1_2026-05-07",
    "room2_2026-05-05",
    "room3_2026-05-05_rep01",
    "room3_2026-05-05_rep02",
    "room3_2026-05-07",
    "room5_2026-05-08",
]

ROOM4_EXCLUDED = [
    "room4_2026-05-07_rep01",
    "room4_2026-05-07_rep02",
    "room4_2026-05-20",
    "room4_2026-05-27",
]

TARGET_ENV = "room4_2026-05-27"
TARGET_LABELS = {
    "S01": "empty",
    "S02": "static_person",
    "S03": "moving_person",
    "S04": "empty",
    "S05": "static_person",
    "S06": "moving_person",
    "S07": "empty",
}

SESSION_MEANING = {
    "S01": "initial_empty",
    "S02": "static_before",
    "S03": "moving_before",
    "S04": "recalibration_empty",
    "S05": "static_after",
    "S06": "moving_after",
    "S07": "final_empty",
}


@dataclass(frozen=True)
class ScenarioRow:
    session_id: str
    position: str
    label: str
    action: str
    duration_sec: float | None


def default_dataset_root() -> Path:
    return Path(__file__).resolve().parents[2] / "dataset"


def parse_duration_sec(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def parse_scenario(path: str | Path) -> dict[str, ScenarioRow]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp949", errors="replace")
    rows: dict[str, ScenarioRow] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or re.match(r"^\|\s*-+", stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 5 or not re.match(r"^S\d+", cells[0], re.I):
            continue
        row = ScenarioRow(cells[0].upper(), cells[1], cells[2], cells[3], parse_duration_sec(cells[4]))
        rows[row.session_id] = row
    return rows


def parse_env_id(env_id: str) -> tuple[str, str, str]:
    match = re.match(r"^(room\d+)_(\d{4}-\d{2}-\d{2})(?:_(.+))?$", env_id)
    if not match:
        return env_id, "", ""
    room_id, date_id, variable = match.groups()
    return room_id, date_id, variable or ""


def read_metadata_full(csv_path: Path) -> pd.DataFrame:
    header = pd.read_csv(csv_path, nrows=0)
    usecols = [c for c in header.columns if c != "data"]
    meta = pd.read_csv(csv_path, usecols=usecols)
    lower = {c.lower(): c for c in meta.columns}
    rename = {}
    for name in [
        "timestamp",
        "local_timestamp",
        "rssi",
        "noise_floor",
        "rate",
        "len",
        "fft_gain",
        "agc_gain",
        "compensate_gain_x1000000",
    ]:
        if name in lower:
            rename[lower[name]] = name
    meta = meta.rename(columns=rename)
    if "local_timestamp" in meta.columns and "timestamp" not in meta.columns:
        meta["timestamp"] = meta["local_timestamp"]
    for col in meta.columns:
        if col not in {"type", "mac"}:
            meta[col] = pd.to_numeric(meta[col], errors="coerce")
    if "rssi" in meta.columns and "noise_floor" in meta.columns:
        meta["snr_proxy"] = meta["rssi"] - meta["noise_floor"]
    if "compensate_gain_x1000000" in meta.columns:
        meta["compensate_gain_scale"] = meta["compensate_gain_x1000000"] / 1_000_000.0
    return meta


def timestamp_array(meta: pd.DataFrame) -> np.ndarray:
    if "timestamp" not in meta.columns:
        return np.array([], dtype=float)
    return meta["timestamp"].to_numpy(dtype=float)


def estimate_seconds_per_row(timestamp: np.ndarray) -> float:
    ts = pd.Series(timestamp).dropna().to_numpy(dtype=float)
    if len(ts) < 4:
        return 1.0 / 30.0
    gaps = np.diff(ts)
    gaps = gaps[np.isfinite(gaps) & (gaps > 0)]
    if len(gaps) == 0:
        return 1.0 / 30.0
    med = float(np.median(gaps))
    if med > 1000:
        return med / 1_000_000.0
    if med > 10:
        return med / 1000.0
    return med


def duration_to_rows(seconds: float, timestamp: np.ndarray, n_rows: int) -> tuple[int, str, float]:
    sec_per_row = estimate_seconds_per_row(timestamp)
    if not np.isfinite(sec_per_row) or sec_per_row <= 0:
        sec_per_row = 1.0 / 30.0
    rows = int(math.ceil(seconds / sec_per_row))
    return max(1, min(rows, n_rows)), "timestamp_or_packet_rate_estimate", sec_per_row


def session_id_from_csv(path: Path) -> str:
    return path.stem.upper()


def load_env(dataset_root: str | Path, env_id: str, required_sessions: dict[str, str] | None = None) -> dict:
    root = Path(dataset_root)
    env_path = root / env_id
    scenario = parse_scenario(env_path / "scenario.md")
    room_id, date_id, variable = parse_env_id(env_id)
    sessions = []
    for csv_path in sorted(env_path.glob("s*.csv"), key=lambda p: p.name.lower()):
        sid = session_id_from_csv(csv_path)
        if required_sessions is not None and sid not in required_sessions:
            continue
        row = scenario.get(sid)
        if not row:
            continue
        amp = load_amplitude_csv(csv_path)
        meta = read_metadata_full(csv_path)
        ts = timestamp_array(meta)
        sessions.append(
            {
                "env_id": env_id,
                "room_id": room_id,
                "date_id": date_id,
                "variable": variable,
                "session_id": sid,
                "csv_path": csv_path,
                "label": row.label,
                "position": row.position,
                "amplitude": amp,
                "metadata": meta,
                "timestamp": ts,
                "seconds_per_row": estimate_seconds_per_row(ts),
            }
        )
    empty_sessions = [s for s in sessions if s["label"] == "empty"]
    return {
        "env_id": env_id,
        "sessions": sessions,
        "empty_sessions": empty_sessions,
        "calibration_session": next((s for s in empty_sessions if s["session_id"] == "S01"), empty_sessions[0] if empty_sessions else None),
    }


def session_by_id(env: dict, session_id: str) -> dict:
    for session in env["sessions"]:
        if session["session_id"] == session_id:
            return session
    raise KeyError(f"{session_id} not found in {env.get('env_id')}")


def load_train_envs(dataset_root: str | Path) -> dict[str, dict]:
    if any(env_id.startswith("room4") for env_id in TRAIN_ENVS):
        raise RuntimeError("room4 leaked into room4-excluded training envs")
    return {env_id: load_env(dataset_root, env_id) for env_id in TRAIN_ENVS}

