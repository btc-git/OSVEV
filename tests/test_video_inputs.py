# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Small verified-input fixtures; container names never bypass fidelity checks."""

from fractions import Fraction
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from export_runner import execute_export
from ffmpeg_export import build_export_plan
from frame_info import FrameInfoHandler
from frame_processor import FrameProcessor
from osvev_app import OSVEV, TimeRangeDialog, VIDEO_INPUT_FILTER

AVAILABLE = importlib.util.find_spec("av") is not None and shutil.which("ffmpeg") and shutil.which("ffprobe")
if AVAILABLE:
    import av
    from pyav_input import InputDecodeError, PyAVInputCapture


def ffmpeg(arguments, data=None):
    result = subprocess.run(["ffmpeg", "-v", "error", *map(str, arguments)],
                            input=data, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


@unittest.skipUnless(AVAILABLE, "PyAV and FFmpeg/FFprobe are required")
class VideoInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="osvev-inputs-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.source = cls.root / "source.mp4"
        encoded = cls.root / "encoded.mp4"
        ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=1.2",
            "-c:v", "libx264", "-crf", "0", "-g", "2", "-bf", "0",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_range", "tv",
            "-color_primaries", "bt709", "-color_trc", "bt709", encoded,
        ])
        ffmpeg(["-i", encoded, "-c:v", "copy", "-bsf:v",
                "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
                "matrix_coefficients=1:video_full_range_flag=0", cls.source])
        cls.paths = {".mp4": cls.source}
        for suffix in (".mov", ".avi", ".mkv", ".m4v"):
            path = cls.root / ("remux" + suffix)
            ffmpeg(["-i", cls.source, "-map", "0:v:0", "-c:v", "copy", path])
            cls.paths[suffix] = path

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="osvev-input-output-")
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)

    def indexed(self, path):
        capture = PyAVInputCapture(str(path))
        self.addCleanup(capture.release)
        ok, first = capture.read()
        self.assertTrue(ok)
        metadata = FrameInfoHandler.get_frame_metadata(str(path))
        capture.configure_index(metadata)
        return capture, first, metadata

    def test_both_dialogs_share_all_container_extensions(self):
        viewer = OSVEV()
        self.addCleanup(viewer.close)
        for suffix in (".mp4", ".mov", ".avi", ".mkv", ".m4v"):
            self.assertIn("*" + suffix, VIDEO_INPUT_FILTER)
        with patch.object(QFileDialog, "getOpenFileName", return_value=("", "")) as dialog:
            viewer.open_video()
            self.assertEqual(dialog.call_args.args[3], VIDEO_INPUT_FILTER)
            viewer.open_time_range()
            self.assertEqual(dialog.call_args.args[3], VIDEO_INPUT_FILTER)

    def test_h264_containers_pixels_seeking_and_full_gui_open(self):
        baseline, first, _ = self.indexed(self.source)
        expected = [first] + [baseline.read()[1] for _ in range(5)]
        for suffix, path in self.paths.items():
            with self.subTest(container=suffix):
                if suffix == ".avi":
                    capture = PyAVInputCapture(str(path))
                    self.addCleanup(capture.release)
                    capture.read()
                    with self.assertRaisesRegex(InputDecodeError, "integer frame PTS"):
                        capture.configure_index(FrameInfoHandler.get_frame_metadata(str(path)))
                    continue
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                capture, first, metadata = self.indexed(path)
                self.assertEqual(len(metadata), 6)
                actual = [first] + [capture.read()[1] for _ in range(5)]
                for left, right in zip(actual, expected):
                    np.testing.assert_array_equal(left, right)
                self.assertEqual(capture.read(), (False, None))
                for index in (5, 0, 3, 1):
                    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                    np.testing.assert_array_equal(capture.read()[1], expected[index])
                    self.assertEqual(capture.native_samples.frame.pts, metadata[index]["pts"])
                viewer = OSVEV()
                try:
                    with patch.object(QMessageBox, "warning") as warning:
                        viewer._load_video(str(path))
                    warning.assert_not_called()
                    self.assertEqual(viewer.frame_count, 6)
                    self.assertEqual(viewer.current_frame_index, 0)
                    self.assertEqual(viewer.frame_metadata, metadata)
                finally:
                    viewer.close()
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_container_partial_indexing_retains_original_numbering_or_rejects(self):
        for suffix, path in self.paths.items():
            with self.subTest(container=suffix), av.open(str(path)) as container:
                if not container.streams.video[0].frames:
                    with self.assertRaisesRegex(ValueError, "open the entire video"):
                        FrameInfoHandler.get_range_metadata(str(path), Fraction(2, 5), Fraction(1))
                    continue
                if suffix == ".avi":
                    with self.assertRaisesRegex(ValueError, "does not match.*packets"):
                        FrameInfoHandler.get_range_metadata(str(path), Fraction(2, 5), Fraction(1))
                    continue
                metadata, selected = FrameInfoHandler.get_range_metadata(
                    str(path), Fraction(2, 5), Fraction(1))
                full, _, all_metadata = self.indexed(path)
                self.assertEqual(selected.offset, 2)
                self.assertEqual(metadata, all_metadata[2:5])
                viewer = OSVEV()
                try:
                    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")), \
                            patch.object(TimeRangeDialog, "get_range",
                                         return_value=(Fraction(2, 5), Fraction(1))), \
                            patch.object(QMessageBox, "warning") as warning:
                        viewer.open_time_range()
                    warning.assert_not_called()
                    self.assertEqual(viewer.source_range.offset, 2)
                    self.assertEqual(viewer.frame_count, 3)
                    self.assertEqual(viewer.frame_metadata, metadata)
                finally:
                    viewer.close()
                partial = PyAVInputCapture(str(path))
                self.addCleanup(partial.release)
                partial.read()
                partial.configure_index(metadata, partial=True)
                for position in (0, 2, 1):
                    partial.set(cv2.CAP_PROP_POS_FRAMES, position)
                    full.set(cv2.CAP_PROP_POS_FRAMES, selected.source_index(position))
                    np.testing.assert_array_equal(partial.read()[1], full.read()[1])
                    self.assertEqual(partial.native_samples.frame.pts, metadata[position]["pts"])

    def test_declared_frame_count_disagreement_still_rejected(self):
        from color_interpretation import InputCompatibilityError
        capture, _, metadata = self.indexed(self.source)
        with self.assertRaisesRegex(InputCompatibilityError, "frame counts disagree"):
            capture.configure_index(metadata[:-1])

    def test_h264_containers_lossless_export_and_timing(self):
        for suffix, path in self.paths.items():
            with self.subTest(container=suffix):
                if suffix == ".avi":
                    metadata = FrameInfoHandler.get_frame_metadata(str(path))
                    with self.assertRaisesRegex(InputDecodeError, "integer frame PTS"):
                        build_export_plan(str(path), str(self.output / "avi.mkv"),
                                          "ffv1", 1, 4, {}, metadata, "pyav")
                    continue
                _, _, metadata = self.indexed(path)
                plan = build_export_plan(str(path), str(self.output / (suffix[1:] + ".mkv")),
                                         "ffv1", 1, 4, {}, metadata, "pyav")
                report = execute_export(plan, metadata)
                self.assertEqual(report["status"], "verified_lossless")
                self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                self.assertTrue(report["timing"]["verified"])
                self.assertTrue(report["source_unchanged"])

    def export_rgb_fixture(self, *, odd=False):
        metadata = FrameInfoHandler.get_frame_metadata(str(self.source))
        settings = {"brightness": 12, "contrast": 15, "sharpness": 10}
        if odd:
            settings.update(crop_right=1, crop_bottom=1)
        destination = self.output / ("odd.mkv" if odd else "rgb.mkv")
        plan = build_export_plan(str(self.source), str(destination), "ffv1",
                                 0, 5, settings, metadata, "pyav")
        report = execute_export(plan, metadata)
        self.assertEqual(report["status"], "verified_lossless")
        capture, first, _ = self.indexed(self.source)
        frames = [first] + [capture.read()[1] for _ in range(5)]
        processor = FrameProcessor()
        expected = [processor.process(frame, **settings).copy() for frame in frames]
        return destination, expected

    def test_actual_osvev_ffv1_export_reopens_exactly_and_seeks(self):
        for odd in (False, True):
            with self.subTest(odd_dimensions=odd):
                path, expected = self.export_rgb_fixture(odd=odd)
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                capture, first, metadata = self.indexed(path)
                self.assertEqual(capture.native_samples.frame.format.name, "bgr0")
                np.testing.assert_array_equal(first, expected[0])
                saved = capture.native_samples
                self.assertFalse(saved.samples.flags.writeable)
                self.assertEqual(saved.packed_bytes(), expected[0].tobytes())
                self.assertEqual(saved.pixel(7, 3), tuple(map(int, expected[0][3, 7, ::-1])))
                for index in (5, 0, 2, 4, 1):
                    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                    ok, decoded = capture.read()
                    self.assertTrue(ok)
                    np.testing.assert_array_equal(decoded, expected[index])
                    self.assertEqual(capture.native_samples.frame.pts, metadata[index]["pts"])
                    self.assertEqual(capture.native_samples.packed_bytes(), expected[index].tobytes())
                    decoded[:] = 0
                    self.assertEqual(capture.native_samples.packed_bytes(), expected[index].tobytes())
                self.assertEqual(saved.packed_bytes(), expected[0].tobytes())
                with self.assertRaisesRegex(ValueError, "full-video opening"):
                    FrameInfoHandler.get_range_metadata(str(path), Fraction(0), Fraction(1))
                with self.assertRaisesRegex(InputDecodeError, "full-video opening"):
                    capture.configure_index(metadata, partial=True)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_three_generation_neutral_ffv1_exports_preserve_every_bgr_frame(self):
        settings = dict(brightness=0, contrast=0, sharpness=0,
                        crop_top=0, crop_bottom=0, crop_left=0, crop_right=0)
        source = self.source
        source_hashes = {source: hashlib.sha256(source.read_bytes()).hexdigest()}
        baseline_hashes = None
        baseline_count = None
        for generation in range(1, 4):
            with self.subTest(generation=generation):
                metadata = FrameInfoHandler.get_frame_metadata(str(source))
                destination = self.output / f"generation-{generation}.mkv"
                plan = build_export_plan(
                    str(source), str(destination), "ffv1", 0, len(metadata) - 1,
                    settings, metadata, "pyav", zoom_level=1, playback_speed=1.0,
                )
                report = execute_export(plan, metadata)
                self.assertEqual(report["status"], "verified_lossless")
                self.assertTrue(report["source_unchanged"])
                self.assertTrue(report["timing"]["verified"])
                self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)

                capture, first, reopened_metadata = self.indexed(destination)
                self.assertEqual(capture.native_samples.frame.format.name, "bgr0")
                frame_size = first.size
                decoded = ffmpeg([
                    "-threads", "1", "-i", destination, "-map", "0:v:0",
                    "-fps_mode", "passthrough", "-pix_fmt", "bgr24",
                    "-f", "rawvideo", "pipe:1",
                ])
                self.assertEqual(len(decoded) % frame_size, 0, "Incomplete BGR24 frame")
                hashes = [
                    hashlib.sha256(decoded[offset:offset + frame_size]).hexdigest()
                    for offset in range(0, len(decoded), frame_size)
                ]
                count = len(hashes)
                self.assertEqual(count, len(metadata))
                self.assertEqual(count, len(reopened_metadata))
                self.assertEqual(count, capture.get(cv2.CAP_PROP_FRAME_COUNT))
                self.assertEqual(count, report["comparison"]["frames_compared"])
                if baseline_hashes is None:
                    baseline_hashes, baseline_count = hashes, count
                    self.assertEqual(count, 6)
                self.assertEqual(count, baseline_count, "FFV1 frame count changed across generations")
                for index, expected_hash in enumerate(baseline_hashes):
                    with self.subTest(frame=index):
                        self.assertEqual(hashes[index], expected_hash,
                                         "Independent decoded BGR24 changed across generations")
                        if index == 0:
                            frame = first
                        else:
                            ok, frame = capture.read()
                            self.assertTrue(ok, "Reopened FFV1 ended before the expected frame")
                        self.assertEqual(hashlib.sha256(frame.tobytes()).hexdigest(), expected_hash,
                                         "OSVEV reopened BGR24 disagrees with independent decoding")
                self.assertEqual(capture.read(), (False, None))
                capture.release()
                for path, expected_hash in source_hashes.items():
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash,
                                     f"Source file changed during generation {generation}: {path.name}")
                source_hashes[destination] = hashlib.sha256(destination.read_bytes()).hexdigest()
                source = destination
        for path, expected_hash in source_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash,
                             f"Source/export file changed: {path.name}")

    def test_rgb_ffv1_inspector_navigation_and_reexport(self):
        path, expected = self.export_rgb_fixture()
        viewer = OSVEV()
        self.addCleanup(viewer.close)
        with patch.object(QMessageBox, "warning") as warning:
            viewer._load_video(str(path))
        warning.assert_not_called()
        self.assertEqual(viewer.frame_count, 6)
        np.testing.assert_array_equal(viewer.current_frame, expected[0])
        native = viewer.current_native_samples.packed_bytes()
        viewer.brightness_slider.setValue(20)
        viewer.crop_spins["crop_left"].setValue(3)
        viewer.crop_spins["crop_top"].setValue(1)
        tooltip = viewer.pixel_values_widget.item(0, 1).toolTip()
        self.assertIn("Native source R/G/B", tooltip)
        self.assertIn("no native Y/U/V", tooltip)
        self.assertNotIn("shared sample", tooltip)
        lens, image = viewer.main_label.lens_rect, viewer.main_label.pixmap
        x = min(60, int((lens.x() + lens.width() // 2) * 61 / image.width())) + 3
        y = min(46, int((lens.y() + lens.height() // 2) * 47 / image.height())) + 1
        r, g, b = map(int, expected[0][y, x, ::-1])
        self.assertIn(f"source ({x}, {y})", tooltip)
        self.assertIn(f"{r}, {g}, {b}", tooltip)
        self.assertEqual(viewer.pixel_values_widget.item(0, 0).text(), "Native Source RGB")
        self.assertEqual(viewer.pixel_values_widget.item(0, 1).text(), f"R {r}, G {g}, B {b}")
        self.assertEqual(viewer.current_native_samples.packed_bytes(), native)
        viewer.set_frame_position(4)
        self.assertEqual(viewer.current_frame_index, 4)
        np.testing.assert_array_equal(viewer.current_frame, expected[4])
        viewer.previous_frame()
        np.testing.assert_array_equal(viewer.current_frame, expected[3])
        metadata = FrameInfoHandler.get_frame_metadata(str(path))
        for kind, suffix in (("png", ".png"), ("ffv1", ".mkv"), ("h264", ".mp4")):
            with self.subTest(output=kind):
                plan = build_export_plan(str(path), str(self.output / ("reexport" + suffix)),
                                         kind, 1, 4, {"contrast": 20}, metadata, "pyav")
                self.assertNotIn("in_color_matrix=rgb", plan.filter_graph)
                report = execute_export(plan, metadata, detailed_diagnostics=True)
                self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                self.assertIn("packed row-major", report["native_hash_format"])
                self.assertIn("native_rgb_sha256", plan.records_path.read_text())
                self.assertNotIn("native_yuv_sha256", plan.records_path.read_text())
                if kind != "h264":
                    self.assertEqual(report["status"], "verified_lossless")
                    self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                if kind != "png":
                    self.assertTrue(report["timing"]["verified"])

    def test_unified_open_entire_uses_full_index_for_ffv1_and_undeclared_mkv(self):
        rgb_path, rgb_frames = self.export_rgb_fixture()
        for path in (rgb_path, self.paths[".mkv"]):
            with self.subTest(path=path):
                viewer = OSVEV()
                try:
                    with patch.object(QFileDialog, "getOpenFileName",
                                      return_value=(str(path), "")), \
                            patch.object(TimeRangeDialog, "get_open_choice",
                                         return_value=(True, None)), \
                            patch.object(FrameInfoHandler, "get_range_metadata") as partial, \
                            patch.object(QMessageBox, "warning") as warning:
                        viewer.open_button.click()
                    warning.assert_not_called()
                    partial.assert_not_called()
                    self.assertIsNone(viewer.source_range)
                    self.assertEqual(viewer.frame_count, 6)
                    if path == rgb_path:
                        np.testing.assert_array_equal(viewer.current_frame, rgb_frames[0])
                finally:
                    viewer.close()

    def test_rgb_packing_aliases_preserve_samples_without_color_conversion(self):
        path, expected = self.export_rgb_fixture()
        for pixel_format in ("bgr0", "rgb0", "bgr24", "rgb24"):
            with self.subTest(pixel_format=pixel_format):
                capture = PyAVInputCapture(str(path))
                self.addCleanup(capture.release)
                frame = av.VideoFrame.from_ndarray(expected[0], format="bgr24").reformat(
                    format=pixel_format)
                frame.colorspace, frame.color_range = 0, 2
                frame.color_primaries, frame.color_trc = 1, 1
                frame.pts, frame.time_base = 0, capture._time_base
                capture._iterator = iter([frame])
                np.testing.assert_array_equal(capture.read()[1], expected[0])
                self.assertEqual(capture.native_samples.packed_bytes(), expected[0].tobytes())

    def hevc_fixture(self, matrix="bt709", full=False):
        code = 1 if matrix == "bt709" else 6
        path = self.output / f"hevc-{matrix}-{full}.mp4"
        encoded = self.output / f"encoded-{matrix}-{full}.mp4"
        ffmpeg(["-i", self.source, "-c:v", "libx265", "-preset", "ultrafast",
                "-x265-params", "lossless=1:pools=none:frame-threads=1:keyint=2:bframes=0",
                "-pix_fmt", "yuv420p", encoded])
        ffmpeg(["-i", encoded, "-c:v", "copy", "-bsf:v",
                f"hevc_metadata=colour_primaries={code}:transfer_characteristics={code}:"
                f"matrix_coefficients={code}:video_full_range_flag={int(full)}:"
                "chroma_sample_loc_type=0", path])
        return path

    def test_hevc_color_controls_native_samples_and_independent_bgr(self):
        source, first, _ = self.indexed(self.source)
        native_expected = [source.native_samples.packed_bytes()]
        for _ in range(5):
            source.read()
            native_expected.append(source.native_samples.packed_bytes())
        for matrix in ("bt709", "bt601"):
            for full in (False, True):
                with self.subTest(matrix=matrix, full=full):
                    path = self.hevc_fixture(matrix, full)
                    capture, first, metadata = self.indexed(path)
                    actual = [first]
                    self.assertEqual(capture.native_samples.packed_bytes(), native_expected[0])
                    for index in range(1, 6):
                        actual.append(capture.read()[1])
                        self.assertEqual(capture.native_samples.packed_bytes(), native_expected[index])
                    range_name = "full" if full else "limited"
                    reference = ffmpeg(["-threads", "1", "-i", path, "-fps_mode", "passthrough",
                                        "-vf", f"scale=in_color_matrix={matrix}:out_color_matrix={matrix}:"
                                        f"in_range={range_name}:out_range=full:flags=bicubic",
                                        "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"])
                    self.assertEqual(b"".join(frame.tobytes() for frame in actual), reference)
                    for index in (5, 0, 2, 4, 1):
                        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                        np.testing.assert_array_equal(capture.read()[1], actual[index])
                        self.assertEqual(capture.native_samples.frame.pts, metadata[index]["pts"])
                        self.assertEqual(capture.native_samples.packed_bytes(), native_expected[index])

    def test_hevc_containers_full_partial_and_lossless_exports(self):
        source = self.hevc_fixture()
        for suffix in (".mp4", ".mov", ".mkv", ".m4v"):
            with self.subTest(container=suffix):
                path = self.output / ("hevc" + suffix)
                muxer = ["-f", "mp4"] if suffix == ".m4v" else []
                ffmpeg(["-i", source, "-c:v", "copy", *muxer, path])
                capture, first, metadata = self.indexed(path)
                viewer = OSVEV()
                try:
                    with patch.object(QMessageBox, "warning") as warning:
                        viewer._load_video(str(path))
                    warning.assert_not_called()
                    self.assertEqual(viewer.frame_count, 6)
                    np.testing.assert_array_equal(viewer.current_frame, first)
                    self.assertIn("Native source Y/U/V",
                                  viewer.pixel_values_widget.item(0, 1).toolTip())
                    viewer.set_frame_position(4)
                    self.assertEqual(viewer.current_native_samples.frame.pts, metadata[4]["pts"])
                finally:
                    viewer.close()
                with av.open(str(path)) as container:
                    declared_count = container.streams.video[0].frames
                if declared_count:
                    selected, source_range = FrameInfoHandler.get_range_metadata(
                        str(path), Fraction(2, 5), Fraction(1))
                    self.assertEqual(source_range.offset, 2)
                    self.assertEqual(selected, metadata[2:5])
                    partial = PyAVInputCapture(str(path))
                    self.addCleanup(partial.release)
                    partial.read()
                    partial.configure_index(selected, partial=True)
                    partial.set(cv2.CAP_PROP_POS_FRAMES, 1)
                    capture.set(cv2.CAP_PROP_POS_FRAMES, 3)
                    np.testing.assert_array_equal(partial.read()[1], capture.read()[1])
                else:
                    with self.assertRaisesRegex(ValueError, "open the entire video"):
                        FrameInfoHandler.get_range_metadata(str(path), Fraction(2, 5), Fraction(1))
                plan = build_export_plan(str(path), str(self.output / ("hevc-" + suffix[1:] + ".mkv")),
                                         "ffv1", 1, 4, {"contrast": 10}, metadata, "pyav")
                report = execute_export(plan, metadata)
                self.assertEqual(report["status"], "verified_lossless")
                self.assertTrue(report["timing"]["verified"])

    def test_rotation_and_display_matrices_rejected_without_replacing_video(self):
        viewer = OSVEV()
        self.addCleanup(viewer.close)
        with patch.object(QMessageBox, "warning") as warning:
            viewer._load_video(str(self.source))
        warning.assert_not_called()
        original_capture = viewer.cap
        original_frame = viewer.current_frame.copy()
        for name, transform in (
            ("rotated.mp4", ["-display_rotation:v:0", "90"]),
            ("rotated.mov", ["-display_rotation:v:0", "90"]),
            ("flipped.mov", ["-display_hflip:v:0"]),
        ):
            rotated = self.output / name
            ffmpeg([*transform, "-i", self.source, "-c:v", "copy", rotated])
            with self.subTest(input=name):
                with self.assertRaisesRegex(ValueError, "rotation/display matrix"):
                    FrameInfoHandler.get_frame_metadata(str(rotated))
                capture = PyAVInputCapture(str(rotated))
                self.addCleanup(capture.release)
                with self.assertRaisesRegex(InputDecodeError, "display matrix"):
                    capture.read()
                with patch.object(QMessageBox, "warning") as warning:
                    viewer._load_video(str(rotated))
                warning.assert_called_once()
                self.assertIn("display matrix", warning.call_args.args[2].lower())
                self.assertIs(viewer.cap, original_capture)
                np.testing.assert_array_equal(viewer.current_frame, original_frame)
        for record in (
            {"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 0}]},
            {"tags": {"rotate": "90"}}, {"rotation": 180},
        ):
            with self.assertRaisesRegex(ValueError, "display matrix"):
                FrameInfoHandler.validate_display_transforms(record)
        capture, _, metadata = self.indexed(self.source)
        altered = [dict(frame) for frame in metadata]
        altered[1]["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": 0}]
        with self.assertRaisesRegex(ValueError, "display matrix"):
            capture.configure_index(altered)

    def test_unsupported_codec_and_real_pixel_formats_rejected(self):
        cases = (
            ("mpeg4.avi", ["-c:v", "mpeg4"], "Unsupported input codec"),
            ("yuv-ffv1.mkv", ["-c:v", "ffv1", "-pix_fmt", "yuv420p"],
             "Unsupported or ambiguous"),
            ("alpha-ffv1.mkv", ["-c:v", "ffv1", "-pix_fmt", "bgra"],
             "Unsupported or ambiguous"),
            ("untagged-rgb.mkv", ["-c:v", "ffv1", "-pix_fmt", "bgr0"],
             "Unsupported or ambiguous"),
            ("hevc10.mp4", ["-c:v", "libx265", "-preset", "ultrafast",
                           "-x265-params", "pools=none:frame-threads=1",
                           "-pix_fmt", "yuv420p10le", "-colorspace", "bt709",
                           "-color_primaries", "bt709", "-color_trc", "bt709",
                           "-color_range", "tv"], "Unsupported or ambiguous"),
        )
        for name, encoding, message in cases:
            with self.subTest(input=name):
                path = self.output / name
                ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=0.4",
                        *encoding, path])
                with self.assertRaisesRegex(InputDecodeError, message):
                    capture = PyAVInputCapture(str(path))
                    self.addCleanup(capture.release)
                    capture.read()

    def test_hevc_hdr_and_ambiguous_color_metadata_rejected(self):
        source = self.hevc_fixture()
        for field, value in (("colour_primaries", 9), ("transfer_characteristics", 16),
                             ("matrix_coefficients", 9), ("colour_primaries", 2)):
            with self.subTest(field=field, value=value):
                path = self.output / f"{field}-{value}.mp4"
                ffmpeg(["-i", source, "-c:v", "copy", "-bsf:v",
                        f"hevc_metadata={field}={value}", path])
                capture = PyAVInputCapture(str(path))
                self.addCleanup(capture.release)
                with self.assertRaisesRegex(InputDecodeError, "Unsupported or ambiguous"):
                    capture.read()

    def test_invalid_index_metadata_and_interlace_remain_rejected(self):
        for path in (self.source, self.hevc_fixture(), self.export_rgb_fixture()[0]):
            capture, _, metadata = self.indexed(path)
            for field, value in (
                ("pts", None), ("pts", True), ("pts", 0.5), ("width", 65),
                ("pix_fmt", "yuv420p10le"), ("color_primaries", "unknown"),
                ("color_transfer", "smpte2084"), ("color_space", "unknown"),
                ("color_range", "unknown"), ("interlaced_frame", 1),
            ):
                with self.subTest(input=path.name, field=field, value=value):
                    altered = [dict(frame) for frame in metadata]
                    altered[-1][field] = value
                    with self.assertRaises(InputDecodeError):
                        capture.configure_index(altered)
            duplicate = [dict(frame) for frame in metadata]
            duplicate[1]["pts"] = duplicate[0]["pts"] - 1
            with self.assertRaisesRegex(InputDecodeError, "nondecreasing"):
                capture.configure_index(duplicate)
            shifted = [dict(frame, pts=frame["pts"] + 1) for frame in metadata]
            with self.assertRaisesRegex(InputDecodeError, "first presentation PTS"):
                capture.configure_index(shifted)
            frame = SimpleNamespace(side_data=[], pts=0, time_base=capture._time_base,
                                    interlaced_frame=True, is_corrupt=False)
            with self.assertRaisesRegex(InputDecodeError, "Progressive"):
                capture._validate_frame(frame)
            frame.interlaced_frame = False
            frame.time_base = capture._time_base * 2
            with self.assertRaisesRegex(InputDecodeError, "stream time base"):
                capture._validate_frame(frame)

    def test_hevc_b_frames_and_irregular_nonzero_pts_keep_source_identity(self):
        encoded, source = self.output / "reordered.mp4", self.output / "vfr-hevc.mp4"
        ffmpeg(["-i", self.source, "-vf", "setpts=PTS+2/TB+if(gte(N\\,3)\\,1/TB\\,0)",
                "-fps_mode", "passthrough", "-c:v", "libx265", "-preset", "ultrafast",
                "-x265-params", "lossless=1:pools=none:frame-threads=1:keyint=6:bframes=2",
                "-pix_fmt", "yuv420p", encoded])
        ffmpeg(["-copyts", "-i", encoded, "-c:v", "copy", "-bsf:v",
                "hevc_metadata=colour_primaries=1:transfer_characteristics=1:"
                "matrix_coefficients=1:video_full_range_flag=0:chroma_sample_loc_type=0", source])
        capture, first, metadata = self.indexed(source)
        self.assertGreater(metadata[0]["pts"], 0)
        self.assertIn("B", [frame["pict_type"] for frame in metadata])
        self.assertGreater(len({b["pts"] - a["pts"] for a, b in zip(metadata, metadata[1:])}), 1)
        baseline, initial, _ = self.indexed(self.source)
        expected = [initial] + [baseline.read()[1] for _ in range(5)]
        np.testing.assert_array_equal(first, expected[0])
        for index in (5, 0, 3, 1, 4, 2):
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            np.testing.assert_array_equal(capture.read()[1], expected[index])
            self.assertEqual(capture.native_samples.frame.pts, metadata[index]["pts"])
        plan = build_export_plan(str(source), str(self.output / "vfr-export.mkv"), "ffv1",
                                 1, 5, {}, metadata, "pyav", playback_speed=2)
        report = execute_export(plan, metadata)
        self.assertEqual(report["status"], "verified_lossless")
        self.assertTrue(report["timing"]["verified"])
