# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Detected corruption is not a color/timing compatibility permission."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import av
import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication, QMessageBox

from export_runner import execute_export
from ffmpeg_export import ExportError, build_export_plan
from ffmpeg_tools import tool_path
from frame_info import FrameInfoHandler
from osvev_app import OSVEV
from pyav_input import InputDecodeError, PyAVInputCapture
from working_copy import create_working_copy, prepare_source


class CorruptInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.directory = tempfile.TemporaryDirectory(prefix="osvev-corrupt-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.intact = cls.root / "intact.mp4"
        subprocess.run([
            tool_path("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=160x120:rate=10:duration=1.2", "-c:v", "libx264",
            "-g", "10", "-bf", "0", "-pix_fmt", "yuv420p", "-bsf:v",
            "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
            "matrix_coefficients=1:video_full_range_flag=0", str(cls.intact),
        ], check=True, capture_output=True, timeout=60)
        cls.damaged = cls.root / "damaged.mp4"
        with av.open(str(cls.intact)) as original, av.open(str(cls.damaged), "w") as output:
            stream = original.streams.video[0]
            target = output.add_stream_from_template(stream)
            for index, packet in enumerate(original.demux(stream)):
                if packet.dts is None:
                    continue
                if index == 3:
                    data = bytearray(bytes(packet))
                    start = len(data) // 5
                    data[start:start + 24] = bytes(24)
                    damaged = av.Packet(bytes(data))
                    damaged.pts, damaged.dts = packet.pts, packet.dts
                    damaged.duration, damaged.time_base = packet.duration, packet.time_base
                    packet = damaged
                packet.stream = target
                output.mux(packet)
        with av.open(str(cls.damaged)) as source:
            source.streams.video[0].codec_context.thread_count = 1
            frames = list(source.decode(video=0))
        if len(frames) != 12 or not frames[3].is_corrupt:
            raise AssertionError("The synthetic fixture must expose a decoder-flagged corrupt fourth frame.")
        cls.metadata = FrameInfoHandler.get_frame_metadata(str(cls.damaged))

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="osvev-corrupt-output-")
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)
        self.source_hash = hashlib.sha256(self.damaged.read_bytes()).digest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.damaged.read_bytes()).digest(), self.source_hash)

    def test_decoder_refuses_corrupt_frame_without_replacing_native_samples(self):
        capture = PyAVInputCapture(str(self.damaged))
        self.addCleanup(capture.release)
        capture.read()
        capture.configure_index(self.metadata)
        capture.read()
        capture.read()
        previous = capture.native_samples
        for seek in (False, True):
            if seek:
                capture.set(cv2.CAP_PROP_POS_FRAMES, 3)
            with self.subTest(seek=seek), self.assertRaisesRegex(InputDecodeError, "corrupt.*PTS"):
                capture.read()
            self.assertIs(capture.native_samples, previous)

    def test_viewer_stops_and_keeps_previous_frame_without_offering_conversion(self):
        with patch.object(OSVEV, "_check_dependencies"), \
                patch.object(OSVEV, "set_windows_taskbar_icon"), \
                patch.object(QMessageBox, "warning") as warning:
            viewer = OSVEV()
            self.addCleanup(viewer.close)
            viewer._load_video(str(self.damaged))
            warning.assert_not_called()
            viewer.set_frame_position(2)
            previous, native = viewer.current_frame.copy(), viewer.current_native_samples
            with patch.object(viewer, "_offer_working_copy") as offer:
                viewer.set_frame_position(3)
            self.assertFalse(viewer.is_playing)
            self.assertEqual(viewer.current_frame_index, 2)
            np.testing.assert_array_equal(viewer.current_frame, previous)
            self.assertIs(viewer.current_native_samples, native)
            self.assertIn("corrupt", warning.call_args.args[2])
            offer.assert_not_called()

    def test_export_failure_records_error_but_never_a_verified_processing_log(self):
        plan = build_export_plan(str(self.damaged), str(self.output / "frame.png"),
                                 "png", 3, 3, {}, self.metadata, "pyav")
        with self.assertRaises((InputDecodeError, ExportError)):
            execute_export(plan, self.metadata, detailed_diagnostics=True)
        report = json.loads(plan.report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "failed_not_verified")
        self.assertTrue(report["error"])
        self.assertFalse(plan.log_path.exists())

    def test_external_concealed_pixels_cannot_be_verified_as_lossless(self):
        plan = build_export_plan(str(self.damaged), str(self.output / "external.png"),
                                 "png", 3, 3, {}, self.metadata, "pyav")
        # Deliberately use permissive decoding to represent an externally concealed output.
        subprocess.run([
            tool_path("ffmpeg"), "-v", "error", "-threads", "1", "-i", str(self.damaged),
            "-vf", "select=eq(n\\,3)", "-frames:v", "1", "-update", "1", plan.destination,
        ], check=True, capture_output=True, timeout=60)
        with self.assertRaisesRegex(InputDecodeError, "corrupt.*PTS"):
            execute_export(plan, self.metadata, verify_only=True, detailed_diagnostics=True)
        self.assertEqual(json.loads(plan.report_path.read_text())["status"], "failed_not_verified")
        self.assertFalse(plan.log_path.exists())

    def test_working_copy_does_not_publish_a_transcode_of_corrupt_frames(self):
        source = prepare_source(self.damaged)
        destination = self.output / "working.mkv"
        with self.assertRaises((RuntimeError, av.FFmpegError)):
            create_working_copy(source, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(Path(str(destination) + ".conversion.jsonl").exists())
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
