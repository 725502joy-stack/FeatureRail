from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPalette, QRadialGradient
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QPropertyAnimation
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect

from .camera_motion import build_camera_path
from .i18n import LANGUAGES, translate
from .point_tracker import TrackResult, track_region
from .preview_renderer import render_output_preview
from .reframer import fit_crop_size
from .settings import AppSettings
from .video_exporter import export_video
from .video_reader import VideoInfo, VideoReader


class PreviewCanvas(QWidget):
    region_selected = Signal(float, float, float, float)
    zoom_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(500, 330)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._image: QImage | None = None
        self._selection_box: tuple[float, float, float, float] | None = None
        self._locked = False
        self._selectable = False
        self._hovered = False
        self._zoom = 1.0
        self._view_center: tuple[float, float] | None = None
        self._drag_start: QPointF | None = None
        self._drag_start_image: tuple[float, float] | None = None
        self._drag_current_image: tuple[float, float] | None = None
        self._pan_start: QPointF | None = None
        self._pan_start_center: tuple[float, float] | None = None
        self.language = "en"

    def set_frame(
        self,
        frame: np.ndarray | None,
        selection_box: tuple[float, float, float, float] | None = None,
        locked: bool = False,
        selectable: bool = False,
    ) -> None:
        old_size = (self._image.width(), self._image.height()) if self._image is not None else None
        self._image = None
        if frame is not None and frame.size:
            rgb = frame[:, :, ::-1].copy()
            height, width = rgb.shape[:2]
            self._image = QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()
            if old_size != (width, height):
                self.reset_zoom(emit=False)
        self._selection_box = selection_box
        self._locked = locked
        self._selectable = selectable
        self.update()

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        if self._image is None:
            return
        if anchor is None:
            anchor = self._base_area().center()
        old_rect = self._image_rect()
        image_anchor = (
            (anchor.x() - old_rect.left()) / max(1e-6, old_rect.width()) * self._image.width(),
            (anchor.y() - old_rect.top()) / max(1e-6, old_rect.height()) * self._image.height(),
        )
        self._zoom = float(np.clip(self._zoom * factor, 1.0, 8.0))
        new_scale = self._base_scale() * self._zoom
        area_center = self._base_area().center()
        self._view_center = (
            image_anchor[0] - (anchor.x() - area_center.x()) / new_scale,
            image_anchor[1] - (anchor.y() - area_center.y()) / new_scale,
        )
        self._clamp_view_center()
        self.zoom_changed.emit(self._zoom)
        self.update()

    def reset_zoom(self, emit: bool = True) -> None:
        self._zoom = 1.0
        if self._image is not None:
            self._view_center = (self._image.width() / 2, self._image.height() / 2)
        else:
            self._view_center = None
        if emit:
            self.zoom_changed.emit(self._zoom)
        self.update()

    def _base_area(self) -> QRectF:
        return QRectF(self.contentsRect().adjusted(14, 14, -14, -14))

    def _base_scale(self) -> float:
        if self._image is None:
            return 1.0
        area = self._base_area()
        return max(1e-6, min(area.width() / self._image.width(), area.height() / self._image.height()))

    def _clamp_view_center(self) -> None:
        if self._image is None:
            return
        area = self._base_area()
        scale = self._base_scale() * self._zoom
        visible_w = area.width() / scale
        visible_h = area.height() / scale
        center_x, center_y = self._view_center or (self._image.width() / 2, self._image.height() / 2)
        if visible_w >= self._image.width():
            center_x = self._image.width() / 2
        else:
            center_x = float(np.clip(center_x, visible_w / 2, self._image.width() - visible_w / 2))
        if visible_h >= self._image.height():
            center_y = self._image.height() / 2
        else:
            center_y = float(np.clip(center_y, visible_h / 2, self._image.height() - visible_h / 2))
        self._view_center = (center_x, center_y)

    def _image_rect(self) -> QRectF:
        if self._image is None:
            return QRectF()
        area = self._base_area()
        scale = self._base_scale() * self._zoom
        center_x, center_y = self._view_center or (self._image.width() / 2, self._image.height() / 2)
        width = self._image.width() * scale
        height = self._image.height() * scale
        left = area.center().x() - center_x * scale
        top = area.center().y() - center_y * scale
        return QRectF(left, top, width, height)

    def _image_position(self, position: QPointF) -> tuple[float, float]:
        rect = self._image_rect()
        return (
            float(np.clip((position.x() - rect.left()) / max(1e-6, rect.width()) * self._image.width(), 0, self._image.width())),
            float(np.clip((position.y() - rect.top()) / max(1e-6, rect.height()) * self._image.height(), 0, self._image.height())),
        )

    def _display_box(self, box: tuple[float, float, float, float]) -> QRectF:
        rect = self._image_rect()
        x0, y0, x1, y1 = box
        return QRectF(
            rect.left() + x0 / self._image.width() * rect.width(),
            rect.top() + y0 / self._image.height() * rect.height(),
            (x1 - x0) / self._image.width() * rect.width(),
            (y1 - y0) / self._image.height() * rect.height(),
        )

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        backdrop = QRadialGradient(self.rect().center(), max(self.width(), self.height()) * 0.82)
        backdrop.setColorAt(0, QColor("#121821"))
        backdrop.setColorAt(0.62, QColor("#0a0d12"))
        backdrop.setColorAt(1, QColor("#07090d"))
        painter.fillRect(self.rect(), backdrop)
        if self._image is None:
            painter.setPen(QColor("#5f6872"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                translate("開啟影片後，拖曳框選一個清晰特徵", self.language),
            )
            return
        image_rect = self._image_rect()
        painter.drawImage(image_rect, self._image)
        painter.setPen(QPen(QColor("#20262d"), 1))
        painter.drawRect(image_rect)
        box = self._selection_box or self._drag_current_image and self._drag_start_image and (
            self._drag_start_image[0], self._drag_start_image[1], self._drag_current_image[0], self._drag_current_image[1]
        )
        if box is not None:
            left, right = sorted((box[0], box[2]))
            top, bottom = sorted((box[1], box[3]))
            region = self._display_box((left, top, right, bottom))
            color = QColor("#f1b75a")
            painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine if self._selectable else Qt.PenStyle.SolidLine))
            painter.setBrush(QColor(241, 183, 90, 30))
            painter.drawRect(region)
            painter.setBrush(color)
            for corner in (region.topLeft(), region.topRight(), region.bottomLeft(), region.bottomRight()):
                painter.drawEllipse(corner, 3, 3)
        if self._locked:
            center = image_rect.center()
            painter.setPen(QPen(QColor("#f1b75a"), 1.5))
            painter.setBrush(QColor(241, 183, 90, 35))
            painter.drawEllipse(center, 12, 12)
            painter.drawLine(QPoint(round(center.x() - 22), round(center.y())), QPoint(round(center.x() - 8), round(center.y())))
            painter.drawLine(QPoint(round(center.x() + 8), round(center.y())), QPoint(round(center.x() + 22), round(center.y())))
            painter.drawLine(QPoint(round(center.x()), round(center.y() - 22)), QPoint(round(center.x()), round(center.y() - 8)))
            painter.drawLine(QPoint(round(center.x()), round(center.y() + 8)), QPoint(round(center.x()), round(center.y() + 22)))
        if self._selectable:
            label = translate("拖曳框選特徵", self.language) if self._hovered else translate("框選追蹤特徵", self.language)
            painter.setPen(QColor("#aab2bc"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(int(image_rect.left() + 12), int(image_rect.top() + 24), label)

    def mouseMoveEvent(self, event: object) -> None:
        position = event.position()
        if self._pan_start is not None and self._pan_start_center is not None:
            scale = self._base_scale() * self._zoom
            delta = position - self._pan_start
            self._view_center = (
                self._pan_start_center[0] - delta.x() / scale,
                self._pan_start_center[1] - delta.y() / scale,
            )
            self._clamp_view_center()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._drag_start is not None:
            self._drag_current_image = self._image_position(position)
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._hovered = self._selectable and self._image_rect().contains(position)
            self.setCursor(Qt.CursorShape.CrossCursor if self._hovered else Qt.CursorShape.ArrowCursor)
        self.update()

    def leaveEvent(self, _event: object) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, event: object) -> None:
        if self._image is None:
            return
        position = event.position()
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan_start = position
            self._pan_start_center = self._view_center or (self._image.width() / 2, self._image.height() / 2)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self._selectable and event.button() == Qt.MouseButton.LeftButton and self._image_rect().contains(position):
            self._drag_start = position
            self._drag_start_image = self._image_position(position)
            self._drag_current_image = self._drag_start_image
            self._selection_box = None
            self.update()

    def mouseReleaseEvent(self, event: object) -> None:
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan_start = None
            self._pan_start_center = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton or self._drag_start is None or self._drag_start_image is None:
            return
        start = self._drag_start
        end = event.position()
        first = self._drag_start_image
        last = self._image_position(end)
        self._drag_start = None
        self._drag_start_image = None
        self._drag_current_image = None
        if abs(end.x() - start.x()) < 7 or abs(end.y() - start.y()) < 7:
            self.update()
            return
        x0, x1 = sorted((first[0], last[0]))
        y0, y1 = sorted((first[1], last[1]))
        if x1 - x0 < 5 or y1 - y0 < 5:
            self.update()
            return
        self._selection_box = (x0, y0, x1, y1)
        self.region_selected.emit(
            x0 / self._image.width(), y0 / self._image.height(),
            x1 / self._image.width(), y1 / self._image.height(),
        )
        self.update()

    def wheelEvent(self, event: object) -> None:
        if self._image is None:
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.2 if delta > 0 else 1 / 1.2, event.position())
            event.accept()


class FrameReaderThread(QThread):
    opened = Signal(object)
    frame_ready = Signal(int, object)
    failed = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self._queue: queue.Queue[int | None] = queue.Queue(maxsize=2)

    def request(self, index: int) -> None:
        while True:
            try:
                self._queue.put_nowait(index)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass

    def stop(self) -> None:
        self.request_stop()
        self.wait(2500)

    def request_stop(self) -> None:
        while True:
            try:
                self._queue.put_nowait(None)
                break
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass

    def run(self) -> None:
        try:
            reader = VideoReader(self.path)
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.opened.emit(reader.info)
        last_index = -1
        try:
            while True:
                try:
                    index = self._queue.get(timeout=0.15)
                except queue.Empty:
                    continue
                if index is None:
                    break
                while True:
                    try:
                        newer = self._queue.get_nowait()
                        if newer is None:
                            return
                        index = newer
                    except queue.Empty:
                        break
                if index == last_index + 1:
                    ok, frame = reader.read()
                    if not ok:
                        frame = None
                else:
                    frame = reader.read_at(index)
                last_index = index
                if frame is not None:
                    self.frame_ready.emit(index, frame)
        finally:
            reader.close()


class TrackThread(QThread):
    completed = Signal(object)
    progress = Signal(int, int, str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, info: VideoInfo, seed: int, selection_box: tuple[float, float, float, float], engine: str):
        super().__init__()
        self.info = info
        self.seed = seed
        self.selection_box = selection_box
        self.engine = engine
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            result = track_region(
                self.info,
                self.seed,
                self.selection_box,
                self.engine,
                lambda current, total, phase: self.progress.emit(current, total, phase),
                self.cancel_event,
            )
            self.completed.emit(result)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


class ExportThread(QThread):
    completed = Signal(object)
    progress = Signal(int, int, str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, info: VideoInfo, result: TrackResult, settings: AppSettings, path: str):
        super().__init__()
        self.info = info
        self.result = result
        self.settings = settings
        self.path = path
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            result = export_video(
                self.info,
                self.result.points,
                self.result.confidence,
                self.settings,
                self.path,
                lambda current, total, phase: self.progress.emit(current, total, phase),
                self.cancel_event,
            )
            self.completed.emit(result)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(str(error))


class CameraPathThread(QThread):
    completed = Signal(int, object)
    cancelled = Signal(int)

    def __init__(self, generation: int, result: TrackResult, fps: float, rail_mode: bool, strength: int):
        super().__init__()
        self.generation = generation
        self.result = result
        self.fps = fps
        self.rail_mode = rail_mode
        self.strength = strength
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            path = build_camera_path(
                self.result.points,
                self.result.confidence,
                self.fps,
                self.strength if self.rail_mode else 0,
                self.cancel_event,
            )
            self.completed.emit(self.generation, path)
        except InterruptedError:
            self.cancelled.emit(self.generation)


class MainWindow(QMainWindow):
    SLIDER_STEPS = 100_000

    def __init__(self) -> None:
        super().__init__()
        self.settings = AppSettings.load()
        self.info: VideoInfo | None = None
        self.reader_thread: FrameReaderThread | None = None
        self.track_thread: TrackThread | None = None
        self.export_thread: ExportThread | None = None
        self.camera_thread: CameraPathThread | None = None
        self.track_result: TrackResult | None = None
        self.camera_path: np.ndarray | None = None
        self.preview_compare_raw = False
        self._preview_crop_cache_signature: tuple[object, ...] | None = None
        self._preview_crop_sizes: dict[str, tuple[int, int]] = {}
        self._camera_generation = 0
        self._camera_path_dirty = False
        self._close_pending = False
        self.current_frame = 0
        self.current_image: np.ndarray | None = None
        self.selection_box: tuple[float, float, float, float] | None = None
        self.is_playing = False
        self.playback_start_time = 0.0
        self.playback_start_frame = 0
        self._loading = False
        self._busy = False

        self.setWindowTitle("FeatureRail")
        self.setMinimumSize(1120, 740)
        self.resize(1480, 920)
        self._build_ui()
        self._apply_theme()

        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(15)
        self.playback_timer.timeout.connect(self._playback_tick)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(85)
        self.refresh_timer.timeout.connect(self._refresh_preview)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self.settings.save)
        self.camera_settings_timer = QTimer(self)
        self.camera_settings_timer.setSingleShot(True)
        self.camera_settings_timer.setInterval(180)
        self.camera_settings_timer.timeout.connect(self._invalidate_camera_path)
        self._load_settings_into_controls()
        self._wire_events()
        self._animate_entrance()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 20, 28, 22)
        outer.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(16)
        logo = QLabel("PL")
        logo.setObjectName("brandMark")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(38, 38)
        brand_stack = QVBoxLayout()
        brand_stack.setSpacing(0)
        brand = QLabel("FEATURERAIL")
        brand.setObjectName("brandTitle")
        descriptor = QLabel("VIDEO STABILIZATION")
        descriptor.setObjectName("brandDescriptor")
        brand_stack.addWidget(brand)
        brand_stack.addWidget(descriptor)
        header.addWidget(logo)
        header.addLayout(brand_stack)
        header.addSpacing(22)
        header_vline = self._vline()
        header.addWidget(header_vline)
        header.addSpacing(6)

        self.video_title = QLabel(self._t("尚未開啟影片"))
        self.video_title.setObjectName("videoTitle")
        self.video_metadata = QLabel("MP4、MOV、MKV、AVI")
        self.video_metadata.setObjectName("metadata")
        video_stack = QVBoxLayout()
        video_stack.setSpacing(2)
        video_stack.addWidget(self.video_title)
        video_stack.addWidget(self.video_metadata)
        header.addLayout(video_stack, 1)

        self.new_target_button = QPushButton(self._t("重新框選"))
        self.new_target_button.setObjectName("subtleButton")
        self.new_target_button.setEnabled(False)
        self.open_button = QPushButton(self._t("開啟影片"))
        self.open_button.setObjectName("openButton")
        header.addWidget(self.new_target_button)
        header.addWidget(self.open_button)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(24)
        work = QVBoxLayout()
        work.setSpacing(12)
        preview_header = QHBoxLayout()
        preview_header.setContentsMargins(0, 2, 0, 0)
        self.preview_state = QLabel(self._t("SOURCE  /  拖曳框選特徵"))
        self.preview_state.setObjectName("eyebrow")
        self.confidence_label = QLabel("")
        self.confidence_label.setObjectName("confidence")
        self.compare_button = QPushButton(self._t("看原始"))
        self.compare_button.setObjectName("zoomButton")
        self.compare_button.setFixedSize(72, 26)
        self.compare_button.setEnabled(False)
        self.compare_button.setToolTip(self._t("在原始追蹤與軌道穩定預覽間切換；不會改變輸出設定。"))
        self.zoom_hint = QLabel(self._t("拖曳框選 · 滾輪放大 · 右鍵平移"))
        self.zoom_hint.setObjectName("smallHint")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("settingValue")
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setObjectName("zoomButton")
        self.zoom_out_button.setFixedSize(27, 26)
        self.zoom_fit_button = QPushButton(self._t("適合"))
        self.zoom_fit_button.setObjectName("zoomButton")
        self.zoom_fit_button.setFixedSize(43, 26)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setObjectName("zoomButton")
        self.zoom_in_button.setFixedSize(27, 26)
        preview_header.addWidget(self.preview_state)
        preview_header.addStretch(1)
        preview_header.addWidget(self.zoom_hint)
        preview_header.addSpacing(10)
        preview_header.addWidget(self.zoom_out_button)
        preview_header.addWidget(self.zoom_label)
        preview_header.addWidget(self.zoom_fit_button)
        preview_header.addWidget(self.zoom_in_button)
        preview_header.addWidget(self.confidence_label)
        preview_header.addWidget(self.compare_button)
        work.addLayout(preview_header)

        preview_shell = QFrame()
        preview_shell.setObjectName("previewShell")
        preview_layout = QVBoxLayout(preview_shell)
        preview_layout.setContentsMargins(1, 1, 1, 1)
        self.canvas = PreviewCanvas()
        preview_layout.addWidget(self.canvas)
        work.addWidget(preview_shell, 1)

        transport = QHBoxLayout()
        transport.setSpacing(12)
        self.play_button = QPushButton("▶")
        self.play_button.setObjectName("playButton")
        self.play_button.setFixedSize(40, 36)
        self.play_button.setEnabled(False)
        self.time_label = QLabel("00:00.00  /  00:00.00")
        self.time_label.setObjectName("timecode")
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, self.SLIDER_STEPS)
        self.time_slider.setEnabled(False)
        transport.addWidget(self.play_button)
        transport.addWidget(self.time_label)
        transport.addWidget(self.time_slider, 1)
        work.addLayout(transport)

        action_row = QHBoxLayout()
        self.target_status = QLabel(self._t("放大後拖曳框住清晰特徵，追蹤後會鎖在正中央。"))
        self.target_status.setObjectName("helperText")
        self.track_button = QPushButton(self._t("開始追蹤"))
        self.track_button.setObjectName("primaryButton")
        self.track_button.setMinimumWidth(152)
        self.track_button.setEnabled(False)
        action_row.addWidget(self.target_status, 1)
        action_row.addWidget(self.track_button)
        work.addLayout(action_row)
        body.addLayout(work, 1)

        inspector = self._build_inspector()
        body.addWidget(inspector)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(176)
        self.status_label = QLabel(self._t("就緒 · CPU 追蹤可直接使用"))
        self.status_label.setObjectName("footerStatus")
        self.cancel_button = QPushButton(self._t("取消"))
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.hide()
        footer.addWidget(self.progress_bar)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.cancel_button)
        outer.addLayout(footer)

    def _build_inspector(self) -> QWidget:
        panel = QFrame()
        self.inspector = panel
        panel.setObjectName("inspector")
        panel.setMinimumWidth(294)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 17, 20, 18)
        layout.setSpacing(13)
        heading = QHBoxLayout()
        self.inspector_accent = QFrame()
        self.inspector_accent.setObjectName("inspectorAccent")
        self.inspector_accent.setFixedSize(4, 27)
        heading.addWidget(self.inspector_accent)
        heading.addSpacing(4)
        self.inspector_title = QLabel(self._t("畫面鎖定"))
        self.inspector_title.setProperty("i18nSource", "畫面鎖定")
        self.inspector_title.setObjectName("inspectorTitle")
        heading.addWidget(self.inspector_title)
        heading.addStretch(1)
        self.lock_badge = QLabel("RAIL MODE" if self.settings.rail_mode else "CENTER LOCK")
        self.lock_badge.setObjectName("settingValue")
        heading.addWidget(self.lock_badge)
        layout.addLayout(heading)
        self._separator(layout)

        layout.addWidget(self._section_label("追蹤引擎"))
        self.tracker_combo = QComboBox()
        self.tracker_combo.addItems([self._t("KLT · CPU")])
        self.tracker_combo.setCurrentText(self.settings.tracker)
        layout.addWidget(self.tracker_combo)
        self.tracker_hint = QLabel(self._t("框內角點一起追蹤，以多個特徵的中位位移鎖定區域中心。"))
        self.tracker_hint.setProperty("i18nSource", "框內角點一起追蹤，以多個特徵的中位位移鎖定區域中心。")
        self.tracker_hint.setObjectName("smallHint")
        self.tracker_hint.setWordWrap(True)
        layout.addWidget(self.tracker_hint)
        self._separator(layout)

        rail_heading = QHBoxLayout()
        rail_heading.addWidget(self._section_label("鏡頭穩定"))
        rail_heading.addStretch(1)
        self.rail_check = QCheckBox(self._t("軌道模式"))
        self.rail_check.setProperty("i18nSource", "軌道模式")
        self.rail_check.setChecked(self.settings.rail_mode)
        self.rail_check.setToolTip(self._t("平滑手持與走路造成的鏡頭晃動，讓畫面更像軌道攝影機拍攝。"))
        rail_heading.addWidget(self.rail_check)
        layout.addLayout(rail_heading)
        self.rail_strength_slider, self.rail_strength_value = self._add_slider(
            layout, "抑震強度", 0, 100, "%"
        )
        self.rail_hint = QLabel(self._t("高強度會大幅抑制走路晃動，主體可能在中心附近小幅移動。"))
        self.rail_hint.setProperty("i18nSource", "高強度會大幅抑制走路晃動，主體可能在中心附近小幅移動。")
        self.rail_hint.setObjectName("smallHint")
        self.rail_hint.setWordWrap(True)
        layout.addWidget(self.rail_hint)
        self._separator(layout)

        framing_heading = QHBoxLayout()
        framing_heading.addWidget(self._section_label("輸出畫幅"))
        framing_heading.addStretch(1)
        self.crop_check = QCheckBox(self._t("自動放大防黑邊"))
        self.crop_check.setProperty("i18nSource", "自動放大防黑邊")
        self.crop_check.setChecked(self.settings.auto_crop)
        framing_heading.addWidget(self.crop_check)
        layout.addLayout(framing_heading)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["Match source", "2160p", "1440p", "1080p", "720p", "Custom"])
        self.resolution_combo.setCurrentText(self.settings.output_preset)
        layout.addWidget(self.resolution_combo)
        custom_row = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(256, 7680)
        self.width_spin.setSingleStep(2)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(144, 4320)
        self.height_spin.setSingleStep(2)
        custom_row.addWidget(self.width_spin)
        custom_row.addWidget(QLabel("×"))
        custom_row.addWidget(self.height_spin)
        self.custom_resolution_row = QWidget()
        self.custom_resolution_row.setLayout(custom_row)
        layout.addWidget(self.custom_resolution_row)
        self._separator(layout)

        export_heading = QHBoxLayout()
        export_heading.addWidget(self._section_label("輸出影片"))
        export_heading.addStretch(1)
        layout.addLayout(export_heading)
        codec_row = QHBoxLayout()
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["H.264", "H.265"])
        self.codec_combo.setCurrentText(self.settings.codec)
        self.encoder_combo = QComboBox()
        self.encoder_combo.addItems(["Auto", "CPU", "AMD AMF"])
        self.encoder_combo.setCurrentText(self.settings.encoder)
        codec_row.addWidget(self.codec_combo, 1)
        codec_row.addWidget(self.encoder_combo, 1)
        layout.addLayout(codec_row)
        self.quality_slider, self.quality_value = self._add_slider(layout, "品質", 15, 35, " CRF")
        self.export_button = QPushButton(self._t("輸出 MP4"))
        self.export_button.setObjectName("exportButton")
        self.export_button.setEnabled(False)
        layout.addWidget(self.export_button)
        layout.addWidget(self._section_label("語言"))
        self.language_combo = QComboBox()
        for code, label in LANGUAGES:
            self.language_combo.addItem(label, code)
        language_index = self.language_combo.findData(self.settings.language)
        self.language_combo.setCurrentIndex(max(0, language_index))
        layout.addWidget(self.language_combo)
        layout.addStretch(1)
        self._update_resolution_controls()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(316)
        scroll.setWidget(panel)
        return scroll

    def _add_slider(self, layout: QVBoxLayout, title: str, low: int, high: int, suffix: str):
        row = QHBoxLayout()
        label = self._small_label(title)
        value = QLabel("")
        value.setObjectName("settingValue")
        row.addWidget(label, 1)
        row.addWidget(value)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        layout.addLayout(row)
        layout.addWidget(slider)
        slider.valueChanged.connect(lambda number, target=value, ending=suffix: target.setText(f"{number}{ending}"))
        slider.setProperty("suffix", suffix)
        return slider, value

    def _t(self, text: str) -> str:
        return translate(text, self.settings.language)

    def _small_label(self, text: str) -> QLabel:
        label = QLabel(self._t(text))
        label.setProperty("i18nSource", text)
        label.setObjectName("settingLabel")
        return label

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(self._t(text))
        label.setProperty("i18nSource", text)
        label.setObjectName("sectionLabel")
        return label

    @staticmethod
    def _separator(layout: QVBoxLayout) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("separator")
        layout.addWidget(line)

    @staticmethod
    def _vline() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setObjectName("separator")
        line.setFixedHeight(30)
        return line

    def _wire_events(self) -> None:
        self.open_button.clicked.connect(self.open_video_dialog)
        self.new_target_button.clicked.connect(self._reset_target)
        self.canvas.region_selected.connect(self._set_region_normalized)
        self.canvas.zoom_changed.connect(lambda zoom: self.zoom_label.setText(f"{zoom * 100:.0f}%"))
        self.zoom_in_button.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        self.zoom_out_button.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.25))
        self.zoom_fit_button.clicked.connect(lambda: self.canvas.reset_zoom())
        self.compare_button.clicked.connect(self._toggle_preview_comparison)
        self.play_button.clicked.connect(self._toggle_playback)
        self.time_slider.valueChanged.connect(self._seek_slider)
        self.track_button.clicked.connect(self._track_clicked)
        self.export_button.clicked.connect(self._export_clicked)
        self.cancel_button.clicked.connect(self._cancel_active)
        self.crop_check.toggled.connect(self._settings_changed)
        self.rail_check.toggled.connect(self._rail_mode_toggled)
        self.rail_strength_slider.valueChanged.connect(self._settings_changed)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        self.resolution_combo.currentTextChanged.connect(self._resolution_changed)
        self.codec_combo.currentTextChanged.connect(self._settings_changed)
        self.encoder_combo.currentTextChanged.connect(self._settings_changed)
        self.tracker_combo.currentTextChanged.connect(self._settings_changed)
        self.quality_slider.valueChanged.connect(self._settings_changed)
        for spin in (self.width_spin, self.height_spin):
            spin.valueChanged.connect(self._settings_changed)

    def _load_settings_into_controls(self) -> None:
        self.width_spin.setValue(self.settings.custom_width)
        self.height_spin.setValue(self.settings.custom_height)
        self.quality_slider.setValue(self.settings.quality)
        self.rail_strength_slider.setValue(self.settings.rail_strength)
        self.rail_strength_slider.setEnabled(self.settings.rail_mode)
        self._apply_language()
        self._settings_changed()

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QMainWindow { background: #10141b; color: #e7e9eb; font-family: 'Segoe UI'; font-size: 10pt; }
            QWidget { background: transparent; color: #e7e9eb; font-family: 'Segoe UI'; font-size: 10pt; }
            QWidget#appRoot { background: qradialgradient(cx: 0.08, cy: 0.02, radius: 1.15, fx: 0.08, fy: 0.02, stop: 0 #1d2633, stop: 0.46 #111720, stop: 1 #0b0e13); }
            QLabel { background: none; background-color: rgba(0, 0, 0, 0); background-image: none; border: none; }
            QLabel#brandMark { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f6c56f, stop:1 #dda348); color: #17191c; font-size: 13pt; font-weight: 800; border-radius: 6px; }
            QLabel#brandTitle { color: #f4f1eb; font-size: 10pt; font-weight: 750; letter-spacing: 1.4px; }
            QLabel#brandDescriptor, QLabel#eyebrow { color: #7d858f; font-size: 8pt; font-weight: 650; letter-spacing: 1.1px; }
            QLabel#videoTitle { color: #e9e9e7; font-size: 10pt; font-weight: 600; }
            QLabel#metadata, QLabel#smallHint, QLabel#helperText { background: none; color: #87909b; font-size: 9pt; }
            QLabel#confidence { background: none; color: #aeb6bf; font-size: 9pt; }
            QLabel#timecode { background: none; color: #b3bac2; font-family: 'Consolas'; font-size: 9pt; min-width: 150px; }
            QLabel#inspectorTitle { background: none; color: #f5f2ed; font-size: 13pt; font-weight: 700; }
            QFrame#inspectorAccent { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f5c36d, stop:1 #ca8f37); border: 0; border-radius: 2px; }
            QLabel#sectionLabel { background: none; color: #b7bec6; font-size: 9pt; font-weight: 650; letter-spacing: .5px; }
            QLabel#settingLabel { background: none; color: #a7afb9; font-size: 9pt; }
            QLabel#settingValue { background: none; color: #e6b667; font-family: 'Consolas'; font-size: 9pt; }
            QLabel#footerStatus { background: none; color: #a4adb7; font-size: 9pt; }
            QFrame#previewShell { background: #080b10; border: 1px solid #85632f; border-radius: 7px; }
            QFrame#inspector { background: #171c24; border: 1px solid #343b46; border-radius: 11px; }
            QFrame#separator { color: #303741; background: #303741; max-height: 1px; }
            QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #232a34, stop:1 #191f28); border: none; color: #e3e6e9; min-height: 34px; padding: 0 13px; border-radius: 9px; }
            QPushButton:hover { background: #303a48; color: #fff4df; }
            QPushButton:pressed { background: #171c24; color: #f4c774; }
            QPushButton:disabled { color: #66707c; background: #171b22; }
            QPushButton#openButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #292e36, stop:1 #1a1e24); color: #f3d69e; font-weight: 700; }
            QPushButton#openButton:hover { background: #38342b; color: #fff0cf; }
            QPushButton#primaryButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f1bd67, stop:1 #dfa348); color: #191712; font-weight: 800; min-height: 42px; border-radius: 10px; }
            QPushButton#primaryButton:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffd484, stop:1 #efb95f); }
            QPushButton#exportButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #282d34, stop:1 #1b2027); color: #f5d18c; font-weight: 700; min-height: 40px; border-radius: 10px; }
            QPushButton#exportButton:hover { background: #383328; color: #fff0cf; }
            QPushButton#playButton { color: #e9ba6a; font-size: 11pt; padding: 0; }
            QPushButton#zoomButton { background: #171d26; color: #c1c9d2; min-height: 26px; padding: 0 5px; border-radius: 8px; }
            QPushButton#zoomButton:hover { background: #2a3340; color: #ffd184; }
            QPushButton#subtleButton, QPushButton#cancelButton { background: #181d25; color: #c6cdd5; }
            QComboBox, QSpinBox { background: #11161d; color: #e0e5eb; border: none; min-height: 34px; padding: 0 9px; selection-background-color: #a97c39; border-radius: 9px; }
            QComboBox:hover, QSpinBox:hover { background: #171e28; }
            QComboBox:focus, QSpinBox:focus { background: #1b222c; }
            QComboBox::drop-down { border: none; width: 21px; }
            QComboBox QAbstractItemView { background: #1b2025; border: none; selection-background-color: #454034; outline: 0; }
            QCheckBox { color: #bec5cc; spacing: 6px; font-size: 9pt; }
            QCheckBox::indicator { width: 15px; height: 15px; border: none; background: #252d37; border-radius: 4px; }
            QCheckBox::indicator:checked { background: #e8b15b; }
            QCheckBox#autoToggle { color: #dbb46f; font-weight: 700; letter-spacing: .7px; }
            QSlider::groove:horizontal { background: #3a424c; height: 4px; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #d6a04b; border-radius: 2px; }
            QSlider::handle:horizontal { background: #efbd65; border: none; width: 13px; height: 13px; margin: -5px 0; border-radius: 4px; }
            QSlider::handle:horizontal:hover { background: #ffda8b; }
            QProgressBar#progressBar { background: #272e38; border: 0; max-height: 4px; border-radius: 2px; }
            QProgressBar#progressBar::chunk { background: #e2ae59; border-radius: 2px; }
        """)
        for label in self.findChildren(QLabel):
            if label.objectName() != "brandMark":
                label.setAutoFillBackground(False)
                label.setStyleSheet("background: transparent; background-color: transparent; border: none;")
        for label in self.inspector.findChildren(QLabel):
            palette = label.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#171c24"))
            label.setPalette(palette)
            label.setAutoFillBackground(True)
            label.setStyleSheet("background-color: #171c24; border: none;")
        open_glow = QGraphicsDropShadowEffect(self.open_button)
        open_glow.setBlurRadius(17)
        open_glow.setOffset(0, 0)
        open_glow.setColor(QColor(225, 165, 70, 38))
        self.open_button.setGraphicsEffect(open_glow)

    def _animate_entrance(self) -> None:
        effect = QGraphicsOpacityEffect(self.centralWidget())
        self.centralWidget().setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(230)
        animation.setStartValue(0.72)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._entrance_animation = animation

    def open_video_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._t("開啟影片"),
            str(Path.home()),
            self._t("影片 (*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mts *.m2ts);;所有檔案 (*.*)"),
        )
        if not path:
            return
        if self.reader_thread and self.reader_thread.isRunning():
            self.reader_thread.stop()
        self._loading = True
        self.info = None
        self.track_result = None
        self.preview_compare_raw = False
        self.compare_button.setText(self._t("看原始"))
        self.compare_button.setEnabled(False)
        self.camera_path = None
        self._camera_generation += 1
        self._camera_path_dirty = False
        if self.camera_thread and self.camera_thread.isRunning():
            self.camera_thread.cancel()
        self.selection_box = None
        self.current_image = None
        self.current_frame = 0
        self._stop_playback()
        self._set_busy(True)
        self.status_label.setText(self._t("正在讀取影片中繼資料…"))
        self.video_title.setText(Path(path).name)
        self.video_metadata.setText(self._t("讀取中…"))
        self.preview_state.setText(self._t("SOURCE  /  拖曳框選特徵"))
        self.target_status.setText(self._t("放大後拖曳框住清晰特徵，追蹤後會鎖在正中央。"))
        self.canvas.reset_zoom()
        self.canvas.set_frame(None)
        self.reader_thread = FrameReaderThread(path)
        self.reader_thread.opened.connect(self._video_opened)
        self.reader_thread.frame_ready.connect(self._frame_loaded)
        self.reader_thread.failed.connect(self._video_failed)
        self.reader_thread.start()

    def _video_opened(self, info: VideoInfo) -> None:
        self.info = info
        self._loading = False
        self._set_busy(False)
        self.video_title.setText(info.name)
        self.video_metadata.setText(f"{info.width} × {info.height}    ·    {info.fps:.3g} FPS    ·    {self._format_time(info.duration)}")
        self.play_button.setEnabled(True)
        self.time_slider.setEnabled(True)
        self.new_target_button.setEnabled(False)
        self.track_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.current_frame = 0
        if self.reader_thread:
            self.reader_thread.request(0)
        self.status_label.setText(self._t("影片已載入 · 拖曳框選要鎖定的特徵"))
        if self.resolution_combo.currentText() == "Match source":
            self.width_spin.setValue(info.width + info.width % 2)
            self.height_spin.setValue(info.height + info.height % 2)

    def _video_failed(self, message: str) -> None:
        self._loading = False
        self._set_busy(False)
        self.status_label.setText(self._t("無法讀取影片"))
        QMessageBox.critical(self, self._t("無法開啟影片"), translate(message, self.settings.language))

    def _frame_loaded(self, index: int, frame: np.ndarray) -> None:
        if self.info is None:
            return
        if self.is_playing:
            # Decode can lag the playback clock; show completed frames instead of dropping every stale one.
            self.current_frame = index
            value = round(index / max(1, self.info.frame_count - 1) * self.SLIDER_STEPS)
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(value)
            self.time_slider.blockSignals(False)
        elif index != self.current_frame:
            return
        self.current_image = frame
        self._refresh_preview()

    def _seek_slider(self, value: int) -> None:
        if self.info is None:
            return
        frame_index = round(value / self.SLIDER_STEPS * (self.info.frame_count - 1))
        self._seek_frame(frame_index)

    def _seek_frame(self, frame_index: int) -> None:
        if self.info is None or not self.reader_thread:
            return
        self.current_frame = min(max(0, frame_index), self.info.frame_count - 1)
        if not self.time_slider.isSliderDown():
            value = round(self.current_frame / max(1, self.info.frame_count - 1) * self.SLIDER_STEPS)
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(value)
            self.time_slider.blockSignals(False)
        self.reader_thread.request(self.current_frame)
        self._update_timecode()

    def _toggle_playback(self) -> None:
        if self.info is None:
            return
        if self.is_playing:
            self._stop_playback()
            return
        if self.current_frame >= self.info.frame_count - 1:
            self._seek_frame(0)
        self.is_playing = True
        self.play_button.setText("Ⅱ")
        self.playback_start_frame = self.current_frame
        self.playback_start_time = time.perf_counter()
        self.playback_timer.start()

    def _stop_playback(self) -> None:
        self.is_playing = False
        self.playback_timer.stop() if hasattr(self, "playback_timer") else None
        if hasattr(self, "play_button"):
            self.play_button.setText("▶")

    def _playback_tick(self) -> None:
        if self.info is None:
            self._stop_playback()
            return
        elapsed = time.perf_counter() - self.playback_start_time
        index = self.playback_start_frame + int(elapsed * self.info.fps)
        if index >= self.info.frame_count:
            self._seek_frame(self.info.frame_count - 1)
            self._stop_playback()
        elif index != self.current_frame:
            self._seek_frame(index)

    def _set_region_normalized(self, x0: float, y0: float, x1: float, y1: float) -> None:
        if self.info is None or self.current_image is None:
            return
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        width, height = self.info.width, self.info.height
        self.selection_box = (
            float(np.clip(left * width, 0, width - 1)),
            float(np.clip(top * height, 0, height - 1)),
            float(np.clip(right * width, 1, width)),
            float(np.clip(bottom * height, 1, height)),
        )
        if self.selection_box[2] - self.selection_box[0] < 5 or self.selection_box[3] - self.selection_box[1] < 5:
            self.selection_box = None
            self.status_label.setText(self._t("框選範圍太小，請放大預覽後再拖曳框選。"))
            return
        self.track_result = None
        self.preview_compare_raw = False
        self.compare_button.setText(self._t("看原始"))
        self.compare_button.setEnabled(False)
        self.camera_path = None
        self._camera_generation += 1
        self._camera_path_dirty = False
        if self.camera_thread and self.camera_thread.isRunning():
            self.camera_thread.cancel()
        self.new_target_button.setEnabled(True)
        self.track_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.track_button.setText(self._t("開始追蹤"))
        self.preview_state.setText(self._t("SOURCE  /  已框選特徵區域"))
        box_w = self.selection_box[2] - self.selection_box[0]
        box_h = self.selection_box[3] - self.selection_box[1]
        self.target_status.setText(f"Feature region  {box_w:.0f} × {box_h:.0f} px  ·  Select Start Tracking")
        self.status_label.setText(self._t("特徵區域已選取 · 每一幀會將區域中心置於輸出正中央"))
        self._refresh_preview()

    def _reset_target(self) -> None:
        self.track_result = None
        self.preview_compare_raw = False
        self.compare_button.setText(self._t("看原始"))
        self.compare_button.setEnabled(False)
        self.camera_path = None
        self._camera_generation += 1
        self._camera_path_dirty = False
        if self.camera_thread and self.camera_thread.isRunning():
            self.camera_thread.cancel()
        self.selection_box = None
        self.track_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.new_target_button.setEnabled(False)
        self.preview_state.setText(self._t("SOURCE  /  拖曳框選特徵"))
        self.confidence_label.clear()
        self.target_status.setText(self._t("放大後拖曳框住清晰特徵，追蹤後會鎖在正中央。"))
        self.status_label.setText(self._t("拖曳框選特徵 · 滾輪放大 · 右鍵平移"))
        self._refresh_preview()

    def _track_clicked(self) -> None:
        if self.track_thread and self.track_thread.isRunning():
            self._cancel_active()
            return
        if self.info is None or self.selection_box is None:
            return
        self._stop_playback()
        thread = TrackThread(
            self.info,
            self.current_frame,
            self.selection_box,
            self.tracker_combo.currentText(),
        )
        self.track_thread = thread
        thread.progress.connect(self._job_progress)
        thread.completed.connect(self._tracking_complete)
        thread.cancelled.connect(self._job_cancelled)
        thread.failed.connect(self._job_failed)
        self.track_button.setText(self._t("取消追蹤"))
        self.track_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self._set_busy(True, "正在追蹤…")
        thread.start()

    def _tracking_complete(self, result: TrackResult) -> None:
        self.track_result = result
        self.preview_compare_raw = False
        self.compare_button.setText(self._t("看原始"))
        self.compare_button.setEnabled(False)
        if self.info is None:
            return
        self.camera_path = None
        self._camera_path_dirty = True
        self._camera_generation += 1
        self._set_busy(False)
        self.track_button.setText(self._t("重新追蹤"))
        self.track_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self.preview_state.setText(
            "OUTPUT  /  軌道穩定"
            if self.settings.rail_mode and self.settings.rail_strength > 0
            else "OUTPUT  /  特徵中心精準鎖定"
        )
        self._set_preview_state()
        self.target_status.setText(
            f"{result.engine}  ·  {result.feature_count} features  ·  {float(np.mean(result.confidence)) * 100:.0f}% confidence"
        )
        completion = (
            self._t("軌道穩定完成 · 已按全片邊界設定無黑邊倍率")
            if self.settings.rail_mode and self.settings.rail_strength > 0
            else self._t("追蹤完成 · 每幀中心對齊 · 已按全片邊界設定無黑邊倍率")
        )
        self.status_label.setText(result.warning or completion)
        self.confidence_label.setText("")
        self._start_camera_path_rebuild()
        self._refresh_preview()
        if result.warning:
            self.status_label.setStyleSheet("color: #e5b96c")
        else:
            self.status_label.setStyleSheet("")

    def _export_clicked(self) -> None:
        if self.export_thread and self.export_thread.isRunning():
            self._cancel_active()
            return
        if self.info is None or self.track_result is None:
            return
        source = Path(self.info.path)
        suggested = source.with_name(source.stem + "_featurerail.mp4")
        path, _ = QFileDialog.getSaveFileName(self, self._t("輸出追蹤影片"), str(suggested), self._t("MP4 影片 (*.mp4)"))
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        self.settings.save()
        thread = ExportThread(self.info, self.track_result, self._current_settings(), path)
        self.export_thread = thread
        thread.progress.connect(self._job_progress)
        thread.completed.connect(self._export_complete)
        thread.cancelled.connect(self._job_cancelled)
        thread.failed.connect(self._job_failed)
        self.export_button.setText(self._t("取消輸出"))
        self.track_button.setEnabled(False)
        self._set_busy(True, self._t("正在輸出影片…"))
        thread.start()

    def _export_complete(self, result: object) -> None:
        self._set_busy(False)
        self.export_button.setText(self._t("輸出 MP4"))
        self.export_button.setEnabled(True)
        self.track_button.setEnabled(True)
        self.status_label.setText(f"Exported: {result.path}" + (f" · {result.warning}" if result.warning else ""))
        self.status_label.setStyleSheet("")

    def _cancel_active(self) -> None:
        if self.track_thread and self.track_thread.isRunning():
            self.track_thread.cancel()
        if self.export_thread and self.export_thread.isRunning():
            self.export_thread.cancel()
        self.status_label.setText(self._t("正在停止工作…"))

    def _job_progress(self, current: int, total: int, phase: str) -> None:
        percent = int(current / max(1, total) * 100)
        self.progress_bar.setValue(max(0, min(100, percent)))
        self.status_label.setText(f"{translate(phase, self.settings.language)}  ·  {percent}%")

    def _job_cancelled(self) -> None:
        self._set_busy(False)
        self.track_button.setText(self._t("重新追蹤" if self.track_result is not None else "開始追蹤"))
        self.export_button.setText(self._t("輸出 MP4"))
        self.track_button.setEnabled(self.info is not None and self.selection_box is not None)
        self.export_button.setEnabled(self.track_result is not None)
        self.status_label.setText(self._t("工作已取消"))

    def _job_failed(self, message: str) -> None:
        self._set_busy(False)
        self.track_button.setText(self._t("重新追蹤" if self.track_result is not None else "開始追蹤"))
        self.export_button.setText(self._t("輸出 MP4"))
        self.track_button.setEnabled(self.info is not None and self.selection_box is not None)
        self.export_button.setEnabled(self.track_result is not None)
        self.status_label.setText(self._t("工作發生錯誤"))
        QMessageBox.critical(self, "FeatureRail", translate(message, self.settings.language))

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.open_button.setEnabled(not busy)
        self.new_target_button.setEnabled(not busy and self.info is not None)
        self.cancel_button.setVisible(busy)
        self.progress_bar.setVisible(busy)
        self.progress_bar.setValue(0)
        if text:
            self.status_label.setText(text)
        if not busy:
            self.status_label.setStyleSheet("")

    def _refresh_preview(self) -> None:
        if self.current_image is None:
            self.canvas.set_frame(None)
            return
        if self.track_result is not None and self.info is not None and self._camera_path_dirty:
            self._start_camera_path_rebuild()
        if self.track_result is not None and self.camera_path is not None and self.info is not None:
            output_size = self.settings.output_size(self.info.width, self.info.height)
            preview_path = self.track_result.points if self.preview_compare_raw else self.camera_path
            cache_signature = (
                id(self.track_result), id(self.camera_path), output_size, self.settings.auto_crop
            )
            if cache_signature != self._preview_crop_cache_signature:
                self._preview_crop_cache_signature = cache_signature
                self._preview_crop_sizes.clear()
            path_key = "raw" if self.preview_compare_raw else "stable"
            crop_size = self._preview_crop_sizes.get(path_key)
            if crop_size is None:
                crop_size = fit_crop_size(
                    (self.info.width, self.info.height), output_size, preview_path, self.settings.auto_crop
                )
                self._preview_crop_sizes[path_key] = crop_size
            preview_scale = min(1.0, 1280 / max(output_size))
            preview_size = (
                max(2, int(round(output_size[0] * preview_scale))),
                max(2, int(round(output_size[1] * preview_scale))),
            )
            output = render_output_preview(
                self.current_image,
                preview_path[self.current_frame],
                preview_size,
                self.settings.auto_crop,
                crop_size,
            )
            scale_x = preview_size[0] / crop_size[0]
            scale_y = preview_size[1] / crop_size[1]
            selection_rect = None
            if self.track_result.seed_box:
                x0, y0, x1, y1 = self.track_result.seed_box
                box_width = (x1 - x0) * scale_x
                box_height = (y1 - y0) * scale_y
                target_x = output_size[0] / 2 + (
                    self.track_result.points[self.current_frame, 0] - preview_path[self.current_frame, 0]
                ) * scale_x
                target_y = output_size[1] / 2 + (
                    self.track_result.points[self.current_frame, 1] - preview_path[self.current_frame, 1]
                ) * scale_y
                selection_rect = (
                    target_x - box_width / 2,
                    target_y - box_height / 2,
                    target_x + box_width / 2,
                    target_y + box_height / 2,
                )
            self.canvas.set_frame(output, selection_rect, locked=True)
            confidence = float(self.track_result.confidence[self.current_frame])
            self.confidence_label.setText(f"{self._t('區域可信度')}  {confidence * 100:.0f}%")
            self.confidence_label.setStyleSheet("color: #75cba1" if confidence >= 0.5 else "color: #e3b667")
            self._update_compare_button()
        else:
            self.canvas.set_frame(
                self.current_image,
                self.selection_box,
                selectable=self.info is not None and not self._busy and self.track_result is None,
            )
            if self.track_result is not None:
                self.confidence_label.setText(self._t("正在準備置中預覽…"))
            else:
                self.confidence_label.clear()
        self._update_timecode()

    def _settings_changed(self, *_: object) -> None:
        previous_rail_settings = (self.settings.rail_mode, self.settings.rail_strength)
        self.settings.auto_crop = self.crop_check.isChecked()
        self.settings.rail_mode = self.rail_check.isChecked()
        self.settings.rail_strength = self.rail_strength_slider.value()
        self.settings.output_preset = self.resolution_combo.currentText()
        self.settings.custom_width = self.width_spin.value()
        self.settings.custom_height = self.height_spin.value()
        self.settings.codec = self.codec_combo.currentText()
        self.settings.encoder = self.encoder_combo.currentText()
        self.settings.quality = self.quality_slider.value()
        self.settings.tracker = self.tracker_combo.currentText()
        if hasattr(self, "language_combo"):
            self.settings.language = self.language_combo.currentData() or "en"
        self.save_timer.start()
        if self.track_result is not None:
            if previous_rail_settings != (self.settings.rail_mode, self.settings.rail_strength):
                self.compare_button.setEnabled(False)
                if not self.settings.rail_mode or self.settings.rail_strength <= 0:
                    self.preview_compare_raw = False
                    self.compare_button.setText("看原始")
                self._set_preview_state()
                self.camera_settings_timer.start()
            self.refresh_timer.start()

    def _language_changed(self, _index: int) -> None:
        self.settings.language = self.language_combo.currentData() or "en"
        self._apply_language()
        self.save_timer.start()

    def _apply_language(self) -> None:
        self.setWindowTitle("FeatureRail")
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if self.settings.language == "ar" else Qt.LayoutDirection.LeftToRight
        )
        self.canvas.language = self.settings.language
        self.canvas.update()
        self.video_title.setText(self.info.name if self.info else self._t("尚未開啟影片"))
        self.new_target_button.setText(self._t("重新框選"))
        self.open_button.setText(self._t("開啟影片"))
        self.zoom_hint.setText(self._t("拖曳框選 · 滾輪放大 · 右鍵平移"))
        self.zoom_fit_button.setText(self._t("適合"))
        self.compare_button.setText(self._t("看穩定" if self.preview_compare_raw else "看原始"))
        self.compare_button.setToolTip(self._t("在原始追蹤與軌道穩定預覽間切換；不會改變輸出設定。"))
        self.target_status.setText(self._t("放大後拖曳框住清晰特徵，追蹤後會鎖在正中央。"))
        self.cancel_button.setText(self._t("取消"))
        self.track_button.setText(self._t("重新追蹤" if self.track_result else "開始追蹤"))
        self.export_button.setText(self._t("輸出 MP4"))
        self.lock_badge.setText("RAIL MODE" if self.settings.rail_mode else "CENTER LOCK")
        self.compare_button.setToolTip(self._t("在原始追蹤與軌道穩定預覽間切換；不會改變輸出設定。"))
        for label in self.findChildren(QLabel):
            source = label.property("i18nSource")
            if source:
                label.setText(self._t(source))
        for checkbox in self.findChildren(QCheckBox):
            source = checkbox.property("i18nSource")
            if source:
                checkbox.setText(self._t(source))
        self._set_preview_state()

    def _rail_mode_toggled(self, enabled: bool) -> None:
        self.preview_compare_raw = False
        self.compare_button.setText(self._t("看原始"))
        self.rail_strength_slider.setEnabled(enabled)
        self.rail_hint.setEnabled(enabled)
        self.lock_badge.setText("RAIL MODE" if enabled else "CENTER LOCK")
        if self.track_result is not None:
            self._set_preview_state()
        self._settings_changed()

    def _toggle_preview_comparison(self) -> None:
        if not self.compare_button.isEnabled():
            return
        self.preview_compare_raw = not self.preview_compare_raw
        self.compare_button.setText(self._t("看穩定" if self.preview_compare_raw else "看原始"))
        self._set_preview_state()
        self._refresh_preview()

    def _update_compare_button(self) -> None:
        available = (
            self.track_result is not None
            and self.camera_path is not None
            and self.settings.rail_mode
            and self.settings.rail_strength > 0
        )
        self.compare_button.setEnabled(available)
        if (not self.settings.rail_mode or self.settings.rail_strength <= 0) and self.preview_compare_raw:
            self.preview_compare_raw = False
            self.compare_button.setText(self._t("看原始"))
        self._set_preview_state()

    def _set_preview_state(self) -> None:
        if self.track_result is None:
            return
        if self.preview_compare_raw:
            self.preview_state.setText(self._t("COMPARE  /  原始追蹤"))
        elif self.settings.rail_mode and self.settings.rail_strength > 0:
            self.preview_state.setText(self._t("OUTPUT  /  軌道穩定"))
        else:
            self.preview_state.setText(self._t("OUTPUT  /  特徵中心精準鎖定"))

    def _invalidate_camera_path(self) -> None:
        if self.track_result is None:
            return
        self._camera_generation += 1
        self._camera_path_dirty = True
        self.camera_path = None
        self._update_compare_button()
        if self.camera_thread and self.camera_thread.isRunning():
            self.camera_thread.cancel()
        self.refresh_timer.start()

    def _start_camera_path_rebuild(self) -> None:
        if self.info is None or self.track_result is None:
            return
        if self.camera_thread and self.camera_thread.isRunning():
            return
        thread = CameraPathThread(
            self._camera_generation,
            self.track_result,
            self.info.fps,
            self.settings.rail_mode,
            self.settings.rail_strength,
        )
        self.camera_thread = thread
        thread.completed.connect(self._camera_path_ready)
        thread.cancelled.connect(self._camera_path_cancelled)
        thread.start()

    def _camera_path_cancelled(self, generation: int) -> None:
        if self._camera_path_dirty and generation != self._camera_generation:
            QTimer.singleShot(35, self._start_camera_path_rebuild)

    def _camera_path_ready(self, generation: int, path: np.ndarray) -> None:
        if generation != self._camera_generation:
            self._camera_path_dirty = True
            QTimer.singleShot(35, self._start_camera_path_rebuild)
            return
        self.camera_path = path
        self._camera_path_dirty = False
        self._update_compare_button()
        self._refresh_preview()

    def _resolution_changed(self, _value: str) -> None:
        self._update_resolution_controls()
        self._settings_changed()

    def _update_resolution_controls(self) -> None:
        custom = self.resolution_combo.currentText() == "Custom"
        self.custom_resolution_row.setVisible(custom)
        if self.info is not None and self.resolution_combo.currentText() == "Match source":
            self.width_spin.setValue(self.info.width + self.info.width % 2)
            self.height_spin.setValue(self.info.height + self.info.height % 2)

    def _current_settings(self) -> AppSettings:
        self._settings_changed()
        return AppSettings(**self.settings.__dict__)

    def _update_timecode(self) -> None:
        if self.info is None:
            return
        current = self.current_frame / self.info.fps
        self.time_label.setText(f"{self._format_time(current)}  /  {self._format_time(self.info.duration)}")

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0, seconds)
        minutes = int(seconds // 60)
        whole = int(seconds % 60)
        centiseconds = int((seconds - int(seconds)) * 100)
        return f"{minutes:02d}:{whole:02d}.{centiseconds:02d}"

    def closeEvent(self, event: object) -> None:
        if not self._close_pending:
            self.settings.save()
            self._stop_playback()
            self._close_pending = True
        active_worker = False
        if self.track_thread and self.track_thread.isRunning():
            self.track_thread.cancel()
            active_worker = True
        if self.export_thread and self.export_thread.isRunning():
            self.export_thread.cancel()
            active_worker = True
        if self.camera_thread and self.camera_thread.isRunning():
            self.camera_thread.cancel()
            active_worker = True
        if self.reader_thread and self.reader_thread.isRunning():
            self.reader_thread.request_stop()
            active_worker = True
        if active_worker:
            self.status_label.setText(self._t("正在停止背景工作，視窗即將關閉…"))
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        event.accept()
