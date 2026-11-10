from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .camera_motion import build_camera_path
from .reframer import fit_crop_size, reframe_frame
from .settings import AppSettings
from .video_reader import VideoInfo, VideoReader


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class ExportResult:
    path: str
    encoder: str
    warning: str = ""


def find_ffmpeg() -> str | None:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        bundled_ffmpeg = Path(bundle_dir) / "ffmpeg.exe"
        if bundled_ffmpeg.is_file():
            return str(bundled_ffmpeg)
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def available_encoders(ffmpeg: str | None = None) -> set[str]:
    executable = ffmpeg or find_ffmpeg()
    if not executable:
        return set()
    result = subprocess.run(
        [executable, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return {name for name in ("libx264", "libx265", "h264_amf", "hevc_amf") if name in result.stdout}


def export_video(
    info: VideoInfo,
    points: np.ndarray,
    confidence: np.ndarray,
    settings: AppSettings,
    output_path: str,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> ExportResult:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise FileNotFoundError("找不到 FFmpeg。請先安裝 FFmpeg，並將 ffmpeg.exe 加入 PATH。")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = settings.output_size(info.width, info.height)
    camera = build_camera_path(
        points,
        confidence,
        info.fps,
        settings.rail_strength if settings.rail_mode else 0,
        cancel,
    )
    crop_size = fit_crop_size((info.width, info.height), (width, height), camera, settings.auto_crop)
    installed = available_encoders(ffmpeg)
    codec = settings.codec.upper()
    hardware_name = "h264_amf" if codec == "H.264" else "hevc_amf"
    software_name = "libx264" if codec == "H.264" else "libx265"
    requested = settings.encoder
    if requested == "AMD AMF":
        if hardware_name not in installed:
            raise RuntimeError(f"這個 FFmpeg 沒有 {hardware_name} 編碼器。請改選 CPU 編碼或安裝含 AMF 的 FFmpeg。")
        encoders = [hardware_name, software_name]
    elif requested == "CPU":
        encoders = [software_name]
    else:
        encoders = ([hardware_name] if hardware_name in installed else []) + [software_name]

    chosen = ""
    warning = ""
    with tempfile.TemporaryDirectory(prefix="featurerail-", dir=str(target.parent)) as workdir:
        silent_video = Path(workdir) / "video_only.mp4"
        muxed_video = Path(workdir) / "complete.mp4"
        for encoder in encoders:
            _check_cancel(cancel)
            try:
                _encode_frames(
                    ffmpeg,
                    encoder,
                    silent_video,
                    info,
                    camera,
                    (width, height),
                    settings,
                    crop_size,
                    progress,
                    cancel,
                )
                chosen = encoder
                break
            except (RuntimeError, BrokenPipeError) as error:
                silent_video.unlink(missing_ok=True)
                if encoder == hardware_name and len(encoders) > 1:
                    warning = f"AMF 初始化失敗，已改用 CPU 編碼：{error}"
                    if progress:
                        progress(0, info.frame_count, "AMF 不可用，切換 CPU 編碼")
                    continue
                raise
        if not chosen:
            raise RuntimeError("無可用的影片編碼器。")
        _check_cancel(cancel)
        mux_message = _mux_audio(ffmpeg, silent_video, info.path, muxed_video, info.duration, cancel)
        if mux_message:
            warning = "音訊改用 AAC 重新編碼，以保留原片聲音。"
        _check_cancel(cancel)
        os.replace(muxed_video, target)
    if progress:
        progress(info.frame_count, info.frame_count, "輸出完成")
    return ExportResult(str(target), chosen, warning)


def _encode_frames(
    ffmpeg: str,
    encoder: str,
    destination: Path,
    info: VideoInfo,
    camera: np.ndarray,
    output_size: tuple[int, int],
    settings: AppSettings,
    crop_size: tuple[int, int],
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> None:
    width, height = output_size
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(Fraction(info.fps).limit_denominator(1001)),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        encoder,
        "-pix_fmt",
        "yuv420p",
    ]
    quality = max(0, min(51, int(settings.quality)))
    if encoder in {"h264_amf", "hevc_amf"}:
        command += ["-quality", "quality", "-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(quality + 2)]
    else:
        command += ["-preset", "medium", "-crf", str(quality)]
        if encoder == "libx265":
            command += ["-x265-params", "log-level=error"]
    command += ["-movflags", "+faststart", str(destination)]

    with tempfile.TemporaryFile() as error_log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=error_log)
        written = 0
        try:
            with VideoReader(info.path) as reader:
                for index in range(info.frame_count):
                    _check_cancel(cancel)
                    ok, frame = reader.read()
                    if not ok:
                        break
                    output = reframe_frame(frame, camera[index], output_size, settings.auto_crop, crop_size)
                    try:
                        process.stdin.write(output.tobytes())
                    except (BrokenPipeError, OSError):
                        raise RuntimeError("FFmpeg 編碼程序意外結束。")
                    written += 1
                    if progress and (written % max(1, info.frame_count // 120) == 0 or written == info.frame_count):
                        progress(written, info.frame_count, f"編碼影格 · {encoder}")
            process.stdin.close()
            return_code = process.wait()
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
            raise
        if return_code != 0:
            error_log.seek(0)
            detail = error_log.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"FFmpeg 編碼器 {encoder} 結束，代碼 {return_code}。")
        if written == 0:
            raise RuntimeError("影片沒有讀到任何影格。")


def _mux_audio(
    ffmpeg: str,
    silent: Path,
    source: str,
    output: Path,
    duration: float,
    cancel: threading.Event | None,
) -> bool:
    base = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(silent),
        "-i",
        source,
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-t",
        f"{duration:.6f}",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return_code, error = _run_mux_command(base, cancel)
    if return_code == 0:
        return False
    output.unlink(missing_ok=True)
    encoded = base.copy()
    audio_index = encoded.index("copy", encoded.index("-c:a"))
    encoded[audio_index] = "aac"
    encoded += ["-b:a", "192k"]
    return_code, error = _run_mux_command(encoded, cancel)
    if return_code != 0:
        raise RuntimeError(f"影片輸出失敗：{error}")
    return True


def _run_mux_command(command: list[str], cancel: threading.Event | None) -> tuple[int, str]:
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    while True:
        if cancel is not None and cancel.is_set():
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise InterruptedError("輸出已取消")
        try:
            _stdout, stderr = process.communicate(timeout=0.15)
            return process.returncode, stderr.decode("utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired:
            continue


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise InterruptedError("輸出已取消")
