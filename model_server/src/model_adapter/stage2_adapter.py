from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import torch
from torch import nn


class Stage2AdapterError(ValueError):
    pass


class TinyCnn(nn.Module):
    def __init__(self, n_features: int, n_classes: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(n_features, 32, kernel_size=5, padding=2),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(32, 64, kernel_size=5, padding=2),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.head = nn.Sequential(nn.Flatten(), nn.Dropout(0.2), nn.Linear(64, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.net(x.transpose(1, 2)))


class Stage2Adapter:
    def __init__(self, artifact_path: str | Path):
        self.artifact_path = Path(artifact_path)
        self.metadata: Dict[str, Any] = {}
        self.label_mapping: Dict[str, int] = {}
        self.model: TinyCnn | None = None
        self.loaded = False

    def load(self) -> None:
        metadata_path = self.artifact_path / "metadata.json"
        label_mapping_path = self.artifact_path / "label_mapping.json"
        checkpoint_path = self.artifact_path / "model.pt"
        if not metadata_path.exists():
            raise Stage2AdapterError(f"missing metadata.json at {metadata_path}")
        if not label_mapping_path.exists():
            raise Stage2AdapterError(f"missing label_mapping.json at {label_mapping_path}")
        if not checkpoint_path.exists():
            raise Stage2AdapterError(f"missing model.pt at {checkpoint_path}")
        self.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.label_mapping = json.loads(label_mapping_path.read_text(encoding="utf-8"))
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        n_features = int(checkpoint.get("n_features", self.metadata.get("input_shape", [128, 192])[1]))
        n_classes = int(checkpoint.get("n_classes", 2))
        model = TinyCnn(n_features, n_classes)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        self.model = model
        self.loaded = True

    def validate_input(self, stage2_input: Dict[str, Any]) -> np.ndarray:
        if not self.loaded:
            self.load()
        if stage2_input.get("input_type") != "window_matrix":
            raise Stage2AdapterError("stage2_input.input_type must be window_matrix")
        expected_feature = self.metadata.get("feature_version")
        if expected_feature and stage2_input.get("feature_version") != expected_feature:
            raise Stage2AdapterError(
                f"stage2 feature_version mismatch: got {stage2_input.get('feature_version')}, expected {expected_feature}"
            )
        values = stage2_input.get("values")
        if not isinstance(values, list) or not values:
            raise Stage2AdapterError("stage2_input.values must be a non-empty matrix")
        matrix = np.asarray(values, dtype=np.float32)
        expected_shape = list(self.metadata.get("input_shape", [128, 192]))
        if list(matrix.shape) != expected_shape:
            raise Stage2AdapterError(f"stage2 input shape mismatch: got {list(matrix.shape)}, expected {expected_shape}")
        if not np.isfinite(matrix).all():
            raise Stage2AdapterError("stage2 input contains NaN or infinite values")
        return matrix

    def predict(self, stage2_input: Dict[str, Any]) -> Dict[str, Any]:
        if not self.loaded:
            self.load()
        if self.model is None:
            raise Stage2AdapterError("Stage 2 model is not loaded")
        matrix = self.validate_input(stage2_input)
        with torch.no_grad():
            logits_t = self.model(torch.from_numpy(matrix[None, :, :]).float())
            prob_t = torch.softmax(logits_t, dim=1)
        logits = [float(value) for value in logits_t[0].tolist()]
        probs = [float(value) for value in prob_t[0].tolist()]
        class_id = int(np.argmax(probs))
        reverse = {int(v): str(k) for k, v in self.label_mapping.items()}
        label = reverse.get(class_id)
        if label is None:
            raise Stage2AdapterError(f"unknown Stage 2 class id: {class_id}")
        return {
            "label": label,
            "confidence": probs[class_id],
            "logits": logits,
            "artifact_id": self.metadata.get("artifact_id", "stage2_static_moving_v001"),
        }

    def get_model_info(self) -> Dict[str, Any]:
        if not self.loaded:
            self.load()
        return {
            "artifact_id": self.metadata.get("artifact_id"),
            "artifact_path": str(self.artifact_path),
            "input_type": self.metadata.get("input_type"),
            "input_shape": self.metadata.get("input_shape"),
            "feature_version": self.metadata.get("feature_version"),
            "label_mapping": self.label_mapping,
        }
