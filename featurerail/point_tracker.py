from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from .video_reader import VideoInfo, VideoReader


@dataclass
class TrackResult:
    points: np.ndarray
    confidence: np.ndarray
    visible: np.ndarray
    seed_index: int
    engine: str
    seed_box: tuple[float, float, float, float]
    feature_count: int
    warning: str = ""


ProgressCallback = Callable[[int, int, str], None]


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise InterruptedError("追蹤已取消")


def _normalise_box(
    selection_box: tuple[float, float, float, float], width: int, height: int
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = map(float, selection_box)
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    left = float(np.clip(left, 0, width - 1))
    right = float(np.clip(right, 1, width))
    top = float(np.clip(top, 0, height - 1))
    bottom = float(np.clip(bottom, 1, height))
    if right - left < 5 or bottom - top < 5:
        raise ValueError("框選範圍太小，請放大後框住特徵的紋理或邊緣。")
    return left, top, right, bottom


def _seed_features(
    gray: np.ndarray, selection_box: tuple[float, float, float, float], max_features: int = 64
) -> np.ndarray:
    height, width = gray.shape[:2]
    x0, y0, x1, y1 = _normalise_box(selection_box, width, height)
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[int(math.floor(y0)):int(math.ceil(y1)), int(math.floor(x0)):int(math.ceil(x1))] = 255
    corners = cv2.goodFeaturesToTrack(
        gray,
        maxCorners=max_features,
        qualityLevel=0.01,
        minDistance=3,
        mask=mask,
        blockSize=5,
        useHarrisDetector=False,
    )
    if corners is None or not len(corners):
        raise ValueError("框內找不到可追蹤的角點。請放大預覽，框選有明顯紋理或輪廓的區域。")
    return corners.reshape(-1, 2).astype(np.float32)


def track_region(
    info: VideoInfo,
    seed_index: int,
    selection_box: tuple[float, float, float, float],
    engine: str = "KLT · CPU",
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> TrackResult:
    """Track a box's internal features and return its robust center trajectory."""
    box = _normalise_box(selection_box, info.width, info.height)
    if "AI" in engine:
        try:
            return _track_cotracker(info, seed_index, box, progress, cancel)
        except InterruptedError:
            raise
        except Exception as error:
            warning = f"CoTracker 3 無法使用（{error}），已改用 OpenCV KLT。"
            result = _track_klt(info, seed_index, box, progress, cancel)
            result.warning = warning
            return result
    return _track_klt(info, seed_index, box, progress, cancel)


def _track_klt(
    info: VideoInfo,
    seed_index: int,
    box: tuple[float, float, float, float],
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> TrackResult:
    count = info.frame_count
    seed = min(max(0, int(seed_index)), count - 1)
    x0, y0, x1, y1 = box
    box_center = np.array(((x0 + x1) / 2, (y0 + y1) / 2), dtype=np.float32)
    points = np.full((count, 2), np.nan, dtype=np.float32)
    confidence = np.zeros(count, dtype=np.float32)
    visible = np.zeros(count, dtype=bool)
    points[seed] = box_center
    confidence[seed] = 1.0
    visible[seed] = True

    with VideoReader(info.path) as reader:
        seed_frame = reader.read_at(seed)
        if seed_frame is None:
            raise OSError("無法讀取框選特徵所在的影格。")
        seed_gray = cv2.cvtColor(seed_frame, cv2.COLOR_BGR2GRAY)
        seed_features = _seed_features(seed_gray, box)
        feature_count = len(seed_features)

        def walk(direction: int) -> None:
            index = seed + direction
            previous_gray = seed_gray
            feature_positions = seed_features.copy()
            center = box_center.copy()
            while 0 <= index < count:
                _check_cancel(cancel)
                if direction > 0:
                    ok, frame = reader.read()
                    if not ok:
                        break
                else:
                    frame = reader.read_at(index)
                    if frame is None:
                        break
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                source = feature_positions.reshape(-1, 1, 2)
                candidates, status, _eigen = cv2.calcOpticalFlowPyrLK(
                    previous_gray,
                    gray,
                    source,
                    None,
                    winSize=(31, 31),
                    maxLevel=3,
                    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 25, 0.01),
                    flags=cv2.OPTFLOW_LK_GET_MIN_EIGENVALS,
                )
                accepted = np.zeros(feature_count, dtype=bool)
                score = 0.0
                if candidates is not None and status is not None:
                    candidate_points = candidates.reshape(-1, 2)
                    back, back_status, _ = cv2.calcOpticalFlowPyrLK(
                        gray,
                        previous_gray,
                        candidates,
                        None,
                        winSize=(31, 31),
                        maxLevel=3,
                        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.01),
                    )
                    if back is not None and back_status is not None:
                        fb_error = np.linalg.norm(back.reshape(-1, 2) - feature_positions, axis=1)
                        inside = (
                            (candidate_points[:, 0] >= 0)
                            & (candidate_points[:, 0] < info.width)
                            & (candidate_points[:, 1] >= 0)
                            & (candidate_points[:, 1] < info.height)
                        )
                        preliminary = status.reshape(-1).astype(bool) & back_status.reshape(-1).astype(bool) & inside & (fb_error < 4.5)
                        if preliminary.any():
                            appearance = np.ones(feature_count, dtype=np.float32)
                            for feature_index in np.flatnonzero(preliminary):
                                old_patch = cv2.getRectSubPix(
                                    previous_gray, (11, 11), tuple(feature_positions[feature_index])
                                )
                                new_patch = cv2.getRectSubPix(
                                    gray, (11, 11), tuple(candidate_points[feature_index])
                                )
                                difference = float(
                                    np.mean(np.abs(old_patch.astype(np.float32) - new_patch.astype(np.float32)))
                                )
                                appearance[feature_index] = math.exp(-max(0.0, difference - 6.0) / 32.0)
                            flow = candidate_points - feature_positions
                            median_flow = np.median(flow[preliminary], axis=0)
                            residual = np.linalg.norm(flow - median_flow, axis=1)
                            inlier_limit = max(3.0, min(18.0, math.hypot(x1 - x0, y1 - y0) * 0.2))
                            accepted = preliminary & (residual <= inlier_limit) & (appearance >= 0.12)
                            if accepted.any():
                                motion = np.median(flow[accepted], axis=0)
                                center += motion
                                feature_positions[accepted] = candidate_points[accepted]
                                local_quality = np.exp(-fb_error[accepted] / 2.0) * appearance[accepted]
                                coverage = float(accepted.sum()) / feature_count
                                score = coverage * float(np.mean(local_quality))

                points[index] = center
                confidence[index] = float(np.clip(score, 0.0, 1.0))
                visible[index] = confidence[index] >= 0.18
                previous_gray = gray
                if progress and (index % 4 == 0 or index == count - 1 or index == 0):
                    completed = index - seed if direction > 0 else count - 1 - index
                    progress(completed, max(1, count - 1), "KLT 區域特徵")
                index += direction

        walk(1)
        _check_cancel(cancel)
        walk(-1)

    valid = np.flatnonzero(np.isfinite(points[:, 0]))
    if len(valid) != count:
        if not len(valid):
            raise RuntimeError("追蹤沒有產生有效特徵座標。")
        for axis in range(2):
            points[:, axis] = np.interp(np.arange(count), valid, points[valid, axis])
    return TrackResult(points, confidence, visible, seed, "OpenCV KLT", box, feature_count)


def _track_cotracker(
    info: VideoInfo,
    seed_index: int,
    box: tuple[float, float, float, float],
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> TrackResult:
    import torch

    count = info.frame_count
    seed = min(max(0, int(seed_index)), count - 1)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = torch.hub.load("facebookresearch/co-tracker", "cotracker3_offline", trust_repo=True)
    model = model.to(device).eval()
    scale = min(1.0, 768.0 / info.width, 768.0 / info.height)
    frame_size = (max(64, round(info.width * scale)), max(64, round(info.height * scale)))
    x0, y0, x1, y1 = box
    box_center = np.array(((x0 + x1) / 2, (y0 + y1) / 2), dtype=np.float32)
    paths: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []

    with VideoReader(info.path) as reader:
        seed_frame = reader.read_at(seed)
        if seed_frame is None:
            raise OSError("無法讀取 CoTracker 起始影格。")
        seed_features = _seed_features(cv2.cvtColor(seed_frame, cv2.COLOR_BGR2GRAY), box)
        feature_count = len(seed_features)
        with torch.inference_mode():
            for direction in (1, -1):
                index = seed
                base_features = seed_features * scale
                boundary_frame = seed_frame
                while 0 <= index < count:
                    _check_cancel(cancel)
                    indices = [index]
                    frames = [boundary_frame]
                    for _ in range(31):
                        next_index = indices[-1] + direction
                        if not 0 <= next_index < count:
                            break
                        if direction > 0:
                            ok, frame = reader.read()
                            if not ok:
                                break
                        else:
                            frame = reader.read_at(next_index)
                            if frame is None:
                                break
                        indices.append(next_index)
                        frames.append(frame)
                    rgb = [
                        cv2.cvtColor(cv2.resize(frame, frame_size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
                        for frame in frames
                    ]
                    tensor = torch.from_numpy(np.stack(rgb)).permute(0, 3, 1, 2).unsqueeze(0).float().to(device)
                    query = torch.cat(
                        (
                            torch.zeros((1, len(base_features), 1), device=device),
                            torch.as_tensor(base_features, device=device).reshape(1, -1, 2),
                        ),
                        dim=2,
                    )
                    tracks, visibility = model(tensor, queries=query)
                    tracked = tracks[0].detach().float().cpu().numpy()  # time × features × xy, resized coordinates
                    visible = visibility[0].detach().float().cpu().numpy()  # time × features
                    centers = box_center + np.median(tracked - base_features[None, :, :], axis=1) / scale
                    frame_confidence = np.mean(visible, axis=1)
                    paths.append((np.asarray(indices), centers, frame_confidence))
                    index = indices[-1]
                    base_features = tracked[-1]
                    boundary_frame = frames[-1]
                    if progress:
                        done = min(count, sum(len(item[0]) for item in paths))
                        progress(done, count, f"CoTracker 3 · {device.upper()}")
                    if len(indices) <= 1:
                        break

    points = np.full((count, 2), np.nan, dtype=np.float32)
    confidence = np.zeros(count, dtype=np.float32)
    for indices, centers, frame_confidence in paths:
        points[indices] = centers
        confidence[indices] = frame_confidence
    visible = confidence >= 0.5
    valid = np.flatnonzero(np.isfinite(points[:, 0]))
    if len(valid) != count:
        if not len(valid):
            raise RuntimeError("CoTracker 沒有回傳有效區域軌跡。")
        for axis in range(2):
            points[:, axis] = np.interp(np.arange(count), valid, points[valid, axis])
    return TrackResult(points, confidence, visible, seed, f"CoTracker 3 · {device.upper()}", box, feature_count)
