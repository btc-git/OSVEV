# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Isolated working-copy contracts, including refusal instead of timestamp repair."""

from dataclasses import replace
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import av
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from color_interpretation import ColorChoices, MissingColorMetadata
from ffmpeg_export import ExportCancelled
from ffmpeg_tools import resolve_arguments
from working_copy import (
    WorkingCopyError, WorkingCopyWorker, create_working_copy, prepare_source,
)
from working_copy_dialog import WorkingCopyDialog
from osvev_app import OSVEV
from frame_info import FrameMetadataWorker


def ffmpeg(args):
    return subprocess.run(resolve_arguments(["ffmpeg", "-v", "error", *map(str, args)]),
                          capture_output=True, check=True, timeout=60).stdout


class WorkingCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.directory = tempfile.TemporaryDirectory(prefix="osvev-working-tests-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.h264 = cls.root / "source.mp4"
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=1.2",
                "-c:v", "libx264", "-bf", "0", "-g", "2", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_range", "tv",
                "-color_primaries", "bt709", "-color_trc", "bt709",
                "-chroma_sample_location", "left", cls.h264])
        tagged = cls.root / "tagged.mp4"
        ffmpeg(["-i", cls.h264, "-c:v", "copy", "-bsf:v",
                "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0",
                tagged])
        cls.h264 = tagged
        cls.mpeg4 = cls.root / "mpeg4.mp4"
        cls.avi = cls.root / "mpeg4.avi"
        cls.bframes = cls.root / "mpeg4-b.avi"
        for destination, bframes in ((cls.mpeg4, 0), (cls.avi, 0), (cls.bframes, 2)):
            ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=1.2",
                    "-c:v", "mpeg4", "-bf", bframes, "-pix_fmt", "yuv420p", destination])
        cls.h264_avi = cls.root / "h264.avi"
        ffmpeg(["-i", cls.h264, "-c:v", "copy", cls.h264_avi])
        cls.derived_avi = cls.root / "derived.avi"
        # Fixture construction sets the known test-source rate; production never adds -r.
        ffmpeg(["-i", cls.h264, "-c:v", "copy", "-r", "5", cls.derived_avi])
        cls.mismatch_avi = cls.root / "mismatch.avi"
        header = bytearray(cls.avi.read_bytes())
        stream_header = header.index(b"strh") + 8
        if header[stream_header:stream_header + 4] != b"vids":
            raise AssertionError("Expected first AVI stream to be video")
        struct.pack_into("<I", header, stream_header + 32, 9)
        cls.mismatch_avi.write_bytes(header)
        cls.ties = cls.root / "vfr-ties.mkv"
        ffmpeg(["-i", cls.h264, "-vf",
                "settb=1/1000,setpts='2000+if(lt(N,2),0,if(lt(N,4),(N-1)*80,350+(N-4)*100))'",
                "-fps_mode", "passthrough", "-enc_time_base", "1:1000",
                "-c:v", "ffv1", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_range", "tv",
                "-color_primaries", "bt709", "-color_trc", "bt709", cls.ties])
        cls.center = cls.root / "center.mp4"
        ffmpeg(["-i", cls.h264, "-c:v", "copy", "-bsf:v", "h264_metadata=chroma_sample_loc_type=1",
                cls.center])

    def setUp(self):
        self.out = tempfile.TemporaryDirectory(prefix="osvev-working-output-")
        self.addCleanup(self.out.cleanup)
        self.destination = Path(self.out.name) / "copy.mkv"

    def convert(self, path, choices=None, authorize=False):
        source = prepare_source(path)
        updates = []
        result = create_working_copy(source, self.destination, choices=choices,
                                     authorize_derived=authorize,
                                     progress=lambda *args: updates.append(args))
        self.assertEqual(result, str(self.destination))
        log = Path(result + ".conversion.jsonl")
        records = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(records[0]["source_sha256"], hashlib.sha256(Path(path).read_bytes()).hexdigest())
        self.assertEqual(records[0]["output_sha256"], hashlib.sha256(self.destination.read_bytes()).hexdigest())
        self.assertEqual(len(records) - 1, 6)
        for record in records[1:]:
            self.assertEqual(record["bgr24_sha256"], record["independent_bgr24_sha256"])
            self.assertEqual(record["bgr24_sha256"], record["output_bgr24_sha256"])
        self.assertEqual(updates[-1][0], 100)
        self.assertTrue(all(0 <= percent <= 100 for percent, _ in updates))
        self.assertFalse(list(Path(self.out.name).glob(".osvev-working-*")))
        return source, records

    def test_h264_original_timestamps_color_pixels_and_reopening(self):
        source, records = self.convert(self.h264)
        self.assertFalse(source.derived_timing)
        self.assertEqual(records[0]["color"]["assumed_fields"], [])
        self.assertEqual([Fraction(r["selected_time_seconds"]) for r in records[1:]], list(source.times))
        self.assertEqual(records[0]["discrepancies"], [])

    def test_mpeg4_requires_color_authorization_and_records_assumptions(self):
        source = prepare_source(self.mpeg4)
        with self.assertRaises(MissingColorMetadata):
            create_working_copy(source, self.destination)
        self.assertFalse(self.destination.exists())
        _, records = self.convert(self.mpeg4, ColorChoices())
        self.assertIn("color_space", records[0]["color"]["assumed_fields"])
        self.assertEqual(records[0]["color"]["effective"]["color_space"], "smpte170m")

    def test_avi_requires_derived_timing_permission_or_is_refused(self):
        source = prepare_source(self.derived_avi)
        self.assertTrue(source.derived_timing)
        with self.assertRaisesRegex(WorkingCopyError, "Explicit authorization"):
            create_working_copy(source, self.destination, choices=ColorChoices())
        _, records = self.convert(self.derived_avi, ColorChoices(), True)
        self.assertIn("reconstructed/derived", records[0]["timing_policy"])
        self.assertTrue(any(r["original_pts"] is None for r in records[1:]))
        with self.assertRaisesRegex(WorkingCopyError, "complete integer"):
            prepare_source(self.bframes)
        with self.assertRaisesRegex(WorkingCopyError, "frame count|rounding timing"):
            prepare_source(self.h264_avi)

    def test_vfr_nonzero_duplicate_timestamps_retained(self):
        source, records = self.convert(self.ties, ColorChoices("bt709", "tv"))
        self.assertEqual(list(source.times), [Fraction(v, 1000) for v in (2000, 2000, 2080, 2160, 2350, 2450)])
        self.assertEqual([Fraction(r["output_pts"]) * Fraction(r["output_time_base"]) for r in records[1:]],
                         list(source.times))

    def test_center_chroma_reuses_verified_two_stage_interpretation(self):
        _, records = self.convert(self.center)
        self.assertEqual(records[0]["color"]["effective"]["chroma_location"], "center")
        self.assertIn("yuv444p", records[0]["command"])
        from test_center_chroma import reference_filter
        reference = ffmpeg(["-i", self.center, "-vf", reference_filter("bt709", False, True),
                            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"])
        self.assertEqual(records[1]["bgr24_sha256"], hashlib.sha256(reference[:64 * 48 * 3]).hexdigest())

    def test_existing_files_source_changes_and_insufficient_space_are_safe(self):
        source = prepare_source(self.h264)
        self.destination.write_bytes(b"keep")
        with self.assertRaisesRegex(WorkingCopyError, "never overwritten"):
            create_working_copy(source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"keep")
        self.destination.unlink()
        with patch("working_copy.shutil.disk_usage") as usage:
            usage.return_value.free = 1
            with self.assertRaisesRegex(WorkingCopyError, "Insufficient free space"):
                create_working_copy(source, self.destination)
        with self.assertRaisesRegex(WorkingCopyError, "changed after preflight"):
            create_working_copy(replace(source, sha256="0" * 64), self.destination)

    def test_failed_pixel_and_duration_verification_clean_all_owned_files(self):
        from working_copy import conversion_command
        source = prepare_source(self.h264)
        for mode in ("pixels", "timing"):
            def changed(*args):
                command = conversion_command(*args)
                if mode == "pixels":
                    command[command.index("-vf") + 1] += ",negate"
                else:
                    command[command.index("-vf") + 1] += ",setpts=PTS+0.2/TB"
                return command
            with patch("working_copy.conversion_command", side_effect=changed):
                with self.assertRaises(WorkingCopyError):
                    create_working_copy(source, self.destination)
            self.assertEqual(list(Path(self.out.name).iterdir()), [])
        durations = list(source.durations)
        durations[1] += Fraction(1, 1000)
        with self.assertRaisesRegex(WorkingCopyError, "duration changed"):
            create_working_copy(replace(source, durations=tuple(durations)), self.destination)
        self.assertEqual(list(Path(self.out.name).iterdir()), [])

    def test_cancellation_during_encoding_verification_and_publication_cleans_up(self):
        source = prepare_source(self.h264)
        for threshold in (0, 45, 90):
            event = threading.Event()
            def progress(percent, message):
                if percent >= threshold:
                    event.set()
            with self.assertRaises(ExportCancelled):
                create_working_copy(source, self.destination, cancelled=event.is_set, progress=progress)
            self.assertEqual(list(Path(self.out.name).iterdir()), [])

    def test_bounded_frame_reads_and_worker_error_delivery(self):
        source = prepare_source(self.h264)
        from working_copy import FFmpegProcess
        read = FFmpegProcess.read_exact
        sizes = []
        def reading(process, size):
            sizes.append(size)
            return read(process, size)
        with patch.object(FFmpegProcess, "read_exact", reading):
            create_working_copy(source, self.destination)
        self.assertEqual(sizes, [64 * 48 * 3] * 6)
        worker = WorkingCopyWorker(source, destination=str(self.destination))
        worker.start()
        self.assertTrue(worker.wait(10000))
        self.assertIn("never overwritten", worker.error)
        self.assertIsNone(worker.result)

    def viewer(self):
        self.enterContext(patch.object(OSVEV, "_check_dependencies"))
        self.enterContext(patch.object(OSVEV, "set_windows_taskbar_icon"))
        viewer = OSVEV()
        self.addCleanup(viewer.close)
        viewer._load_video(str(self.h264))
        self.assertEqual(viewer.frame_count, 6)
        return viewer

    def test_only_typed_compatibility_failures_offer_conversion(self):
        viewer = self.viewer()
        original = viewer.cap
        with patch.object(viewer, "_offer_working_copy", return_value=True) as offer, \
                patch.object(viewer, "_create_working_copy") as create, \
                patch.object(QMessageBox, "warning") as warning:
            viewer._load_video(str(self.mpeg4))
            offer.assert_called_once()
            create.assert_called_once_with(str(self.mpeg4))
            self.assertIn("Unsupported input codec", offer.call_args.args[0])
            self.assertIs(viewer.cap, original)
            self.assertTrue(original.isOpened())
            warning.assert_not_called()
        with patch.object(viewer, "_offer_working_copy") as offer, \
                patch.object(QMessageBox, "warning") as warning:
            viewer._load_video(str(self.root / "does-not-exist.mp4"))
            offer.assert_not_called()
            warning.assert_called_once()
            self.assertIs(viewer.cap, original)
        worker = FrameMetadataWorker("bad", opener=lambda path: (_ for _ in ()).throw(OSError("disk failure")))
        worker.run()
        self.assertIsNone(worker.compatibility_error)
        self.assertIn("disk failure", worker.error)

    def test_dialog_full_conversion_and_user_authorized_reopening(self):
        viewer = self.viewer()
        old = viewer.cap
        timer = QTimer()
        failures = []
        def choose():
            dialog = viewer._working_copy_dialog
            if dialog is not None and dialog.create.isEnabled():
                timer.stop()
                try:
                    self.assertEqual(viewer.cap, old)
                    self.assertEqual(dialog.matrix.currentIndex(), 0)
                    dialog._create()
                except AssertionError as error:
                    failures.append(error)
                    dialog.reject()
        timer.timeout.connect(choose)
        timer.start(5)
        self.addCleanup(timer.stop)
        with patch.object(viewer, "_offer_working_copy", return_value=True), \
                patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.destination), "")), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(QMessageBox, "warning") as warning:
            viewer._load_video(str(self.mpeg4))
        self.assertFalse(failures)
        warning.assert_not_called()
        self.assertEqual(viewer.video_path, str(self.destination))
        self.assertEqual(viewer.frame_count, 6)
        self.assertEqual(viewer.current_native_samples.matrix, "rgb")
        self.assertFalse(old.isOpened())
        self.assertIsNone(viewer._working_copy_dialog)

    def test_preflight_cancel_is_responsive_and_preserves_previous_video(self):
        viewer = self.viewer()
        old = viewer.cap
        beats = []
        heartbeat = QTimer()
        heartbeat.timeout.connect(lambda: beats.append(time.monotonic()))
        heartbeat.start(5)
        self.addCleanup(heartbeat.stop)
        def slow(path, cancelled, progress, **kwargs):
            while not cancelled():
                time.sleep(.01)
            raise ExportCancelled()
        with patch("working_copy.prepare_source", side_effect=slow):
            QTimer.singleShot(100, lambda: viewer._working_copy_dialog.reject())
            viewer._create_working_copy(str(self.mpeg4))
        self.assertGreater(len(beats), 3)
        self.assertIs(viewer.cap, old)
        self.assertTrue(old.isOpened())
        self.assertFalse(self.destination.exists())

    def test_dialog_conversion_cancel_stops_process_and_removes_staging(self):
        from working_copy import conversion_command
        viewer = self.viewer()
        old = viewer.cap
        def realtime(*args):
            command = conversion_command(*args)
            command.insert(command.index("-i"), "-re")
            return command
        poll = QTimer()
        def choose():
            dialog = viewer._working_copy_dialog
            if dialog and dialog.create.isEnabled():
                poll.stop()
                dialog._create()
                QTimer.singleShot(150, dialog.reject)
        poll.timeout.connect(choose)
        poll.start(5)
        self.addCleanup(poll.stop)
        started = time.monotonic()
        with patch("working_copy.conversion_command", side_effect=realtime), \
                patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.destination), "")), \
                patch.object(QMessageBox, "warning") as warning:
            viewer._create_working_copy(str(self.h264))
        self.assertLess(time.monotonic() - started, 3)
        warning.assert_not_called()
        self.assertIs(viewer.cap, old)
        self.assertEqual(list(Path(self.out.name).iterdir()), [])

    def test_dialog_requires_explicit_derived_checkbox_and_default_cancel_preserves_source(self):
        dialog = WorkingCopyDialog(str(self.derived_avi))
        self.addCleanup(dialog.close)
        timer = QTimer()
        seen = []
        def ready():
            if dialog.source is not None:
                timer.stop()
                seen.append(dialog.create.isEnabled())
                seen.append(dialog.derived.isChecked())
                dialog.derived.setChecked(True)
                seen.append(dialog.create.isEnabled())
                dialog.reject()
        timer.timeout.connect(ready)
        timer.start(5)
        self.addCleanup(timer.stop)
        dialog.exec()
        self.assertEqual(seen, [False, False, True])
        self.assertIsNone(dialog.result_path)

    def test_unsupported_metadata_decreases_and_fine_timing_are_refused(self):
        from working_copy import scan_source
        data, streams = scan_source(self.h264, lambda: False, lambda *args: None)
        variants = []
        for field, value in (("pts", -1), ("pts", 1), ("pix_fmt", "yuv420p10le"),
                             ("interlaced_frame", 1), ("color_transfer", "smpte2084"),
                             ("chroma_location", "topleft"), ("width", 66)):
            candidate = deepcopy(data)
            candidate["frames"][1][field] = value
            variants.append(candidate)
        candidate = deepcopy(data)
        candidate["frames"][0]["side_data_list"] = [{"side_data_type": "Display Matrix"}]
        variants.append(candidate)
        candidate = deepcopy(data)
        candidate["streams"][0]["nb_frames"] = "7"
        variants.append(candidate)
        for index, candidate in enumerate(variants):
            with self.subTest(case=index), patch("working_copy.scan_source", return_value=(candidate, streams)):
                with self.assertRaises((WorkingCopyError, ValueError, RuntimeError)):
                    prepare_source(self.h264)

    def test_4k_conversion_worker_keeps_gui_responsive_and_reads_one_frame_at_a_time(self):
        source_path = Path(self.out.name) / "4k-source.mkv"
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=2:duration=1",
                "-c:v", "ffv1", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_range", "tv",
                "-color_primaries", "bt709", "-color_trc", "bt709", source_path])
        source = prepare_source(source_path)
        worker = WorkingCopyWorker(source, destination=str(self.destination),
                                   choices=ColorChoices("bt709", "tv"))
        beats = []
        heartbeat = QTimer()
        heartbeat.timeout.connect(lambda: beats.append(time.monotonic()))
        heartbeat.start(5)
        worker.start()
        deadline = time.monotonic() + 30
        while worker.isRunning() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        if worker.isRunning():
            worker.requestInterruption()
            worker.wait()
            self.fail("4K conversion exceeded bounded fixture timeout")
        heartbeat.stop()
        self.assertIsNone(worker.error)
        self.assertEqual(worker.result, str(self.destination))
        self.assertGreater(len(beats), 5)
        self.assertLess(max(b - a for a, b in zip(beats, beats[1:])), 1)
        self.assertTrue(self.destination.exists())

    def test_publication_collision_disk_error_and_cancel_remove_only_owned_files(self):
        source = prepare_source(self.h264)
        from working_copy import destination_paths
        calls = []
        def collision(*args):
            calls.append(True)
            if len(calls) == 2:
                self.destination.write_bytes(b"another user")
            return destination_paths(*args)
        with patch("working_copy.destination_paths", side_effect=collision):
            with self.assertRaisesRegex(WorkingCopyError, "never overwritten"):
                create_working_copy(source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"another user")
        self.destination.unlink()
        opening = Path.open
        def disk_error(path, *args, **kwargs):
            if path == Path(str(self.destination) + ".conversion.jsonl"):
                raise OSError("disk full publishing log")
            return opening(path, *args, **kwargs)
        with patch.object(Path, "open", disk_error):
            with self.assertRaisesRegex(OSError, "disk full"):
                create_working_copy(source, self.destination)
        self.assertEqual(list(Path(self.out.name).iterdir()), [])
        flag = threading.Event()
        def cancel_publication(percent, text):
            if text.startswith("Publishing"):
                flag.set()
        with self.assertRaises(ExportCancelled):
            create_working_copy(source, self.destination, cancelled=flag.is_set, progress=cancel_publication)
        self.assertEqual(list(Path(self.out.name).iterdir()), [])

    def test_missing_color_dialog_can_choose_copy_without_authorizing_normal_open(self):
        from color_dialog import ColorAssumptionsDialog
        dialog = ColorAssumptionsDialog("missing", allow_working_copy=True)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.working_copy_requested)
        dialog._working_copy()
        self.assertTrue(dialog.working_copy_requested)
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)

    def test_encoder_failure_and_source_mutation_never_publish(self):
        from working_copy import conversion_command, file_sha256
        source = prepare_source(self.h264)
        def broken(*args):
            command = conversion_command(*args)
            command[command.index("-c:v") + 1] = "not_an_encoder"
            return command
        with patch("working_copy.conversion_command", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "FFmpeg failed"):
                create_working_copy(source, self.destination)
        self.assertEqual(list(Path(self.out.name).iterdir()), [])
        calls = []
        def changed(path, cancelled):
            if path == Path(source.path):
                calls.append(True)
                if len(calls) == 2:
                    return "changed"
            return file_sha256(path, cancelled)
        with patch("working_copy.file_sha256", side_effect=changed):
            with self.assertRaisesRegex(WorkingCopyError, "changed during"):
                create_working_copy(source, self.destination)
        self.assertEqual(list(Path(self.out.name).iterdir()), [])

    def test_hevc_conversion_has_exact_original_timing(self):
        path = Path(self.out.name) / "hevc.mp4"
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=1.2",
                "-c:v", "libx265", "-x265-params", "pools=1:frame-threads=1",
                "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_range", "tv",
                "-color_primaries", "bt709", "-color_trc", "bt709",
                "-chroma_sample_location", "left", path])
        tagged = Path(self.out.name) / "hevc-tagged.mp4"
        ffmpeg(["-i", path, "-c:v", "copy", "-bsf:v",
                "hevc_metadata=colour_primaries=1:transfer_characteristics=1:"
                "matrix_coefficients=1:video_full_range_flag=0:chroma_sample_loc_type=0", tagged])
        source, records = self.convert(tagged)
        self.assertFalse(source.derived_timing)
        self.assertEqual([Fraction(r["output_pts"]) * Fraction(r["output_time_base"]) for r in records[1:]],
                         list(source.times))

    def test_converter_is_not_an_eager_dependency_of_ordinary_application_import(self):
        script = (
            "import sys\n"
            "class NoAV:\n"
            " def find_spec(self, fullname, path=None, target=None):\n"
            "  if fullname == 'av' or fullname.startswith('av.'):\n"
            "   raise ModuleNotFoundError('PyAV deliberately absent')\n"
            "sys.meta_path.insert(0, NoAV())\n"
            "import osvev_app\n"
            "assert 'working_copy' not in sys.modules\n"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_avi_mismatch_requires_permission_then_verifies_all_recovered_frames(self):
        source = prepare_source(self.mismatch_avi)
        self.assertEqual((source.declared_count, len(source.frames)), (9, 6))
        self.assertTrue(source.count_mismatch)
        before = hashlib.sha256(self.mismatch_avi.read_bytes()).hexdigest()
        with self.assertRaisesRegex(WorkingCopyError, "Explicit authorization.*AVI declares 9"):
            create_working_copy(source, self.destination, choices=ColorChoices())
        self.assertEqual(list(Path(self.out.name).iterdir()), [])
        create_working_copy(source, self.destination, choices=ColorChoices(),
                            authorize_count_mismatch=True)
        records = [json.loads(line) for line in
                   Path(str(self.destination) + ".conversion.jsonl").read_text().splitlines()]
        header = records[0]
        self.assertEqual(header["frame_count_policy"]["original_declared_count"], 9)
        self.assertEqual(header["frame_count_policy"]["decoded_recovered_count"], 6)
        self.assertTrue(header["frame_count_policy"]["mismatch_authorized"])
        self.assertIn("not proof", header["frame_count_policy"]["limitation"])
        self.assertEqual(header["discrepancies"][0]["recovered_minus_declared"], -3)
        self.assertEqual(len(records[1:]), 6)
        for record, stamp in zip(records[1:], source.times):
            self.assertEqual(record["bgr24_sha256"], record["independent_bgr24_sha256"])
            self.assertEqual(record["bgr24_sha256"], record["output_bgr24_sha256"])
            self.assertEqual(Fraction(record["output_time_seconds"]), stamp)
        self.assertEqual(hashlib.sha256(self.mismatch_avi.read_bytes()).hexdigest(), before)

    def test_mismatch_dialog_requires_own_permission_and_worker_forwards_it(self):
        source = prepare_source(self.mismatch_avi)
        worker = WorkingCopyWorker(source, destination=str(self.destination), choices=ColorChoices(),
                                   authorize_count_mismatch=True)
        worker.start()
        self.assertTrue(worker.wait(10000))
        self.assertIsNone(worker.error)
        self.assertEqual(worker.result, str(self.destination))
        dialog = WorkingCopyDialog(str(self.mismatch_avi))
        self.addCleanup(dialog.close)
        timer = QTimer()
        observed = []
        def ready():
            if dialog.source is not None:
                timer.stop()
                observed.extend([dialog.count_permission.isChecked(), dialog.create.isEnabled()])
                self.assertIn("declares 9", dialog.count_note.text())
                dialog.count_permission.setChecked(True)
                observed.append(dialog.create.isEnabled())
                dialog.reject()
        timer.timeout.connect(ready)
        timer.start(5)
        self.addCleanup(timer.stop)
        dialog.exec()
        self.assertEqual(observed, [False, False, True])

    def test_mismatch_preflight_rejects_independent_pixel_errors_and_cancels(self):
        from working_copy import FFmpegProcess
        original = FFmpegProcess.read_exact
        def different(process, size):
            data = original(process, size)
            return bytes([data[0] ^ 1]) + data[1:]
        with patch.object(FFmpegProcess, "read_exact", different):
            with self.assertRaisesRegex(WorkingCopyError, "native pixels/order"):
                prepare_source(self.mismatch_avi)
        event = threading.Event()
        def progress(percent, message):
            if message.startswith("Corroborating"):
                event.set()
        with self.assertRaises(ExportCancelled):
            prepare_source(self.mismatch_avi, event.is_set, progress)
        self.assertEqual(list(Path(self.out.name).iterdir()), [])

    def test_mismatch_preflight_refuses_corrupt_recording(self):
        from working_copy import scan_source
        data, _ = scan_source(self.mismatch_avi, lambda: False, lambda *args: None)
        damaged = bytearray(self.mismatch_avi.read_bytes())
        frame = data["frames"][2]
        position, size = int(frame["pkt_pos"]), int(frame["pkt_size"])
        damaged[position:position + size] = b"\x00" * size
        path = Path(self.out.name) / "damaged.avi"
        path.write_bytes(damaged)
        with self.assertRaises((WorkingCopyError, RuntimeError, av.FFmpegError)):
            prepare_source(path)

    def test_mismatch_rejects_error_diagnostics_even_on_success_exit(self):
        from working_copy import finish_recovered_decode
        with tempfile.TemporaryFile() as errors:
            errors.write(b"demuxer error despite successful process exit")
            process = Mock(error_stream=errors)
            with self.assertRaisesRegex(WorkingCopyError, "unexplained FFmpeg error"):
                finish_recovered_decode(process)
            process.finish.assert_called_once()

    def test_full_range_avi_mismatch_preserves_native_samples(self):
        path = Path(self.out.name) / "full-range-mismatch.avi"
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=1.2",
                "-c:v", "libx264", "-bf", "0", "-pix_fmt", "yuvj420p",
                "-color_range", "pc", "-colorspace", "bt709",
                "-color_primaries", "bt709", "-color_trc", "bt709",
                "-chroma_sample_location", "left", "-r", "5", path])
        data = bytearray(path.read_bytes())
        struct.pack_into("<I", data, data.index(b"strh") + 8 + 32, 9)
        path.write_bytes(data)
        source = prepare_source(path)
        self.assertEqual(source.frames[0]["pix_fmt"], "yuvj420p")
        create_working_copy(source, self.destination, choices=ColorChoices(),
                            authorize_derived=True, authorize_count_mismatch=True)
        self.assertTrue(self.destination.exists())

    def test_mismatch_and_derived_timing_permissions_are_independent(self):
        path = Path(self.out.name) / "derived-mismatch.avi"
        data = bytearray(self.derived_avi.read_bytes())
        struct.pack_into("<I", data, data.index(b"strh") + 8 + 32, 9)
        path.write_bytes(data)
        source = prepare_source(path)
        self.assertTrue(source.derived_timing)
        self.assertTrue(source.count_mismatch)
        with self.assertRaisesRegex(WorkingCopyError, "derived best-effort"):
            create_working_copy(source, self.destination, choices=ColorChoices(),
                                authorize_count_mismatch=True)
        with self.assertRaisesRegex(WorkingCopyError, "AVI declares"):
            create_working_copy(source, self.destination, choices=ColorChoices(),
                                authorize_derived=True)
        create_working_copy(source, self.destination, choices=ColorChoices(),
                            authorize_derived=True, authorize_count_mismatch=True)
        self.assertTrue(self.destination.exists())

    def test_mismatch_permission_does_not_relax_timing_or_nonavi_counts(self):
        from working_copy import scan_source
        data, streams = scan_source(self.mismatch_avi, lambda: False, lambda *args: None)
        candidate = deepcopy(data)
        candidate["frames"][1]["duration"] = 0
        with patch("working_copy.scan_source", return_value=(candidate, streams)):
            with self.assertRaisesRegex(WorkingCopyError, "positive duration"):
                prepare_source(self.mismatch_avi)
        candidate = deepcopy(data)
        candidate["frames"][2]["pts"] = -1
        with patch("working_copy.scan_source", return_value=(candidate, streams)):
            with self.assertRaisesRegex(WorkingCopyError, "Negative or decreasing"):
                prepare_source(self.mismatch_avi)
        data, streams = scan_source(self.h264, lambda: False, lambda *args: None)
        data["streams"][0]["nb_frames"] = "9"
        with patch("working_copy.scan_source", return_value=(data, streams)):
            with self.assertRaisesRegex(WorkingCopyError, "declares 9 frames but 6 packets.*index itself is inconsistent"):
                prepare_source(self.h264)

    def edit_list_mp4(self):
        """Stream-copied MP4 whose edit list discards three leading stored samples."""
        long_source = Path(self.out.name) / "long.mp4"
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=3",
                "-c:v", "libx264", "-bf", "0", "-g", "10", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_range", "tv", "-color_primaries", "bt709",
                "-color_trc", "bt709", "-chroma_sample_location", "left", "-bsf:v",
                "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0",
                long_source])
        path = Path(self.out.name) / "edit-list.mp4"
        ffmpeg(["-ss", "0.6", "-i", long_source, "-c", "copy", path])
        return long_source, path

    def test_mp4_edit_list_count_discrepancy_converts_recovered_frames(self):
        from pyav_input import PyAVInputCapture
        from frame_info import FrameInfoHandler
        _, path = self.edit_list_mp4()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        capture = PyAVInputCapture(str(path))
        self.addCleanup(capture.release)
        self.assertTrue(capture.read()[0])
        capture.configure_index(FrameInfoHandler.get_frame_metadata(str(path)))
        self.assertEqual(capture.count_discrepancy["discard_flagged_undecoded_count"], 3)
        source = prepare_source(path)
        self.assertEqual((source.declared_count, source.count_audit["packet_count"], len(source.frames)),
                         (15, 15, 12))
        self.assertEqual(source.count_audit["discard_flagged_undecoded_count"], 3)
        self.assertEqual(source.count_audit["pre_keyframe_undecoded_count"], 0)
        with self.assertRaisesRegex(WorkingCopyError, "Explicit authorization.*declares 15 frames in 15 packets"):
            create_working_copy(source, self.destination, choices=ColorChoices())
        self.assertEqual(list(Path(self.out.name).glob("copy*")), [])
        create_working_copy(source, self.destination, choices=ColorChoices(),
                            authorize_count_mismatch=True)
        records = [json.loads(line) for line in
                   Path(str(self.destination) + ".conversion.jsonl").read_text().splitlines()]
        header, frames = records[0], records[1:]
        policy = header["frame_count_policy"]
        self.assertEqual((policy["original_declared_count"], policy["packet_count"],
                          policy["decoded_recovered_count"]), (15, 15, 12))
        self.assertTrue(policy["mismatch_authorized"])
        discrepancy = header["discrepancies"][0]
        self.assertEqual(discrepancy["kind"], "Container declared/packet/decoded frame count mismatch")
        self.assertEqual(discrepancy["recovered_minus_declared"], -3)
        self.assertEqual(discrepancy["frames_added_or_removed_to_match_declared_count"], 0)
        self.assertFalse(discrepancy["timestamps_normalized"])
        self.assertTrue(any("discard" in text for text in discrepancy["assumptions"]))
        self.assertIn("ffv1", header["command"])
        self.assertEqual(len(frames), 12)
        for record, stamp, original in zip(frames, source.times, source.frames):
            self.assertEqual(record["original_pts"], original["pts"])
            self.assertEqual(Fraction(record["output_time_seconds"]), stamp)
            self.assertEqual(record["bgr24_sha256"], record["independent_bgr24_sha256"])
            self.assertEqual(record["bgr24_sha256"], record["output_bgr24_sha256"])
        self.assertEqual(Fraction(frames[0]["output_time_seconds"]), 0)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(header["source_sha256_after"], before)

    def test_mp4_leading_pre_keyframe_packets_are_explained_and_logged(self):
        long_source, _ = self.edit_list_mp4()
        path = Path(self.out.name) / "no-leading-idr.mp4"
        ffmpeg(["-i", long_source, "-c", "copy", "-bsf:v", "noise=drop='lt(n\\,1)'", path])
        source = prepare_source(path)
        audit = source.count_audit
        self.assertEqual((audit["declared_count"], audit["packet_count"], audit["decoded_count"]), (14, 14, 5))
        self.assertEqual(audit["pre_keyframe_undecoded_count"], 9)
        create_working_copy(source, self.destination, choices=ColorChoices(),
                            authorize_count_mismatch=True)
        frames = [json.loads(line) for line in
                  Path(str(self.destination) + ".conversion.jsonl").read_text().splitlines()][1:]
        # Original absolute times are kept, not rebased to zero.
        self.assertEqual([Fraction(record["output_time_seconds"]) for record in frames],
                         list(source.times))
        self.assertEqual(source.times[0], 2)

    def test_explained_mp4_discrepancy_opens_original_directly_with_warning(self):
        import cv2
        import numpy as np
        from frame_info import FrameInfoHandler
        _, path = self.edit_list_mp4()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        metadata = FrameInfoHandler.get_frame_metadata(str(path))
        viewer = self.viewer()
        with patch.object(viewer, "_offer_working_copy") as offer, \
                patch.object(QMessageBox, "warning") as warning:
            viewer._load_video(str(path))
        offer.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1], "Frame-count discrepancy")
        self.assertIn("declares 15 frames in 15 packets, but 12 frames decode", warning.call_args.args[2])
        self.assertIn("discard", warning.call_args.args[2])
        self.assertEqual(viewer.video_path, str(path))
        self.assertEqual(viewer.frame_count, 12)
        self.assertEqual([record["pts"] for record in viewer.frame_metadata],
                         [record["pts"] for record in metadata])
        self.assertEqual(viewer.cap.native_samples.frame.pts, metadata[0]["pts"])
        # Sequential reads and exact-PTS seeks agree with an independent PyAV decode.
        expected = []
        with av.open(str(path)) as container:
            for frame in container.decode(video=0):
                expected.append((frame.pts, frame.to_ndarray(format="bgr24")))
        self.assertEqual([pts for pts, _ in expected], [record["pts"] for record in metadata])
        for index in (7, 0, 11, 4):
            viewer.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, image = viewer.cap.read()
            self.assertTrue(ok)
            self.assertEqual(viewer.cap.native_samples.frame.pts, expected[index][0])
            np.testing.assert_array_equal(image, expected[index][1])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_direct_open_count_discrepancy_survives_export_logs(self):
        from export_runner import execute_export
        from ffmpeg_export import build_export_plan
        from frame_info import FrameInfoHandler
        from pyav_input import PyAVInputCapture
        long_source, edit_list = self.edit_list_mp4()
        leading = Path(self.out.name) / "leading.mp4"
        ffmpeg(["-i", long_source, "-c", "copy", "-bsf:v", "noise=drop='lt(n\\,1)'", leading])
        for path, count, discarded, pre_keyframe in ((edit_list, 15, 3, 0), (leading, 14, 0, 9)):
            with self.subTest(path=path.name):
                before = hashlib.sha256(path.read_bytes()).digest()
                metadata = FrameInfoHandler.get_frame_metadata(str(path))
                capture = PyAVInputCapture(str(path))
                try:
                    capture.read()
                    capture.configure_index(metadata)
                    audit = capture.count_discrepancy
                finally:
                    capture.release()
                plan = build_export_plan(str(path), str(path.with_suffix(".png")),
                                         "png", 0, 0, {}, metadata, "pyav")
                self.assertEqual(plan.count_discrepancy, audit)
                report = execute_export(plan, metadata, detailed_diagnostics=True)
                self.assertEqual(report["status"], "verified_lossless")
                self.assertEqual(report["source_frame_count_discrepancy"], audit)
                saved = json.loads(plan.report_path.read_text())
                self.assertEqual(saved["source_frame_count_discrepancy"], audit)
                self.assertEqual(audit["discard_flagged_undecoded_count"], discarded)
                self.assertEqual(audit["pre_keyframe_undecoded_count"], pre_keyframe)
                text = plan.log_path.read_text(encoding="utf-8")
                self.assertIn(f"Container-declared frames: {count}", text)
                self.assertIn(f"Demuxed packets: {count}", text)
                self.assertIn(f"Decoded/presented frames: {len(metadata)}", text)
                self.assertIn("not container sample indices", text)
                self.assertIn("not proof of recording completeness", text)
                for explanation in audit["assumptions"]:
                    self.assertIn(explanation, text)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_unexplained_or_avi_count_discrepancy_still_rejected_on_open(self):
        from color_interpretation import InputCompatibilityError
        from frame_info import FrameInfoHandler
        from pyav_input import PyAVInputCapture
        _, path = self.edit_list_mp4()
        metadata = FrameInfoHandler.get_frame_metadata(str(path))
        unexplained = deepcopy(metadata)
        unexplained.pop(5)
        for candidate, message in ((unexplained, "neither decoded nor discard-flagged"),
                                   (metadata[:-1], "neither decoded nor discard-flagged")):
            capture = PyAVInputCapture(str(path))
            self.addCleanup(capture.release)
            self.assertTrue(capture.read()[0])
            with self.assertRaisesRegex(InputCompatibilityError,
                                        "declares 15 frames but 11 were decoded.*" + message):
                capture.configure_index(candidate)
            self.assertIsNone(capture.count_discrepancy)
        # AVI recovery still requires the working copy's independent corroboration.
        capture = PyAVInputCapture(str(path))
        self.addCleanup(capture.release)
        self.assertTrue(capture.read()[0])
        with patch("working_copy.audit_count_discrepancy", return_value={"avi": True}), \
                self.assertRaisesRegex(InputCompatibilityError, r"declares 15 frames but 12 were decoded\.$"):
            capture.configure_index(metadata)
        # Through the GUI, refusals keep the previous video and offer the working copy.
        viewer = self.viewer()
        original = viewer.cap
        with patch("working_copy.audit_count_discrepancy", return_value={"avi": True}), \
                patch.object(viewer, "_offer_working_copy", return_value=False) as offer, \
                patch.object(QMessageBox, "warning") as warning:
            viewer._load_video(str(path))
        offer.assert_called_once()
        self.assertIn("frame counts disagree", offer.call_args.args[0])
        warning.assert_not_called()
        self.assertIs(viewer.cap, original)
        self.assertEqual(viewer.frame_count, 6)

    def test_mp4_count_discrepancy_unsafe_conditions_refused(self):
        from working_copy import scan_source
        _, path = self.edit_list_mp4()
        data, streams = scan_source(path, lambda: False, lambda *args: None)
        cases = {
            "unexplained": lambda frames: frames.pop(5),
            "one-to-one": lambda frames: frames[3].update(pts=frames[2]["pts"]),
            "lack original PTS": lambda frames: frames[3].update(pts=None, best_effort_timestamp=frames[3]["pts"]),
        }
        for message, mutate in cases.items():
            with self.subTest(message):
                candidate = deepcopy(data)
                mutate(candidate["frames"])
                with patch("working_copy.scan_source", return_value=(candidate, streams)):
                    with self.assertRaisesRegex(WorkingCopyError, message):
                        prepare_source(path)
        candidate = deepcopy(data)
        candidate["streams"][0]["nb_frames"] = "16"
        with patch("working_copy.scan_source", return_value=(candidate, streams)):
            with self.assertRaisesRegex(WorkingCopyError, "index itself is inconsistent"):
                prepare_source(path)
        damaged = bytearray(path.read_bytes())
        frame = data["frames"][6]
        position, size = int(frame["pkt_pos"]), int(frame["pkt_size"])
        damaged[position:position + size] = b"\xff" * size
        broken = Path(self.out.name) / "damaged-edit-list.mp4"
        broken.write_bytes(damaged)
        with self.assertRaises(WorkingCopyError):
            prepare_source(broken)
        self.assertEqual(list(Path(self.out.name).glob("copy*")), [])

    def fractional_fixture(self, rate="30000/1001", count=12, expression=None):
        path = Path(self.out.name) / f"rate-{rate.replace('/', '-')}.mp4"
        arguments = ["-f", "lavfi", "-i", f"testsrc2=size=64x48:rate={rate}",
                     "-frames:v", str(count)]
        if expression:
            arguments += ["-vf", expression, "-fps_mode", "passthrough", "-enc_time_base", "1:30000"]
        ffmpeg([*arguments, "-c:v", "libx264", "-bf", "0", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_range", "tv", "-color_primaries", "bt709",
                "-color_trc", "bt709", "-chroma_sample_location", "left", "-bsf:v",
                "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0",
                path])
        return path

    def assert_precision_copy(self, path, **permissions):
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        source = prepare_source(path, allow_precision_rounding=True)
        create_working_copy(source, self.destination, choices=ColorChoices(),
                            authorize_precision_rounding=True, **permissions)
        records = [json.loads(line) for line in Path(str(self.destination) + ".conversion.jsonl").read_text().splitlines()]
        header, frames = records[0], records[1:]
        self.assertEqual(len(frames), len(source.frames))
        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), before)
        self.assertEqual(header["source_sha256_after"], before)
        audit = header["precision_audit"]
        self.assertEqual(audit["authorized_millisecond_rounding"], source.needs_rounding)
        self.assertLessEqual(abs(Fraction(audit["endpoint_drift_seconds"])), Fraction(1, 1000))
        self.assertLessEqual(abs(Fraction(audit["presentation_span_drift_seconds"])), Fraction(1, 1000))
        from working_copy import millisecond
        for index, record in enumerate(frames):
            self.assertEqual(Fraction(record["output_time_seconds"]), millisecond(source.times[index]))
            self.assertEqual(Fraction(record["pts_deviation_seconds"]),
                             Fraction(record["output_time_seconds"]) - source.times[index])
            self.assertLessEqual(abs(Fraction(record["pts_deviation_seconds"])), Fraction(1, 2000))
            self.assertLessEqual(abs(Fraction(record["duration_deviation_seconds"])), Fraction(1, 1000))
            self.assertEqual(record["bgr24_sha256"], record["independent_bgr24_sha256"])
            self.assertEqual(record["bgr24_sha256"], record["output_bgr24_sha256"])
        self.destination.unlink()
        Path(str(self.destination) + ".conversion.jsonl").unlink()
        return source, header, frames

    def test_common_fractional_rates_and_exact_controls(self):
        for rate in ("24000/1001", "30000/1001", "60000/1001", "25", "30", "60", "400", "600"):
            with self.subTest(rate=rate):
                path = self.fractional_fixture(rate)
                self.assert_precision_copy(path)
                if rate == "25":
                    source = prepare_source(path)
                    self.assertFalse(source.needs_rounding)
                    create_working_copy(source, self.destination)
                    records = [json.loads(line) for line in Path(str(self.destination) + ".conversion.jsonl").read_text().splitlines()]
                    self.assertFalse(records[0]["precision_rounding_authorized"])
                    self.destination.unlink()
                    Path(str(self.destination) + ".conversion.jsonl").unlink()

    def test_absolute_rounding_does_not_accumulate_over_many_frames(self):
        path = self.fractional_fixture(count=301)
        source, header, frames = self.assert_precision_copy(path)
        self.assertEqual(Fraction(frames[-1]["output_time_seconds"]), Fraction(1001, 100))
        self.assertLessEqual(Fraction(header["precision_audit"]["max_pts_deviation_seconds"]), Fraction(1, 2000))

    def test_fractional_precision_across_codecs_containers_and_center_chroma(self):
        source = self.fractional_fixture()
        for extension in ("mov", "m4v"):
            path = Path(self.out.name) / f"remux.{extension}"
            ffmpeg(["-i", source, "-c:v", "copy", path])
            with self.subTest(container=extension):
                self.assert_precision_copy(path)
        for codec in ("mpeg4", "libx265"):
            path = Path(self.out.name) / f"{codec}.mp4"
            extra = ["-x265-params", "pools=1:frame-threads=1"] if codec == "libx265" else []
            ffmpeg(["-i", source, "-c:v", codec, *extra, "-pix_fmt", "yuv420p",
                    "-colorspace", "bt709", "-color_range", "tv",
                    "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-chroma_sample_location", "left", path])
            with self.subTest(codec=codec):
                self.assert_precision_copy(path)
        path = Path(self.out.name) / "fractional-center.mp4"
        ffmpeg(["-i", source, "-c:v", "copy", "-bsf:v",
                "h264_metadata=chroma_sample_loc_type=1", path])
        _, header, _ = self.assert_precision_copy(path)
        self.assertEqual(header["color"]["effective"]["chroma_location"], "center")

    def test_fractional_vfr_nonzero_times(self):
        path = self.fractional_fixture(expression=(
            "settb=1/30000,setpts='60017+N*1001+if(gte(N,6),3000,0)'"))
        source, header, frames = self.assert_precision_copy(path)
        self.assertGreater(len(set(b - a for a, b in zip(source.times, source.times[1:]))), 1)
        self.assertGreater(Fraction(frames[0]["output_time_seconds"]), 1)

    def test_dialog_fits_available_height_and_keeps_permissions_scrollable(self):
        source = prepare_source(self.fractional_fixture(), allow_precision_rounding=True)
        screen = Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 1000, 600)
        with patch.object(WorkingCopyDialog, "_prepare"), \
                patch.object(WorkingCopyDialog, "screen", return_value=screen):
            dialog = WorkingCopyDialog(source.path)
            self.addCleanup(dialog.close)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            dialog.worker = Mock(result=source, error=None, cancelled=False)
            dialog._finished()
            dialog.show()
            for _ in range(3):
                self.app.processEvents()
            self.assertGreaterEqual(dialog.width(), 700)
            self.assertLessEqual(dialog.height(), 560)
            self.assertGreater(dialog.options_scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(dialog.options_scroll.horizontalScrollBar().maximum(), 0)
            dialog.options_scroll.ensureWidgetVisible(dialog.precision_permission)
            self.app.processEvents()
            self.assertTrue(dialog.precision_permission.isVisible())
            self.assertFalse(dialog.create.isEnabled())
            dialog.precision_permission.setChecked(True)
            self.assertTrue(dialog.create.isEnabled())
            self.assertLessEqual(dialog.create.mapTo(dialog, QPoint()).y() + dialog.create.height(),
                                 dialog.height())

    def test_precision_requires_separate_permission_and_dialog_worker_wires_it(self):
        path = self.fractional_fixture()
        with self.assertRaisesRegex(WorkingCopyError, "rounding timing"):
            prepare_source(path)
        source = prepare_source(path, allow_precision_rounding=True)
        with self.assertRaisesRegex(WorkingCopyError, "Explicit authorization.*rounding"):
            create_working_copy(source, self.destination)
        self.assertFalse(self.destination.exists())
        worker = WorkingCopyWorker(source, destination=self.destination, authorize_precision_rounding=True)
        worker.run()
        self.assertIsNone(worker.error)
        self.assertEqual(worker.result, str(self.destination))
        dialog = WorkingCopyDialog(str(path))
        self.addCleanup(dialog.close)
        states = []
        def ready():
            if dialog.source is not None:
                states.append((dialog.precision_permission.isVisible(),
                               dialog.precision_permission.isChecked(), dialog.create.isEnabled()))
                dialog.precision_permission.setChecked(True)
                states.append(dialog.create.isEnabled())
                dialog.reject()
        timer = QTimer()
        timer.timeout.connect(ready)
        timer.start(5)
        QTimer.singleShot(5000, dialog.reject)
        dialog.exec()
        timer.stop()
        self.assertIsNotNone(dialog.source)
        self.assertTrue(dialog.precision_permission.isChecked())
        self.assertEqual(states, [(True, False, False), True])

    def test_precision_rejects_collisions_zero_durations_and_excess_drift(self):
        from working_copy import precision_plan, audit_precision, millisecond
        source = prepare_source(self.h264)
        with self.assertRaisesRegex(WorkingCopyError, "collision"):
            precision_plan(replace(source, times=(Fraction(0), Fraction(1, 10000))))
        tiny = replace(source, frames=source.frames[:1], times=(Fraction(0),),
                       durations=(Fraction(1, 10000),))
        self.assertEqual(precision_plan(tiny), (Fraction(0),))
        audit = audit_precision(tiny, [{"pts": 0, "duration": 1}], Fraction(1, 1000), Fraction(1, 1000), True)
        self.assertEqual(audit["durations_raised_to_one_millisecond"][0]["deviation_seconds"], "9/10000")
        for output in (0, 2):
            with self.subTest(output_duration=output), self.assertRaisesRegex(
                    WorkingCopyError, "Every source frame|beyond policy"):
                audit_precision(tiny, [{"pts": 0, "duration": output}], Fraction(1, 1000),
                                Fraction(output, 1000), True)
        with self.assertRaisesRegex(WorkingCopyError, "one-millisecond policy"):
            precision_plan(replace(tiny, durations=(Fraction(0),)))
        self.assertEqual(millisecond(Fraction(1, 2000)), Fraction(1, 1000))
        self.assertEqual(millisecond(Fraction(3, 2000)), Fraction(2, 1000))
        # Build rational output metadata in the output millisecond time base.
        expected = [{"pts": int(t * 1000), "duration": int(d * 1000)}
                    for t, d in zip(source.times, source.durations)]
        endpoint = source.times[-1] + source.durations[-1]
        bad = deepcopy(expected)
        bad[2]["pts"] += 2
        with self.assertRaisesRegex(WorkingCopyError, "beyond policy"):
            audit_precision(source, bad, Fraction(1, 1000), endpoint, True)
        bad = deepcopy(expected)
        bad[2]["duration"] += 2
        with self.assertRaisesRegex(WorkingCopyError, "beyond policy"):
            audit_precision(source, bad, Fraction(1, 1000), endpoint, True)
        with self.assertRaisesRegex(WorkingCopyError, "end time"):
            audit_precision(source, expected, Fraction(1, 1000), endpoint + Fraction(2, 1000), True)
        tied = replace(source, frames=source.frames[:2],
                       times=(Fraction(1, 3000), Fraction(1, 3000)),
                       durations=(Fraction(1, 50), Fraction(1, 50)))
        self.assertEqual(precision_plan(tied), (Fraction(0), Fraction(0)))
        audit_precision(tied, [{"pts": 0, "duration": 20}] * 2,
                        Fraction(1, 1000), Fraction(1, 50), True)
        span = replace(source, frames=source.frames[:2],
                       times=(Fraction(49, 100000), Fraction(1051, 100000)),
                       durations=(Fraction(1051, 100000), Fraction(1051, 100000)))
        with self.assertRaisesRegex(WorkingCopyError, "presentation span"):
            audit_precision(span, [{"pts": 0, "duration": 11}, {"pts": 11, "duration": 11}],
                            Fraction(1, 1000), Fraction(22, 1000), True)
        half = replace(source, frames=source.frames[:1], times=(Fraction(0),),
                       durations=(Fraction(1, 2000),))
        with self.assertRaisesRegex(WorkingCopyError, "end time"):
            audit_precision(half, [{"pts": 0, "duration": 1}], Fraction(1, 1000), Fraction(0), True)

    def test_high_rate_new_collisions_refused_in_preflight(self):
        path = self.fractional_fixture(rate="2000")
        with self.assertRaisesRegex(WorkingCopyError, "collision"):
            prepare_source(path, allow_precision_rounding=True)
        self.assertFalse(self.destination.exists())

    def test_precision_verification_failure_and_cancel_remove_owned_files(self):
        from working_copy import conversion_command
        source = prepare_source(self.fractional_fixture(), allow_precision_rounding=True)
        def shifted(*args):
            command = conversion_command(*args)
            command[command.index("-vf") + 1] += ",setpts=PTS+0.002/TB"
            return command
        with patch("working_copy.conversion_command", side_effect=shifted):
            with self.assertRaisesRegex(WorkingCopyError, "beyond policy"):
                create_working_copy(source, self.destination, authorize_precision_rounding=True)
        self.assertFalse(self.destination.exists())
        event = threading.Event()
        with self.assertRaises(ExportCancelled):
            create_working_copy(source, self.destination, authorize_precision_rounding=True,
                                cancelled=event.is_set,
                                progress=lambda percent, text: event.set() if percent >= 45 else None)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(Path(self.out.name).glob(".osvev-working-*")), [])

    def reversal_mp4(self):
        """H.264 MP4 whose container PTS swap decode positions 6/7 and 14/15 (two local reversals)."""
        base = Path(self.out.name) / "reversal-base.mp4"
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=4",
                "-c:v", "libx264", "-bf", "0", "-g", "10", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_range", "tv", "-color_primaries", "bt709",
                "-color_trc", "bt709", "-chroma_sample_location", "left", "-bsf:v",
                "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0",
                base])
        path = Path(self.out.name) / "reversal.mp4"
        swap = ("if(eq(N,6),PTS+DURATION,if(eq(N,7),PTS-DURATION,"
                "if(eq(N,14),PTS+DURATION,if(eq(N,15),PTS-DURATION,PTS))))")
        ffmpeg(["-i", base, "-c", "copy", "-bsf:v", f"setts=pts='{swap}':dts='DTS-DURATION'", path])
        return path

    def test_mp4_pts_reversals_convert_in_original_pts_order_with_verified_mapping(self):
        path = self.reversal_mp4()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        source = prepare_source(path)
        expected_order = (0, 1, 2, 3, 4, 5, 7, 6, 8, 9, 10, 11, 12, 13, 15, 14, 16, 17, 18, 19)
        self.assertEqual(source.order, expected_order)
        self.assertEqual(source.reorder_audit["reversal_count"], 2)
        self.assertEqual([r["source_frames"] for r in source.reorder_audit["reversals"]], [[7, 8], [15, 16]])
        self.assertEqual(list(source.times), sorted(source.times))
        with self.assertRaisesRegex(WorkingCopyError, "Explicit authorization is required: 2 presentation-time"):
            create_working_copy(source, self.destination, choices=ColorChoices())
        self.assertFalse(self.destination.exists())
        create_working_copy(source, self.destination, choices=ColorChoices(), authorize_pts_reordering=True)
        with av.open(str(self.destination)) as container:
            stream = container.streams.video[0]
            frames = list(container.decode(stream))
        self.assertEqual(len(frames), 20)
        self.assertEqual([frame.pts * frame.time_base for frame in frames], list(source.times))
        self.assertTrue(all(frame.duration * frame.time_base == Fraction(1, 5) for frame in frames))
        lines = [json.loads(line) for line in
                 Path(str(self.destination) + ".conversion.jsonl").read_text().splitlines()]
        header, records = lines[0], [line for line in lines if "output_frame" in line]
        policy = header["frame_order_policy"]
        self.assertTrue(policy["pts_reordering_authorized"])
        self.assertEqual(policy["moved_decode_indices"], [6, 14])
        self.assertEqual([(m["source_frame"], m["output_frame"]) for m in policy["moved_frames"]],
                         [(7, 8), (15, 16)])
        discrepancy = header["discrepancies"][-1]
        self.assertIn("PTS order", discrepancy["kind"])
        self.assertEqual(discrepancy["frames_added_duplicated_or_removed"], 0)
        self.assertFalse(discrepancy["timestamps_normalized"])
        self.assertEqual([(r["source_frame"], r["output_frame"]) for r in records],
                         [(index + 1, position + 1) for position, index in enumerate(expected_order)])
        for record, frame in zip(records, frames):
            self.assertEqual(record["independent_native_pts"], record["original_pts"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_pts_reordering_refuses_ambiguous_or_unverifiable_cases(self):
        from working_copy import conversion_command, reordering_graph, scan_source
        path = self.reversal_mp4()
        data, streams = scan_source(path, lambda: False, lambda *args: None)
        step = data["frames"][1]["pts"] - data["frames"][0]["pts"]
        cases = {
            "duplicate presentation times": lambda frames: frames[8].update(pts=frames[9]["pts"]),
            "would move 19 positions": lambda frames: frames[0].update(pts=frames[19]["pts"] + step),
            "displaced frames are not themselves": lambda frames: [
                frames[i].update(pts=frames[5]["pts"] + (4 - i) * step // 4) for i in (1, 2, 3)],
            "Negative or decreasing": lambda frames: frames[16].update(
                pts=None, best_effort_timestamp=frames[16]["pts"]),
        }
        for message, damage in cases.items():
            candidate = deepcopy(data)
            if message == "Negative or decreasing":
                for frame in candidate["frames"]:
                    frame["best_effort_timestamp"] = frame["pts"]
            damage(candidate["frames"])
            with self.subTest(message), patch("working_copy.scan_source", return_value=(candidate, streams)):
                with self.assertRaisesRegex(WorkingCopyError, message):
                    prepare_source(path)
        source = prepare_source(path)
        with self.assertRaisesRegex(WorkingCopyError, "coarser than one microsecond"):
            reordering_graph(replace(source, time_base=Fraction(1, 10_000_000)))
        def decoder_order(*args):
            command = conversion_command(*args)
            graph = command[command.index("-filter_complex") + 1]
            command[command.index("-filter_complex") + 1] = (
                "[0:v:0]setpts=N/(5*TB)" + graph[graph.index(",settb="):])
            return command
        with patch("working_copy.conversion_command", side_effect=decoder_order):
            with self.assertRaisesRegex(WorkingCopyError, "independent|Independent|Frame"):
                create_working_copy(source, self.destination, choices=ColorChoices(),
                                    authorize_pts_reordering=True)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(Path(self.out.name).glob(".osvev-working-*")), [])

    def test_pts_reordering_dialog_requires_permission_and_worker_forwards_it(self):
        path = self.reversal_mp4()
        source = prepare_source(path)
        worker = WorkingCopyWorker(source, destination=str(self.destination), choices=ColorChoices(),
                                   authorize_pts_reordering=True)
        worker.start()
        self.assertTrue(worker.wait(20000))
        self.assertIsNone(worker.error)
        dialog = WorkingCopyDialog(str(path))
        self.addCleanup(dialog.close)
        timer = QTimer()
        observed = []
        def ready():
            if dialog.source is not None:
                timer.stop()
                observed.extend([dialog.reorder_permission.isChecked(), dialog.create.isEnabled()])
                self.assertIn("2 local presentation-time reversal", dialog.reorder_note.text())
                dialog.reorder_permission.setChecked(True)
                observed.append(dialog.create.isEnabled())
                dialog.reject()
        timer.timeout.connect(ready)
        timer.start(5)
        self.addCleanup(timer.stop)
        dialog.exec()
        self.assertEqual(observed, [False, False, True])

    def test_sub_half_millisecond_duration_rounds_up_to_one_millisecond_and_is_verified(self):
        from working_copy import conversion_command, interpretation
        base = self.fractional_fixture()
        path = Path(self.out.name) / "tiny-duration.mp4"
        ffmpeg(["-i", base, "-c", "copy", "-video_track_timescale", "90000", "-bsf:v",
                "setts=pts=PTS+3*DURATION:dts='DTS+if(eq(N,8),DURATION-2,0)'", path])
        source = prepare_source(path, allow_precision_rounding=True)
        self.assertEqual(source.durations[8], Fraction(1, 15000))
        with self.assertRaisesRegex(WorkingCopyError, "Explicit authorization.*rounding"):
            create_working_copy(source, self.destination, choices=ColorChoices())
        _, header, frames = self.assert_precision_copy(path)
        raised = header["precision_audit"]["durations_raised_to_one_millisecond"]
        self.assertEqual(raised, [{"frame": 9, "original_duration_seconds": "1/15000",
                                   "output_duration_seconds": "1/1000", "deviation_seconds": "7/7500"}])
        self.assertEqual(Fraction(frames[8]["output_duration_seconds"]), Fraction(1, 1000))
        self.assertEqual(Fraction(frames[8]["duration_deviation_seconds"]), Fraction(7, 7500))
        self.assertEqual(Fraction(frames[8]["output_time_seconds"]), Fraction(367, 1000))
        self.assertEqual(Fraction(frames[9]["output_time_seconds"]), Fraction(400, 1000))
        def without_fix(*args):
            command = conversion_command(*args)
            del command[command.index("-bsf:v"):command.index("-bsf:v") + 2]
            return command
        with patch("working_copy.conversion_command", side_effect=without_fix):
            with self.assertRaisesRegex(WorkingCopyError, "Frame 9: .*beyond policy"):
                create_working_copy(source, self.destination, choices=ColorChoices(),
                                    authorize_precision_rounding=True)
        self.assertFalse(self.destination.exists())
        ordinary = prepare_source(base, allow_precision_rounding=True)
        self.assertNotIn("-bsf:v", conversion_command(ordinary, self.destination,
                                                      interpretation(ordinary, ColorChoices())))

    def nest_like_alternating_durations_mp4(self):
        """7.5 fps MP4 with 133/133/134 ms durations, offset 0.4 ms so millisecond rounding is needed."""
        base = self.fractional_fixture(rate="15/2")
        rescaled = Path(self.out.name) / "alternating-base.mp4"
        ffmpeg(["-i", base, "-c", "copy", "-video_track_timescale", "90000", rescaled])
        path = Path(self.out.name) / "alternating-durations.mp4"
        stamp = "(36+(266+133*N+floor(N/3))*90)"
        ffmpeg(["-i", rescaled, "-c", "copy", "-video_track_timescale", "90000", "-bsf:v",
                f"setts=pts='{stamp}':dts='{stamp}':duration='if(eq(mod(N,3),2),12060,11970)'", path])
        return path

    def test_matroska_default_duration_elision_within_one_millisecond_is_verified_and_logged(self):
        path = self.nest_like_alternating_durations_mp4()
        source = prepare_source(path, allow_precision_rounding=True)
        self.assertEqual({d * 1000 for d in source.durations}, {133, 134})
        with self.assertRaisesRegex(WorkingCopyError, "Explicit authorization.*rounding"):
            create_working_copy(source, self.destination, choices=ColorChoices())
        _, header, frames = self.assert_precision_copy(path)
        policy = header["precision_audit"]["matroska_default_duration"]
        self.assertEqual(policy["output_frame_rate"], "15/2")
        self.assertEqual(policy["elided_millisecond_durations_seconds"], ["133/1000", "67/500"])
        reported = policy["frames_reported_as_default_duration"]
        self.assertEqual([item["frame"] for item in reported], [3, 6, 9, 12])
        self.assertTrue(all(item["deviation_seconds"] == "-1/1000" for item in reported))
        self.assertEqual(Fraction(frames[2]["duration_seconds"]), Fraction(134, 1000))
        self.assertEqual(Fraction(frames[2]["output_duration_seconds"]), Fraction(133, 1000))
        self.assertEqual(Fraction(frames[2]["duration_deviation_seconds"]), Fraction(-1, 1000))
        self.assertEqual(Fraction(frames[3]["output_time_seconds"]), Fraction(666, 1000))

    def test_default_duration_allowance_is_limited_to_muxer_elision(self):
        from working_copy import audit_precision, default_duration_bounds
        self.assertEqual(default_duration_bounds(Fraction(15, 2)), {Fraction(133, 1000), Fraction(134, 1000)})
        self.assertEqual(default_duration_bounds(Fraction(25)), {Fraction(40, 1000)})
        self.assertEqual(default_duration_bounds(None), set())
        source = prepare_source(self.h264)
        pair = replace(source, frames=source.frames[:2],
                       times=(Fraction(2664, 10000), Fraction(4004, 10000)),
                       durations=(Fraction(134, 1000), Fraction(133, 1000)))
        output = [{"pts": 266, "duration": 133}, {"pts": 400, "duration": 133}]
        audit = audit_precision(pair, output, Fraction(1, 1000), Fraction(533, 1000), True,
                                output_rate=Fraction(15, 2))
        self.assertEqual(audit["matroska_default_duration"]["frames_reported_as_default_duration"][0]["frame"], 1)
        for rate in (None, Fraction(25), Fraction(30000, 1001)):
            with self.subTest(rate=rate), self.assertRaisesRegex(WorkingCopyError, "Frame 1: .*beyond policy"):
                audit_precision(pair, output, Fraction(1, 1000), Fraction(533, 1000), True, output_rate=rate)
        exact = replace(pair, times=(Fraction(266, 1000), Fraction(400, 1000)))
        with self.assertRaisesRegex(WorkingCopyError, "Frame 1: .*beyond policy"):
            audit_precision(exact, output, Fraction(1, 1000), Fraction(533, 1000), False,
                            output_rate=Fraction(15, 2))
        beyond = replace(pair, durations=(Fraction(1345, 10000), Fraction(133, 1000)))
        with self.assertRaisesRegex(WorkingCopyError, "Frame 1: .*beyond policy"):
            audit_precision(beyond, output, Fraction(1, 1000), Fraction(533, 1000), True,
                            output_rate=Fraction(15, 2))
