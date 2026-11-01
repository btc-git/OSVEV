# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Source ordinals, not timestamps alone, identify tied presentation frames."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from frame_info import FrameInfoHandler
from ffmpeg_export import ExportError, build_export_plan
from export_runner import execute_export
from osvev_app import OSVEV
from playback_timing import PlaybackClock, PlaybackTimeline
from pyav_input import InputDecodeError, PyAVInputCapture
from test_scrub_preview import wait_for
from test_timeline_slider import move_slider


def ffmpeg(args, data=None):
    return subprocess.run(["ffmpeg", "-v", "error", *map(str, args)], input=data,
                          capture_output=True, check=True, timeout=120).stdout


class DuplicatePTSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory(prefix="osvev-duplicate-pts-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.path = Path(cls.temp.name) / "duplicates.mkv"
        cls.pixels = np.random.default_rng(55).integers(0, 256, (8, 48, 64, 3), np.uint8)
        cls.pts = [0, 0, 80, 160, 160, 160, 240, 320]
        ffmpeg([
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "64x48", "-r", "12.5", "-i", "pipe:0",
            "-vf", "settb=1/1000,setpts='if(lt(N,2),0,if(lt(N,3),80,if(lt(N,6),160,(N-3)*80)))',"
            "setparams=range=full:colorspace=gbr:color_primaries=bt709:color_trc=bt709",
            "-fps_mode", "passthrough", "-enc_time_base", "1:1000",
            "-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0", cls.path,
        ], cls.pixels.tobytes())
        cls.metadata = FrameInfoHandler.get_frame_metadata(str(cls.path))
        if [entry["pts"] for entry in cls.metadata] != cls.pts:
            raise AssertionError("Fixture did not preserve duplicate PTS")

    def capture(self, metadata=None):
        capture = PyAVInputCapture(str(self.path))
        self.addCleanup(capture.release)
        capture.read()
        capture.configure_index(self.metadata if metadata is None else metadata)
        return capture

    def test_full_decode_and_random_seek_preserve_each_occurrence(self):
        capture = self.capture()
        self.assertTrue(capture.has_duplicate_pts)
        for index in (1, 0, 5, 4, 3, 7, 2, 6, 0, 1):
            self.assertTrue(capture.set(cv2.CAP_PROP_POS_FRAMES, index))
            ok, pixels = capture.read()
            self.assertTrue(ok)
            self.assertEqual(capture.native_samples.frame.pts, self.pts[index])
            np.testing.assert_array_equal(pixels, self.pixels[index])
        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        for index in range(8):
            np.testing.assert_array_equal(capture.read()[1], self.pixels[index])
        self.assertEqual(capture.read(), (False, None))

    def test_missing_decreasing_and_partial_ties_rejected_without_mutation(self):
        for value in (None, True, .5, -1):
            metadata = deepcopy(self.metadata)
            metadata[4]["pts"] = value
            before = deepcopy(metadata)
            with self.subTest(value=value), self.assertRaises(InputDecodeError):
                self.capture(metadata)
            self.assertEqual(metadata, before)
        capture = PyAVInputCapture(str(self.path))
        self.addCleanup(capture.release)
        capture.read()
        with self.assertRaisesRegex(InputDecodeError, "Entire Video|full-video"):
            capture.configure_index(self.metadata, partial=True)

    def viewer(self, path=None):
        self.enterContext(patch.object(OSVEV, "_check_dependencies"))
        self.enterContext(patch.object(OSVEV, "set_windows_taskbar_icon"))
        warning = self.enterContext(patch.object(QMessageBox, "warning"))
        viewer = OSVEV()

        def close():
            viewer.close()
            wait_for(lambda: viewer._scrub_preview._task is None)
            warning.assert_not_called()

        self.addCleanup(close)
        viewer.show()
        viewer._load_video(str(self.path if path is None else path))
        return viewer

    def test_stepping_and_live_scrub_distinguish_equal_time_frames(self):
        viewer = self.viewer()
        viewer.set_frame_position(0)
        np.testing.assert_array_equal(viewer.current_frame, self.pixels[0])
        viewer.manual_next_frame()
        self.assertEqual(viewer.current_frame_index, 1)
        np.testing.assert_array_equal(viewer.current_frame, self.pixels[1])
        viewer.previous_frame()
        np.testing.assert_array_equal(viewer.current_frame, self.pixels[0])
        slider = viewer.timeline_slider
        QTest.mousePress(slider, Qt.MouseButton.LeftButton,
                         pos=slider._geometry()[1].center())
        _, handle, groove = slider._geometry()
        point = groove.center()
        for index in (1, 5, 4, 0):
            point.setX(round(groove.x() + handle.width() / 2
                             + index / 7 * (groove.width() - handle.width())))
            move_slider(slider, point)
            wait_for(lambda: viewer.current_frame_index == index)
            np.testing.assert_array_equal(viewer.current_frame, self.pixels[index])
            self.assertEqual(viewer.current_native_samples.frame.pts, self.pts[index])
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=point)
        np.testing.assert_array_equal(viewer.current_frame, self.pixels[0])

    def test_ordered_playback_deadlines_loops_stalls_and_speed_changes(self):
        timeline = PlaybackTimeline.from_metadata(
            self.metadata, 8, 12.5, time_base=Fraction(1, 1000))
        self.assertTrue(timeline.preserve_order)
        self.assertEqual(timeline.times, tuple(value / 1000 for value in self.pts))
        for speed in (.5, 1, 5):
            with self.subTest(speed=speed):
                clock = PlaybackClock(timeline, 0, speed, 100)
                self.assertEqual(clock.stride, 1)
                self.assertEqual(clock.deadline(1), 100)  # No invented tie duration.
                observed = []
                for position in range(1, 17):
                    # Simulated long stalls must not select past equal-time frames.
                    target = clock.due(100 + 50 + position)
                    self.assertEqual(target.position, position)
                    self.assertEqual((target.intentional_skips, target.late_skips), (0, 0))
                    observed.append(target.index)
                    clock.presented(target)
                self.assertEqual(observed, [i % 8 for i in range(1, 17)])
                self.assertAlmostEqual(clock.deadline(8), 100 + timeline.duration / speed)
        metadata = deepcopy(self.metadata)
        metadata[-1].pop("duration_time", None)
        metadata[-1].pop("pkt_duration_time", None)
        with self.assertRaisesRegex(ValueError, "explicit final"):
            PlaybackTimeline.from_metadata(metadata, 8, 12.5, time_base=Fraction(1, 1000))

    def test_viewer_playback_preserves_tied_frame_order_and_pause_reanchor(self):
        viewer = self.viewer()
        self.assertTrue(viewer._playback_timeline.preserve_order)
        viewer.set_frame_position(0)
        with patch("osvev_app.time.perf_counter", return_value=100):
            viewer.resume_video()
        viewer.timer.stop()
        self.assertIn("no independently established", viewer.play_pause_button.toolTip())
        for index in range(1, 8):
            with patch("osvev_app.time.perf_counter", return_value=110):
                viewer._playback_tick()
            viewer.timer.stop()
            self.app.processEvents()
            self.assertEqual(viewer.current_frame_index, index)
            np.testing.assert_array_equal(viewer.current_frame, self.pixels[index])
        viewer.pause_video()
        viewer.set_frame_position(4)
        viewer.resume_video()
        self.assertEqual(viewer._playback_clock.anchor, 4)
        self.assertEqual(viewer._playback_clock.stride, 1)
        viewer.pause_video()

    def test_lossless_exports_preserve_ties_order_hashes_and_durable_source_timestamps(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            for kind, extension in (("png", ".png"), ("ffv1", ".mkv")):
                plan = build_export_plan(str(self.path), str(Path(temporary) / (kind + extension)),
                                         kind, 0, 7, {}, self.metadata, "pyav")
                report = execute_export(plan, self.metadata)
                self.assertEqual(report["status"], "verified_lossless")
                self.assertEqual(report["comparison"]["frames_compared"], 8)
                self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                self.assertTrue(report["source_unchanged"])
                self.assertEqual([r["pts"] for r in report["original_timestamps"]], self.pts)
                self.assertEqual([r["occurrence"] for r in report["original_timestamps"]],
                                 [1, 2, 1, 1, 2, 3, 1, 1])
                text = plan.log_path.read_text(encoding="utf-8")
                self.assertIn("source frames (1-based) [1, 2]", text)
                self.assertIn("source frames (1-based) [4, 5, 6]", text)
                self.assertIn("no independently established display interval", text)
                self.assertIn("Source frame 6: PTS 160; occurrence 3", text)
                if kind == "ffv1":
                    self.assertTrue(report["timing"]["verified"])
                    self.assertEqual(report["timing"]["max_timestamp_error_seconds"], 0)
                    self.assertEqual(report["timing"]["pts_tolerance_seconds"], 0)
                    indexed = FrameInfoHandler.get_frame_metadata(plan.destination)
                    self.assertEqual([entry["pts"] for entry in indexed], self.pts)
                    pixels = ffmpeg(["-i", plan.destination, "-fps_mode", "passthrough",
                                     "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
                    self.assertEqual(pixels, self.pixels.tobytes())
                else:
                    for index in range(8):
                        np.testing.assert_array_equal(cv2.imread(str(plan.output_path(index))),
                                                      self.pixels[index])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_nonzero_origin_and_selection_ending_in_ties_keep_absolute_pts(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "nonzero.mkv"
            ffmpeg(["-i", self.path, "-vf", "setpts=PTS+2000",
                    "-fps_mode", "passthrough", "-enc_time_base", "1:1000",
                    "-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0", source])
            metadata = FrameInfoHandler.get_frame_metadata(str(source))
            self.assertEqual([r["pts"] for r in metadata], [2000 + p for p in self.pts])
            for first, last in ((0, 7), (3, 5), (0, 1)):
                plan = build_export_plan(str(source), str(Path(temporary) / f"out-{first}-{last}.mkv"),
                                         "ffv1", first, last, {}, metadata, "pyav")
                report = execute_export(plan, metadata)
                self.assertEqual(report["timing"]["max_timestamp_error_seconds"], 0)
                indexed = FrameInfoHandler.get_frame_metadata(plan.destination)
                self.assertEqual([r["pts"] for r in indexed],
                                 [r["pts"] for r in metadata[first:last + 1]])
                self.assertEqual(ffmpeg(["-i", plan.destination, "-fps_mode", "passthrough",
                                         "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]),
                                 self.pixels[first:last + 1].tobytes())

    def test_export_restrictions_and_exact_timing_detect_jitter(self):
        with tempfile.TemporaryDirectory() as temporary:
            def plan(kind, first=0, last=7, **kwargs):
                extension = ".mp4" if kind == "h264" else ".mkv"
                return build_export_plan(str(self.path), str(Path(temporary) / (kind + extension)),
                                         kind, first, last, {}, self.metadata, "pyav", **kwargs)
            with self.assertRaisesRegex(ExportError, "H.264/MP4.*duplicate PTS"):
                plan("h264")
            with self.assertRaisesRegex(ExportError, "1x export speed"):
                plan("ffv1", playback_speed=2)
            self.assertFalse(plan("h264", 6, 7).preserve_source_pts)
            export = plan("ffv1")
            command = export.command()
            command[command.index("-vf") + 1] += ",setpts='if(eq(N,1),PTS+1000,PTS)'"
            ffmpeg(command[1:])
            with self.assertRaisesRegex(ExportError, "timing/duration"):
                execute_export(export, self.metadata, verify_only=True)
            self.assertFalse(export.log_path.exists())

    def test_invalid_duplicate_playback_timing_preserves_previous_loaded_video(self):
        viewer = self.viewer()
        original = viewer.cap
        pixels = viewer.current_frame.copy()
        metadata = deepcopy(self.metadata)
        metadata[-1].pop("duration_time", None)
        metadata[-1].pop("pkt_duration_time", None)
        with patch.object(FrameInfoHandler, "get_frame_metadata", return_value=metadata), \
                patch.object(QMessageBox, "warning") as warning:
            viewer._load_video(str(self.path))
        warning.assert_called_once()
        self.assertIn("explicit final frame duration", warning.call_args.args[-1])
        self.assertIs(viewer.cap, original)
        self.assertTrue(original.isOpened())
        np.testing.assert_array_equal(viewer.current_frame, pixels)


if __name__ == "__main__":
    unittest.main()
