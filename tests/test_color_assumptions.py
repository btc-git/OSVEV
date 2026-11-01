# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Authorized missing-color interpretations remain explicit and reproducible."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import av
import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication, QMessageBox

from color_dialog import ColorAssumptionsDialog
from color_interpretation import (
    ColorChoices, MissingColorMetadata, FIELDS, resolve_reported,
    conversion_options, color_audit, native_reported,
)
from export_runner import execute_export
from ffmpeg_export import build_export_plan
from ffmpeg_tools import tool_path
from frame_info import FrameInfoHandler
from frame_processor import FrameProcessor
from osvev_app import OSVEV
from pyav_input import InputDecodeError, PyAVInputCapture, reject_hdr_side_data


def ffmpeg(args):
    return subprocess.run([tool_path("ffmpeg"), "-v", "error", *map(str, args)],
                          capture_output=True, check=True, timeout=60).stdout


class ColorPolicyTests(unittest.TestCase):
    def test_missing_fields_individually_and_together(self):
        full = dict(zip(FIELDS, ("bt709", "pc", "bt709", "bt709", "left")))
        for field in FIELDS:
            original = {k: v for k, v in full.items() if k != field}
            before = deepcopy(original)
            with self.assertRaises(MissingColorMetadata):
                resolve_reported(original, None)
            effective = resolve_reported(original, ColorChoices())
            self.assertEqual(original, before)
            for key in original:
                self.assertEqual(effective[key], original[key])
            audit = color_audit([original], ColorChoices(), {})
            self.assertNotEqual(audit["interpretation"][field]["provenance"], "reported")
        for matrix in ("bt709", "smpte170m"):
            for range_name in ("tv", "pc"):
                effective = resolve_reported({}, ColorChoices(matrix, range_name))
                self.assertEqual(effective, dict(zip(
                    FIELDS, (matrix, range_name, matrix, matrix, "left")
                )))

    def test_explicit_unsupported_fields_are_never_overridden(self):
        for field, value in (
            ("color_space", "bt2020nc"), ("color_transfer", "smpte2084"),
            ("color_transfer", "arib-std-b67"), ("color_primaries", "bt2020"),
            ("chroma_location", "top"), ("color_range", "invalid"),
        ):
            with self.subTest(field=field), self.assertRaises(InputDecodeError):
                resolve_reported({field: value}, ColorChoices())

    def test_stream_frame_conflicts_and_hdr_side_data_are_rejected(self):
        frame = Mock(colorspace=1, color_range=1, color_primaries=1, color_trc=1)
        stream = Mock(colorspace=6, color_range=1, color_primaries=1, color_trc=1)
        with self.assertRaisesRegex(ValueError, "contradict"):
            native_reported(frame, stream)
        for name in ("Mastering display metadata", "CONTENT_LIGHT_LEVEL", "DOVI_CONF",
                     "HDR Dynamic Metadata SMPTE2094-40"):
            with self.assertRaisesRegex(InputDecodeError, "HDR"):
                reject_hdr_side_data([{"side_data_type": name}])


class ColorFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory(prefix="osvev-assumed-color-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.sources = {}
        for codec in ("h264", "hevc"):
            path = cls.root / f"untagged-{codec}.mp4"
            encoder = ["-c:v", "libx264", "-crf", "0", "-g", "3", "-bf", "0"] if codec == "h264" else [
                "-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
                "pools=none:frame-threads=1:keyint=3:bframes=0",
            ]
            ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=6:duration=1",
                    *encoder, "-pix_fmt", "yuv420p", path])
            cls.sources[codec] = path
        cls.metadata = {codec: FrameInfoHandler.get_frame_metadata(str(path))
                        for codec, path in cls.sources.items()}

    def setUp(self):
        self.out = tempfile.TemporaryDirectory(prefix="osvev-assumed-out-")
        self.addCleanup(self.out.cleanup)
        self.root_out = Path(self.out.name)

    def indexed(self, codec="h264", choices=ColorChoices(), metadata=None, partial=False):
        capture = PyAVInputCapture(str(self.sources[codec]), color_choices=choices)
        self.addCleanup(capture.release)
        self.assertTrue(capture.read()[0])
        capture.configure_index(self.metadata[codec] if metadata is None else metadata, partial=partial)
        return capture

    def test_strict_inputs_require_authorization_without_modifying_source(self):
        for codec, path in self.sources.items():
            before = hashlib.sha256(path.read_bytes()).digest()
            capture = PyAVInputCapture(str(path))
            self.addCleanup(capture.release)
            with self.assertRaises(MissingColorMetadata):
                capture.read()
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_each_matrix_range_matches_independent_ffmpeg_and_native_samples(self):
        for codec, path in self.sources.items():
            before_metadata = deepcopy(self.metadata[codec])
            native_expected = []
            original_tags = []
            with av.open(str(path)) as container:
                for frame in container.decode(video=0):
                    original_tags.append((frame.colorspace, frame.color_range,
                                          frame.color_primaries, frame.color_trc))
                    native_expected.append(b"".join(
                        np.frombuffer(plane, np.uint8).reshape(plane.height, plane.line_size)[
                            :, :plane.width].tobytes() for plane in frame.planes
                    ))
            for matrix in ("smpte170m", "bt709"):
                for range_name in ("tv", "pc"):
                    with self.subTest(codec=codec, matrix=matrix, range=range_name):
                        capture = self.indexed(codec, ColorChoices(matrix, range_name))
                        expected = ffmpeg([
                            "-i", path, "-vf",
                            "scale=" + conversion_options(capture.effective_color) + ",format=bgr24",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
                        ])
                        frames = np.frombuffer(expected, np.uint8).reshape(-1, 48, 64, 3)
                        for index in (5, 0, 2, 1, 4):
                            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                            ok, actual = capture.read()
                            self.assertTrue(ok)
                            np.testing.assert_array_equal(actual, frames[index])
                            self.assertEqual(capture.native_samples.packed_bytes(), native_expected[index])
                            self.assertEqual(capture.native_samples.frame.pts,
                                             self.metadata[codec][index]["pts"])
                            native_frame = capture.native_samples.frame
                            self.assertEqual(
                                (native_frame.colorspace, native_frame.color_range,
                                 native_frame.color_primaries, native_frame.color_trc),
                                original_tags[index],
                            )
                        self.assertEqual(self.metadata[codec], before_metadata)
                        # HEVC explicitly reports limited range; Full must not replace it.
                        expected_range = self.metadata[codec][0].get("color_range", range_name)
                        self.assertEqual(capture.effective_color["color_range"], expected_range)

    def test_partial_index_and_lossless_export_reproduction_for_both_codecs(self):
        settings = {"brightness": 10, "contrast": 15, "sharpness": 20}
        for codec, path in self.sources.items():
            source_hash = hashlib.sha256(path.read_bytes()).digest()
            original = deepcopy(self.metadata[codec])
            for matrix in ("smpte170m", "bt709"):
                for range_name in ("tv", "pc"):
                    choices = ColorChoices(matrix, range_name)
                    selected, source_range = FrameInfoHandler.get_range_metadata(
                        str(path), Fraction(1, 6), Fraction(5, 6))
                    capture = self.indexed(codec, choices, selected, partial=True)
                    plan = build_export_plan(
                        str(path), str(self.root_out / f"{codec}-{matrix}-{range_name}.mkv"),
                        "ffv1", source_range.offset, source_range.offset + len(selected) - 1,
                        settings, selected, "pyav", source_range=source_range, color_choices=choices,
                    )
                    report = execute_export(plan, selected)
                    self.assertEqual(report["status"], "verified_lossless")
                    command = plan.command()
                    self.assertIn("in_h_chr_pos=0:in_v_chr_pos=128", plan.filter_graph)
                    reproduction = self.root_out / f"reproduction-{codec}-{matrix}-{range_name}.mkv"
                    ffmpeg(command[1:-1] + [str(reproduction)])
                    decoded = ffmpeg(["-i", plan.destination, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
                    reproduced = ffmpeg(["-i", reproduction, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
                    self.assertEqual(decoded, reproduced)
                    expected = []
                    for index in range(len(selected)):
                        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                        ok, frame = capture.read()
                        self.assertTrue(ok)
                        expected.append(FrameProcessor.process_frame(frame, **settings).tobytes())
                    self.assertEqual(decoded, b"".join(expected))
                    reopened = PyAVInputCapture(plan.destination)
                    try:
                        self.assertTrue(reopened.read()[0])
                        exported_metadata = FrameInfoHandler.get_frame_metadata(plan.destination)
                        reopened.configure_index(exported_metadata)
                        self.assertEqual(len(exported_metadata), len(selected))
                    finally:
                        reopened.release()
                    log = plan.log_path.read_text(encoding="utf-8")
                    for field in FIELDS:
                        self.assertIn(field, log)
                    self.assertIn("Not established from source metadata", log)
                    self.assertIn("user-selected", log)
                    self.assertIn("assumed SDR", log)
            self.assertEqual(self.metadata[codec], original)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), source_hash)

    def test_individually_missing_encoded_tags_and_explicit_full_range(self):
        for codec, path in self.sources.items():
            bsf = "h264_metadata" if codec == "h264" else "hevc_metadata"
            for missing in ("matrix_coefficients", "colour_primaries", "transfer_characteristics"):
                tags = {"matrix_coefficients": 1, "colour_primaries": 1,
                        "transfer_characteristics": 1, "video_full_range_flag": 1}
                tags[missing] = 2
                source = self.root_out / f"{codec}-{missing}.mp4"
                ffmpeg(["-i", path, "-c:v", "copy", "-bsf:v",
                        bsf + "=" + ":".join(f"{key}={value}" for key, value in tags.items()), source])
                capture = PyAVInputCapture(str(source), color_choices=ColorChoices("bt709", "tv"))
                try:
                    ok, frame = capture.read()
                    self.assertTrue(ok)
                    metadata = FrameInfoHandler.get_frame_metadata(str(source))
                    capture.configure_index(metadata)
                    self.assertEqual(capture.effective_color["color_range"], "pc")
                    self.assertEqual(capture.effective_color["color_space"], "bt709")
                    reference = ffmpeg([
                        "-i", source, "-vf", "scale=" + conversion_options(capture.effective_color)
                        + ",format=bgr24", "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "bgr24", "pipe:1",
                    ])
                    self.assertEqual(frame.tobytes(), reference)
                finally:
                    capture.release()

    def test_metadata_missing_one_field_and_rejections_remain_strict(self):
        capture = self.indexed()
        base = resolve_reported(self.metadata["h264"][0], ColorChoices())
        # Exercise missing chroma as well as tags that codecs infer at decode time.
        for field in FIELDS:
            metadata = [dict(entry, **base) for entry in self.metadata["h264"]]
            for entry in metadata:
                entry.pop(field)
            capture.configure_index(metadata)
            self.assertNotEqual(capture.color_audit["interpretation"][field]["provenance"], "reported")
        for field, value in (("pts", None), ("pts", .5), ("interlaced_frame", 1),
                             ("pix_fmt", "yuv420p10le"), ("color_transfer", "smpte2084"),
                             ("chroma_location", "center"), ("color_space", "bt709")):
            metadata = deepcopy(self.metadata["h264"])
            metadata[-1][field] = value
            with self.subTest(field=field), self.assertRaises(InputDecodeError):
                capture.configure_index(metadata)
        metadata = deepcopy(self.metadata["h264"])
        metadata[-1]["pts"] = metadata[0]["pts"]
        with self.assertRaisesRegex(InputDecodeError, "nondecreasing"):
            capture.configure_index(metadata)

    def test_png_and_compatibility_h264_keep_interpretation_in_logs(self):
        choices = ColorChoices("bt709", "pc")
        source = self.sources["h264"]
        for kind, extension in (("png", ".png"), ("h264", ".mp4")):
            plan = build_export_plan(
                str(source), str(self.root_out / (kind + extension)), kind, 1, 1,
                {}, self.metadata["h264"], "pyav", color_choices=choices,
            )
            report = execute_export(plan, self.metadata["h264"])
            expected = "verified_lossless" if kind == "png" else "verified_lossy_comparison"
            self.assertEqual(report["status"], expected)
            self.assertEqual(report["color_interpretation"]["policy"]["missing_matrix"], "bt709")
            self.assertIn("source color interpretation", plan.log_path.read_text().lower())

    def viewer(self):
        patches = [patch.object(OSVEV, "_check_dependencies"),
                   patch.object(OSVEV, "set_windows_taskbar_icon"),
                   patch.object(QMessageBox, "warning")]
        mocks = []
        for item in patches:
            mocks.append(item.start())
            self.addCleanup(item.stop)
        viewer = OSVEV()
        self.addCleanup(viewer.close)
        viewer.show()
        self.app.processEvents()
        return viewer, mocks[-1]

    def test_user_authorization_retries_full_and_partial_load_and_inspection(self):
        for interval in (None, (Fraction(1, 6), Fraction(5, 6))):
            viewer, warning = self.viewer()
            with patch.object(ColorAssumptionsDialog, "get_choices",
                              return_value=ColorChoices("bt709", "pc")) as prompt:
                viewer._load_video(str(self.sources["h264"]), interval)
            prompt.assert_called_once()
            warning.assert_not_called()
            self.assertEqual(viewer.cap.color_choices, ColorChoices("bt709", "pc"))
            self.assertIn("Not established", viewer.pixel_values_widget.item(0, 1).toolTip())
            self.assertIn("user-selected", viewer.pixel_values_widget.item(0, 1).toolTip())
            self.assertEqual(viewer.frame_metadata[0].get("color_space"), None)
            viewer.manual_next_frame()
            self.assertEqual(viewer.current_frame_index, 1)

    def test_cancel_preserves_previous_video_and_candidate_resources_close(self):
        viewer, warning = self.viewer()
        previous = Mock()
        viewer.cap = previous
        viewer.video_path = "previous.mp4"
        with patch.object(ColorAssumptionsDialog, "get_choices", return_value=None) as prompt:
            viewer._load_video(str(self.sources["h264"]))
        prompt.assert_called_once()
        self.assertIs(viewer.cap, previous)
        self.assertEqual(viewer.video_path, "previous.mp4")
        previous.release.assert_not_called()
        self.assertIsNone(viewer._metadata_worker)
        self.assertIsNone(viewer._loading_progress)
        warning.assert_not_called()

    def test_dialog_defaults_and_cancel_are_explicit(self):
        dialog = ColorAssumptionsDialog("Missing metadata")
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.matrix.currentText(), "BT.601")
        self.assertEqual(dialog.range_combo.currentText(), "Limited")
        with patch.object(ColorAssumptionsDialog, "exec", return_value=0):
            self.assertIsNone(ColorAssumptionsDialog.get_choices("Missing metadata"))

    def test_export_dialog_receives_authorized_policy(self):
        viewer, _ = self.viewer()
        choices = ColorChoices("bt709", "pc")
        with patch.object(ColorAssumptionsDialog, "get_choices", return_value=choices):
            viewer._load_video(str(self.sources["h264"]))
        with patch("export_dialog.ExportDialog") as dialog:
            viewer.open_export_dialog()
        self.assertEqual(dialog.call_args.kwargs["color_choices"], choices)

    def test_unsupported_hdr_and_10bit_inputs_do_not_offer_assumptions(self):
        viewer, warning = self.viewer()
        for name, args in (
            ("hdr", ["-c:v", "copy", "-bsf:v", "hevc_metadata=transfer_characteristics=16"]),
            ("10bit", ["-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                       "-x265-params", "pools=none:frame-threads=1"]),
        ):
            source = self.root_out / f"{name}.mp4"
            ffmpeg(["-i", self.sources["hevc"], *args, source])
            with patch.object(ColorAssumptionsDialog, "get_choices") as prompt:
                viewer._load_video(str(source))
            prompt.assert_not_called()
            self.assertIsNone(viewer.cap)
        self.assertEqual(warning.call_count, 2)


if __name__ == "__main__":
    unittest.main()
