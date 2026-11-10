from __future__ import annotations

import math

import cv2
import numpy as np


def fit_crop_size(
    source_size: tuple[int, int],
    output_size: tuple[int, int],
    centers: np.ndarray,
    auto_zoom: bool = True,
) -> tuple[int, int]:
    """Choose one stable, border-safe crop for the full shot while preserving its aspect ratio."""
    source_w, source_h = source_size
    output_w, output_h = output_size
    aspect = output_w / max(1, output_h)
    if auto_zoom and len(centers):
        centers = np.asarray(centers, dtype=np.float64)
        centers = centers[np.isfinite(centers).all(axis=1)]
    else:
        centers = np.empty((0, 2), dtype=np.float64)

    if len(centers):
        left_room = max(0.0, float(np.min(centers[:, 0])))
        right_room = max(0.0, float(source_w - np.max(centers[:, 0])))
        top_room = max(0.0, float(np.min(centers[:, 1])))
        bottom_room = max(0.0, float(source_h - np.max(centers[:, 1])))
        safe_w = max(2, min(source_w, int(math.floor(2 * min(left_room, right_room)))))
        safe_h = max(2, min(source_h, int(math.floor(2 * min(top_room, bottom_room)))))
    else:
        safe_w, safe_h = source_w, source_h

    if safe_w / max(1, safe_h) >= aspect:
        crop_h = safe_h
        crop_w = max(2, min(safe_w, int(math.floor(crop_h * aspect))))
    else:
        crop_w = safe_w
        crop_h = max(2, min(safe_h, int(math.floor(crop_w / aspect))))
    return crop_w, crop_h


def reframe_frame(
    frame: np.ndarray,
    camera_center: tuple[float, float] | np.ndarray,
    output_size: tuple[int, int],
    auto_zoom: bool = True,
    crop_size: tuple[int, int] | None = None,
) -> np.ndarray:
    """Center the tracked feature exactly and scale the image to fill the output without black edges."""
    source_h, source_w = frame.shape[:2]
    output_w, output_h = output_size
    if crop_size is None:
        crop_size = fit_crop_size((source_w, source_h), output_size, np.asarray([camera_center]), auto_zoom)
    crop_w = max(2, min(source_w, int(crop_size[0])))
    crop_h = max(2, min(source_h, int(crop_size[1])))
    cx = float(np.clip(camera_center[0], 0, source_w - 1))
    cy = float(np.clip(camera_center[1], 0, source_h - 1))
    patch = cv2.getRectSubPix(frame, (crop_w, crop_h), (cx, cy))
    interpolation = cv2.INTER_AREA if crop_w > output_w or crop_h > output_h else cv2.INTER_CUBIC
    return cv2.resize(patch, (output_w, output_h), interpolation=interpolation)
