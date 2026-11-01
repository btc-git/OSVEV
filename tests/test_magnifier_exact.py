# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import os
import subprocess
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

from export_scaling import scaling_filter
from ffmpeg_tools import tool_path
from magnifier_exact import ExactMagnifier, MagnifierRequest, render_exact
from osvev_app import OSVEV


def ffmpeg_crop_scale(source, crop, zoom, method):
    x, y, width, height = crop
    filters = [f"crop=w={width}:h={height}:x={x}:y={y}:exact=1"]
    scale = scaling_filter(width, height, zoom, method)
    if scale:
        filters.append(scale)
    result = subprocess.run(
        [tool_path("ffmpeg"), "-v", "error", "-filter_threads", "1",
         "-f", "rawvideo", "-pixel_format", "bgr24",
         "-video_size", f"{source.shape[1]}x{source.shape[0]}", "-i", "pipe:0",
         "-vf", ",".join(filters), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
        input=source.tobytes(), capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return np.frombuffer(result.stdout, np.uint8).reshape(
        height * zoom, width * zoom, 3
    )


def request_for(roi, zoom=3, method="Lanczos"):
    height, width = roi.shape[:2]
    return MagnifierRequest(
        roi.copy(), zoom, method, (0, 0, width, height),
        (width * zoom, height * zoom), 1, False, False,
    )


def pixmap_bgr(label):
    image = label.pixmap().toImage().convertToFormat(QImage.Format.Format_BGR888)
    bits = image.bits()
    bits.setsize(image.sizeInBytes())
    rows = np.frombuffer(bits, np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()


class ExactPixelTests(unittest.TestCase):
    def test_all_methods_positions_and_zooms_match_standalone_ffmpeg(self):
        source = np.random.default_rng(103).integers(0, 256, (31, 41, 3), np.uint8)
        original = source.copy()
        for crop in ((0, 0, 13, 11), (7, 5, 17, 13), (22, 18, 19, 13)):
            x, y, width, height = crop
            roi = source[y:y + height, x:x + width]
            for zoom in (1, 3, 30):
                for method in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
                    with self.subTest(crop=crop, zoom=zoom, method=method):
                        actual = render_exact(request_for(roi, zoom, method))
                        expected = ffmpeg_crop_scale(source, crop, zoom, method)
                        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(source, original)

    def test_viewport_additional_zoom_and_highlights_preserve_source(self):
        roi = np.random.default_rng(18).integers(0, 256, (23, 29, 3), np.uint8)
        original = roi.copy()
        for method in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
            scaled = ffmpeg_crop_scale(roi, (0, 0, 29, 23), 3, method)
            for added, source in ((False, False), (True, False), (False, True), (True, True)):
                request = replace(
                    request_for(roi, method=method), visible=(9, 7, 7, 5),
                    viewport=(51, 37), additional_zoom=5,
                    highlight_added=added, highlight_source=source,
                )
                expected = scaled[21:36, 27:48].copy()
                mask = np.zeros(expected.shape[:2], bool)
                mask[::3, ::3] = True
                if added:
                    expected[~mask] = (0, 255, 0)
                if source:
                    expected[mask] = (0, 255, 0)
                expected = cv2.resize(expected, None, fx=5, fy=5,
                                      interpolation=cv2.INTER_NEAREST)
                x = (expected.shape[1] - 51) // 2
                y = (expected.shape[0] - 37) // 2
                np.testing.assert_array_equal(
                    render_exact(request), expected[y:y + 37, x:x + 51]
                )
        np.testing.assert_array_equal(roi, original)

    def test_large_requests_are_explicitly_refused(self):
        request = request_for(np.zeros((400, 400, 3), np.uint8), 30)
        with self.assertRaisesRegex(ValueError, "256 MiB"):
            render_exact(request)


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertTrue(predicate(), "Timed out waiting for magnifier")


class AsyncMagnifierTests(QtTests):
    def setUp(self):
        self.controller = ExactMagnifier()
        self.controller._timer.setInterval(0)
        self.results, self.errors = [], []
        self.controller.ready.connect(self.results.append)
        self.controller.failed.connect(self.errors.append)

    def tearDown(self):
        self.controller.close()
        self.wait_for(lambda: self.controller._task is None)

    def test_debounce_coalesces_changes_before_start(self):
        request = request_for(np.zeros((5, 7, 3), np.uint8))
        with patch("magnifier_exact.render_exact", return_value=request.roi) as render:
            for _ in range(10):
                self.controller.request(request)
            self.wait_for(lambda: len(self.results) == 1)
            self.assertEqual(render.call_count, 1)

    def test_slow_render_is_nonblocking_and_only_latest_replacement_is_published(self):
        entered, release = threading.Event(), threading.Event()
        request = request_for(np.zeros((5, 7, 3), np.uint8))
        calls, heartbeat = [], []

        def slow_render(item):
            calls.append(item.zoom)
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test worker was not released")
            return np.full((1, 1, 3), item.zoom, np.uint8)

        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: heartbeat.append(True))
        with patch("magnifier_exact.render_exact", side_effect=slow_render):
            try:
                timer.start()
                self.controller.request(request)
                self.wait_for(entered.is_set)
                for zoom in (4, 5, 6):
                    self.controller.request(replace(request, zoom=zoom))
                    self.app.processEvents()
                self.wait_for(lambda: len(heartbeat) >= 3)
                self.assertEqual(calls, [3])
                self.assertEqual(self.results, [])
            finally:
                release.set()
                timer.stop()
            self.wait_for(lambda: len(self.results) == 1)
            self.assertEqual(calls, [3, 6])
            self.assertEqual(int(self.results[0][0, 0, 0]), 6)

    def test_invalidated_results_and_errors_are_discarded_and_close_never_waits(self):
        for error in (False, True):
            entered, release = threading.Event(), threading.Event()

            def slow_render(item):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Test worker was not released")
                if error:
                    raise RuntimeError("Superseded render")
                return item.roi

            with patch("magnifier_exact.render_exact", side_effect=slow_render):
                try:
                    self.controller.request(request_for(np.zeros((3, 5, 3), np.uint8)))
                    self.wait_for(entered.is_set)
                    self.controller.invalidate()
                    if error:
                        self.controller.close()
                    self.assertFalse(release.is_set())
                    self.assertIsNotNone(self.controller._task)
                finally:
                    release.set()
                self.wait_for(lambda: self.controller._task is None)
            self.assertEqual(self.results, [])
            self.assertEqual(self.errors, [])

    def test_current_failure_is_reported(self):
        with patch("magnifier_exact.render_exact", side_effect=RuntimeError("Scaling failed")):
            self.controller.request(request_for(np.zeros((3, 5, 3), np.uint8)))
            self.wait_for(lambda: bool(self.errors))
        self.assertEqual(self.errors, ["Scaling failed"])

    def test_real_high_zoom_scaling_keeps_gui_timer_responsive(self):
        heartbeat = []
        timer = QTimer()
        timer.setInterval(2)
        timer.timeout.connect(lambda: heartbeat.append(True))
        request = request_for(np.random.default_rng(15).integers(
            0, 256, (96, 128, 3), np.uint8
        ), 30)
        try:
            timer.start()
            self.controller.request(request)
            self.wait_for(lambda: bool(self.results))
        finally:
            timer.stop()
        self.assertGreater(len(heartbeat), 1)
        self.assertEqual(self.errors, [])


class ViewerExactTests(QtTests):
    def setUp(self):
        self.messages = []
        self.patches = [
            patch.object(OSVEV, "_check_dependencies"),
            patch.object(OSVEV, "set_windows_taskbar_icon"),
            patch.object(QMessageBox, "warning",
                         side_effect=lambda *args: self.messages.append(args[1:])),
        ]
        for item in self.patches:
            item.start()
        self.viewer = OSVEV(decoder_backend="opencv")
        self.viewer.show()
        self.app.processEvents()
        self.viewer.current_frame = np.random.default_rng(33).integers(
            0, 256, (120, 160, 3), np.uint8
        )
        self.viewer.frame_count = 3
        self.viewer.timeline_slider.setMaximum(2)
        self.viewer.update_frame()
        self.ready = []
        self.viewer._exact_magnifier.ready.connect(self.ready.append)

    def tearDown(self):
        self.viewer.close()
        self.wait_for(lambda: self.viewer._exact_magnifier._task is None)
        for item in reversed(self.patches):
            item.stop()

    def wait_exact(self):
        self.wait_for(lambda: self.viewer.zoom_label.toolTip().startswith("FFmpeg-matched"))
        self.assertEqual(self.messages, [])

    def test_off_default_switching_restores_identical_fast_preview(self):
        self.assertFalse(self.viewer.match_export_checkbox.isChecked())
        self.viewer.zoom_dropdown.setCurrentText("3x")
        self.viewer.interpolation_dropdown.setCurrentText("Lanczos")
        preview = pixmap_bgr(self.viewer.zoom_label)
        native_roi = self.viewer.current_zoomed_roi.copy()
        main_key = self.viewer.main_label.pixmap.cacheKey()
        self.viewer.match_export_checkbox.setChecked(True)
        self.wait_exact()
        self.assertFalse(np.array_equal(preview, pixmap_bgr(self.viewer.zoom_label)))
        np.testing.assert_array_equal(self.viewer.current_zoomed_roi, native_roi)
        self.assertEqual(self.viewer.main_label.pixmap.cacheKey(), main_key)
        self.viewer.match_export_checkbox.setChecked(False)
        np.testing.assert_array_equal(pixmap_bgr(self.viewer.zoom_label), preview)

    def test_paused_steps_refresh_exact_pixels_and_inspector(self):
        self.viewer.zoom_dropdown.setCurrentText("3x")
        self.viewer.interpolation_dropdown.setCurrentText("Bicubic")
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = True
        self.viewer.cap.read.return_value = (True, np.full((120, 160, 3), 73, np.uint8))
        self.viewer.match_export_checkbox.setChecked(True)
        self.wait_exact()
        self.viewer.manual_next_frame()
        self.assertEqual(self.viewer.current_frame_index, 1)
        self.assertFalse(self.viewer.is_playing)
        self.wait_exact()
        self.assertTrue(np.all(pixmap_bgr(self.viewer.zoom_label) == 73))
        self.assertTrue(np.all(self.viewer.current_zoomed_roi == 73))
        self.viewer.cap.read.return_value = (True, np.full((120, 160, 3), 19, np.uint8))
        self.viewer.previous_frame()
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.wait_exact()
        self.assertTrue(np.all(pixmap_bgr(self.viewer.zoom_label) == 19))

    def test_displayed_exact_view_matches_independent_crop_scale_for_all_methods(self):
        self.viewer.zoom_dropdown.setCurrentText("3x")
        self.viewer.match_export_checkbox.setChecked(True)
        for method in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
            with self.subTest(method=method):
                self.viewer.interpolation_dropdown.setCurrentText(method)
                self.viewer.update_zoomed_view()
                request = self.viewer._exact_magnifier._pending[1]
                frame = self.viewer._get_processed_frame()
                lens = self.viewer.main_label.lens_rect
                pixmap = self.viewer.main_label.pixmap
                x = int(lens.x() * frame.shape[1] / pixmap.width())
                y = int(lens.y() * frame.shape[0] / pixmap.height())
                height, width = request.roi.shape[:2]
                expected = ffmpeg_crop_scale(frame, (x, y, width, height), 3, method)
                vx, vy, vw, vh = request.visible
                expected = expected[vy * 3:(vy + vh) * 3, vx * 3:(vx + vw) * 3]
                viewport_width, viewport_height = request.viewport
                cx = max(0, (expected.shape[1] - viewport_width) // 2)
                cy = max(0, (expected.shape[0] - viewport_height) // 2)
                expected = expected[cy:cy + viewport_height, cx:cx + viewport_width]
                self.wait_exact()
                np.testing.assert_array_equal(pixmap_bgr(self.viewer.zoom_label), expected)

    def test_inspector_reports_ffmpeg_pixel_under_crosshair_for_all_methods(self):
        self.viewer.zoom_dropdown.setCurrentText("3x")
        self.viewer.match_export_checkbox.setChecked(True)
        for method in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
            with self.subTest(method=method):
                self.viewer.interpolation_dropdown.setCurrentText(method)
                self.viewer.update_zoomed_view()
                request = self.viewer._exact_magnifier._pending[1]
                self.assertIn("pending", self.viewer.pixel_values_widget.item(1, 1).text())
                frame = self.viewer._get_processed_frame()
                lens = self.viewer.main_label.lens_rect
                pixmap = self.viewer.main_label.pixmap
                x = int(lens.x() * frame.shape[1] / pixmap.width())
                y = int(lens.y() * frame.shape[0] / pixmap.height())
                height, width = request.roi.shape[:2]
                expected = ffmpeg_crop_scale(frame, (x, y, width, height), 3, method)
                tx, ty, _ = self.viewer._crosshair_target(frame)
                self.assertEqual(request.sample, (tx - x * 3, ty - y * 3))
                b, g, r = map(int, expected[ty - y * 3, tx - x * 3])
                self.wait_for(lambda: "FFmpeg-matched" in
                              self.viewer.pixel_values_widget.item(1, 1).text())
                crop = self.viewer.get_crop_params()
                sb, sg, sr = map(int, self.viewer.current_frame[
                    ty // 3 + crop["crop_top"], tx // 3 + crop["crop_left"]])
                self.assertEqual(self.viewer.pixel_values_widget.item(1, 1).text(),
                                 f"{r}, {g}, {b} (source RGB: {sr}, {sg}, {sb}) (FFmpeg-matched)")
        self.viewer.match_export_checkbox.setChecked(False)
        self.assertNotIn("FFmpeg", self.viewer.pixel_values_widget.item(1, 1).text())
        self.assertEqual(self.messages, [])

    def test_playback_uses_preview_and_pause_requests_exact(self):
        self.viewer.match_export_checkbox.setChecked(True)
        self.wait_exact()
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = True
        self.viewer.resume_video()
        self.viewer.timer.stop()
        self.assertTrue(self.viewer.is_playing)
        self.assertIsNone(self.viewer._exact_magnifier._pending)
        self.assertTrue(self.viewer.zoom_label.toolTip().startswith("Fast preview"))
        self.viewer.pause_video()
        self.wait_exact()

    def test_step_and_switch_off_discard_inflight_pixels(self):
        entered, release = threading.Event(), threading.Event()
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = True
        self.viewer.cap.read.return_value = (True, np.full((120, 160, 3), 73, np.uint8))

        def slow_render(request):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test worker was not released")
            return render_exact(request)

        with patch("magnifier_exact.render_exact", side_effect=slow_render):
            try:
                self.viewer.match_export_checkbox.setChecked(True)
                self.wait_for(entered.is_set)
                self.viewer.manual_next_frame()
                self.viewer.match_export_checkbox.setChecked(False)
                preview = pixmap_bgr(self.viewer.zoom_label)
            finally:
                release.set()
            self.wait_for(lambda: self.viewer._exact_magnifier._task is None)
        self.assertEqual(self.ready, [])
        np.testing.assert_array_equal(pixmap_bgr(self.viewer.zoom_label), preview)
        self.viewer.match_export_checkbox.setChecked(True)
        self.wait_exact()
        self.assertTrue(np.all(pixmap_bgr(self.viewer.zoom_label) == 73))

    def test_lens_idle_and_all_controls_replace_pending_request(self):
        self.viewer.match_export_checkbox.setChecked(True)
        self.viewer.main_label.dragging = True
        self.viewer.main_label.lens_rect.moveTopLeft(QPoint(0, 0))
        self.viewer.update_zoomed_view()
        self.viewer.zoom_dropdown.setCurrentText("3x")
        self.viewer.interpolation_dropdown.setCurrentText("Lanczos")
        self.viewer.nn_zoom_slider.setValue(5)
        self.viewer.green_checkbox.setChecked(True)
        self.viewer.brightness_slider.setValue(20)
        self.viewer.crop_spins["crop_left"].setValue(2)
        pending = self.viewer._exact_magnifier._pending[1]
        self.assertEqual((pending.zoom, pending.interpolation, pending.additional_zoom),
                         (3, "Lanczos", 5))
        self.assertTrue(pending.highlight_added)
        np.testing.assert_array_equal(pending.roi, self.viewer.current_zoomed_roi)
        self.wait_exact()
        np.testing.assert_array_equal(pixmap_bgr(self.viewer.zoom_label), render_exact(pending))

    def test_scaling_error_is_visible_and_disables_mode(self):
        with patch("magnifier_exact.render_exact", side_effect=RuntimeError("Cannot scale")):
            self.viewer.match_export_checkbox.setChecked(True)
            self.wait_for(lambda: bool(self.messages))
        self.assertFalse(self.viewer.match_export_checkbox.isChecked())
        self.assertIn("Cannot scale", self.messages[0][1])
