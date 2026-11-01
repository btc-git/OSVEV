# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Event, get_ident
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from color_interpretation import ColorChoices
from frame_info import FrameInfoHandler
from frame_processor import FrameProcessor
from pyav_input import PyAVInputCapture
from scrub_preview import PreviewSource, ScrubPreview
from test_timeline_slider import move_slider


SETTINGS = dict(brightness=10, contrast=15, sharpness=10, compression=0,
                crop_top=2, crop_bottom=4, crop_left=6, crop_right=2)


def wait_for(predicate, timeout=10):
    end = time.perf_counter() + timeout
    while not predicate() and time.perf_counter() < end:
        QTest.qWait(5)
    if not predicate():
        raise AssertionError("Timed out waiting for preview")


class ScrubPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory(prefix="osvev-scrub-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.sources = {}
        for name, size in (("small", "96x64"), ("4k", "3840x2160")):
            path = Path(cls.temp.name) / (name + ".mp4")
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                f"testsrc2=size={size}:rate=12:duration=1.5",
                "-vf", "setpts=PTS+2/TB", "-c:v", "libx264", "-preset", "ultrafast",
                "-threads", "1", "-g", "12", "-bf", "2", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_range", "tv",
                "-color_primaries", "bt709", "-color_trc", "bt709",
                "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
                "matrix_coefficients=1:video_full_range_flag=0", str(path),
            ], check=True, capture_output=True, timeout=90)
            cls.sources[name] = PreviewSource(
                str(path), "pyav", FrameInfoHandler.get_frame_metadata(str(path)))
        path = Path(cls.temp.name) / "untagged.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=96x64:rate=12:duration=1.5",
            "-c:v", "libx264", "-g", "12", "-bf", "2", "-pix_fmt", "yuv420p", str(path),
        ], check=True, capture_output=True, timeout=30)
        cls.sources["untagged"] = PreviewSource(
            str(path), "pyav", FrameInfoHandler.get_frame_metadata(str(path)),
            color_choices=ColorChoices(matrix="bt709", range_name="pc"),
        )

    def setUp(self):
        self.preview = ScrubPreview()
        self.results = []
        self.errors = []
        self.preview.ready.connect(self.results.append)
        self.preview.failed.connect(self.errors.append)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.preview.cancel()
        wait_for(lambda: self.preview._task is None)
        self.assertEqual(self.errors, [])

    def test_slow_drag_stationary_hold_and_exact_processed_native_pixels(self):
        source = self.sources["small"]
        self.preview.begin(source)
        reference = PyAVInputCapture(source.path)
        self.addCleanup(reference.release)
        reference.read()
        reference.configure_index(source.metadata)
        processor = FrameProcessor()
        self.addCleanup(processor.close)
        for index in (1, 7, 3, 17, 0):
            self.preview.request(index, SETTINGS)
            wait_for(lambda: self.results and self.results[-1].index == index)
            result = self.results[-1]
            self.assertTrue(reference.set(cv2.CAP_PROP_POS_FRAMES, index))
            ok, frame = reference.read()
            self.assertTrue(ok)
            np.testing.assert_array_equal(result.frame, frame)
            np.testing.assert_array_equal(result.processed, processor.process(frame, **SETTINGS))
            self.assertEqual(result.native.frame.pts, source.metadata[index]["pts"])
        count = len(self.results)
        QTest.qWait(200)
        self.assertEqual(len(self.results), count)  # No re-decoding stationary frames.

    def test_fast_direction_changes_coalesce_without_starving_live_previews(self):
        entered, release = Event(), Event()
        original = FrameProcessor.process
        calls = []
        gui_thread = get_ident()

        def slow(processor, frame, **settings):
            calls.append(get_ident())
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test gate timed out")
            return original(processor, frame, **settings)

        with patch.object(FrameProcessor, "process", slow):
            self.preview.begin(self.sources["small"])
            self.preview.request(1, SETTINGS)
            wait_for(entered.is_set)
            for index in [15, 2, 16, 3, 14, 4] * 20 + [9]:
                self.preview.request(index, SETTINGS)
            self.assertEqual(self.preview._task._pending[1], 9)
            release.set()
            wait_for(lambda: self.results and self.results[-1].index == 9)
            self.assertEqual([result.index for result in self.results], [1, 9])
            self.assertEqual(len(calls), 2)
            self.assertNotIn(gui_thread, calls)

    def test_cancel_and_source_change_reject_old_results_and_cleanup(self):
        source = self.sources["small"]
        entered, release = Event(), Event()
        original = FrameProcessor.process
        calls = []

        def blocked(processor, frame, **settings):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test gate timed out")
            return original(processor, frame, **settings)

        with patch.object(FrameProcessor, "process", blocked):
            self.preview.begin(source)
            self.preview.request(3, SETTINGS)
            wait_for(entered.is_set)
            self.preview.cancel()
            self.preview.begin(source)
            self.preview.request(11, SETTINGS)
            release.set()
            wait_for(lambda: bool(self.results))
            self.assertEqual([result.index for result in self.results], [11])
        self.preview.cancel()
        wait_for(lambda: self.preview._task is None)

    def test_partial_index_and_authorized_color_interpretation(self):
        source = self.sources["untagged"]
        metadata = source.metadata[4:12]
        self.preview.begin(PreviewSource(
            source.path, "pyav", metadata, partial=True,
            color_choices=source.color_choices,
        ))
        self.preview.request(5, SETTINGS)
        wait_for(lambda: bool(self.results))
        self.assertEqual(self.results[-1].native.frame.pts, metadata[5]["pts"])
        reference = PyAVInputCapture(
            source.path, color_choices=source.color_choices)
        self.addCleanup(reference.release)
        reference.read()
        reference.configure_index(metadata, partial=True)
        reference.set(cv2.CAP_PROP_POS_FRAMES, 5)
        np.testing.assert_array_equal(self.results[-1].frame, reference.read()[1])

    def test_adaptive_rate_leaves_headroom_for_slow_processing(self):
        original = FrameProcessor.process
        starts = []

        def delayed(processor, frame, **settings):
            starts.append(time.perf_counter())
            time.sleep(.16)
            return original(processor, frame, **settings)

        with patch.object(FrameProcessor, "process", delayed):
            self.preview.begin(self.sources["small"])
            for index in (1, 2, 3):
                self.preview.request(index, SETTINGS)
                wait_for(lambda: self.results and self.results[-1].index == index)
        self.assertTrue(all(gap >= .19 for gap in np.diff(starts)))

    def test_continuous_fast_motion_keeps_presenting_and_never_replays_older_results(self):
        original = FrameProcessor.process
        requested = []

        def delayed(processor, frame, **settings):
            time.sleep(.08)
            return original(processor, frame, **settings)

        def move():
            index = (len(requested) * 7) % 17
            requested.append(index)
            self.preview.request(index, SETTINGS)

        timer = QTimer()
        timer.setInterval(15)
        timer.timeout.connect(move)
        self.addCleanup(timer.stop)
        with patch.object(FrameProcessor, "process", delayed):
            self.preview.begin(self.sources["small"])
            move()
            timer.start()
            QTest.qWait(700)
            timer.stop()
            self.assertGreaterEqual(len(self.results), 3)
            self.assertLess(len(self.results), len(requested) // 2)
            self.preview.request(17, SETTINGS)
            wait_for(lambda: self.preview._last_presented == self.preview._sequence)
        self.assertEqual(self.results[-1].index, 17)
        count = len(self.results)
        self.preview._ready(
            self.preview._generation, self.preview._last_presented - 1, self.results[0])
        self.assertEqual(len(self.results), count)

    def test_queued_result_is_rejected_after_cancel(self):
        self.preview.begin(self.sources["small"])
        self.preview.request(3, SETTINGS)
        task = self.preview._task
        end = time.perf_counter() + 5
        while not task._awaiting and time.perf_counter() < end:
            time.sleep(.005)  # Deliberately hold Qt delivery until cancellation.
        self.assertTrue(task._awaiting)
        self.preview.cancel()
        wait_for(lambda: self.preview._task is None)
        self.assertEqual(self.results, [])

    def test_preview_failure_is_explicit_and_resources_are_released(self):
        source = self.sources["small"]
        with patch.object(FrameProcessor, "process", side_effect=RuntimeError("Preview failure")):
            self.preview.begin(source)
            self.preview.request(3, SETTINGS)
            wait_for(lambda: bool(self.errors))
            wait_for(lambda: self.preview._task is None)
        self.assertEqual(self.errors, ["Preview failure"])
        self.errors.clear()

    def test_actual_osvev_ffv1_export_keeps_exact_rgb_native_samples(self):
        from export_runner import execute_export
        from ffmpeg_export import build_export_plan
        source = self.sources["small"]
        output = Path(self.temp.name) / "scrub-export.mkv"
        plan = build_export_plan(
            source.path, str(output), "ffv1", 0, 5, {}, source.metadata, "pyav")
        self.assertEqual(execute_export(plan, source.metadata)["status"], "verified_lossless")
        metadata = FrameInfoHandler.get_frame_metadata(str(output))
        self.preview.begin(PreviewSource(str(output), "pyav", metadata))
        self.preview.request(4, SETTINGS)
        wait_for(lambda: bool(self.results))
        result = self.results[-1]
        reference = PyAVInputCapture(str(output))
        self.addCleanup(reference.release)
        reference.read()
        reference.configure_index(metadata)
        reference.set(cv2.CAP_PROP_POS_FRAMES, 4)
        np.testing.assert_array_equal(result.frame, reference.read()[1])
        self.assertEqual(result.native.packed_bytes(), reference.native_samples.packed_bytes())
        self.assertEqual(result.native.matrix, "rgb")

    def viewer(self, name="small"):
        from osvev_app import OSVEV
        self.enterContext(patch.object(OSVEV, "_check_dependencies"))
        self.enterContext(patch.object(OSVEV, "set_windows_taskbar_icon"))
        warning = self.enterContext(patch.object(QMessageBox, "warning"))
        viewer = OSVEV()

        def cleanup():
            viewer.close()
            wait_for(lambda: viewer._scrub_preview._task is None)
            warning.assert_not_called()

        self.addCleanup(cleanup)
        viewer.show()
        viewer._load_video(self.sources[name].path)
        self.app.processEvents()
        return viewer

    @staticmethod
    def point(viewer, fraction):
        slider = viewer.timeline_slider
        _, handle, groove = slider._geometry()
        point = groove.center()
        point.setX(round(groove.x() + handle.width() / 2
                         + fraction * (groove.width() - handle.width())))
        return point

    def test_integrated_slow_drag_hold_and_release_force_main_decoder_seek(self):
        viewer = self.viewer()
        slider = viewer.timeline_slider
        reads = []
        original_read = PyAVInputCapture.read

        def traced_read(capture):
            reads.append((capture is viewer.cap, get_ident()))
            return original_read(capture)

        QTest.mousePress(slider, Qt.MouseButton.LeftButton,
                         pos=slider._geometry()[1].center())
        with patch.object(viewer._audio, "pause") as audio_pause, \
                patch.object(viewer, "get_frame", wraps=viewer.get_frame) as get, \
                patch.object(PyAVInputCapture, "read", traced_read):
            for fraction in (.3, .7, .4):
                move_slider(slider, self.point(viewer, fraction))
                selected = slider.sliderPosition()
                wait_for(lambda: viewer.current_frame_index == selected)
                self.assertTrue(viewer._scrub_has_preview)
                self.assertEqual(viewer._last_decoded_index, -1)
                self.assertEqual(viewer.current_native_samples.frame.pts,
                                 viewer.frame_metadata[selected]["pts"])
                from video_summary import clock_text
                pts = viewer.frame_metadata[selected]["pts"]
                start = viewer.frame_metadata[0]["pts"]
                expected_time = clock_text(
                    (pts - start) * viewer.current_native_samples.frame.time_base)
                self.assertIn(f"Time: {expected_time} /", viewer.video_time_label.text())
                self.assertEqual(slider.sliderPosition(), selected)
                get.assert_not_called()
            audio_pause.assert_not_called()  # Already paused on press.
            final = viewer.current_frame.copy()
            processed = viewer._get_processed_frame().copy()
            selected = slider.sliderPosition()
            QTest.qWait(170)
            self.assertEqual(viewer.current_frame_index, selected)
            QTest.mouseRelease(slider, Qt.MouseButton.LeftButton,
                               pos=self.point(viewer, .4))
            get.assert_called_once_with(selected)
            self.assertEqual(viewer._last_decoded_index, selected)
            np.testing.assert_array_equal(viewer.current_frame, final)
            np.testing.assert_array_equal(viewer._get_processed_frame(), processed)
        self.assertFalse(viewer.is_playing)
        self.assertTrue(any(not main for main, _ in reads))
        for main, thread in reads:
            self.assertEqual(thread == get_ident(), main)

    def test_integrated_release_discards_blocked_preview_and_source_reload_is_safe(self):
        viewer = self.viewer()
        slider = viewer.timeline_slider
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        original = FrameProcessor.process
        gui_thread = get_ident()

        def blocked(processor, frame, **settings):
            if get_ident() != gui_thread and not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test gate timed out")
            return original(processor, frame, **settings)

        with patch.object(FrameProcessor, "process", blocked):
            QTest.mousePress(slider, Qt.MouseButton.LeftButton,
                             pos=slider._geometry()[1].center())
            move_slider(slider, self.point(viewer, .7))
            wait_for(entered.is_set)
            move_slider(slider, self.point(viewer, .2))
            QTest.mouseRelease(slider, Qt.MouseButton.LeftButton,
                               pos=self.point(viewer, .2))
            selected = slider.value()
            final = viewer.current_frame.copy()
            self.assertEqual(viewer.current_frame_index, selected)
            release.set()
            wait_for(lambda: viewer._scrub_preview._task is None)
            np.testing.assert_array_equal(viewer.current_frame, final)
            self.assertEqual(viewer.current_frame_index, selected)
        QTest.mousePress(slider, Qt.MouseButton.LeftButton,
                         pos=slider._geometry()[1].center())
        move_slider(slider, self.point(viewer, .8))
        generation = viewer._scrub_preview._generation
        viewer._load_video(self.sources["small"].path, (2.3, 2.8))
        wait_for(lambda: viewer._scrub_preview._task is None)
        self.assertGreater(viewer._scrub_preview._generation, generation)
        self.assertEqual(viewer.current_frame_index, 0)
        self.assertIsNotNone(viewer.source_range)
        self.assertEqual(viewer.current_native_samples.frame.pts, viewer.frame_metadata[0]["pts"])

    def test_processing_change_during_stationary_hold_rejects_old_settings(self):
        viewer = self.viewer()
        slider = viewer.timeline_slider
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        original = FrameProcessor.process
        gui_thread = get_ident()

        def blocked(processor, frame, **settings):
            if get_ident() != gui_thread and not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test gate timed out")
            return original(processor, frame, **settings)

        results = []
        viewer._scrub_preview.ready.connect(results.append)
        with patch.object(FrameProcessor, "process", blocked):
            QTest.mousePress(slider, Qt.MouseButton.LeftButton,
                             pos=slider._geometry()[1].center())
            move_slider(slider, self.point(viewer, .6))
            selected = slider.sliderPosition()
            wait_for(entered.is_set)
            viewer.brightness_slider.setValue(25)
            release.set()
            wait_for(lambda: viewer.current_frame_index == selected)
        self.assertEqual([result.settings["brightness"] for result in results], [25])
        processor = FrameProcessor()
        self.addCleanup(processor.close)
        expected = processor.process(viewer.current_frame, brightness=25)
        np.testing.assert_array_equal(viewer._get_processed_frame(), expected)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=self.point(viewer, .6))

    def test_4k_integrated_preview_uses_processed_cache_and_keeps_mouse_responsive(self):
        viewer = self.viewer("4k")
        viewer.brightness_slider.setValue(10)
        viewer.contrast_slider.setValue(15)
        viewer.sharpness_slider.setValue(10)
        viewer.crop_spins["crop_top"].setValue(2)
        slider = viewer.timeline_slider
        beats = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: beats.append(time.perf_counter()))
        self.addCleanup(timer.stop)
        QTest.mousePress(slider, Qt.MouseButton.LeftButton,
                         pos=slider._geometry()[1].center())
        timer.start()
        latencies = []
        with patch.object(viewer.frame_processor, "process",
                          side_effect=AssertionError("Preview processed on GUI thread")):
            for movements in ((.4,), tuple([.8, .1, .7, .3] * 20 + [.9]), (.2,)):
                started = time.perf_counter()
                for fraction in movements:
                    mouse_started = time.perf_counter()
                    move_slider(slider, self.point(viewer, fraction))
                    self.assertLess(time.perf_counter() - mouse_started, .1)
                selected = slider.sliderPosition()
                self.assertEqual(slider.value(), selected)
                wait_for(lambda: viewer.current_frame_index == selected)
                latencies.append(time.perf_counter() - started)
        timer.stop()
        gaps = np.diff(beats)
        self.assertGreater(len(beats), 5)
        self.assertLess(max(gaps), .25)
        self.assertLess(max(latencies), 3)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=self.point(viewer, .2))
        print("4K integrated scrub benchmark: request-to-view ms",
              [round(value * 1000, 1) for value in latencies],
              "max GUI heartbeat gap ms", round(max(gaps) * 1000, 1))

    def test_4k_isolated_benchmark_keeps_gui_heartbeat_and_newest_target(self):
        source = self.sources["4k"]
        beats = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: beats.append(time.perf_counter()))
        timer.start()
        self.addCleanup(timer.stop)
        self.preview.begin(source)
        latencies = []
        for index in (5, 17, 2, 15):
            started = time.perf_counter()
            self.preview.request(index, SETTINGS)
            wait_for(lambda: self.results and self.results[-1].index == index, timeout=15)
            latencies.append(time.perf_counter() - started)
        timer.stop()
        gaps = np.diff(beats)
        self.assertGreater(len(beats), 5)
        self.assertLess(max(gaps), .25)
        self.assertLess(max(latencies), 3)
        self.assertEqual(self.results[-1].frame.shape, (2160, 3840, 3))
        print("4K isolated scrub benchmark: request-to-preview ms",
              [round(value * 1000, 1) for value in latencies],
              "max GUI heartbeat gap ms", round(max(gaps) * 1000, 1))


if __name__ == "__main__":
    unittest.main()
