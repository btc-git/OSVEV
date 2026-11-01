# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton

from osvev_app import OSVEV, TimeRangeDialog, VIDEO_INPUT_FILTER


class NavigationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.warning = self.enterContext(patch.object(QMessageBox, "warning"))
        self.information = self.enterContext(patch.object(QMessageBox, "information"))
        self.enterContext(patch.object(OSVEV, "_check_dependencies"))
        self.enterContext(patch.object(OSVEV, "set_windows_taskbar_icon"))
        self.viewer = OSVEV(decoder_backend="opencv")
        self.addCleanup(self.viewer.close)
        self.viewer.current_frame = np.zeros((48, 64, 3), np.uint8)
        self.viewer.frame_count = 7
        self.viewer.timeline_slider.setMaximum(6)
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = True
        self.viewer.frame_metadata = [
            {"pict_type": kind, "key_frame": key}
            for kind, key in (("I", 0), ("P", 1), ("B", 0), ("I", 0),
                              ("P", 1), ("I", 1), ("B", 0))
        ]

    def test_compact_button_order_labels_and_tooltips(self):
        buttons = self.viewer.findChildren(QPushButton)
        navigation = [
            self.viewer.prev_frame_button, self.viewer.prev_iframe_button,
            self.viewer.play_pause_button, self.viewer.next_iframe_button,
            self.viewer.next_frame_button,
        ]
        layout = self.viewer.open_button.parentWidget().layout()
        row = next(layout.itemAt(i).layout() for i in range(layout.count())
                   if layout.itemAt(i).layout() is not None
                   and layout.itemAt(i).layout().indexOf(self.viewer.open_button) >= 0)
        positions = [row.indexOf(button) for button in navigation]
        self.assertEqual(positions, list(range(positions[0], positions[0] + 5)))
        self.assertEqual([button.text() for button in navigation],
                         ["◀ Frame", "◀ I-Frame", "Play", "I-Frame ▶", "Frame ▶"])
        self.assertEqual(self.viewer.export_button.text(), "Export")
        self.assertEqual(sum(button.text() == "Open" for button in buttons), 1)
        self.assertFalse(any("Open Time Range" in button.text() for button in buttons))
        for button in navigation[:2] + navigation[3:]:
            self.assertTrue(button.toolTip())

    def test_iframe_navigation_uses_picture_type_not_keyframe_and_skips_current(self):
        self.viewer.current_frame_index = 3
        self.viewer.is_playing = True
        self.viewer.timer.start(1000)
        with patch.object(self.viewer, "get_frame", return_value=self.viewer.current_frame) as get, \
                patch.object(self.viewer, "update_frame") as update:
            self.viewer.prev_iframe_button.click()
            get.assert_called_once_with(0)
            self.assertEqual(self.viewer.current_frame_index, 0)
            self.assertFalse(self.viewer.is_playing)
            self.assertFalse(self.viewer.timer.isActive())
            self.assertEqual(self.viewer.timeline_slider.value(), 0)
            update.assert_called_once()
            self.viewer.next_iframe_button.click()
            self.assertEqual(get.call_args.args, (3,))
            self.viewer.next_iframe_button.click()
            self.assertEqual(get.call_args.args, (5,))
            self.assertEqual(self.viewer.timeline_slider.value(), 5)
        self.warning.assert_not_called()

    def test_iframe_boundaries_do_not_wrap_or_decode(self):
        with patch.object(self.viewer, "get_frame") as get:
            self.viewer.current_frame_index = 0
            self.viewer.navigate_iframe(-1)
            self.viewer.current_frame_index = 6
            self.viewer.navigate_iframe(1)
            get.assert_not_called()
        self.assertEqual(self.information.call_count, 2)
        self.assertEqual(self.viewer.current_frame_index, 6)

    def test_missing_type_is_not_replaced_with_keyframe_or_skipped(self):
        for metadata in (
            [],
            [{**entry, "pict_type": None} if i == 4 else entry
             for i, entry in enumerate(self.viewer.frame_metadata)],
        ):
            self.viewer.frame_metadata = metadata
            self.viewer.current_frame_index = 3
            with patch.object(self.viewer, "get_frame") as get:
                self.viewer.navigate_iframe(1)
                get.assert_not_called()
            self.assertEqual(self.viewer.current_frame_index, 3)
        self.assertEqual(self.warning.call_count, 2)

    def test_failed_iframe_read_keeps_index_timeline_and_view(self):
        self.viewer.current_frame_index = 3
        self.viewer.timeline_slider.setValue(3)
        for result in (None, RuntimeError("Seek failed")):
            with patch.object(self.viewer, "get_frame", side_effect=(
                result if isinstance(result, Exception) else lambda _: None
            )), patch.object(self.viewer, "update_frame") as update:
                self.viewer.navigate_iframe(1)
                self.assertEqual(self.viewer.current_frame_index, 3)
                self.assertEqual(self.viewer.timeline_slider.value(), 3)
                update.assert_not_called()
        self.assertEqual(self.warning.call_count, 2)

    def test_frame_buttons_still_use_original_methods(self):
        self.viewer.current_frame_index = 3
        self.viewer.cap.read.return_value = (True, self.viewer.current_frame.copy())
        with patch.object(self.viewer, "update_frame"):
            self.viewer.prev_frame_button.click()
            self.assertEqual(self.viewer.current_frame_index, 2)
            self.viewer.next_frame_button.click()
            self.assertEqual(self.viewer.current_frame_index, 3)

    def test_keyboard_frame_and_iframe_navigation_with_timeline_focus(self):
        self.viewer.show()
        self.viewer.activateWindow()
        self.viewer.timeline_slider.setEnabled(True)
        self.viewer.timeline_slider.setFocus()
        self.app.processEvents()
        self.viewer.current_frame_index = 3
        with patch.object(self.viewer, "get_frame", return_value=self.viewer.current_frame), \
                patch.object(self.viewer, "update_frame"):
            for key, modifiers, expected in (
                (Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier, 2),
                (Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, 3),
                (Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier, 0),
                (Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier, 3),
                (Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier, 5),
            ):
                QTest.keyClick(self.viewer.timeline_slider, key, modifiers)
                self.assertEqual(self.viewer.current_frame_index, expected)
                self.assertEqual(self.viewer.timeline_slider.value(), expected)
        self.warning.assert_not_called()
        self.assertTrue(all(not shortcut.autoRepeat()
                            for shortcut in self.viewer.findChildren(QShortcut)))

    def _wait_until(self, predicate, timeout=2000):
        for _ in range(timeout // 10):
            if predicate():
                break
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_held_arrow_repeats_single_frames_until_release_or_boundary(self):
        self.viewer.ARROW_HOLD_DELAY_MS = 20
        self.viewer.ARROW_HOLD_INTERVAL_MS = 10
        self.viewer.show()
        self.viewer.activateWindow()
        self.viewer.timeline_slider.setEnabled(True)
        self.viewer.timeline_slider.setFocus()
        self.app.processEvents()
        visited = []
        def get(index):
            visited.append(index)
            return self.viewer.current_frame
        slider = self.viewer.timeline_slider
        with patch.object(self.viewer, "get_frame", side_effect=get), \
                patch.object(self.viewer, "update_frame"):
            self.viewer.current_frame_index = 2
            QTest.keyPress(slider, Qt.Key.Key_Right)
            self.assertEqual(self.viewer.current_frame_index, 3)
            self._wait_until(lambda: not self.viewer._arrow_hold_direction)
            QTest.keyRelease(slider, Qt.Key.Key_Right)
            self.assertEqual(visited, [3, 4, 5, 6])
            self.assertEqual(slider.value(), 6)

            visited.clear()
            self.viewer.current_frame_index = 6
            QTest.keyPress(slider, Qt.Key.Key_Left)
            self._wait_until(lambda: self.viewer.current_frame_index <= 3)
            QTest.keyRelease(slider, Qt.Key.Key_Left)
            stopped = self.viewer.current_frame_index
            QTest.qWait(80)
            self.assertEqual(self.viewer.current_frame_index, stopped)
            self.assertEqual(visited, list(range(5, stopped - 1, -1)))
            self.assertFalse(self.viewer._arrow_hold_timer.isActive())

            # A single press keeps its existing wrap-around behavior, but holding never wraps.
            visited.clear()
            self.viewer.current_frame_index = 0
            QTest.keyClick(slider, Qt.Key.Key_Left)
            QTest.qWait(80)
            self.assertEqual(visited, [6])

            # Shift+arrow keeps single I-frame navigation without repeating.
            visited.clear()
            self.viewer.current_frame_index = 0
            QTest.keyPress(slider, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
            QTest.qWait(80)
            QTest.keyRelease(slider, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(visited, [3])

            # Another key (Shift or Space) ends the hold; a failed read also ends it.
            for other in (Qt.Key.Key_Shift, Qt.Key.Key_Space):
                visited.clear()
                self.viewer.current_frame_index = 1
                with patch.object(self.viewer, "resume_video") as toggle:
                    QTest.keyPress(slider, Qt.Key.Key_Right)
                    QTest.keyPress(slider, other)
                    QTest.qWait(80)
                    QTest.keyRelease(slider, other)
                    QTest.keyRelease(slider, Qt.Key.Key_Right)
                self.assertEqual(visited, [2])
                self.assertEqual(toggle.call_count, other == Qt.Key.Key_Space)
        self.warning.assert_not_called()
        with patch.object(self.viewer, "get_frame", return_value=None), \
                patch.object(self.viewer, "update_frame"):
            self.viewer.current_frame_index = 1
            QTest.keyPress(slider, Qt.Key.Key_Right)
            QTest.qWait(80)
            self.assertFalse(self.viewer._arrow_hold_direction)
            self.assertEqual(self.warning.call_count, 1)
            QTest.keyRelease(slider, Qt.Key.Key_Right)

    def test_space_toggles_playback_not_focused_button_and_dialog_is_isolated(self):
        self.viewer.show()
        self.viewer.activateWindow()
        self.viewer.reset_button.setFocus()
        self.app.processEvents()
        with patch.object(self.viewer, "reset_settings") as reset:
            QTest.keyClick(self.viewer.reset_button, Qt.Key.Key_Space)
            self.assertTrue(self.viewer.is_playing)
            QTest.keyClick(self.viewer.reset_button, Qt.Key.Key_Space)
            self.assertFalse(self.viewer.is_playing)
            reset.assert_not_called()
        dialog = TimeRangeDialog(self.viewer)
        self.addCleanup(dialog.close)
        dialog.setModal(True)
        dialog.show()
        dialog.activateWindow()
        self.app.processEvents()
        with patch.object(self.viewer, "get_frame") as get:
            QTest.keyClick(dialog, Qt.Key.Key_Space)
            QTest.keyClick(dialog, Qt.Key.Key_Right)
            QTest.keyClick(dialog, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
            get.assert_not_called()
            self.assertFalse(self.viewer.is_playing)

    def test_open_button_dispatches_entire_video_without_partial_arguments(self):
        for name in ("input.mp4", "input.mkv", "export-ffv1.mkv"):
            with patch.object(QFileDialog, "getOpenFileName", return_value=(name, "")) as pick, \
                    patch.object(TimeRangeDialog, "get_open_choice",
                                 return_value=(True, None)), \
                    patch.object(self.viewer, "_load_video") as load:
                self.viewer.open_button.click()
                load.assert_called_once_with(name)
                self.assertEqual(pick.call_args.args[3], VIDEO_INPUT_FILTER)

    def test_selected_range_routes_to_existing_partial_loader(self):
        self.viewer.decoder_backend = "pyav"
        with patch.object(QFileDialog, "getOpenFileName", return_value=("input.mp4", "")), \
                patch.object(TimeRangeDialog, "get_open_choice",
                             return_value=(True, (1.5, 4.5))) as choice, \
                patch.object(self.viewer, "_load_video") as load:
            self.viewer.open_button.click()
            choice.assert_called_once_with(self.viewer, allow_range=True, video_path="input.mp4")
            load.assert_called_once_with("input.mp4", (1.5, 4.5))

    def test_cancel_at_either_stage_does_not_load_or_pause(self):
        with patch.object(QFileDialog, "getOpenFileName", return_value=("", "")), \
                patch.object(TimeRangeDialog, "get_open_choice") as choice, \
                patch.object(self.viewer, "_load_video") as load:
            self.viewer.open_button.click()
            choice.assert_not_called()
            load.assert_not_called()
        with patch.object(QFileDialog, "getOpenFileName", return_value=("input.mp4", "")), \
                patch.object(TimeRangeDialog, "get_open_choice",
                             return_value=(False, None)), \
                patch.object(self.viewer, "_load_video") as load, \
                patch.object(self.viewer, "pause_video") as pause:
            self.viewer.open_button.click()
            load.assert_not_called()
            pause.assert_not_called()

    def test_dialog_defaults_to_entire_and_ignores_unused_range_fields(self):
        dialog = TimeRangeDialog(choose_scope=True)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.scope_dropdown.currentText(), "Entire Video")
        self.assertFalse(dialog.range_fields.isHidden())
        self.assertFalse(dialog.range_fields.isEnabled())
        dialog.start_edit.setText("invalid")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIsNone(dialog.time_range)
        self.warning.assert_not_called()

    def test_range_fields_validation_and_legacy_range_dialog(self):
        for choose_scope in (False, True):
            dialog = TimeRangeDialog(choose_scope=choose_scope)
            self.addCleanup(dialog.close)
            dialog.scope_dropdown.setCurrentIndex(1)
            self.assertFalse(dialog.range_fields.isHidden())
            dialog.start_edit.setText("00:00:03.5")
            dialog.end_edit.setText("2")
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            dialog.end_edit.setText("5")
            dialog.accept()
            self.assertEqual(dialog.time_range, (3.5, 5))
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.warning.call_count, 2)

    def test_legacy_decoder_only_offers_entire_video(self):
        dialog = TimeRangeDialog(choose_scope=True, allow_range=False)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.scope_dropdown.isEnabled())
        self.assertEqual(dialog.scope_dropdown.currentIndex(), 0)
        with patch.object(QFileDialog, "getOpenFileName", return_value=("input.avi", "")), \
                patch.object(TimeRangeDialog, "get_open_choice",
                             return_value=(True, None)) as choice, \
                patch.object(self.viewer, "_load_video"):
            self.viewer.open_button.click()
            choice.assert_called_once_with(self.viewer, allow_range=False, video_path="input.avi")
