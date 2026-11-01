# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Partial time-range indexing keeps exact original source-frame correspondence."""

from fractions import Fraction
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from export_runner import execute_export
from ffmpeg_export import ExportError, build_export_plan
from frame_info import FrameInfoHandler, MetadataCancelled, SourceRange, parse_time

AVAILABLE = importlib.util.find_spec("av") is not None and shutil.which("ffmpeg") and shutil.which("ffprobe")
SETTINGS = {"brightness": 15, "contrast": 25, "sharpness": 40,
            "crop_top": 3, "crop_bottom": 5, "crop_left": 7, "crop_right": 1}


def ffmpeg(*arguments):
    result = subprocess.run(["ffmpeg", "-v", "error", *map(str, arguments)],
                            capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))


class ParseTimeTests(unittest.TestCase):
    def test_formats_are_exact(self):
        self.assertEqual(parse_time("180"), 180)
        self.assertEqual(parse_time("03:00"), 180)
        self.assertEqual(parse_time("00:03:00.5"), Fraction(361, 2))
        self.assertEqual(parse_time("1:02:03.25"), Fraction(3723) + Fraction(1, 4))
        for bad in ("", "abc", "-1", "1:60", "1::2", "1:2:3:4"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_time(bad)


@unittest.skipUnless(AVAILABLE, "Optional PyAV and FFmpeg/FFprobe are required")
class RangeImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="osvev-range-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        encoded = root / "encoded.mp4"
        # 360 frames, B-frames, 1 s GOPs and irregular (VFR) timestamps from frame 150.
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=96x64:rate=30:duration=12",
               "-vf", "setpts='N/(30*TB)+if(gte(N,150),0.01*mod(N,3)/TB,0)'",
               "-fps_mode", "passthrough", "-video_track_timescale", "90000",
               "-c:v", "libx264", "-g", "30", "-bf", "3", "-pix_fmt", "yuv420p",
               "-colorspace", "bt709", "-color_range", "tv",
               "-color_primaries", "bt709", "-color_trc", "bt709", encoded)
        cls.source = root / "range source.mp4"
        ffmpeg("-i", encoded, "-c:v", "copy", "-bsf:v",
               "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
               "matrix_coefficients=1:video_full_range_flag=0", cls.source)
        cls.full = FrameInfoHandler.get_frame_metadata(str(cls.source))
        cls.base = Fraction(1, 90000)
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="osvev-range-out-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def expected_slice(self, start, end):
        s, e = Fraction(start), Fraction(end)
        indices = [i for i, f in enumerate(self.full) if s <= f["pts"] * self.base < e]
        frames = [self.full[i].copy() for i in indices]
        FrameInfoHandler.annotate_iframe_distances(frames)
        return indices[0], frames

    def test_partial_metadata_equals_full_scan_slice(self):
        self.assertEqual(len(self.full), 360)
        source_bytes = hashlib.sha256(self.source.read_bytes()).hexdigest()
        for start, end in (("0", "1"), ("4.51", "6.2"), ("5", "5.5"), ("1.99", "2.01"),
                           ("6.7", "8.3"), ("11.5", "100"), ("0", "100")):
            with self.subTest(start=start, end=end):
                metadata, source_range = FrameInfoHandler.get_range_metadata(
                    str(self.source), parse_time(start), parse_time(end))
                offset, expected = self.expected_slice(parse_time(start), parse_time(end))
                self.assertEqual(metadata, expected)
                self.assertEqual(source_range.offset, offset)
                self.assertEqual(source_range.source_frame_count, len(self.full))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_bytes)

    def test_progress_scan_preserves_exact_full_and_partial_metadata(self):
        updates = []
        metadata = FrameInfoHandler.get_frame_metadata(
            str(self.source), progress=lambda *args: updates.append(args), expected_frames=360
        )
        self.assertEqual(metadata, self.full)
        self.assertIn(("Reading source frame metadata", 360, 360), updates)
        updates.clear()
        metadata, source_range = FrameInfoHandler.get_range_metadata(
            str(self.source), Fraction(5), Fraction(6),
            progress=lambda *args: updates.append(args),
        )
        offset, expected = self.expected_slice("5", "6")
        self.assertEqual(metadata, expected)
        self.assertEqual(source_range.offset, offset)
        self.assertTrue(any("packets" in stage for stage, _, _ in updates))
        self.assertTrue(all(total == 0 for _, _, total in updates))

    def test_cancel_index_validation_does_not_install_partial_timestamps(self):
        from pyav_input import PyAVInputCapture
        capture = PyAVInputCapture(str(self.source))
        self.addCleanup(capture.release)
        ok, first = capture.read()
        self.assertTrue(ok)
        checks = []

        def cancelled():
            checks.append(True)
            return len(checks) == 2

        with self.assertRaises(MetadataCancelled):
            capture.configure_index(self.full, cancelled=cancelled)
        self.assertEqual(len(checks), 2)
        self.assertEqual(capture._timestamps, ())
        capture.configure_index(self.full)
        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, reopened = capture.read()
        self.assertTrue(ok)
        np.testing.assert_array_equal(reopened, first)

    def test_empty_and_invalid_intervals_are_rejected(self):
        for start, end in ((Fraction(13), Fraction(14)), (Fraction(5), Fraction(5)),
                           (Fraction(6), Fraction(5)), (Fraction(-1), Fraction(1))):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                FrameInfoHandler.get_range_metadata(str(self.source), start, end)
        first = Fraction(self.full[100]["pts"]) * self.base
        with self.assertRaises(ValueError):  # half-open: no frame strictly between neighbours
            FrameInfoHandler.get_range_metadata(str(self.source), first + Fraction(1, 1000),
                                                first + Fraction(2, 1000))

    def test_partial_capture_seeks_to_exact_original_frames(self):
        from pyav_input import PyAVInputCapture
        metadata, source_range = FrameInfoHandler.get_range_metadata(
            str(self.source), Fraction(451, 100), Fraction(62, 10))
        full = PyAVInputCapture(str(self.source))
        partial = PyAVInputCapture(str(self.source))
        self.addCleanup(full.release)
        self.addCleanup(partial.release)
        full.read()
        full.configure_index(self.full)
        partial.read()
        partial.configure_index(metadata, partial=True)
        self.assertEqual(partial.get(cv2.CAP_PROP_FRAME_COUNT), len(metadata))
        positions = [0, 1, 2, len(metadata) - 1, 7, 3, 0, len(metadata) - 2]
        # First read without set() must return the range start, not file frame 0.
        for number, position in enumerate(positions):
            if number:
                partial.set(cv2.CAP_PROP_POS_FRAMES, position)
            ok, frame = partial.read()
            self.assertTrue(ok)
            full.set(cv2.CAP_PROP_POS_FRAMES, source_range.source_index(position))
            ok, expected = full.read()
            self.assertTrue(ok)
            np.testing.assert_array_equal(frame, expected)
            self.assertEqual(partial.native_samples.packed_bytes(), full.native_samples.packed_bytes())
            self.assertEqual(partial.native_samples.frame.pts, metadata[position]["pts"])
        partial.set(cv2.CAP_PROP_POS_FRAMES, len(metadata) - 1)
        self.assertTrue(partial.read()[0])
        self.assertEqual(partial.read(), (False, None))
        with self.assertRaises(RuntimeError):
            partial.set(cv2.CAP_PROP_POS_FRAMES, len(metadata))

    def plan(self, kind, first, last, metadata, source_range=None, name="out", **kwargs):
        suffix = {"png": ".png", "ffv1": ".mkv", "h264": ".mp4"}[kind]
        return build_export_plan(str(self.source), str(self.root / (name + suffix)), kind, first, last,
                                 SETTINGS, metadata, "pyav", source_range=source_range, **kwargs)

    def test_partial_plans_match_full_plans_for_same_original_frames(self):
        metadata, source_range = FrameInfoHandler.get_range_metadata(
            str(self.source), Fraction(451, 100), Fraction(83, 10))
        offset, last = source_range.offset, source_range.offset + len(metadata) - 1
        for kind, first, final in (("png", offset + 3, offset + 3), ("ffv1", offset, last),
                                   ("h264", offset + 10, offset + 70)):
            with self.subTest(kind=kind):
                partial = self.plan(kind, first, final, metadata, source_range, playback_speed=2)
                full = self.plan(kind, first, final, self.full, playback_speed=2)
                self.assertEqual(partial.command(), full.command())
                self.assertEqual(partial.expected_times, full.expected_times)
                self.assertEqual(partial.expected_duration, full.expected_duration)
                self.assertEqual(partial.frame_count, full.frame_count)
                self.assertIn(f"trim=start_frame={first}:end_frame={final + 1}", partial.filter_graph)
        for first, final in ((offset - 1, offset), (last, last + 1), (0, 1)):
            with self.assertRaises(ExportError):
                self.plan("ffv1", first, final, metadata, source_range)

    def test_partial_exports_verify_pixels_and_timing(self):
        metadata, source_range = FrameInfoHandler.get_range_metadata(
            str(self.source), Fraction(451, 100), Fraction(62, 10))
        offset = source_range.offset
        cases = (("ffv1", offset, offset + len(metadata) - 1, 2),
                 ("png", offset + 5, offset + 7, 1),
                 ("h264", offset + 1, offset + 20, Fraction(1, 2)))
        for kind, first, last, speed in cases:
            with self.subTest(kind=kind):
                plan = self.plan(kind, first, last, metadata, source_range,
                                 name=f"range-{kind}", playback_speed=float(speed))
                report = execute_export(plan, metadata)
                self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                self.assertEqual(report["comparison"]["frames_compared"], last - first + 1)
                if kind != "h264":
                    self.assertEqual(report["status"], "verified_lossless")
                    self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                if kind != "png":
                    self.assertTrue(report["timing"]["verified"])
                text = plan.log_path.read_text(encoding="utf-8")
                self.assertIn(f"Frames (1-based, inclusive): {first + 1}-{last + 1}", text)
                self.assertIn(f"Selected frames: {last - first + 1} / {len(self.full)}", text)
                self.assertIn("Loaded time range: source PTS 4.510000-6.200000 s", text)
                self.assertIn(f"source frames {offset + 1}-{offset + len(metadata)}", text)

    def test_unified_range_open_and_iframe_navigation_use_verified_local_index(self):
        from osvev_app import OSVEV, TimeRangeDialog
        viewer = OSVEV()
        self.addCleanup(viewer.close)
        start, end = Fraction(451, 100), Fraction(83, 10)
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.source), "")), \
                patch.object(TimeRangeDialog, "get_open_choice",
                             return_value=(True, (start, end))), \
                patch.object(QMessageBox, "warning") as warning, \
                patch.object(QMessageBox, "information"):
            viewer.open_button.click()
            offset, expected = self.expected_slice(start, end)
            self.assertEqual(viewer.frame_metadata, expected)
            self.assertEqual(viewer.source_range.offset, offset)
            iframes = [i for i, frame in enumerate(expected) if frame["pict_type"] == "I"]
            self.assertGreaterEqual(len(iframes), 2)
            viewer.set_frame_position(0)
            next_i = next(i for i in iframes if i > 0)
            viewer.next_iframe_button.click()
            self.assertEqual(viewer.current_frame_index, next_i)
            self.assertEqual(viewer.current_native_samples.frame.pts, expected[next_i]["pts"])
            viewer.next_iframe_button.click()
            second_i = next(i for i in iframes if i > next_i)
            self.assertEqual(viewer.current_frame_index, second_i)
            viewer.prev_iframe_button.click()
            self.assertEqual(viewer.current_frame_index, next_i)
            self.assertEqual(viewer.current_native_samples.frame.pts, expected[next_i]["pts"])
            viewer.set_frame_position(iframes[-1])
            viewer.next_iframe_button.click()
            self.assertEqual(viewer.current_frame_index, iframes[-1])
            warning.assert_not_called()

    def test_app_range_open_navigation_and_export_dialog(self):
        from export_dialog import ExportDialog
        from osvev_app import OSVEV, TimeRangeDialog
        messages = []
        viewer = OSVEV()
        self.addCleanup(viewer.deleteLater)
        self.addCleanup(lambda: viewer.cap is not None and viewer.cap.release())
        start, end = Fraction(451, 100), Fraction(62, 10)
        with patch.object(QMessageBox, "warning", side_effect=lambda *a: messages.append(a)), \
                patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.source), "")), \
                patch.object(TimeRangeDialog, "get_range", return_value=(start, end)):
            viewer.open_time_range()
            offset, expected = self.expected_slice(start, end)
            self.assertEqual(viewer.frame_count, len(expected))
            self.assertEqual(viewer.frame_metadata, expected)
            self.assertEqual(viewer.timeline_slider.maximum(), len(expected) - 1)
            self.assertEqual(viewer.source_range.offset, offset)
            self.assertEqual(viewer.current_native_samples.frame.pts, expected[0]["pts"])
            rows = dict(FrameInfoHandler.format_frame_data(
                expected[0], 0, viewer.frame_count, viewer.frame_rate, "1x", "1x", 0,
                viewer.source_range))
            self.assertEqual(rows["Source Frame"], f"{offset + 1} of {len(self.full)}")
            self.assertTrue(rows["Loaded Range Frame"].startswith(f"1 of {len(expected)}"))
            for index in (5, len(expected) - 1, 0, 9):
                viewer.set_frame_position(index)
                self.app.processEvents()
                self.assertEqual(viewer.current_native_samples.frame.pts, expected[index]["pts"])
            viewer.manual_next_frame()
            self.assertEqual(viewer.current_native_samples.frame.pts, expected[10]["pts"])
            viewer.set_frame_position(len(expected) - 1)
            viewer.manual_next_frame()  # wraps within the loaded range
            self.assertEqual(viewer.current_frame_index, 0)
            self.assertEqual(viewer.current_native_samples.frame.pts, expected[0]["pts"])
            viewer.set_frame_position(4)
            dialog = ExportDialog(str(self.source), viewer.current_frame_index, viewer.frame_metadata,
                                  viewer.get_processing_params(), "pyav", viewer,
                                  source_range=viewer.source_range)
            self.addCleanup(dialog.deleteLater)
            self.assertEqual(dialog.first_spin.minimum(), offset + 1)
            self.assertEqual(dialog.first_spin.maximum(), offset + len(expected))
            self.assertEqual(dialog.first_spin.value(), offset + 5)
            self.assertEqual(dialog.selection_combo.itemText(2), "Whole loaded time range")
            dialog.format_combo.setCurrentIndex(1)
            dialog.path_edit.setText(str(self.root / "dialog.mkv"))
            dialog.selection_combo.setCurrentIndex(0)
            self.assertIsNotNone(dialog.plan, dialog.status_label.text())
            self.assertEqual((dialog.plan.first, dialog.plan.last), (offset + 4, offset + 4))
            dialog.selection_combo.setCurrentIndex(2)
            self.assertEqual((dialog.plan.first, dialog.plan.last), (offset, offset + len(expected) - 1))
            # Re-opening the whole video restores full numbering.
            viewer.open_video()
            self.assertIsNone(viewer.source_range)
            self.assertEqual(viewer.frame_count, len(self.full))
            self.assertEqual(viewer.current_native_samples.frame.pts, self.full[0]["pts"])
        self.assertEqual(messages, [])

    def test_source_range_maps_positions(self):
        self.assertEqual(SourceRange(120, 900, Fraction(4), Fraction(6)).source_index(5), 125)

    def test_timeline_mouse_seeks_preserve_full_and_partial_vfr_pixels_and_pts(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from osvev_app import OSVEV
        from pyav_input import PyAVInputCapture
        from test_timeline_slider import move_slider
        viewer = OSVEV()
        self.addCleanup(viewer.close)
        reference = PyAVInputCapture(str(self.source))
        self.addCleanup(reference.release)
        self.assertTrue(reference.read()[0])
        reference.configure_index(self.full)
        viewer.show()
        with patch.object(QMessageBox, "warning") as warning:
            for interval in (None, (Fraction(451, 100), Fraction(62, 10))):
                with self.subTest(interval=interval):
                    viewer._load_video(str(self.source), interval)
                    self.app.processEvents()
                    slider = viewer.timeline_slider
                    _, handle, groove = slider._geometry()
                    point = groove.center()
                    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=point)
                    self.assertLessEqual(abs(viewer.current_frame_index
                                             - (viewer.frame_count - 1) / 2), 1)
                    QTest.mousePress(slider, Qt.MouseButton.LeftButton,
                                     pos=slider._geometry()[1].center())
                    with patch.object(viewer, "get_frame", wraps=viewer.get_frame) as get:
                        move_slider(slider, point)
                        move_slider(slider, point + handle.bottomRight())
                        get.assert_not_called()
                        point.setX(slider.width() + 30)
                        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=point)
                        get.assert_called_once_with(viewer.frame_count - 1)
                    for fraction in (0, .35, 1):
                        _, handle, groove = slider._geometry()
                        point.setX(round(groove.x() + handle.width() / 2
                                         + fraction * (groove.width() - handle.width())))
                        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=point)
                        local = viewer.current_frame_index
                        source = viewer.source_range.source_index(local) if interval else local
                        self.assertEqual(viewer.current_native_samples.frame.pts,
                                         self.full[source]["pts"])
                        self.assertTrue(reference.set(cv2.CAP_PROP_POS_FRAMES, source))
                        ok, pixels = reference.read()
                        self.assertTrue(ok)
                        np.testing.assert_array_equal(viewer.current_frame, pixels)
                        self.assertEqual(slider.value(), local)
            warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
