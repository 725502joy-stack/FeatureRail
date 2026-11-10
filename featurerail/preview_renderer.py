from __future__ import annotations

import numpy as np

from .reframer import reframe_frame


def render_output_preview(
    frame: np.ndarray,
    camera_center: tuple[float, float] | np.ndarray,
    output_size: tuple[int, int],
    auto_zoom: bool,
    crop_size: tuple[int, int],
) -> np.ndarray:
    return reframe_frame(frame, camera_center, output_size, auto_zoom, crop_size)
