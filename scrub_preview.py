# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Bounded, independent timeline preview decoding; no GUI or playback decoder access."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Condition
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot

from color_interpretation import ColorChoices
from frame_info import MetadataCancelled
from frame_processor import FrameProcessor

if TYPE_CHECKING:
    from pyav_input import NativeRGBSamples, NativeSamples


@dataclass(frozen=True)
class PreviewSource:
    path: str
    backend: str
    metadata: list[dict]
    partial: bool = False
    color_choices: ColorChoices | None = None


@dataclass(frozen=True)
class PreviewFrame:
    index: int
    frame: np.ndarray
    native: NativeSamples | NativeRGBSamples | None
    processed: np.ndarray
    settings: dict
    elapsed: float


class _Signals(QObject):
    ready = pyqtSignal(int, int, object)
    failed = pyqtSignal(int, str)
    finished = pyqtSignal(int)


class _PreviewTask(QRunnable):
    def __init__(self, generation, source):
        super().__init__()
        self.generation = generation
        self.source = source
        self.signals = _Signals()
        self._condition = Condition()
        self._stopped = False
        self._pending = None
        self._awaiting = False

    def submit(self, request):
        with self._condition:
            self._pending = request
            self._condition.notify()

    def stop(self):
        with self._condition:
            self._stopped = True
            self._pending = None
            self._condition.notify()

    def stopped(self):
        with self._condition:
            return self._stopped

    def acknowledge(self):
        with self._condition:
            self._awaiting = False
            self._condition.notify()

    def _next(self, earliest):
        with self._condition:
            while not self._stopped:
                delay = earliest - time.perf_counter()
                if self._pending is not None and not self._awaiting and delay <= 0:
                    request, self._pending = self._pending, None
                    return request
                self._condition.wait(delay if delay > 0 else None)
        return None

    def run(self):
        capture = None
        processor = FrameProcessor()
        try:
            source = self.source
            if source.backend == "pyav":
                from pyav_input import PyAVInputCapture
                capture = PyAVInputCapture(source.path, color_choices=source.color_choices)
                if not capture.read()[0]:
                    raise RuntimeError("Could not read the preview source.")
                capture.configure_index(
                    source.metadata, partial=source.partial, cancelled=self.stopped,
                )
            else:
                capture = cv2.VideoCapture(source.path)
                if not capture.isOpened():
                    raise RuntimeError("Could not open the preview source.")
            earliest = 0.0
            last_index = None
            frame = native = None
            while (request := self._next(earliest)) is not None:
                sequence, index, settings = request
                started = time.perf_counter()
                if index != last_index:
                    if not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                        raise RuntimeError(f"Could not seek preview frame {index + 1}.")
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        raise RuntimeError(f"Could not read preview frame {index + 1}.")
                    native = capture.native_samples if source.backend == "pyav" else None
                    last_index = index
                if self.stopped():
                    break
                processed = processor.process(frame, **settings)
                elapsed = time.perf_counter() - started
                # At most 8 previews/s, with headroom when decoding/processing is slow.
                earliest = started + max(.125, elapsed * 1.25)
                with self._condition:
                    if self._stopped:
                        break
                    self._awaiting = True
                self.signals.ready.emit(self.generation, sequence, PreviewFrame(
                    index, frame, native, processed, settings, elapsed,
                ))
        except MetadataCancelled:
            if not self.stopped():
                self.signals.failed.emit(self.generation, "Preview indexing was cancelled.")
        except (cv2.error, OSError, ValueError, RuntimeError, ImportError, MemoryError) as error:
            if not self.stopped():
                self.signals.failed.emit(self.generation, str(error))
        finally:
            if capture is not None:
                capture.release()
            processor.close()
            self.signals.finished.emit(self.generation)


class ScrubPreview(QObject):
    """One active worker, one newest request and at most one unconsumed result."""

    ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0
        self._sequence = 0
        self._last_presented = 0
        self._settings = None
        self._source = None
        self._pending = None
        self._task = None

    def begin(self, source: PreviewSource):
        self.cancel()
        self._source = source

    def cancel(self):
        self._generation += 1
        self._source = self._pending = None
        self._settings = None
        if self._task is not None:
            self._task.stop()

    def request(self, index: int, settings: dict):
        if self._source is None:
            return
        self._sequence += 1
        self._pending = (self._sequence, index, settings.copy())
        self._settings = settings.copy()
        if self._task is None:
            self._start()
        elif self._task.generation == self._generation:
            self._task.submit(self._pending)
            self._pending = None

    def _start(self):
        if self._source is None or self._pending is None:
            return
        task = _PreviewTask(self._generation, self._source)
        self._task = task
        task.signals.ready.connect(self._ready)
        task.signals.failed.connect(self._failed)
        task.signals.finished.connect(self._finished)
        task.submit(self._pending)
        self._pending = None
        QThreadPool.globalInstance().start(task)

    @pyqtSlot(int, int, object)
    def _ready(self, generation, sequence, result):
        if self._task is not None and self._task.generation == generation:
            self._task.acknowledge()
        if (generation == self._generation and sequence > self._last_presented
                and result.settings == self._settings):
            self._last_presented = sequence
            self.ready.emit(result)

    @pyqtSlot(int, str)
    def _failed(self, generation, error):
        if generation == self._generation:
            self.cancel()
            self.failed.emit(error)

    @pyqtSlot(int)
    def _finished(self, generation):
        if self._task is None or self._task.generation != generation:
            return
        self._task = None
        self._start()
