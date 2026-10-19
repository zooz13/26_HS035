from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


DEFAULT_CONFIG: Dict[str, Any] = {
    "model": {
        "model_version": "stage1_threshold_v001",
        "stage1_mode": "threshold",
        "stage1_artifact_path": "artifacts/models/stage1/stage1_threshold_v001",
        "stage2_mode": "static_moving_cnn",
        "stage2_artifact_path": "artifacts/models/stage2/stage2_static_moving_v001",
    },
    "state_store": {
        "backend": "memory",
        "max_room_result_history": 1000,
    },
    "s3_upload": {
        "bucket": "project6-52-oregon-csi-project-s3",
        "region": "us-west-2",
        "presign_ttl_sec": 900,
        "allowed_prefixes": ["rawlog/", "preprocessed/"],
    },
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_model_server_config(path: str | Path | None = None) -> Dict[str, Any]:
    config = _deep_copy(DEFAULT_CONFIG)
    config_path = Path(path) if path is not None else repo_root() / "configs" / "model_server.yaml"
    if not config_path.exists():
        return config

    parsed = _parse_simple_yaml(config_path.read_text(encoding="utf-8"))
    _merge(config, parsed)
    model = config.setdefault("model", {})
    artifact_path = model.get("stage1_artifact_path")
    if artifact_path:
        path_obj = Path(str(artifact_path))
        if not path_obj.is_absolute():
            model["stage1_artifact_path"] = str(repo_root() / path_obj)
    stage2_artifact_path = model.get("stage2_artifact_path")
    if stage2_artifact_path:
        path_obj = Path(str(stage2_artifact_path))
        if not path_obj.is_absolute():
            model["stage2_artifact_path"] = str(repo_root() / path_obj)
    return config


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    stack: list[tuple[int, Dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        key, _, value = raw_line.strip().partition(":")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        current = stack[-1][1]
        if not value.strip():
            new_node: Dict[str, Any] = {}
            current[key] = new_node
            stack.append((indent, new_node))
        else:
            current[key] = _coerce_value(value.strip())
    return root


def _coerce_value(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        body = value[1:-1].strip()
        if not body:
            return []
        return [_coerce_value(part.strip()) for part in body.split(",")]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value.strip("\"'")


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _deep_copy(value: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, item in value.items():
        out[key] = _deep_copy(item) if isinstance(item, dict) else item
    return out
