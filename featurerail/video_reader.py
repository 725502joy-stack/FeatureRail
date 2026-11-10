from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2


@dataclass(frozen=True)
class VideoInfo:
    path: str
    name: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float


class VideoReader:
    """Small synchronous reader intended to live inside a worker thread."""

    def __init__(self, path: str):
        self.path = str(Path(path))
        self.capture = cv2.VideoCapture(self.path)
        if not self.capture.isOpened():
            raise OSError(f"無法開啟影片：{self.path}")
        width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(self.capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or fps != fps:
            fps = 30.0
        if width <= 0 or height <= 0:
            self.close()
            raise OSError("影片回報了無效的影像尺寸。")
        self.info = VideoInfo(
            path=self.path,
            name=Path(self.path).name,
            width=width,
            height=height,
            fps=fps,
            frame_count=max(1, frame_count),
            duration=max(1, frame_count) / fps,
        )

    def read(self) -> tuple[bool, object | None]:
        return self.capture.read()

    def read_at(self, frame_index: int) -> object | None:
        index = min(max(0, int(frame_index)), self.info.frame_count - 1)
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        return frame if ok else None

    def close(self) -> None:
        if getattr(self, "capture", None) is not None:
            self.capture.release()
            self.capture = None

    def __enter__(self) -> "VideoReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
