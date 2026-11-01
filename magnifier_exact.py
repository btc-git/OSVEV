# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Optional, latest-request-only FFmpeg magnifier rendering."""

from dataclasses import dataclass, field

import cv2
import numpy as np
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal, pyqtSlot

from frame_processor import FrameProcessor


@dataclass(frozen=True)
class MagnifierRequest:
    roi: np.ndarray
    zoom: int
    interpolation: str
    visible: tuple[int, int, int, int]
    viewport: tuple[int, int]
    additional_zoom: int
    highlight_added: bool
    highlight_source: bool
    # Optional (x, y) in the FFmpeg-scaled region whose unhighlighted pixel the
    # Pixel Inspector reports; render_exact stores it in sample_result.
    sample: tuple[int, int] | None = None
    sample_result: dict = field(default_factory=dict, compare=False, hash=False)


def render_exact(request: MagnifierRequest) -> np.ndarray:
    """Scale the whole selected crop before extracting the displayed viewport."""
    # A full-region scale is necessary to preserve swscale's sampling phase.
    output_bytes = request.roi.size * request.zoom ** 2
    if output_bytes > 256 * 1024 * 1024:
        raise ValueError(
            "Exact magnifier rendering would exceed 256 MiB for the scaled BGR image. "
            "Reduce the selected region or zoom, or turn off Match FFmpeg Export."
        )
    processor = FrameProcessor()
    try:
        scaled = processor.scale_for_export(
            request.roi, request.zoom, request.interpolation
        )
        if request.sample is not None:
            sample_x, sample_y = request.sample
            if 0 <= sample_y < scaled.shape[0] and 0 <= sample_x < scaled.shape[1]:
                request.sample_result["pixel"] = scaled[sample_y, sample_x].copy()
        x, y, width, height = request.visible
        zoom = request.zoom
        result = scaled[y * zoom:(y + height) * zoom,
                        x * zoom:(x + width) * zoom].copy()
    finally:
        processor.close()
    if request.highlight_added or request.highlight_source:
        mask = np.zeros(result.shape[:2], dtype=bool)
        mask[::zoom, ::zoom] = True
        if request.highlight_added:
            result[~mask] = (0, 255, 0)
        if request.highlight_source:
            result[mask] = (0, 255, 0)
    if request.additional_zoom > 1:
        result = cv2.resize(
            result, None, fx=request.additional_zoom, fy=request.additional_zoom,
            interpolation=cv2.INTER_NEAREST,
        )
    viewport_width, viewport_height = request.viewport
    height, width = result.shape[:2]
    crop_x = max(0, (width - viewport_width) // 2)
    crop_y = max(0, (height - viewport_height) // 2)
    return np.ascontiguousarray(result[
        crop_y:crop_y + viewport_height, crop_x:crop_x + viewport_width
    ])


class _Result(QObject):
    finished = pyqtSignal(int, object, str)


class _Render(QRunnable):
    def __init__(self, generation: int, request: MagnifierRequest):
        super().__init__()
        self.generation = generation
        self.request = request
        self.signals = _Result()

    def run(self):
        try:
            result = render_exact(self.request)
        except (RuntimeError, ValueError, MemoryError, ImportError, cv2.error) as error:
            self.signals.finished.emit(self.generation, None, str(error))
        else:
            self.signals.finished.emit(self.generation, result, "")


class ExactMagnifier(QObject):
    """One active calculation plus one debounced replacement; never waits on the GUI."""

    ready = pyqtSignal(object)
    sampled = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0
        self._pending = None
        self._task = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._start)

    def invalidate(self):
        self._generation += 1
        self._pending = None
        self._timer.stop()

    def request(self, request: MagnifierRequest):
        self.invalidate()
        if not self._closed:
            self._pending = (self._generation, request)
            self._timer.start()

    def close(self):
        self._closed = True
        self.invalidate()

    @pyqtSlot()
    def _start(self):
        if self._closed or self._task is not None or self._pending is None:
            return
        generation, request = self._pending
        self._pending = None
        self._task = _Render(generation, request)
        self._task.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(self._task)

    @pyqtSlot(int, object, str)
    def _finished(self, generation, result, error):
        task, self._task = self._task, None
        if not self._closed and generation == self._generation:
            if error:
                self.failed.emit(error)
            else:
                self.ready.emit(result)
                pixel = task.request.sample_result.get("pixel") if task is not None else None
                if pixel is not None:
                    self.sampled.emit(pixel)
        if not self._timer.isActive():
            self._start()
