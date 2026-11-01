# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import importlib.util
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from export_dialog import ExportDialog
from export_runner import FFmpegProcess, execute_export as run_export
from ffmpeg_export import (
    ExportCancelled, ExportError, build_export_plan, powershell_command,
)
from frame_info import FrameInfoHandler
from ffmpeg_tools import tool_path
from frame_processor import FrameProcessor
from processing_filters import PIPELINE_ID, enhancement_filter
from osvev_app import OSVEV
from sample_media import requires_samples

AVAILABLE = importlib.util.find_spec("av") is not None and shutil.which("ffmpeg") and shutil.which("ffprobe")

def execute_export(*args, **kwargs):
    # Existing per-frame audit regressions explicitly retain diagnostic records.
    return run_export(*args, detailed_diagnostics=True, **kwargs)


def run_ffmpeg(arguments, data=None):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", *map(str, arguments)], input=data,
        capture_output=True, timeout=60,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


@unittest.skipUnless(AVAILABLE, "Optional PyAV and FFmpeg/FFprobe are required")
class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_temp = tempfile.TemporaryDirectory(prefix="osvev-export-tests-")
        cls.addClassCleanup(cls.root_temp.cleanup)
        root = Path(cls.root_temp.name)
        cls.source = root / "source with spaces 'quoted'.mp4"
        encoded = root / "encoded.mp4"
        frames = np.random.default_rng(101).integers(0, 256, (5, 48, 64, 3), dtype=np.uint8)
        run_ffmpeg([
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "64x48", "-r", "5", "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_range", "tv",
            "-color_primaries", "bt709", "-color_trc", "bt709", encoded,
        ], frames.tobytes())
        run_ffmpeg([
            "-i", encoded, "-c:v", "copy", "-bsf:v",
            "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
            "matrix_coefficients=1:video_full_range_flag=0", cls.source,
        ])
        cls.metadata = FrameInfoHandler.get_frame_metadata(str(cls.source))
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="osvev-export-out-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def plan(self, kind="ffv1", first=0, last=4, contrast=20, *, name="output", **kwargs):
        extension = {"ffv1": ".mkv", "png": ".png", "h264": ".mp4"}[kind]
        return build_export_plan(
            str(self.source), str(self.root / (name + extension)), kind, first, last,
            {"contrast": contrast}, self.metadata, "pyav", **kwargs,
        )

    def wait_worker(self, dialog):
        deadline = time.monotonic() + 20
        while dialog.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertIsNone(dialog.worker, "Export worker did not finish.")

    def records(self, plan):
        return [json.loads(line) for line in plan.records_path.read_text().splitlines()]

    def test_default_exports_write_only_media_and_readable_verified_log(self):
        for kind, first, last in (("ffv1", 1, 3), ("h264", 1, 3), ("png", 2, 2), ("png", 0, 4)):
            with self.subTest(kind=kind, count=last-first+1):
                plan = self.plan(kind, first, last, name=f"default-{kind}-{first}",
                                 playback_speed=2)
                report = run_export(plan, self.metadata)
                self.assertFalse(plan.report_path.exists())
                self.assertFalse(plan.records_path.exists())
                self.assertIsNone(report["frame_records"])
                self.assertTrue(report["source_unchanged"])
                self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                self.assertIsNotNone(datetime.fromisoformat(report["created_at"]).utcoffset())
                text = plan.log_path.read_text(encoding="utf-8")
                for value in (
                    report["created_at"], plan.source, report["source_sha256_before"],
                    report["powershell_command"], "\n".join(report["ffmpeg_version"].splitlines()).rstrip(),
                    report["status"], report["processing_pipeline"],
                    f"OSVEV version: {report['osvev_version']} (commit {report['osvev_commit']})",
                    f"Frames (1-based, inclusive): {first + 1}-{last + 1}",
                ):
                    self.assertIn(value, text)
                for omitted in (report["reference_hash_command"], report["output_hash_command"],
                                "Processing library versions", "filter_mismatch_frames"):
                    self.assertNotIn(omitted, text)
                self.assertEqual(report["ffmpeg_executable"], tool_path("ffmpeg"))
                for output in report["outputs"]:
                    self.assertIn(output["path"], text)
                    self.assertIn(output["sha256"], text)
                    self.assertEqual(output["sha256"], hashlib.sha256(Path(output["path"]).read_bytes()).hexdigest())
                if kind == "png":
                    self.assertIn("Export speed: not applicable", text)
                else:
                    self.assertIn("Export speed: 2x", text)
                    self.assertTrue(report["timing"]["verified"])
                    self.assertIn("Playback timing: verified", text)
                if kind == "h264":
                    self.assertIn("LOSSY", text)
                else:
                    self.assertIn("Result: VERIFIED lossless", text)
                expected = {plan.log_path}
                expected.update(plan.output_path(i) for i in range(plan.count) if plan.sequence)
                if not plan.sequence:
                    expected.add(Path(plan.destination))
                self.assertEqual(set(self.root.glob(f"default-{kind}-{first}*")), expected)

    def test_default_failure_cancellation_and_mismatch_never_create_log(self):
        plan = self.plan(name="failure")
        command = plan.command()
        command[command.index("-c:v") + 1] = "not_an_encoder"
        with patch.object(type(plan), "command", return_value=command):
            with self.assertRaisesRegex(ExportError, "FFmpeg failed"):
                run_export(plan, self.metadata)
        cancelled = self.plan(name="cancelled")
        flag = threading.Event()
        def progress(percent, message):
            if 45 < percent < 90:
                flag.set()
        with self.assertRaises(ExportCancelled):
            run_export(cancelled, self.metadata, flag.is_set, progress)
        mismatch = self.plan(name="mismatch-default", contrast=20)
        run_ffmpeg(self.plan(name="mismatch-default", contrast=0).command()[1:])
        with self.assertRaisesRegex(ExportError, "does not preserve"):
            run_export(mismatch, self.metadata, verify_only=True)
        for failed in (plan, cancelled, mismatch):
            self.assertFalse(failed.log_path.exists())
            self.assertFalse(failed.report_path.exists())
            self.assertFalse(failed.records_path.exists())

    def test_existing_log_and_late_log_collision_are_never_overwritten(self):
        plan = self.plan(name="existing-log")
        plan.log_path.write_text("keep existing log")
        with self.assertRaisesRegex(ExportError, "never overwritten"):
            run_export(plan, self.metadata)
        self.assertEqual(plan.log_path.read_text(), "keep existing log")
        self.assertFalse(Path(plan.destination).exists())
        late = self.plan(name="late-log")
        def progress(percent, message):
            if percent == 90:
                late.log_path.write_text("concurrent log")
        with self.assertRaises(FileExistsError):
            run_export(late, self.metadata, progress=progress)
        self.assertEqual(late.log_path.read_text(), "concurrent log")

    def test_log_contains_combined_settings_crop_and_external_verification(self):
        plan = build_export_plan(
            str(self.source), str(self.root / "cropped.mkv"), "ffv1", 1, 3,
            {"brightness": 20, "contrast": 32, "sharpness": 30,
             "crop_top": 1, "crop_bottom": 3, "crop_left": 5, "crop_right": 7},
            self.metadata, "pyav", playback_speed=0.5,
        )
        run_ffmpeg(plan.command()[1:])
        report = run_export(plan, self.metadata, verify_only=True)
        text = plan.log_path.read_text(encoding="utf-8")
        self.assertFalse(report["encoding_command_executed_by_osvev"])
        self.assertIn("Command executed by OSVEV: False", text)
        self.assertIn("Not independently known", text)
        self.assertIn("Export speed: 0.5x", text)
        self.assertIn("Output dimensions: [52, 44]", text)
        for key, value in plan.settings.items():
            self.assertIn(f"{key}: {value}", text)
        self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
        self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
        self.assertIn("Result: VERIFIED lossless - all 3 output frames are pixel-identical", text)

    def test_incomplete_log_write_is_removed_and_error_surfaces(self):
        plan = self.plan()
        original_open = Path.open
        class BrokenLog:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def write(self, text):
                self.stream.write(text[:30])
                raise OSError("simulated log write failure")
        def open_path(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return BrokenLog(stream) if path == plan.log_path else stream
        with patch.object(Path, "open", open_path):
            with self.assertRaisesRegex(OSError, "simulated log write failure"):
                run_export(plan, self.metadata)
        self.assertFalse(plan.log_path.exists())
        self.assertTrue(Path(plan.destination).exists())

    def test_all_201_contrast_settings_match_ffmpeg_eq(self):
        ramp = np.tile(np.arange(256, dtype=np.uint8)[None, :, None], (16, 1, 3))
        for contrast in range(-100, 101):
            with self.subTest(contrast=contrast):
                actual = run_ffmpeg([
                    "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "256x16", "-i", "pipe:0",
                    "-vf", enhancement_filter(contrast=contrast), "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
                ], ramp.tobytes())
                expected = FrameProcessor.process_frame(ramp, contrast=contrast).tobytes()
                self.assertEqual(actual, expected)

    def test_png_current_range_and_whole_preserve_intended_pixels(self):
        for first, last, contrast in ((2, 2, 0), (1, 3, 32), (0, 4, -20)):
            with self.subTest(first=first, last=last):
                plan = self.plan("png", first, last, contrast, name=f"png-{first}-{last}")
                report = execute_export(plan, self.metadata)
                self.assertEqual(report["status"], "verified_lossless")
                self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                records = self.records(plan)
                self.assertEqual([row["source_index"] for row in records], list(range(first, last + 1)))
                self.assertEqual(len(report["outputs"]), last - first + 1)
                for row in records:
                    self.assertEqual(row["intended_bgr_sha256"], row["ffmpeg_filter_bgr_sha256"])
                    self.assertEqual(row["intended_bgr_sha256"], row["output_bgr_sha256"])
                    self.assertIsNone(row["output_pts"])
                for index in range(plan.count):
                    self.assertTrue(plan.output_path(index).is_file())

    def test_ffv1_current_range_whole_and_audit(self):
        for first, last in ((2, 2), (1, 3), (0, 4)):
            with self.subTest(first=first, last=last):
                plan = self.plan(first=first, last=last, name=f"ffv1-{first}-{last}")
                progress = []
                report = execute_export(plan, self.metadata, progress=lambda n, text: progress.append(n))
                self.assertEqual(report["status"], "verified_lossless")
                self.assertEqual(report["comparison"]["frames_compared"], plan.count)
                self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                self.assertEqual(report["source_sha256_before"], report["source_sha256_after"])
                self.assertTrue(report["source_unchanged"])
                self.assertTrue(report["encoding_command_executed_by_osvev"])
                self.assertIn("ffmpeg version", report["ffmpeg_version"])
                self.assertEqual(report["command_arguments"], plan.command())
                self.assertEqual(json.loads(plan.report_path.read_text())["status"], "verified_lossless")
                self.assertTrue(plan.log_path.exists())
                self.assertEqual(progress[-1], 100)
                for row in self.records(plan):
                    self.assertIsNotNone(row["output_pts"])

    def test_h264_is_explicitly_lossy_and_records_pixel_differences(self):
        plan = self.plan("h264", 1, 3)
        report = execute_export(plan, self.metadata)
        self.assertEqual(report["status"], "verified_lossy_comparison")
        self.assertIn("LOSSY", report["encoding"])
        self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
        self.assertGreater(report["comparison"]["output_mismatch_frames"], 0)
        self.assertGreater(report["comparison"]["output_max_channel_difference"], 0)
        self.assertFalse(plan.lossless)

    def test_full_range_bt601_source_and_frame_selection(self):
        full = self.root / "full.mp4"
        run_ffmpeg([
            "-i", self.source, "-map", "0:v:0", "-c:v", "copy", "-bsf:v",
            "h264_metadata=colour_primaries=6:transfer_characteristics=6:"
            "matrix_coefficients=6:video_full_range_flag=1", full,
        ])
        metadata = FrameInfoHandler.get_frame_metadata(str(full))
        plan = build_export_plan(
            str(full), str(self.root / "full.mkv"), "ffv1", 1, 4,
            {"contrast": 32}, metadata, "pyav",
        )
        report = execute_export(plan, metadata)
        self.assertEqual(report["status"], "verified_lossless")
        self.assertEqual((report["matrix"], report["source_range"]), ("bt601", "full"))

    def test_unsupported_enhancements_and_combinations_block_generation(self):
        for settings in (
            {"compression": 1},
            {"brightness": 20, "contrast": 20, "sharpness": 30, "compression": 1},
            {"compression": 20, "contrast": 20},
        ):
            with self.subTest(settings=settings), self.assertRaisesRegex(ExportError, "teaching simulation"):
                build_export_plan(str(self.source), str(self.root / "no.mkv"),
                                  "ffv1", 0, 4, settings, self.metadata, "pyav")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_core_enhancements_export_png_ffv1_and_lossy_h264(self):
        settings_list = (
            {"brightness": 20}, {"sharpness": 30},
            {"brightness": 20, "contrast": 32, "sharpness": 30},
            {"brightness": -100, "contrast": -100, "sharpness": 100},
            {"brightness": 100, "contrast": 100, "sharpness": 100},
        )
        for kind in ("png", "ffv1", "h264"):
            for index, settings in enumerate(settings_list):
                with self.subTest(kind=kind, settings=settings):
                    extension = {"png": ".png", "ffv1": ".mkv", "h264": ".mp4"}[kind]
                    plan = build_export_plan(
                        str(self.source), str(self.root / f"core-{kind}-{index}{extension}"),
                        kind, 0, 4, settings, self.metadata, "pyav",
                    )
                    report = execute_export(plan, self.metadata)
                    self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                    self.assertEqual(report["processing_pipeline"], PIPELINE_ID)
                    if kind != "h264":
                        self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                        self.assertEqual(report["status"], "verified_lossless")

    def test_core_combinations_all_source_matrices_and_ranges(self):
        for matrix, color in (("bt709", 1), ("bt601", 6)):
            for full in (0, 1):
                with self.subTest(matrix=matrix, full=full):
                    source = self.root / f"tag-{color}-{full}.mp4"
                    run_ffmpeg([
                        "-i", self.source, "-c:v", "copy", "-bsf:v",
                        f"h264_metadata=colour_primaries={color}:transfer_characteristics={color}:"
                        f"matrix_coefficients={color}:video_full_range_flag={full}", source,
                    ])
                    metadata = FrameInfoHandler.get_frame_metadata(str(source))
                    plan = build_export_plan(
                        str(source), str(self.root / f"tag-{color}-{full}.mkv"),
                        "ffv1", 0, 4, {"brightness": -20, "contrast": 32, "sharpness": 100,
                                      "crop_top": 1, "crop_left": 1},
                        metadata, "pyav",
                    )
                    report = execute_export(plan, metadata)
                    self.assertEqual(report["status"], "verified_lossless")
                    self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)

    def test_live_combined_pixels_after_stepping_and_scrubbing_match_export(self):
        settings = {"brightness": 20, "contrast": 32, "sharpness": 30}
        plan = build_export_plan(
            str(self.source), str(self.root / "navigation.mkv"), "ffv1", 0, 4,
            settings, self.metadata, "pyav",
        )
        execute_export(plan, self.metadata)
        expected = [row["output_bgr_sha256"] for row in self.records(plan)]
        messages = []
        with patch.object(OSVEV, "_check_dependencies"), \
                patch.object(OSVEV, "set_windows_taskbar_icon"), \
                patch.object(QMessageBox, "warning", side_effect=lambda *args: messages.append(args[1:])):
            viewer = OSVEV(decoder_backend="pyav")
            self.addCleanup(viewer.close)
            viewer.show()
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.source), "")):
                viewer.open_video()
            viewer.brightness_slider.setValue(20)
            viewer.contrast_slider.setValue(32)
            viewer.sharpness_slider.setValue(30)
            for index in (0, 4, 2, 1, 3):
                viewer.set_frame_position(index)
                self.app.processEvents()
                self.assertEqual(viewer.current_frame_index, index)
                self.assertEqual(
                    hashlib.sha256(viewer._get_processed_frame().tobytes()).hexdigest(),
                    expected[index],
                )
            viewer.previous_frame()
            self.assertEqual(viewer.current_frame_index, 2)
            self.assertEqual(hashlib.sha256(viewer._get_processed_frame().tobytes()).hexdigest(), expected[2])
            viewer.manual_next_frame()
            self.assertEqual(viewer.current_frame_index, 3)
            self.assertEqual(hashlib.sha256(viewer._get_processed_frame().tobytes()).hexdigest(), expected[3])
        self.assertEqual(messages, [])

    def test_invalid_backend_selection_metadata_settings_and_extension(self):
        for first, last in ((-1, 2), (2, 1), (0, 5)):
            with self.assertRaises(ExportError):
                self.plan(first=first, last=last)
        for settings in ({"contrast": 101}, {"contrast": 1.5}, {"contrast": True}):
            with self.assertRaises(ExportError):
                build_export_plan(str(self.source), str(self.root / "no.mkv"),
                                  "ffv1", 0, 4, settings, self.metadata, "pyav")
        with self.assertRaisesRegex(ExportError, "PyAV"):
            build_export_plan(str(self.source), str(self.root / "no.mkv"),
                              "ffv1", 0, 4, {}, self.metadata, "opencv")
        with self.assertRaisesRegex(ExportError, "extension"):
            build_export_plan(str(self.source), str(self.root / "no.mp4"),
                              "ffv1", 0, 4, {}, self.metadata, "pyav")
        metadata = [dict(row) for row in self.metadata]
        metadata[1]["color_space"] = "unknown"
        with self.assertRaises(RuntimeError):
            build_export_plan(str(self.source), str(self.root / "no.mkv"),
                              "ffv1", 0, 4, {}, metadata, "pyav")

    def test_cropped_enhancements_pixels_and_video_timing_all_speeds(self):
        settings = {"brightness": 20, "contrast": 32, "sharpness": 30,
                    "crop_top": 3, "crop_bottom": 5, "crop_left": 7, "crop_right": 9}
        for kind in ("png", "ffv1", "h264"):
            for speed in (0.5, 1, 2, 5):
                for first, last in ((0, 4), (1, 3), (4, 4)):
                    with self.subTest(kind=kind, speed=speed, selection=(first, last)):
                        extension = {"png": ".png", "ffv1": ".mkv", "h264": ".mp4"}[kind]
                        path = self.root / f"crop-{kind}-{speed}-{first}{extension}"
                        plan = build_export_plan(
                            str(self.source), str(path), kind, first, last,
                            settings, self.metadata, "pyav", playback_speed=speed,
                        )
                        self.assertEqual((plan.width, plan.height), (48, 40))
                        self.assertEqual((plan.source_width, plan.source_height), (64, 48))
                        self.assertIn("crop=w=48:h=40:x=7:y=3:exact=1", plan.filter_graph)
                        report = execute_export(plan, self.metadata)
                        self.assertEqual(report["comparison"]["frames_compared"], last - first + 1)
                        self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                        if kind != "h264":
                            self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                        if kind == "png":
                            self.assertEqual(plan.playback_speed, 1)
                            self.assertNotIn("timing", report)
                        else:
                            self.assertEqual(report["export_playback_speed"], speed)
                            self.assertTrue(report["timing"]["verified"])
                            self.assertEqual(report["timing"]["timestamp_mismatch_frames"], 0)
                            self.assertAlmostEqual(
                                report["timing"]["expected_duration_seconds"],
                                (last - first + 1) / 5 / speed, places=6,
                            )
                            self.assertLessEqual(
                                report["timing"]["duration_error_seconds"],
                                report["timing"]["duration_tolerance_seconds"],
                            )

    def test_odd_and_single_pixel_crops_preserved_not_normalized(self):
        for settings, size in (
            ({"crop_left": 1, "crop_top": 1}, (63, 47)),
            ({"crop_left": 63, "crop_top": 47}, (1, 1)),
        ):
            for kind in ("png", "ffv1"):
                with self.subTest(kind=kind, size=size):
                    ext = ".png" if kind == "png" else ".mkv"
                    plan = build_export_plan(
                        str(self.source), str(self.root / f"odd-{size[0]}-{kind}{ext}"),
                        kind, 1, 3, settings, self.metadata, "pyav",
                    )
                    self.assertEqual((plan.width, plan.height), size)
                    report = execute_export(plan, self.metadata)
                    self.assertEqual(report["status"], "verified_lossless")
                    self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
            with self.assertRaisesRegex(ExportError, "No pixels"):
                build_export_plan(str(self.source), str(self.root / "odd.mp4"), "h264",
                                  0, 4, settings, self.metadata, "pyav")

    def test_invalid_crop_and_speed_and_missing_final_duration_block_plan(self):
        for settings in ({"crop_left": -1}, {"crop_top": True}, {"crop_bottom": 1.5},
                         {"crop_top": 48}, {"crop_left": 32, "crop_right": 32}):
            with self.subTest(settings=settings), self.assertRaises(ExportError):
                build_export_plan(str(self.source), str(self.root / "invalid.mkv"), "ffv1",
                                  0, 4, settings, self.metadata, "pyav")
        for speed in (0, -1, 100, True, "2", float("nan"), float("inf")):
            with self.subTest(speed=speed), self.assertRaises(ExportError):
                self.plan(playback_speed=speed)
        missing = [dict(frame) for frame in self.metadata]
        missing[-1].pop("duration_time", None)
        missing[-1].pop("pkt_duration_time", None)
        with self.assertRaisesRegex(ExportError, "duration"):
            build_export_plan(str(self.source), str(self.root / "duration.mkv"), "ffv1",
                              0, 4, {}, missing, "pyav")

    def test_wrong_external_speed_fails_even_when_lossless_pixels_match(self):
        good = self.plan(playback_speed=2)
        wrong = self.plan(playback_speed=1)
        run_ffmpeg(wrong.command()[1:])
        with self.assertRaisesRegex(ExportError, "timing/duration"):
            execute_export(good, self.metadata, verify_only=True)
        report = json.loads(good.report_path.read_text())
        self.assertFalse(good.log_path.exists())
        self.assertEqual(report["status"], "failed_not_verified")
        self.assertFalse(report["timing"]["verified"])
        self.assertGreater(report["timing"]["timestamp_mismatch_frames"], 0)

    def test_external_coarse_time_base_cannot_inflate_verification_tolerance(self):
        correct = self.plan("h264", 0, 2, playback_speed=2)
        wrong = self.plan("h264", 0, 2, playback_speed=1)
        command = wrong.command()
        command[-1:-1] = ["-video_track_timescale", "5"]
        run_ffmpeg(command[1:])
        with self.assertRaisesRegex(ExportError, "too coarse"):
            execute_export(correct, self.metadata, verify_only=True)
        self.assertEqual(json.loads(correct.report_path.read_text())["status"], "failed_not_verified")
        self.assertFalse(correct.log_path.exists())

    def test_variable_timestamps_nonzero_origin_and_scaled_tail(self):
        source = self.root / "vfr.mp4"
        encoded = self.root / "vfr-encoded.mp4"
        run_ffmpeg([
            "-i", self.source, "-an", "-filter_threads", "1", "-vf",
            "settb=1/1000000,setpts=PTS+2/TB+if(gte(N\\,2)\\,0.3/TB\\,0)",
            "-enc_time_base", "1:1000000", "-fps_mode", "passthrough",
            "-c:v", "libx264", "-crf", "0", encoded,
        ])
        run_ffmpeg(["-copyts", "-i", encoded, "-c:v", "copy", "-bsf:v",
                    "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
                    "matrix_coefficients=1:video_full_range_flag=0", source])
        metadata = FrameInfoHandler.get_frame_metadata(str(source))
        self.assertGreater(float(metadata[0]["best_effort_timestamp_time"]), 0)
        for kind, extension in (("ffv1", ".mkv"), ("h264", ".mp4")):
            for speed in (0.5, 1, 2, 5):
                with self.subTest(kind=kind, speed=speed):
                    plan = build_export_plan(
                        str(source), str(self.root / f"vfr-{kind}-{speed}{extension}"), kind, 0, 4,
                        {"brightness": 20, "contrast": 32, "sharpness": 30, "crop_left": 2},
                        metadata, "pyav", playback_speed=speed,
                    )
                    report = execute_export(plan, metadata)
                    self.assertTrue(report["timing"]["verified"])
                    self.assertEqual(report["comparison"]["frames_compared"], 5)
                    records = self.records(plan)
                    self.assertAlmostEqual(records[0]["output_time_seconds"], 0, places=6)
                    self.assertAlmostEqual(
                        records[2]["expected_output_time_seconds"]
                        - records[1]["expected_output_time_seconds"], 0.5 / speed, places=6,
                    )

    def test_speed_dialog_defaults_selected_speed_and_png_bypass(self):
        dialog = ExportDialog(str(self.source), 2, self.metadata,
                              {"crop_left": 2}, "pyav", playback_speed=5)
        self.addCleanup(dialog.close)
        dialog.format_combo.setCurrentIndex(1)
        dialog.path_edit.setText(str(self.root / "speed-gui.mkv"))
        self.assertEqual(dialog.plan.playback_speed, 5)
        self.assertFalse(hasattr(dialog, "speed_combo"))
        self.assertIn("expected duration", dialog.status_label.text())
        dialog.format_combo.setCurrentIndex(0)
        dialog.path_edit.setText(str(self.root / "speed-gui.png"))
        self.assertEqual(dialog.plan.playback_speed, 1)
        self.assertNotIn("setts", powershell_command(dialog.plan.command()))
        self.assertIn("PNG: no playback timing", dialog.status_label.text())

    def test_all_other_offered_speeds_and_cropped_hash_commands(self):
        for kind, extension in (("ffv1", ".mkv"), ("h264", ".mp4")):
            for speed in (0.05, 0.1, 0.25, 0.75, 3, 4):
                with self.subTest(kind=kind, speed=speed):
                    plan = build_export_plan(
                        str(self.source), str(self.root / f"offered-{kind}-{speed}{extension}"),
                        kind, 1, 3, {"crop_left": 2}, self.metadata, "pyav", playback_speed=speed,
                    )
                    report = execute_export(plan, self.metadata)
                    self.assertTrue(report["timing"]["verified"])
        plan = self.plan(playback_speed=5, name="speed-hashes")
        execute_export(plan, self.metadata)
        rows = self.records(plan)
        for command, key in ((plan.reference_command(hashes=True), "ffmpeg_filter_bgr_sha256"),
                             (plan.output_decode_command(hashes=True), "output_bgr_sha256")):
            text = run_ffmpeg(command[1:]).decode()
            hashes = [line.split(",")[-1].strip() for line in text.splitlines() if line and not line.startswith("#")]
            self.assertEqual(hashes, [row[key] for row in rows])

    def test_inspection_options_never_enter_processing_or_export_command(self):
        settings = {"brightness": 20, "contrast": 32, "sharpness": 30,
                    "crop_top": 2, "crop_left": 2}
        destination = str(self.root / "display-only.mkv")
        plain = build_export_plan(str(self.source), destination, "ffv1", 0, 4,
                                  settings, self.metadata, "pyav", playback_speed=2)
        display = build_export_plan(
            str(self.source), destination, "ffv1", 0, 4,
            {**settings, "additional_zoom": 50,
             "highlight": True, "overlays": True},
            self.metadata, "pyav", playback_speed=2,
        )
        self.assertEqual(display.command(), plain.command())
        self.assertEqual((display.width, display.height), (62, 46))

    def test_scaled_combined_exports_all_interpolations_formats_and_speeds(self):
        settings = {"brightness": 20, "contrast": 32, "sharpness": 30,
                    "crop_top": 1, "crop_bottom": 2, "crop_left": 3, "crop_right": 4}
        for interpolation in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
            for kind, extension in (("png", ".png"), ("ffv1", ".mkv"), ("h264", ".mp4")):
                for speed in ((1,) if kind == "png" else (0.5, 1, 2, 5)):
                    with self.subTest(interpolation=interpolation, kind=kind, speed=speed):
                        plan = build_export_plan(
                            str(self.source), str(self.root / f"scaled-{interpolation}-{kind}-{speed}{extension}"),
                            kind, 1, 3, settings, self.metadata, "pyav",
                            playback_speed=speed, zoom_level=2, interpolation=interpolation,
                        )
                        self.assertEqual((plan.width, plan.height), (114, 90))
                        self.assertLess(plan.filter_graph.index("crop=w="), plan.filter_graph.index("scale=w="))
                        report = execute_export(plan, self.metadata)
                        self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                        self.assertEqual(report["comparison"]["frames_compared"], 3)
                        if kind != "h264":
                            self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                        if kind != "png":
                            self.assertTrue(report["timing"]["verified"])
                        text = plan.log_path.read_text()
                        self.assertIn(f"Export interpolation: {interpolation}", text)
                        self.assertIn("not claimed pixel-identical to either viewer", text)

    def test_scaling_neutral_identity_invalid_settings_and_large_zoom_not_capped(self):
        from export_scaling import scaling_filter
        frame = np.random.default_rng(300).integers(0, 256, (9, 13, 3), dtype=np.uint8)
        processor = FrameProcessor()
        for method in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
            self.assertIs(processor.scale_for_export(frame, 1, method), frame)
            self.assertEqual(scaling_filter(13, 9, 1, method), "")
        for zoom in (0, -1, True, 1.5, float("nan")):
            with self.subTest(zoom=zoom), self.assertRaises(ExportError):
                self.plan(zoom_level=zoom)
        with self.assertRaises(ExportError):
            self.plan(interpolation="unknown")
        plan = self.plan(zoom_level=100)
        self.assertEqual((plan.width, plan.height), (6400, 4800))
        # 1x keeps the previous command exactly; interpolation introduces no resampling.
        self.assertEqual(self.plan(interpolation="Lanczos").command(), self.plan().command())

    def test_large_resolution_warns_before_starting_export_without_zoom_cap(self):
        dialog = ExportDialog(str(self.source), 2, self.metadata, {}, "pyav", zoom_level=200)
        self.addCleanup(dialog.close)
        dialog.path_edit.setText(str(self.root / "large.png"))
        self.assertIsNotNone(dialog.plan)
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.No) as warning, \
                patch("export_dialog.ExportWorker") as worker:
            dialog._execute()
            warning.assert_called_once()
            self.assertIn("12800x9600", warning.call_args.args[2])
            worker.assert_not_called()
        self.assertFalse(dialog.plan.log_path.exists())
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes), \
                patch("export_dialog.ExportWorker") as worker:
            dialog._execute()
            worker.return_value.start.assert_called_once()
        dialog.worker = None

    def test_dialog_snapshots_zoom_interpolation_speed_and_ignores_jpeg(self):
        settings = {"brightness": 20, "compression": 80, "crop_top": 1, "crop_left": 1}
        dialog = ExportDialog(str(self.source), 2, self.metadata, settings, "pyav",
                              playback_speed=2, zoom_level=3, interpolation="Lanczos")
        self.addCleanup(dialog.close)
        dialog.format_combo.setCurrentIndex(1)
        dialog.path_edit.setText(str(self.root / "snapshot.mkv"))
        self.assertEqual(settings["compression"], 80)
        self.assertEqual(dialog.plan.settings["compression"], 0)
        self.assertEqual((dialog.plan.zoom_level, dialog.plan.interpolation, dialog.plan.playback_speed),
                         (3, "Lanczos", 2))
        self.assertEqual((dialog.plan.width, dialog.plan.height), (189, 141))

    def test_main_viewer_passes_selected_export_controls_without_changing_displays(self):
        with patch.object(OSVEV, "_check_dependencies"), \
                patch.object(OSVEV, "set_windows_taskbar_icon"):
            viewer = OSVEV()
        self.addCleanup(viewer.close)
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.source), "")):
            viewer.open_video()
        viewer.zoom_dropdown.setCurrentText("3x")
        viewer.interpolation_dropdown.setCurrentText("Bicubic")
        viewer.compression_slider.setValue(40)
        viewer.nn_zoom_slider.setValue(4)
        viewer.speed_multiplier = 2
        before = viewer._get_processed_frame().copy()
        with patch("export_dialog.ExportDialog") as dialog:
            viewer.open_export_dialog()
            self.assertEqual(dialog.call_args.kwargs,
                             {"playback_speed": 2, "zoom_level": 3, "interpolation": "Bicubic",
                              "source_range": None, "color_choices": None})
            self.assertEqual(dialog.call_args.args[3]["compression"], 40)
            dialog.return_value.exec.assert_called_once()
        np.testing.assert_array_equal(viewer._get_processed_frame(), before)
        self.assertEqual(viewer.nn_zoom_slider.value(), 4)
        self.assertEqual(viewer.compression_slider.value(), 40)

    def test_scaling_graph_reused_and_nearest_preserves_replicated_pixels(self):
        frame = np.random.default_rng(34).integers(0, 256, (11, 17, 3), dtype=np.uint8)
        processor = FrameProcessor()
        expected = np.repeat(np.repeat(frame, 3, axis=0), 3, axis=1)
        for _ in range(3):
            processed = processor.process(frame)
            actual = processor.scale_for_export(processed, 3, "Nearest")
            np.testing.assert_array_equal(actual, expected)
            graph = processor._scaling_graph
            processor.process(frame)
            self.assertIs(processor._scaling_graph, graph)
        processor.close()
        self.assertIsNone(processor._scaling_graph)

    @requires_samples()
    def test_real_source_cropped_timing_all_speeds(self):
        root = Path(__file__).resolve().parent.parent
        for filename in ("short-clip.mp4", "gas-station.mp4"):
            metadata = FrameInfoHandler.get_frame_metadata(str(root / filename))
            for kind, extension in (("ffv1", ".mkv"), ("h264", ".mp4")):
                for speed in (0.5, 1, 2, 5):
                    with self.subTest(source=filename, kind=kind, speed=speed):
                        plan = build_export_plan(
                            str(root / filename),
                            str(self.root / f"{filename}-{kind}-{speed}{extension}"),
                            kind, 17, 24,
                            {"brightness": 20, "contrast": 32, "sharpness": 30,
                             "crop_top": 13, "crop_bottom": 15, "crop_left": 21, "crop_right": 23},
                            metadata, "pyav", playback_speed=speed,
                        )
                        report = execute_export(plan, metadata)
                        self.assertTrue(report["timing"]["verified"])
                        self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                        self.assertEqual(report["comparison"]["frames_compared"], 8)
                        if kind == "ffv1":
                            self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)

    def test_original_and_existing_outputs_and_sidecars_are_never_overwritten(self):
        original = self.source.read_bytes()
        with self.assertRaises(ExportError):
            build_export_plan(str(self.source), str(self.source), "h264", 0, 4,
                              {}, self.metadata, "pyav")
        self.assertEqual(self.source.read_bytes(), original)
        plan = self.plan()
        Path(plan.destination).write_bytes(b"keep existing")
        with self.assertRaisesRegex(ExportError, "never overwritten"):
            execute_export(plan, self.metadata)
        self.assertEqual(Path(plan.destination).read_bytes(), b"keep existing")
        plan = self.plan(name="audit-existing")
        plan.report_path.write_text("keep existing audit")
        with self.assertRaises(ExportError):
            execute_export(plan, self.metadata)
        self.assertEqual(plan.report_path.read_text(), "keep existing audit")
        plan = self.plan("png", 0, 4, name="sequence-existing")
        plan.output_path(4).write_bytes(b"keep sequence")
        with self.assertRaisesRegex(ExportError, "prefix already exists"):
            execute_export(plan, self.metadata)

    def test_copied_command_external_execution_then_verification(self):
        plan = self.plan(first=1, last=3, contrast=32, name="external 'copy'")
        result = subprocess.run(plan.command(), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(plan.report_path.exists())
        existing = self.plan(first=1, last=3, contrast=32, name="external 'copy'",
                             allow_existing_output=True)
        report = execute_export(existing, self.metadata, verify_only=True)
        self.assertEqual(report["status"], "verified_lossless")
        self.assertFalse(report["encoding_command_executed_by_osvev"])
        self.assertIn("Not independently known", report["external_output_provenance"])
        self.assertTrue(report["source_unchanged"])

    @unittest.skipUnless(os.name == "nt", "PowerShell-ready command is a Windows interface")
    def test_generated_powershell_command_executes_literal_quoted_paths(self):
        plan = self.plan(first=1, last=3, name="powershell 'literal'")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell_command(plan.command())],
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        report = execute_export(plan, self.metadata, verify_only=True)
        self.assertEqual(report["status"], "verified_lossless")

    def test_independent_framehash_commands_match_audit_fields(self):
        plan = self.plan(first=1, last=3)
        execute_export(plan, self.metadata)
        records = self.records(plan)
        for command, key in (
            (plan.reference_command(hashes=True), "ffmpeg_filter_bgr_sha256"),
            (plan.output_decode_command(hashes=True), "output_bgr_sha256"),
        ):
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            hashes = [
                line.rsplit(",", 1)[1].strip()
                for line in result.stdout.decode().splitlines()
                if line and not line.startswith("#")
            ]
            self.assertEqual(hashes, [row[key] for row in records])

    def test_mismatching_external_output_is_failed_not_lossless_success(self):
        plan = self.plan(first=1, last=3, contrast=20, name="mismatch")
        command = self.plan(first=1, last=3, contrast=0, name="mismatch").command()
        result = subprocess.run(command, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaisesRegex(ExportError, "does not preserve"):
            execute_export(plan, self.metadata, verify_only=True)
        report = json.loads(plan.report_path.read_text())
        self.assertEqual(report["status"], "failed_not_verified")
        self.assertFalse(plan.log_path.exists())
        self.assertGreater(report["comparison"]["output_mismatch_frames"], 0)

    def test_command_quoting_and_explicit_pixel_hash_domain(self):
        plan = self.plan()
        text = powershell_command(plan.command())
        self.assertIn("''quoted''", text)
        self.assertTrue(text.startswith("& 'ffmpeg' "))
        self.assertIn("trim=start_frame=0:end_frame=5", text)
        self.assertIn("-noautorotate", text)
        for command in (plan.reference_command(hashes=True), plan.output_decode_command(hashes=True)):
            self.assertIn("bgr24", command)
            self.assertIn("sha256", command)
            self.assertIn("framehash", command)

    def test_cancellation_during_verification_retains_unverified_audit(self):
        plan = self.plan()
        flag = threading.Event()
        def progress(percent, message):
            if 45 < percent < 90:
                flag.set()
        with self.assertRaises(ExportCancelled):
            execute_export(plan, self.metadata, flag.is_set, progress)
        self.assertTrue(Path(plan.destination).exists())
        self.assertEqual(json.loads(plan.report_path.read_text())["status"], "cancelled_not_verified")
        self.assertFalse(plan.log_path.exists())
        self.assertGreater(len(self.records(plan)), 0)
        self.assertLess(len(self.records(plan)), plan.count)

    def test_watchdog_cancels_blocked_process_read(self):
        event = threading.Event()
        timer = threading.Timer(0.15, event.set)
        timer.start()
        self.addCleanup(timer.join)
        start = time.monotonic()
        with FFmpegProcess([sys.executable, "-c", "import time;time.sleep(30)"], event.is_set) as process:
            with self.assertRaises(ExportCancelled):
                process.read_exact(1)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIsNotNone(process.process.poll())

    def test_encode_failure_is_audited_with_ffmpeg_error(self):
        plan = self.plan()
        command = plan.command()
        command[command.index("-c:v") + 1] = "not_an_encoder"
        with patch.object(type(plan), "command", return_value=command):
            with self.assertRaisesRegex(ExportError, "FFmpeg failed"):
                execute_export(plan, self.metadata)
        report = json.loads(plan.report_path.read_text())
        self.assertEqual(report["status"], "failed_not_verified")
        self.assertIn("not_an_encoder", report["error"])

    def test_encoding_cancellation_is_audited_without_claiming_success(self):
        plan = self.plan()
        event = threading.Event()
        def progress(percent, message):
            if percent == 5:
                event.set()
        with self.assertRaises(ExportCancelled):
            execute_export(plan, self.metadata, event.is_set, progress)
        self.assertEqual(json.loads(plan.report_path.read_text())["status"], "cancelled_not_verified")

    def test_dialog_build_copy_execute_and_verification_worker(self):
        dialog = ExportDialog(str(self.source), 2, self.metadata, {"contrast": 32}, "pyav")
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.path_edit.setText(str(self.root / "gui.png"))
        self.assertIsNotNone(dialog.plan)
        self.assertEqual((dialog.plan.first, dialog.plan.last), (2, 2))
        dialog._copy()
        self.assertEqual(QApplication.clipboard().text(), dialog.command_text.toPlainText())
        messages = []
        with patch.object(QMessageBox, "warning", side_effect=lambda *args: messages.append(args[1:])):
            dialog._execute()
            self.assertIsNotNone(dialog.worker)
            self.assertFalse(dialog.path_edit.isEnabled())
            self.assertFalse(dialog.diagnostics_check.isEnabled())
            self.wait_worker(dialog)
        self.assertEqual(messages, [])
        self.assertIn("VERIFIED lossless", dialog.status_label.text())
        self.assertFalse(dialog.diagnostics_check.isChecked())
        self.assertTrue(dialog.diagnostics_check.isEnabled())
        self.assertIn("optional", dialog.diagnostics_check.text())
        self.assertIn("every export", dialog.diagnostics_note.text())
        self.assertIn("Troubleshooting", dialog.diagnostics_note.text())
        self.assertTrue(Path(str(self.root / "gui.png") + ".txt").exists())
        self.assertFalse(Path(str(self.root / "gui.png") + ".osvev.json").exists())
        self.assertEqual(dialog.progress_bar.value(), 100)
        self.assertTrue(dialog.cancel_button.isEnabled())
        self.assertTrue((self.root / "gui.png").is_file())

    def test_dialog_ffv1_playback_guidance_only_for_ffv1(self):
        dialog = ExportDialog(str(self.source), 0, self.metadata, {}, "pyav")
        self.addCleanup(dialog.close)
        dialog.show()
        self.assertFalse(dialog.ffv1_playback_note.isVisible())
        dialog.format_combo.setCurrentIndex(1)
        self.assertTrue(dialog.ffv1_playback_note.isVisible())
        self.assertIn("lossless", dialog.ffv1_playback_note.text())
        self.assertIn("some video players may not support it", dialog.ffv1_playback_note.text())
        self.assertIn('href="https://www.videolan.org/vlc/"', dialog.ffv1_playback_note.text())
        self.assertTrue(dialog.ffv1_playback_note.openExternalLinks())
        dialog.path_edit.setText(str(self.root / "guidance.mkv"))
        self.assertIsNotNone(dialog.plan)
        self.assertEqual(dialog.plan.kind, "ffv1")
        self.assertTrue(dialog.ffv1_playback_note.isVisible())
        dialog.format_combo.setCurrentIndex(2)
        self.assertFalse(dialog.ffv1_playback_note.isVisible())
        dialog.format_combo.setCurrentIndex(0)
        self.assertFalse(dialog.ffv1_playback_note.isVisible())

    def test_dialog_selection_changes_and_unsupported_settings(self):
        dialog = ExportDialog(str(self.source), 2, self.metadata, {}, "pyav")
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.path_edit.setText(str(self.root / "ranges.png"))
        self.assertFalse(dialog.range_widget.isVisible())
        self.assertFalse(dialog.range_label.isVisible())
        self.assertEqual(dialog.execute_button.text(), "Export")
        self.assertFalse(hasattr(dialog, "verify_button"))
        dialog.selection_combo.setCurrentIndex(1)
        self.assertTrue(dialog.range_widget.isVisible())
        self.assertTrue(dialog.range_label.isVisible())
        dialog.first_spin.setValue(2)
        dialog.last_spin.setValue(4)
        self.assertEqual((dialog.plan.first, dialog.plan.last), (1, 3))
        self.assertTrue(dialog.plan.sequence)
        dialog.selection_combo.setCurrentIndex(2)
        self.assertFalse(dialog.range_widget.isVisible())
        self.assertEqual((dialog.plan.first, dialog.plan.last), (0, 4))
        simulation = ExportDialog(str(self.source), 2, self.metadata, {"compression": 20}, "pyav")
        self.addCleanup(simulation.close)
        simulation.path_edit.setText(str(self.root / "without-jpeg.png"))
        self.assertIsNotNone(simulation.plan)
        self.assertEqual(simulation.plan.settings["compression"], 0)

    def test_dialog_verify_existing_output(self):
        plan = self.plan("png", 2, 2, 32, name="gui-existing")
        result = subprocess.run(plan.command(), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        dialog = ExportDialog(str(self.source), 2, self.metadata, {"contrast": 32}, "pyav")
        self.addCleanup(dialog.close)
        dialog.diagnostics_check.setChecked(True)
        dialog.path_edit.setText(plan.destination)
        dialog._execute(verify_only=True)
        self.wait_worker(dialog)
        self.assertIn("VERIFIED lossless", dialog.status_label.text())
        self.assertFalse(json.loads(plan.report_path.read_text())["encoding_command_executed_by_osvev"])

    def test_dialog_cancel_requests_worker_and_waits_for_clean_finish(self):
        dialog = ExportDialog(str(self.source), 2, self.metadata, {}, "pyav")
        self.addCleanup(dialog.close)
        dialog.path_edit.setText(str(self.root / "cancel.png"))
        def slow_execute(plan, metadata, cancelled, progress, **kwargs):
            while not cancelled():
                time.sleep(0.01)
            raise ExportCancelled()
        with patch("export_runner.execute_export", side_effect=slow_execute):
            dialog._execute()
            dialog.reject()
            self.assertIsNotNone(dialog.worker)
            self.wait_worker(dialog)
        self.assertIn("NOT VERIFIED", dialog.status_label.text())
        self.assertTrue(dialog.cancel_button.isEnabled())

    def test_closing_viewer_stops_export_worker_and_exits_modal_dialog(self):
        with patch.object(OSVEV, "_check_dependencies"), \
                patch.object(OSVEV, "set_windows_taskbar_icon"):
            viewer = OSVEV()
        self.addCleanup(viewer.close)
        dialog = ExportDialog(str(self.source), 2, self.metadata, {}, "pyav", viewer)
        viewer._export_dialog = dialog
        dialog.path_edit.setText(str(self.root / "close.png"))
        def slow_execute(plan, metadata, cancelled, progress, **kwargs):
            while not cancelled():
                time.sleep(0.01)
            raise ExportCancelled()
        with patch("export_runner.execute_export", side_effect=slow_execute):
            dialog._execute()
            worker = dialog.worker
            viewer.close()
            self.assertFalse(worker.isRunning())
            self.wait_worker(dialog)
        self.assertFalse(dialog.isVisible())
        self.assertFalse(viewer.timer.isActive())
        viewer._export_dialog = None


if __name__ == "__main__":
    unittest.main()
