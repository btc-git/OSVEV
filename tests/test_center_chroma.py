# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Declared chroma siting must affect reconstruction, not just validation."""

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

import av
import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication, QMessageBox

from color_interpretation import ColorChoices, InputDecodeError
from export_runner import execute_export
from ffmpeg_export import build_export_plan
from frame_info import FrameInfoHandler
from frame_processor import FrameProcessor
from osvev_app import OSVEV
from pyav_input import PyAVInputCapture
from scrub_preview import PreviewSource, ScrubPreview
from test_scrub_preview import wait_for


def ffmpeg(args):
    return subprocess.run(["ffmpeg", "-v", "error", *map(str, args)],
                          capture_output=True, check=True, timeout=60).stdout


def reference_filter(matrix, full, center, *, horizontal=None):
    m = "bt709" if matrix == "bt709" else "bt601"
    r = "full" if full else "limited"
    if center:
        x = 128 if horizontal is None else horizontal
        return (
            f"scale=in_color_matrix={m}:out_color_matrix={m}:"
            f"in_range={r}:out_range={r}:flags=bicubic:"
            f"in_h_chr_pos={x}:in_v_chr_pos=128:out_h_chr_pos=0:out_v_chr_pos=0,"
            "format=yuv444p,"
            f"scale=in_color_matrix={m}:out_color_matrix={m}:"
            f"in_range={r}:out_range=full:flags=bicubic,format=bgr24"
        )
    return (f"scale=in_color_matrix={m}:out_color_matrix={m}:"
            f"in_range={r}:out_range=full:flags=bicubic,format=bgr24")


class CenterChromaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory(prefix="osvev-center-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.fixtures = {}
        cls.untagged = {}
        for codec in ("h264", "hevc"):
            for location, matrix, full in (("left", "bt709", False),
                                            ("center", "bt709", False),
                                            ("center", "smpte170m", True)):
                path = cls.root / f"{codec}-{location}-{matrix}.mp4"
                tag = 1 if matrix == "bt709" else 6
                encoder = (["-c:v", "libx264", "-crf", "0", "-g", "3", "-bf", "2"]
                           if codec == "h264" else
                           ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
                            "pools=none:frame-threads=1:keyint=3:bframes=2:open-gop=0"])
                ffmpeg([
                    "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=6:duration=1",
                    *encoder, "-pix_fmt", "yuv420p",
                    "-bsf:v", f"{codec}_metadata=colour_primaries={tag}:"
                    f"transfer_characteristics={tag}:matrix_coefficients={tag}:"
                    f"video_full_range_flag={int(full)}:"
                    f"chroma_sample_loc_type={int(location == 'center')}", path,
                ])
                metadata = FrameInfoHandler.get_frame_metadata(str(path))
                if {entry["chroma_location"] for entry in metadata} != {location}:
                    raise AssertionError(f"Fixture did not declare {location}")
                cls.fixtures[codec, location, matrix] = (path, metadata, full)
            path = cls.root / f"untagged-center-{codec}.mp4"
            ffmpeg([
                "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=6:duration=1",
                *encoder, "-pix_fmt", "yuv420p", "-bsf:v",
                f"{codec}_metadata=chroma_sample_loc_type=1", path,
            ])
            cls.untagged[codec] = (path, FrameInfoHandler.get_frame_metadata(str(path)))

    def indexed(self, path, metadata, choices=None, partial=False):
        capture = PyAVInputCapture(str(path), color_choices=choices)
        self.addCleanup(capture.release)
        self.assertTrue(capture.read()[0])
        capture.configure_index(metadata, partial=partial)
        return capture

    def test_exact_pixels_seeking_and_native_samples_for_both_chroma_locations(self):
        for (codec, location, matrix), (path, metadata, full) in self.fixtures.items():
            with self.subTest(codec=codec, location=location, matrix=matrix):
                before = hashlib.sha256(path.read_bytes()).digest()
                original = deepcopy(metadata)
                expected = ffmpeg([
                    "-i", path, "-vf", reference_filter(matrix, full, location == "center"),
                    "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
                ])
                frames = np.frombuffer(expected, np.uint8).reshape(-1, 64, 96, 3)
                if location == "center":
                    wrong = ffmpeg([
                        "-i", path, "-vf", reference_filter(matrix, full, True, horizontal=0),
                        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
                    ])
                    self.assertNotEqual(expected, wrong, "Fixture must expose chroma phase errors")
                native = []
                with av.open(str(path)) as source:
                    for frame in source.decode(video=0):
                        native.append(b"".join(
                            np.frombuffer(plane, np.uint8).reshape(plane.height, plane.line_size)[
                                :, :plane.width].tobytes() for plane in frame.planes))
                capture = self.indexed(path, metadata)
                for index in (5, 0, 3, 1, 4, 2):
                    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                    ok, pixels = capture.read()
                    self.assertTrue(ok)
                    np.testing.assert_array_equal(pixels, frames[index])
                    self.assertEqual(capture.native_samples.packed_bytes(), native[index])
                    self.assertEqual(capture.native_samples.frame.pts, metadata[index]["pts"])
                    self.assertEqual(capture.native_samples.chroma_location, location)
                self.assertEqual(metadata, original)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_lossless_exports_independent_command_and_chroma_logs(self):
        settings = dict(brightness=10, contrast=15, sharpness=20, crop_left=2)
        output = tempfile.TemporaryDirectory(prefix="osvev-center-export-")
        self.addCleanup(output.cleanup)
        for key, (path, metadata, full) in self.fixtures.items():
            codec, location, matrix = key
            with self.subTest(key=key):
                destination = Path(output.name) / f"{path.stem}.mkv"
                plan = build_export_plan(str(path), str(destination), "ffv1", 0, 5,
                                         settings, metadata, "pyav")
                report = execute_export(plan, metadata)
                self.assertEqual(report["status"], "verified_lossless")
                expected = ffmpeg([
                    "-i", path, "-vf", reference_filter(matrix, full, location == "center"),
                    "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
                ])
                frames = np.frombuffer(expected, np.uint8).reshape(-1, 64, 96, 3)
                processor = FrameProcessor()
                try:
                    expected = b"".join(processor.process(f, **settings).tobytes() for f in frames)
                finally:
                    processor.close()
                self.assertEqual(ffmpeg(["-i", destination, "-f", "rawvideo",
                                         "-pix_fmt", "bgr24", "pipe:1"]), expected)
                reproduction = destination.with_stem(destination.stem + "-reproduced")
                ffmpeg(plan.command()[1:-1] + [str(reproduction)])
                self.assertEqual(ffmpeg(["-i", reproduction, "-f", "rawvideo",
                                         "-pix_fmt", "bgr24", "pipe:1"]), expected)
                self.assertIn(f"Source chroma location (reported): {location}",
                              plan.log_path.read_text(encoding="utf-8"))
                if location == "center":
                    self.assertIn("in_h_chr_pos=128:in_v_chr_pos=128", plan.filter_graph)
                    self.assertIn("format=pix_fmts=yuv444p", plan.filter_graph)
                    self.assertEqual(plan.color_audit["interpretation"]["chroma_location"],
                                     {"effective": "center", "provenance": "reported"})
                else:
                    self.assertNotIn("yuv444p", plan.filter_graph.split("format=bgr24")[0])

    def test_gui_first_frame_and_partial_range_inspection_are_center_correct(self):
        self.enterContext(patch.object(OSVEV, "_check_dependencies"))
        self.enterContext(patch.object(OSVEV, "set_windows_taskbar_icon"))
        warning = self.enterContext(patch.object(QMessageBox, "warning"))
        for codec in ("h264", "hevc"):
            path, metadata, _ = self.fixtures[codec, "center", "bt709"]
            expected = np.frombuffer(ffmpeg([
                "-i", path, "-vf", reference_filter("bt709", False, True),
                "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
            ]), np.uint8).reshape(-1, 64, 96, 3)
            viewer = OSVEV()
            self.addCleanup(viewer.close)
            viewer.show()
            for interval, source_index in ((None, 0), ((Fraction(1, 3), Fraction(5, 6)), 2)):
                viewer._load_video(str(path), interval)
                self.app.processEvents()
                np.testing.assert_array_equal(viewer.current_frame, expected[source_index])
                self.assertEqual(viewer.current_native_samples.frame.pts,
                                 metadata[source_index]["pts"])
                viewer.update_pixel_inspector()
                self.assertIn("chroma location: center",
                              viewer.pixel_values_widget.item(0, 1).toolTip())
        warning.assert_not_called()

    def test_preview_and_missing_color_policy_preserve_reported_center(self):
        for codec in ("h264", "hevc"):
            path, metadata = self.untagged[codec]
            selected = metadata[2:5]
            choices = ColorChoices("smpte170m", "pc")
            self.assertEqual({entry["chroma_location"] for entry in selected}, {"center"})
            self.assertTrue(any(entry.get("color_transfer") in (None, "unknown")
                                for entry in selected))
            capture = self.indexed(path, selected, choices, partial=True)
            self.assertEqual(capture.effective_color["chroma_location"], "center")
            capture.set(cv2.CAP_PROP_POS_FRAMES, 1)
            expected = capture.read()[1]
            preview = ScrubPreview()
            results, errors = [], []
            preview.ready.connect(results.append)
            preview.failed.connect(errors.append)
            try:
                preview.begin(PreviewSource(str(path), "pyav", selected, True, choices))
                preview.request(1, {})
                wait_for(lambda: bool(results) or bool(errors))
                self.assertEqual(errors, [])
                np.testing.assert_array_equal(results[0].frame, expected)
                np.testing.assert_array_equal(results[0].processed, expected)
                self.assertEqual(results[0].native.chroma_location, "center")
                self.assertEqual(results[0].native.frame.pts, selected[1]["pts"])
            finally:
                preview.cancel()
                wait_for(lambda: preview._task is None)

    def test_partial_and_png_h264_exports_use_same_center_conversion(self):
        output = tempfile.TemporaryDirectory(prefix="osvev-center-formats-")
        self.addCleanup(output.cleanup)
        for codec in ("h264", "hevc"):
            path, metadata, _ = self.fixtures[codec, "center", "bt709"]
            selected, source_range = FrameInfoHandler.get_range_metadata(
                str(path), Fraction(1, 3), Fraction(5, 6))
            for kind, suffix in (("ffv1", ".mkv"), ("png", ".png"), ("h264", ".mp4")):
                with self.subTest(codec=codec, kind=kind):
                    first = source_range.offset
                    last = first if kind == "png" else first + len(selected) - 1
                    plan = build_export_plan(
                        str(path), str(Path(output.name) / (codec + suffix)), kind, first, last,
                        {}, selected, "pyav", source_range=source_range,
                    )
                    report = execute_export(plan, selected)
                    self.assertEqual(report["status"], "verified_lossy_comparison"
                                     if kind == "h264" else "verified_lossless")
                    self.assertEqual(report["source_chroma_location"], "center")

    def test_unsupported_and_changing_siting_remain_rejected(self):
        path, metadata, _ = self.fixtures["h264", "center", "bt709"]
        for location in ("top", "topleft", "bottom", "bottomleft", "unknown", None, "left"):
            altered = deepcopy(metadata)
            altered[-1]["chroma_location"] = location
            capture = PyAVInputCapture(str(path))
            self.addCleanup(capture.release)
            capture.read()
            with self.subTest(location=location), self.assertRaises(InputDecodeError):
                capture.configure_index(altered)


if __name__ == "__main__":
    unittest.main()
