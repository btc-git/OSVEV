# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Direct timeline selection with separate live-preview and final-seek requests."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class TimelineSlider(QSlider):
    seekRequested = pyqtSignal(int)
    previewRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._grab_offset = 0
        self._last_mouse_x = None

    def _geometry(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderHandle, self,
        )
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option,
            QStyle.SubControl.SC_SliderGroove, self,
        )
        return option, handle, groove

    def _position_at(self, x):
        option, handle, groove = self._geometry()
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(),
            round(x - self._grab_offset - groove.x()),
            max(0, groove.width() - handle.width()), option.upsideDown,
        )

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        _, handle, _ = self._geometry()
        on_handle = handle.contains(event.position().toPoint())
        self._grab_offset = (
            event.position().x() - handle.x() if on_handle else handle.width() / 2
        )
        self._last_mouse_x = event.position().x()
        self.setSliderDown(True)
        if not on_handle:
            self.setSliderPosition(self._position_at(event.position().x()))
            self.seekRequested.emit(self.sliderPosition())
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.isSliderDown():
            super().mouseMoveEvent(event)
            return
        self._last_mouse_x = event.position().x()
        self.setSliderPosition(self._position_at(event.position().x()))
        self.previewRequested.emit(self.sliderPosition())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.isSliderDown():
            super().mouseReleaseEvent(event)
            return
        if event.position().x() != self._last_mouse_x:
            self.setSliderPosition(self._position_at(event.position().x()))
        self.setSliderDown(False)
        event.accept()
