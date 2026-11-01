# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Header-only Open dialog information must not masquerade as verified timing."""

from fractions import Fraction
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QDialogButtonBox

from osvev_app import TimeRangeDialog
from sample_media import requires_samples
from video_summary import VideoSummaryProbe, indexed_bounds, summary_text, reported_bounds, clock_text


class SummaryTests(unittest.TestCase):
    def test_reported_values_and_unavailable_values(self):
        self.assertEqual(
            summary_text({"streams": [{"width": 1920, "height": 1080, "duration": "1.5"}]},
                         1048576),
            "Reported duration: 00:00:01.500 | 1920x1080 | 1.00 MiB",
        )
        self.assertEqual(summary_text({}, None),
                         "Reported duration: unknown | unknown | unknown")
        self.assertIn("unknown | unknown", summary_text(
            {"streams": [{"duration": "nan", "width": -1, "height": 10}],
             "format": {"duration": "N/A"}}, None))
        self.assertIn("00:00:03.000", summary_text({"format": {"duration": 3}}, 0))

    def test_clock_formatting_carries_and_does_not_wrap_at_24_hours(self):
        for value, expected in (("59.9996", "00:01:00.000"),
                                ("3661.125", "01:01:01.125"),
                                ("90000", "25:00:00.000")):
            self.assertEqual(clock_text(Fraction(value)), expected)

    def test_estimated_bounds_pair_duration_with_appropriate_origin(self):
        cases = [
            ({"streams": [{"start_time": "12.125", "duration": "3.5"}]},
             ("12.125", "15.625")),
            ({"streams": [{"start_time": "12"}],
              "format": {"start_time": "10", "duration": "8"}}, ("12", "18")),
            ({"format": {"start_time": "10", "duration": "8"}}, ("10", "18")),
            ({"streams": [{"duration": "8"}]}, ("0", "8")),
            ({"streams": [{"start_time": "12", "duration": "N/A"}]}, ("12", "72")),
            ({"streams": [{"start_time": "12", "duration": "-1"}]}, ("12", "72")),
            ({"streams": [{"start_time": "12", "duration": "nan"}]}, ("12", "72")),
            ({"streams": [{"start_time": "-2", "duration": "5"}]}, ("0", "3")),
            ({"streams": [{"start_time": "2"}],
              "format": {"start_time": "0", "duration": "1"}}, ("2", "62")),
            ({}, ("0", "60")),
            ({"streams": [{"start_time": "1.0009", "duration": "0.00001"}]},
             ("1", "1.001")),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                start, end = reported_bounds(data)
                self.assertEqual((start, end), tuple(Fraction(v) for v in expected))
                self.assertGreater(end, start)

    def test_index_boundaries_use_explicit_duration_not_fps_or_container_duration(self):
        frames = [
            {"best_effort_timestamp_time": "12.125"},
            {"best_effort_timestamp_time": "12.9", "duration_time": "0.075"},
        ]
        self.assertEqual(indexed_bounds(frames), (Fraction("12.125"), Fraction("12.975")))
        for bad in ([], [{}], [{"best_effort_timestamp_time": 2}],
                    [{"best_effort_timestamp_time": 2, "duration_time": 0}],
                    [{"best_effort_timestamp_time": "nan", "duration_time": 1}]):
            self.assertIsNone(indexed_bounds(bad))


class DialogSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, **kwargs):
        dialog = TimeRangeDialog(choose_scope=True, **kwargs)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        return dialog

    def test_scope_switch_keeps_size_and_fields_visible(self):
        dialog = self.dialog()
        before = dialog.size()
        self.assertTrue(dialog.start_edit.isVisible())
        self.assertFalse(dialog.start_edit.isEnabled())
        dialog.scope_dropdown.setCurrentIndex(1)
        self.app.processEvents()
        self.assertTrue(dialog.start_edit.isEnabled())
        self.assertEqual(dialog.size(), before)
        dialog._summary_ready({"streams": [{"start_time": "123456.125",
                                           "width": 1920, "height": 1080,
                                           "duration": 9999999}]}, 2**40, "")
        self.app.processEvents()
        self.assertEqual(dialog.size(), before)
        self.assertEqual(dialog.start_edit.text(), clock_text(Fraction("123456.125")))
        self.assertEqual(dialog.end_edit.text(), clock_text(Fraction("10123455.125")))
        self.assertEqual(dialog.bounds_note.text(),
                         "Time range defaults are based on reported metadata and may be approximate.")
        dialog.scope_dropdown.setCurrentIndex(0)
        self.assertEqual(dialog.size(), before)

    def test_unknown_start_uses_zero_and_reported_duration_as_estimate(self):
        dialog = self.dialog()
        for stream in ({}, {"start_time": "N/A"}, {"start_time": "-2"}):
            dialog._summary_ready({"streams": [stream], "format": {"duration": "9999"}},
                                  None, "")
            self.assertEqual(dialog.start_edit.text(), "00:00:00.000")
            self.assertEqual(dialog.end_edit.text(), "02:46:39.000")

    def test_entire_video_visibly_greys_only_time_range_section(self):
        dialog = self.dialog()
        size = dialog.size()
        fields = dialog.range_fields.findChildren(QLabel) + [dialog.start_edit, dialog.end_edit]
        for widget in fields:
            widget.ensurePolished()
            self.assertFalse(widget.isEnabled())
            role = QPalette.ColorRole.Text if widget is dialog.start_edit or widget is dialog.end_edit else QPalette.ColorRole.WindowText
            self.assertEqual(widget.palette().color(QPalette.ColorGroup.Disabled, role),
                             QColor("#808080"))
        self.assertTrue(dialog.summary_label.isEnabled())
        self.assertTrue(dialog.findChild(QDialogButtonBox).isEnabled())
        inactive_image = dialog.range_fields.grab().toImage()
        dialog.scope_dropdown.setCurrentIndex(1)
        self.app.processEvents()
        self.assertTrue(all(widget.isEnabled() for widget in fields))
        self.assertNotEqual(dialog.start_edit.palette().color(
            QPalette.ColorGroup.Active, QPalette.ColorRole.Text), QColor("#808080"))
        self.assertNotEqual(inactive_image, dialog.range_fields.grab().toImage())
        self.assertEqual(dialog.size(), size)
        dialog.scope_dropdown.setCurrentIndex(0)
        self.app.processEvents()
        self.assertFalse(dialog.start_edit.isEnabled())
        self.assertEqual(dialog.size(), size)

    def test_missing_duration_uses_60_seconds_and_formatted_range_validates(self):
        dialog = self.dialog()
        dialog._summary_ready({"streams": [{"start_time": "3661.125"}]}, None, "")
        self.assertEqual(dialog.start_edit.text(), "01:01:01.125")
        self.assertEqual(dialog.end_edit.text(), "01:02:01.125")
        dialog.scope_dropdown.setCurrentIndex(1)
        dialog.accept()
        self.assertEqual(dialog.time_range, (Fraction("3661.125"), Fraction("3721.125")))

    def test_late_probe_does_not_overwrite_user_edits(self):
        dialog = self.dialog()
        dialog.start_edit.setText("4")
        dialog.start_edit.setModified(True)
        dialog._summary_ready({"streams": [{"start_time": "12"}]}, 10, "")
        self.assertEqual(dialog.start_edit.text(), "4")
        self.assertEqual(dialog.end_edit.text(), "00:01:00.000")

    def test_complete_existing_index_supplies_nonzero_exclusive_end(self):
        parent = QWidget()
        self.addCleanup(parent.close)
        parent.video_path = str(Path(__file__).resolve())
        parent.decoder_backend = "pyav"
        parent.source_range = None
        parent.frame_count = 2
        parent.frame_metadata = [
            {"best_effort_timestamp_time": "5.000001"},
            {"best_effort_timestamp_time": "5.000003", "duration_time": "0.000002"},
        ]
        with patch.object(VideoSummaryProbe, "start"):
            dialog = self.dialog(parent=parent, video_path=parent.video_path)
        dialog._summary_ready({"streams": [{"start_time": "0", "duration": "1000"}]}, 10, "")
        self.assertEqual(dialog.start_edit.text(), "00:00:05.000001")
        self.assertEqual(dialog.end_edit.text(), "00:00:05.000005")
        dialog.scope_dropdown.setCurrentIndex(1)
        dialog.accept()
        self.assertEqual(dialog.time_range, (Fraction("5.000001"), Fraction("5.000005")))

    def test_partial_index_cannot_supply_whole_source_boundaries(self):
        parent = QWidget()
        self.addCleanup(parent.close)
        parent.video_path = "source.mp4"
        parent.decoder_backend = "pyav"
        parent.source_range = object()
        parent.frame_count = 1
        parent.frame_metadata = [{"best_effort_timestamp_time": "10", "duration_time": "1"}]
        with patch.object(VideoSummaryProbe, "start"):
            dialog = self.dialog(parent=parent, video_path="source.mp4")
        self.assertIsNone(dialog._known_bounds)

    def test_failed_header_is_explicit_and_does_not_prevent_entire_open(self):
        dialog = self.dialog()
        dialog._summary_ready({}, 100, "Header information unavailable: invalid input")
        self.assertIn("invalid input", dialog.summary_label.toolTip())
        self.assertIn("Header unavailable", dialog.bounds_note.text())
        dialog.accept()
        self.assertIsNone(dialog.time_range)

    def pump_until(self, predicate):
        end = time.monotonic() + 3
        while not predicate() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    @requires_samples("gas-station.mp4")
    def test_real_probe_is_header_only_and_returns_file_size(self):
        path = str(Path(__file__).resolve().parent.parent / "gas-station.mp4")
        probe = VideoSummaryProbe(path)
        self.addCleanup(probe.stop)
        result = []
        probe.ready.connect(lambda *args: result.append(args))
        probe.start()
        self.pump_until(lambda: bool(result))
        data, size, error = result[0]
        self.assertEqual(error, "")
        self.assertEqual(size, os.path.getsize(path))
        self.assertGreater(data["streams"][0]["width"], 0)
        arguments = probe.process.arguments()
        self.assertNotIn("-show_frames", arguments)
        self.assertNotIn("-show_packets", arguments)
        self.assertNotIn("-count_frames", arguments)
        self.assertEqual(probe.process.state(), QProcess.ProcessState.NotRunning)

    def test_cancel_kills_running_probe_and_gui_remains_responsive(self):
        # Use a controlled long-lived process instead of relying on a slow video.
        import sys
        probe = VideoSummaryProbe("source.mp4")
        probe.process.setProgram(sys.executable)
        probe.process.setArguments(["-c", "import time; time.sleep(30)"])
        probe.process.start()
        self.assertTrue(probe.process.waitForStarted(1000))
        ticks = []
        QTimer.singleShot(20, lambda: ticks.append(True))
        self.pump_until(lambda: bool(ticks))
        start = time.monotonic()
        probe.stop()
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(probe.process.state(), QProcess.ProcessState.NotRunning)
        self.assertFalse(probe.timer.isActive())

    def test_dialog_rejection_stops_probe(self):
        with patch.object(VideoSummaryProbe, "start"):
            dialog = self.dialog(video_path="source.mp4")
        with patch.object(dialog.probe, "stop") as stop:
            dialog.reject()
            stop.assert_called_once()

    def test_probe_timeout_stops_process_and_reports_unavailable(self):
        probe = VideoSummaryProbe("source.mp4")
        result = []
        probe.ready.connect(lambda *args: result.append(args))
        with patch.object(probe.process, "kill") as kill, \
                patch.object(probe.process, "waitForFinished"), \
                patch.object(probe.process, "state", return_value=QProcess.ProcessState.Running):
            probe._timeout()
        kill.assert_called_once()
        self.assertEqual(len(result), 1)
        self.assertIn("timed out", result[0][2])
        self.assertFalse(probe.timer.isActive())

    def test_invalid_probe_output_is_unknown_not_success(self):
        for output in (b"not json", b'{"streams": [null]}', b'{"format": null}'):
            probe = VideoSummaryProbe("source.mp4")
            result = []
            probe.ready.connect(lambda *args: result.append(args))
            with patch.object(probe.process, "readAllStandardOutput", return_value=output):
                probe._finished(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual(result[0][0], {})
            self.assertIn("unavailable", result[0][2])

    def test_process_launch_failure_is_reported(self):
        probe = VideoSummaryProbe("source.mp4")
        result = []
        probe.ready.connect(lambda *args: result.append(args))
        probe._error(QProcess.ProcessError.FailedToStart)
        self.assertEqual(len(result), 1)
        self.assertIn("unavailable", result[0][2])


if __name__ == "__main__":
    unittest.main()
