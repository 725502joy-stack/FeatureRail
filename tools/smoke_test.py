"""Create a synthetic moving-point clip and exercise tracking, camera, reframing and export."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from featurerail.camera_motion import build_camera_path
from featurerail.i18n import LANGUAGES
from featurerail.main_window import MainWindow, PreviewCanvas
from featurerail.point_tracker import TrackResult, track_region
from featurerail.reframer import fit_crop_size, reframe_frame
from featurerail.settings import AppSettings
from featurerail.video_exporter import export_video, find_ffmpeg
from featurerail.video_reader import VideoInfo, VideoReader


def make_frame(width: int, height: int, x: int, y: int, frame_number: int) -> np.ndarray:
    frame = np.full((height, width, 3), (31, 36, 41), dtype=np.uint8)
    for grid_x in range(0, width, 24):
        cv2.line(frame, (grid_x, 0), (grid_x, height - 1), (45, 51, 57), 1)
    for grid_y in range(0, height, 24):
        cv2.line(frame, (0, grid_y), (width - 1, grid_y), (45, 51, 57), 1)
    for row in range(4):
        for column in range(4):
            tone = 245 if (row + column) % 2 else 28
            cv2.rectangle(frame, (x + column * 6, y + row * 6), (x + column * 6 + 5, y + row * 6 + 5), (tone, tone, tone), -1)
    cv2.circle(frame, (x + 12, y + 12), 15, (70, 142, 211), 2)
    cv2.putText(frame, str(frame_number), (6, height - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (160, 165, 170), 1)
    if 43 <= frame_number <= 45:
        cv2.rectangle(frame, (x - 3, y - 3), (x + 27, y + 27), (31, 36, 41), -1)
    if 56 <= frame_number <= 59:
        frame = cv2.GaussianBlur(frame, (9, 9), 2.1)
    return frame


def moving_target_x(index: int) -> int:
    if index < 24:
        return 38 + index
    if index < 38:
        return 62 + (index - 24) * 3
    if index < 58:
        return 104 - (index - 38) * 2
    return 64 + (index - 58)


def check_selection_canvas() -> dict[str, bool]:
    app = QApplication.instance() or QApplication([])
    canvas = PreviewCanvas()
    canvas.resize(640, 400)
    canvas.set_frame(np.zeros((200, 320, 3), dtype=np.uint8), selectable=True)
    canvas.show()
    app.processEvents()
    image_rect = canvas._image_rect()
    start = QPoint(round(image_rect.left() + image_rect.width() * 0.25), round(image_rect.top() + image_rect.height() * 0.25))
    end = QPoint(round(image_rect.left() + image_rect.width() * 0.75), round(image_rect.top() + image_rect.height() * 0.75))
    selected: list[tuple[float, float, float, float]] = []
    canvas.region_selected.connect(lambda *box: selected.append(box))
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, end, delay=20)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
    app.processEvents()
    if not selected or not np.allclose(selected[-1], (0.25, 0.25, 0.75, 0.75), atol=0.015):
        raise AssertionError(f"Drag selection returned the wrong feature region: {selected[-1] if selected else None}")

    wheel = QWheelEvent(
        QPointF(320, 200),
        QPointF(320, 200),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(canvas, wheel)
    app.processEvents()
    if canvas._zoom <= 1.0:
        raise AssertionError("Mouse wheel did not zoom the selection preview.")
    canvas.hide()
    return {"region_drag": True, "wheel_zoom": True}


def check_preview_comparison() -> bool:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    original_settings = AppSettings(**window.settings.__dict__)
    window.save_timer.stop()
    window.rail_check.blockSignals(True)
    window.rail_strength_slider.blockSignals(True)
    window.rail_check.setChecked(True)
    window.rail_strength_slider.setValue(92)
    window.rail_check.blockSignals(False)
    window.rail_strength_slider.blockSignals(False)
    window.settings.rail_mode = True
    window.settings.rail_strength = 92

    width, height, frame_count = 320, 180, 12
    window.info = VideoInfo("synthetic.mp4", "synthetic.mp4", width, height, 30, frame_count, 0.4)
    window.current_frame = 5
    gradient = np.zeros((height, width, 3), dtype=np.uint8)
    gradient[:, :, 0] = np.arange(width, dtype=np.uint8)[None, :]
    gradient[:, :, 1] = np.arange(height, dtype=np.uint8)[:, None]
    window.current_image = gradient
    points = np.column_stack(
        (np.full(frame_count, 100, dtype=np.float32), np.full(frame_count, 90, dtype=np.float32))
    )
    window.track_result = TrackResult(
        points=points,
        confidence=np.ones(frame_count, dtype=np.float32),
        visible=np.ones(frame_count, dtype=bool),
        seed_index=0,
        engine="smoke test",
        seed_box=(90, 80, 110, 100),
        feature_count=16,
    )
    window.camera_path = points.copy()
    window.camera_path[:, 0] += 24
    window._camera_path_dirty = False

    try:
        window._refresh_preview()
        if not window.compare_button.isEnabled():
            raise AssertionError("The A/B preview button was not enabled after tracking completed.")
        if window.canvas._image.width() > 1280 or window.canvas._image.height() > 1280:
            raise AssertionError("The preview rendered above its bounded display resolution.")
        stable_pixel = window.canvas._image.pixelColor(80, 90).rgba()
        window._toggle_preview_comparison()
        if not window.preview_compare_raw or window.preview_state.text() != window._t("COMPARE  /  原始追蹤"):
            raise AssertionError("The A/B button did not switch to the original tracking path.")
        raw_pixel = window.canvas._image.pixelColor(80, 90).rgba()
        if stable_pixel == raw_pixel:
            raise AssertionError("The A/B preview did not render a visibly different camera path.")
        window._toggle_preview_comparison()
        if window.preview_compare_raw or window.preview_state.text() != window._t("OUTPUT  /  軌道穩定"):
            raise AssertionError("The A/B button did not return to the rail-stabilized preview.")
        if set(window._preview_crop_sizes) != {"raw", "stable"}:
            raise AssertionError("The preview did not cache both full-path crop calculations.")
        window.is_playing = True
        window.current_frame = 6
        delayed_frame = gradient.copy()
        window._frame_loaded(5, delayed_frame)
        if window.current_frame != 5 or window.current_image is not delayed_frame:
            raise AssertionError("Playback discarded a decoded frame that arrived one frame late.")
        window.is_playing = False
        for index, (_code, _name) in enumerate(LANGUAGES):
            window.language_combo.setCurrentIndex(index)
            app.processEvents()
            if not window.rail_check.text() or not window.language_combo.currentText():
                raise AssertionError(f"The interface language at index {index} did not load.")
        restore_language_index = window.language_combo.findData(original_settings.language)
        window.language_combo.setCurrentIndex(max(0, restore_language_index))
    finally:
        window.save_timer.stop()
        window.settings = original_settings
        window.close()
        app.processEvents()
    return True


def main() -> None:
    canvas_checks = check_selection_canvas()
    preview_comparison = check_preview_comparison()
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to run the export smoke test.")
    width, height, fps, frame_count = 320, 180, 24, 72
    with tempfile.TemporaryDirectory(prefix="featurerail-smoke-") as temp:
        directory = Path(temp)
        silent_input = directory / "moving_target.avi"
        source = directory / "moving_target_with_audio.mp4"
        output = directory / "featurerail_result.mp4"
        writer = cv2.VideoWriter(
            str(silent_input),
            cv2.VideoWriter_fourcc(*"MJPG"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            raise RuntimeError("OpenCV could not create the synthetic video.")
        expected: list[tuple[float, float]] = []
        selected_box: tuple[float, float, float, float] | None = None
        for index in range(frame_count):
            x = moving_target_x(index)
            expected.append((x + 12.0, 56.0))
            if index == 20:
                selected_box = (float(x), 44.0, float(x + 24), 68.0)
            writer.write(make_frame(width, height, x, 44, index))
        writer.release()

        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(silent_input),
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:sample_rate=44100:duration={frame_count / fps}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                str(source),
            ],
            check=True,
        )

        with VideoReader(str(source)) as reader:
            info = reader.info
        assert selected_box is not None
        result = track_region(info, 20, selected_box, engine="KLT · CPU")
        truth = np.asarray(expected, dtype=np.float32)
        usable = np.r_[0:42, 46:56, 60:frame_count]
        error = np.linalg.norm(result.points[usable] - truth[usable], axis=1)
        median_error = float(np.median(error))
        if median_error > 5.0:
            raise AssertionError(f"KLT median tracking error is too high: {median_error:.2f}px")
        occlusion_confidence = float(np.mean(result.confidence[43:46]))
        visible_confidence = float(np.mean(result.confidence[30:38]))
        if occlusion_confidence >= visible_confidence:
            raise AssertionError("Occlusion was not reflected in the tracking confidence.")

        exact_points = np.column_stack((np.linspace(20, 280, frame_count), np.linspace(35, 145, frame_count))).astype(np.float32)
        exact_confidence = np.ones(frame_count, dtype=np.float32)
        exact_path = build_camera_path(exact_points, exact_confidence)
        if not np.array_equal(exact_path, exact_points):
            raise AssertionError("The tracked region center was altered instead of being frame-locked.")

        fps_value = 30
        time = np.arange(360, dtype=np.float32) / fps_value
        slow_path = np.column_stack(
            (120 + 18 * time, 90 + 4 * np.sin(2 * np.pi * 0.12 * time))
        )
        walking_shake = np.column_stack(
            (
                2.5 * np.sin(2 * np.pi * 1.7 * time),
                2.8 * np.sin(2 * np.pi * 1.8 * time) + 1.2 * np.sin(2 * np.pi * 2.6 * time),
            )
        )
        shaky_path = (slow_path + walking_shake).astype(np.float32)
        rail_path = build_camera_path(
            shaky_path,
            np.ones(len(shaky_path), dtype=np.float32),
            fps_value,
            rail_strength=92,
        )
        interior = (time > 2.0) & (time < 10.0)
        raw_shake_rms = float(np.sqrt(np.mean(np.sum(walking_shake[interior] ** 2, axis=1))))
        stabilized_shake_rms = float(
            np.sqrt(np.mean(np.sum((rail_path[interior] - slow_path[interior]) ** 2, axis=1)))
        )
        if stabilized_shake_rms >= raw_shake_rms * 0.25:
            raise AssertionError(
                f"Rail mode did not sufficiently reduce walking shake: {stabilized_shake_rms:.2f}px remains."
            )

        quick_move = np.full((90, 2), (50.0, 90.0), dtype=np.float32)
        quick_move[20:40, 0] = 50 + 12 * np.arange(20, dtype=np.float32)
        quick_move[40:, 0] = 278
        moving_rail_path = build_camera_path(
            quick_move,
            np.ones(len(quick_move), dtype=np.float32),
            fps_value,
            rail_strength=92,
        )
        if abs(float(moving_rail_path[60, 0]) - 278.0) > 8.0:
            raise AssertionError("Rail mode failed to follow and settle after a fast intentional move.")

        lost_confidence = exact_confidence.copy()
        lost_confidence[30:33] = 0
        held_path = build_camera_path(exact_points, lost_confidence)
        if not np.array_equal(held_path[30:33], np.repeat(held_path[29:30], 3, axis=0)):
            raise AssertionError("The center lock did not hold its last good position during feature loss.")

        edge_image = np.full((height, width, 3), 170, dtype=np.uint8)
        edge_centers = np.asarray(((0.0, height / 2), (width, height / 2), (width / 2, 0.0), (width / 2, height)))
        crop_size = fit_crop_size((width, height), (160, 90), edge_centers, auto_zoom=True)
        for edge in edge_centers:
            cropped = reframe_frame(edge_image, edge, (160, 90), auto_zoom=True, crop_size=crop_size)
            if cropped.shape != (90, 160, 3) or int(cropped.min()) != 170:
                raise AssertionError("Auto crop introduced an empty/black output border near a frame edge.")

        settings = AppSettings(
            auto_crop=True,
            output_preset="Custom",
            custom_width=320,
            custom_height=180,
            codec="H.264",
            encoder="CPU",
            quality=28,
        )
        exported = export_video(info, result.points, result.confidence, settings, str(output))
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,width,height",
                "-of",
                "json",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        streams = json.loads(probe.stdout)["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        if (video["width"], video["height"]) != (320, 180):
            raise AssertionError(f"Unexpected output dimensions: {video}")
        if not any(stream["codec_type"] == "audio" for stream in streams):
            raise AssertionError("Original audio was not preserved in the exported MP4.")
        print(
            json.dumps(
                {
                    "tracking_median_error_px": round(median_error, 3),
                    "occlusion_confidence": round(occlusion_confidence, 3),
                    "visible_confidence": round(visible_confidence, 3),
                    "walking_shake_reduction_percent": round(
                        (1 - stabilized_shake_rms / raw_shake_rms) * 100, 1
                    ),
                    "output": str(output),
                    "encoder": exported.encoder,
                    "audio_preserved": True,
                    "frames": info.frame_count,
                    "canvas_region_drag": canvas_checks["region_drag"],
                    "canvas_wheel_zoom": canvas_checks["wheel_zoom"],
                    "preview_ab_comparison": preview_comparison,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
