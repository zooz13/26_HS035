"""Stage-2 CNN inference helpers."""

from __future__ import annotations

import numpy as np
import torch
from torch import nn


def predict(model: nn.Module, x: np.ndarray, batch_size: int = 256) -> np.ndarray:
    device = next(model.parameters()).device
    model.eval()
    preds = []
    with torch.no_grad():
        for start in range(0, len(x), batch_size):
            xb = torch.from_numpy(x[start : start + batch_size]).float().to(device)
            preds.append(model(xb).argmax(dim=1).cpu().numpy())
    return np.concatenate(preds)
