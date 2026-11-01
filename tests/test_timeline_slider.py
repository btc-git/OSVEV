# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from osvev_app import OSVEV
from playback_timing import PlaybackClock, PlaybackTimeline


def move_slider(slider, point):
    QApplication.sendEvent(slider, QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(point), QPointF(slider.mapToGlobal(point)),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))


class TimelineSliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(patch.object(OSVEV, "_check_dependencies"))
        self.enterContext(patch.object(OSVEV, "set_windows_taskbar_icon"))
        self.warning = self.enterContext(patch.object(QMessageBox, "warning"))
        self.viewer = OSVEV(decoder_backend="opencv")
        self.addCleanup(self.viewer.close)
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = True
        self.viewer.current_frame = np.zeros((24, 32, 3), np.uint8)
        self.viewer.frame_count = 101
        self.viewer.frame_rate = 25
        self.slider = self.viewer.timeline_slider
        self.slider.setRange(0, 100)
        self.slider.setEnabled(True)
        self.viewer.show()
        self.app.processEvents()
        self.get = self.enterContext(patch.object(
            self.viewer, "get_frame", return_value=self.viewer.current_frame))
        self.update = self.enterContext(patch.object(self.viewer, "update_frame"))

    def point(self, fraction):
        _, handle, groove = self.slider._geometry()
        return QPoint(round(groove.x() + handle.width() / 2
                            + fraction * (groove.width() - handle.width())),
                      handle.center().y())

    def test_track_click_seeks_on_press_without_duplicate_release(self):
        point = self.point(.75)
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(self.viewer.current_frame_index, 75)
        self.get.assert_called_once_with(75)
        self.update.assert_called_once()
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=point)
        self.get.assert_called_once()
        self.assertEqual(self.slider.value(), 75)

    def test_handle_drag_moves_visually_without_decoding_until_release(self):
        handle = self.slider._geometry()[1]
        start = QPoint(handle.x() + 2, handle.center().y())
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual(self.slider.value(), 0)
        for fraction in (.1, .2, .4, .6, .8):
            move_slider(self.slider, self.point(fraction))
        self.assertGreaterEqual(self.slider.sliderPosition(), 80)
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.get.assert_not_called()
        final = self.point(.9)
        move_slider(self.slider, final)
        selected = self.slider.sliderPosition()
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=final)
        self.get.assert_called_once_with(selected)
        self.assertEqual(self.viewer.current_frame_index, selected)
        self.assertEqual(self.slider.value(), selected)
        self.assertFalse(self.slider.isSliderDown())

    def test_track_press_then_drag_and_release_at_latest_position(self):
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=self.point(.25))
        self.assertEqual(self.viewer.current_frame_index, 25)
        move_slider(self.slider, self.point(.6))
        self.assertEqual(self.viewer.current_frame_index, 25)
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=self.point(.9))
        self.assertEqual(self.viewer.current_frame_index, 90)
        self.assertEqual([call.args for call in self.get.call_args_list], [(25,), (90,)])

    def test_edges_clamp_and_single_frame_remains_valid(self):
        for point, expected in ((QPoint(self.slider.width() + 100, 5), 100),
                                (QPoint(-100, 5), 0)):
            QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=point)
            self.assertEqual(self.viewer.current_frame_index, expected)
        self.slider.setMaximum(0)
        self.viewer.current_frame_index = 0
        self.get.reset_mock()
        QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(.5))
        self.assertEqual(self.slider.value(), 0)
        self.assertTrue(all(call.args == (0,) for call in self.get.call_args_list))

    def test_right_to_left_style_mapping(self):
        self.slider.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(.25))
        self.assertEqual(self.viewer.current_frame_index, 75)

    def test_playback_updates_only_due_frame_and_drag_pauses_audio(self):
        self.viewer._playback_timeline = PlaybackTimeline.from_metadata([], 101, 25)
        self.viewer._playback_clock = PlaybackClock(
            self.viewer._playback_timeline, 0, 1, 100)
        self.viewer.is_playing = True
        with patch("osvev_app.time.perf_counter", return_value=100.039):
            self.viewer._playback_tick()
        self.assertEqual(self.slider.value(), 0)
        with patch("osvev_app.time.perf_counter", return_value=100.041):
            self.viewer._playback_tick()
        self.assertEqual(self.slider.value(), 1)
        self.assertEqual(self.viewer.current_frame_index, 1)
        self.get.reset_mock()
        with patch.object(self.viewer._audio, "pause") as pause:
            QTest.mousePress(self.slider, Qt.MouseButton.LeftButton,
                             pos=self.slider._geometry()[1].center())
            pause.assert_called_once()
            self.assertFalse(self.viewer.is_playing)
            self.assertFalse(self.viewer.timer.isActive())
            move_slider(self.slider, self.point(.6))
            self.get.assert_not_called()
            QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=self.point(.6))
        self.assertFalse(self.viewer.is_playing)
        self.viewer.resume_video()
        self.assertEqual(self.viewer._playback_clock.anchor, self.viewer.current_frame_index)

    def test_failed_seek_keeps_previous_frame_and_restores_handle(self):
        self.get.side_effect = RuntimeError("Exact seek failed")
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=self.point(.5))
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.assertEqual(self.slider.value(), 0)
        self.update.assert_not_called()
        self.warning.assert_called_once()
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=self.point(.5))
        self.warning.assert_called_once()
