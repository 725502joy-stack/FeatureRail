from __future__ import annotations

import threading
import math

import numpy as np


def build_camera_path(
    points: np.ndarray,
    confidence: np.ndarray,
    fps: float = 30.0,
    rail_strength: int = 0,
    cancel: threading.Event | None = None,
) -> np.ndarray:
    """Build a zero-phase rail path, adapting stabilization to preserve fast intentional moves."""
    points = np.asarray(points, dtype=np.float32)
    confidence = np.asarray(confidence, dtype=np.float32)
    if points.size == 0:
        return np.empty((0, 2), dtype=np.float32)
    path = points.copy()
    last = path[0].copy()
    for index in range(1, len(path)):
        if index % 128 == 0 and cancel is not None and cancel.is_set():
            raise InterruptedError("置中路徑計算已取消")
        if not np.isfinite(path[index]).all() or confidence[index] < 0.06:
            path[index] = last
        else:
            last = path[index].copy()
    strength = float(np.clip(rail_strength, 0, 100)) / 100.0
    if strength <= 0 or len(path) < 3:
        return path

    fps = max(1.0, float(fps))
    max_sigma = max(1.0, fps * (0.08 + 0.42 * strength))
    coarse = _gaussian_smooth(path, max(0.8, fps * 0.07), cancel)
    velocity = np.zeros(len(path), dtype=np.float32)
    velocity[1:] = np.linalg.norm(np.diff(coarse, axis=0), axis=1) * fps
    velocity = _gaussian_smooth(velocity[:, None], max(0.8, fps * 0.12), cancel)[:, 0]

    smoothed = _gaussian_smooth(path, max_sigma, cancel)
    minimum_sigma = max(0.8, fps * 0.035)
    adaptive_sigma = minimum_sigma + (max_sigma - minimum_sigma) / (1.0 + velocity / 250.0)
    blend = np.clip(adaptive_sigma / max_sigma, 0.18, 1.0)[:, None]
    return (path + (smoothed - path) * blend).astype(np.float32)


def _gaussian_smooth(
    values: np.ndarray,
    sigma: float,
    cancel: threading.Event | None,
) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if len(values) < 2:
        return values.copy()
    radius = max(1, int(math.ceil(3 * sigma)))
    offsets = np.arange(-radius, radius + 1, dtype=np.int64)
    weights = np.exp(-0.5 * (offsets / max(0.1, sigma)) ** 2).astype(np.float32)
    weights /= weights.sum()
    result = np.empty_like(values)
    period = 2 * len(values) - 2
    for start in range(0, len(values), 4096):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("軌道穩定計算已取消")
        end = min(len(values), start + 4096)
        indices = np.arange(start, end, dtype=np.int64)[:, None] + offsets[None, :]
        reflected = np.mod(indices, period)
        reflected = np.where(reflected < len(values), reflected, period - reflected)
        samples = values[reflected]
        result[start:end] = np.sum(samples * weights[None, :, None], axis=1)
    return result
