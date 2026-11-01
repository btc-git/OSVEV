# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import contextlib
from fractions import Fraction
import hashlib
import io
import os
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QRect, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from frame_info import FrameInfoHandler, MetadataCancelled, SourceRange
from frame_processor import FrameProcessor
from osvev_app import OSVEV
from sample_media import requires_samples


def pixmap_bgr(pixmap):
    from PyQt6.QtGui import QImage
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_BGR888)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    rows = np.frombuffer(ptr, np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()


def exact_nearest(image, width, height):
    """Repeat whole pixels with the inspector's mapping, x * source // displayed."""
    rows = (np.arange(height) * image.shape[0]) // height
    columns = (np.arange(width) * image.shape[1]) // width
    return image[rows][:, columns]


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.random.default_rng(42).integers(
            0, 256, (32, 48, 3), dtype=np.uint8
        )

    def test_neutral_processing_is_byte_identical_and_independent(self):
        result = FrameProcessor.process_frame(self.frame)
        self.assertEqual(
            hashlib.sha256(result.tobytes()).digest(),
            hashlib.sha256(self.frame.tobytes()).digest(),
        )
        self.assertFalse(np.shares_memory(result, self.frame))

    def test_adjustments_do_not_modify_input(self):
        original = self.frame.copy()
        for settings in (
            {"brightness": 100}, {"contrast": -100},
            {"sharpness": 100}, {"compression": 99},
        ):
            result = FrameProcessor.process_frame(self.frame, **settings)
            self.assertEqual(result.shape, self.frame.shape)
            self.assertEqual(result.dtype, np.uint8)
            np.testing.assert_array_equal(self.frame, original)

    def test_compression_is_only_applied_when_requested(self):
        result = FrameProcessor.process_frame(self.frame, compression=50)
        self.assertFalse(np.array_equal(result, self.frame))

    def test_metadata_speed_factor_is_exact_reciprocal(self):
        self.assertAlmostEqual(FrameInfoHandler.get_speed_factor("3x"), 1 / 3)
        self.assertAlmostEqual(FrameInfoHandler.get_speed_factor("0.75x"), 4 / 3)

    def test_frame_info_does_not_claim_source_was_upscaled(self):
        values = dict(FrameInfoHandler.format_frame_data(
            {"width": 48, "height": 32}, 0, 5, 30, "3x", "1x", 0
        ))
        self.assertEqual(values["Source Resolution"], "48x32")
        self.assertEqual(values["Source Pixels"], "1,536")
        self.assertEqual(values["Magnifier Zoom"], "3x (view only)")

    def test_metadata_error_is_not_empty_success(self):
        with self.assertRaises(FileNotFoundError):
            FrameInfoHandler.get_frame_metadata("nonexistent-source-video.mp4")

    @requires_samples("short-clip.mp4")
    def test_metadata_cancellation_before_start(self):
        source = os.path.join(os.path.dirname(os.path.dirname(__file__)), "short-clip.mp4")
        with self.assertRaises(MetadataCancelled):
            FrameInfoHandler.get_frame_metadata(source, lambda: True)


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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
        self.viewer.current_frame = np.random.default_rng(3).integers(
            0, 256, (240, 320, 3), dtype=np.uint8
        )
        self.viewer.frame_count = 100
        self.viewer.timeline_slider.setMaximum(99)
        self.viewer.timeline_slider.setEnabled(True)
        self.viewer.update_frame()

    def tearDown(self):
        self.viewer.timer.stop()
        self.viewer.close()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()

    def test_inspector_reports_actual_processed_pixel(self):
        self.viewer.current_frame[:] = [0, 0, 255]
        self.viewer.process_cache.clear()
        self.viewer.brightness_slider.setValue(100)
        result = next(iter(self.viewer.process_cache.values()))
        b, g, r = map(int, result[0, 0])
        self.assertEqual(self.viewer.pixel_values_widget.item(1, 0).text(), "Processed RGB")
        self.assertEqual(self.viewer.pixel_values_widget.item(1, 1).text(),
                         f"{r}, {g}, {b} (source RGB: 255, 0, 0)")

    def test_time_counter_uses_exact_pts_with_duplicates_nonzero_start_and_vfr(self):
        viewer = self.viewer
        viewer.frame_count = 4
        viewer.frame_metadata = [
            {"pts": pts, "best_effort_timestamp_time": "deliberately unused",
             "duration_time": "0.024"}
            for pts in (90000, 90000, 90009, 90041)
        ]
        viewer.current_native_samples = SimpleNamespace(
            frame=SimpleNamespace(time_base=Fraction(1, 3000)))
        for index, expected in enumerate(("00:00:00.000", "00:00:00.000",
                                          "00:00:00.003", "00:00:00.014")):
            viewer.current_frame_index = index
            viewer._update_video_time()
            self.assertEqual(viewer.video_time_label.text(),
                             f"Time: {expected} / 00:00:00.038")
        viewer.source_range = SourceRange(100, 200, Fraction(30), Fraction(31))
        viewer._update_video_time()
        self.assertEqual(viewer.video_time_label.text(),
                         "Time (loaded range): 00:00:00.014 / 00:00:00.038")
        del viewer.frame_metadata[-1]["duration_time"]
        viewer._update_video_time()
        self.assertTrue(viewer.video_time_label.text().endswith("/ unknown"))
        viewer.current_native_samples = None
        viewer.frame_metadata = []
        viewer.source_range = None

    def test_time_counter_navigation_and_unknown_timing_never_use_fps(self):
        viewer = self.viewer
        viewer.frame_count = 4
        viewer.timeline_slider.setMaximum(3)
        viewer.frame_metadata = [
            {"best_effort_timestamp_time": time, "duration_time": "0.025"}
            for time in ("8.000", "8.040", "8.120", "8.200")
        ]
        viewer.cap = Mock()
        viewer.cap.isOpened.return_value = True
        viewer.cap.read.return_value = (True, viewer.current_frame.copy())
        viewer.set_frame_position(2)
        self.assertEqual(viewer.video_time_label.text(),
                         "Time: 00:00:00.120 / 00:00:00.225")
        viewer.next_frame()
        self.assertIn("Time: 00:00:00.200 /", viewer.video_time_label.text())
        viewer.previous_frame()
        self.assertIn("Time: 00:00:00.120 /", viewer.video_time_label.text())
        viewer.frame_metadata = []
        viewer.frame_rate = 30
        viewer.update_frame()
        self.assertEqual(viewer.video_time_label.text(), "Time: — / unknown")

    def test_audio_line_distinguishes_container_streams_from_available_qt_tracks(self):
        viewer = self.viewer
        viewer.cap = Mock()
        viewer.cap.has_audio = True
        viewer.cap.audio_streams = (
            {"codec": "aac", "channels": 1, "sample_rate": 44100},
            {"codec": "aac", "channels": 2, "sample_rate": 48000},
            {"codec": "unsupported"},
        )
        with patch.object(viewer._audio, "available_tracks", return_value=["AAC", "AAC"]), \
                patch.object(viewer._audio, "active_track", return_value=1), \
                patch.object(viewer._audio, "select_track") as select:
            viewer._show_audio_status("Paused")
            self.assertIn("3 audio streams", viewer.audio_status_label.text())
            self.assertIn("2 Qt tracks", viewer.audio_status_label.text())
            self.assertIn("Qt track 2: AAC", viewer.audio_status_label.text())
            self.assertNotIn("48000", viewer.audio_status_label.text())
            self.assertEqual(viewer.audio_track_dropdown.currentIndex(), 1)
            self.assertFalse(viewer.audio_track_dropdown.isHidden())
            viewer.audio_track_dropdown.activated.emit(0)
            select.assert_called_once_with(0)
        with patch.object(viewer._audio, "available_tracks", return_value=[]), \
                patch.object(viewer._audio, "active_track", return_value=-1):
            viewer._show_audio_status("Unavailable")
            self.assertTrue(viewer.audio_track_dropdown.isHidden())
            self.assertIn("0 Qt tracks", viewer.audio_status_label.text())
            self.assertIn("Unavailable", viewer.audio_status_label.text())

    def test_main_view_enlarging_uses_exact_nearest_or_selected_interpolation(self):
        source = self.viewer.current_frame.copy()
        processed = self.viewer._get_processed_frame().copy()
        sizes, lenses, inspector, rendered = set(), set(), set(), {}
        for zoom in ("1x", "3x", "1x"):
            self.viewer.zoom_dropdown.setCurrentText(zoom)
            self.app.processEvents()
            for name, flag in OSVEV._INTERPOLATION_FLAGS.items():
                self.viewer.interpolation_dropdown.setCurrentText(name)
                self.app.processEvents()
                pixmap = self.viewer.main_label.pixmap
                size = (pixmap.width(), pixmap.height())
                self.assertGreater(size[0], 320)
                expected = (exact_nearest(processed, *size)
                            if zoom == "1x" or name == "Nearest"
                            else cv2.resize(processed, size, interpolation=flag))
                np.testing.assert_array_equal(pixmap_bgr(pixmap), expected)
                rendered[zoom, name] = pixmap_bgr(pixmap)
                sizes.add(size)
                lenses.add(self.viewer.main_label.lens_rect.getRect())
                inspector.add(self.viewer.pixel_values_widget.item(0, 1).text())
                np.testing.assert_array_equal(self.viewer._get_processed_frame(), processed)
        self.assertEqual(len(sizes), 1)
        self.assertEqual(len(lenses), 1)
        self.assertEqual(len(inspector), 1)
        np.testing.assert_array_equal(rendered["1x", "Nearest"], rendered["1x", "Lanczos"])
        np.testing.assert_array_equal(rendered["1x", "Nearest"], rendered["3x", "Nearest"])
        self.assertFalse(np.array_equal(rendered["3x", "Nearest"], rendered["3x", "Lanczos"]))
        np.testing.assert_array_equal(self.viewer.current_frame, source)

    def test_main_view_crosshair_pixel_is_inspector_pixel_when_enlarged(self):
        processed = self.viewer._get_processed_frame()
        displayed = pixmap_bgr(self.viewer.main_label.pixmap)
        height, width = displayed.shape[:2]
        for x in range(0, width, 7):
            for y in range(0, height, 11):
                self.viewer.main_label.lens_rect = QRect(x - 50, y - 50, 100, 100)
                target_x, target_y, _ = self.viewer._crosshair_target(processed)
                np.testing.assert_array_equal(displayed[y, x], processed[target_y, target_x])

    def test_main_view_shrinking_area_averages_without_changing_processed_frame(self):
        self.viewer.current_frame = np.random.default_rng(5).integers(
            0, 256, (1500, 2600, 3), dtype=np.uint8)
        self.viewer.process_cache.clear()
        processed = self.viewer._get_processed_frame().copy()
        for zoom in ("1x", "3x"):
            for name in OSVEV._INTERPOLATION_FLAGS:
                self.viewer.zoom_dropdown.setCurrentText(zoom)
                self.viewer.interpolation_dropdown.setCurrentText(name)
                self.viewer.update_frame()
                self.app.processEvents()
                pixmap = self.viewer.main_label.pixmap
                size = (pixmap.width(), pixmap.height())
                self.assertLess(size[0], 2600)
                np.testing.assert_array_equal(
                    pixmap_bgr(pixmap),
                    cv2.resize(processed, size, interpolation=cv2.INTER_AREA))
        np.testing.assert_array_equal(self.viewer._get_processed_frame(), processed)
        self.assertEqual(self.messages, [])

    def test_main_view_renders_physical_pixels_at_high_device_pixel_ratio(self):
        def state():
            self.viewer.update_frame()
            self.app.processEvents()
            return (self.viewer.main_label.image_size(), self.viewer.main_label.size(),
                    self.viewer.main_label.lens_rect.getRect(),
                    pixmap_bgr(self.viewer.zoom_label.pixmap()).tobytes(),
                    [self.viewer.pixel_values_widget.item(row, 1).text()
                     for row in range(self.viewer.pixel_values_widget.rowCount())])

        processed = self.viewer._get_processed_frame()
        for zoom, name in (("1x", "Nearest"), ("3x", "Bicubic")):
            self.viewer.zoom_dropdown.setCurrentText(zoom)
            self.viewer.interpolation_dropdown.setCurrentText(name)
            self.viewer.main_label.lens_rect = QRect(90, 70, 100, 100)
            single = state()
            for ratio in (1.5, 2.0):
                with patch.object(self.viewer.main_label, "devicePixelRatioF",
                                  return_value=ratio):
                    self.assertEqual(state(), single)
                pixmap = self.viewer.main_label.pixmap
                logical = self.viewer.main_label.image_size()
                self.assertEqual(pixmap.devicePixelRatio(), ratio)
                self.assertEqual((pixmap.width(), pixmap.height()),
                                 (int(logical.width() * ratio + 0.5),
                                  int(logical.height() * ratio + 0.5)))
            if zoom == "1x":
                displayed = pixmap_bgr(pixmap)
                np.testing.assert_array_equal(
                    displayed, exact_nearest(processed, pixmap.width(), pixmap.height()))
                for x in range(0, logical.width(), 13):
                    for y in range(0, logical.height(), 17):
                        self.viewer.main_label.lens_rect = QRect(x - 50, y - 50, 100, 100)
                        target_x, target_y, _ = self.viewer._crosshair_target(processed)
                        # A logical position covers a 2x2 physical block; its top-left
                        # physical pixel is the inspector's pixel.
                        np.testing.assert_array_equal(
                            displayed[2 * y, 2 * x], processed[target_y, target_x])
        self.assertEqual(self.messages, [])

    def test_device_pixel_ratio_change_redraws_main_view(self):
        from PyQt6.QtCore import QEvent
        with patch.object(self.viewer, "update_frame") as update:
            QApplication.sendEvent(self.viewer, QEvent(QEvent.Type.DevicePixelRatioChange))
        update.assert_called_once_with()

    def test_shared_enhancements_present_unaligned_width_without_reprocessing(self):
        self.viewer.current_frame = np.zeros((240, 318, 3), dtype=np.uint8)
        self.viewer.process_cache.clear()
        self.viewer.brightness_slider.setValue(20)
        self.viewer.contrast_slider.setValue(32)
        self.viewer.sharpness_slider.setValue(30)
        self.app.processEvents()
        self.assertEqual(self.messages, [])
        self.assertIsNotNone(self.viewer.main_label.pixmap)
        processed = self.viewer._get_processed_frame()
        self.assertTrue(processed.flags.c_contiguous)
        graph = self.viewer.frame_processor._graph
        self.viewer.update_frame()
        self.assertIs(self.viewer._get_processed_frame(), processed)
        self.assertIs(self.viewer.frame_processor._graph, graph)

    def test_maximum_zoom_is_fresh_and_bounded(self):
        self.viewer.zoom_dropdown.setCurrentText("30x")
        logs = io.StringIO()
        with contextlib.redirect_stdout(logs):
            self.viewer.nn_zoom_slider.setValue(50)
            before = self.viewer.zoom_label.pixmap().cacheKey()
            self.viewer.current_frame = np.zeros_like(self.viewer.current_frame)
            self.viewer.current_frame_index += 1
            self.viewer.update_frame()
        self.assertEqual(logs.getvalue(), "")
        pixmap = self.viewer.zoom_label.pixmap()
        self.assertNotEqual(pixmap.cacheKey(), before)
        self.assertLessEqual(pixmap.width(), self.viewer.zoom_label.width())
        self.assertLessEqual(pixmap.height(), self.viewer.zoom_label.height())

    def test_timeline_drag_without_preview_commits_position_on_release(self):
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = True
        self.viewer.cap.read.return_value = (True, self.viewer.current_frame.copy())
        self.viewer.timeline_slider.setSliderDown(True)
        self.viewer.timeline_slider.setSliderPosition(20)
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.viewer.cap.set.assert_not_called()
        self.viewer.timeline_slider.setSliderDown(False)
        self.assertEqual(self.viewer.current_frame_index, 20)
        self.viewer.cap.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 20)

    def test_pause_and_close_stop_timer_even_if_capture_failed(self):
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = False
        self.viewer.is_playing = True
        self.viewer.timer.start(1000)
        self.viewer.pause_video()
        self.assertFalse(self.viewer.is_playing)
        self.assertFalse(self.viewer.timer.isActive())
        self.viewer.timer.start(1000)
        self.viewer.close()
        self.assertFalse(self.viewer.timer.isActive())

    def test_consecutive_frames_do_not_seek(self):
        self.viewer.cap = Mock()
        self.viewer.cap.read.return_value = (True, self.viewer.current_frame.copy())
        self.viewer._last_decoded_index = 0
        self.viewer.get_frame(1)
        self.viewer.cap.set.assert_not_called()
        self.viewer.get_frame(10)
        self.viewer.cap.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 10)

    def test_failed_seek_keeps_displayed_frame_index(self):
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = True
        self.viewer.cap.read.return_value = (False, None)
        self.viewer.current_frame_index = 4
        self.viewer.timeline_slider.setValue(20)
        self.viewer.set_frame_position()
        self.assertEqual(self.viewer.current_frame_index, 4)
        self.assertEqual(self.viewer.timeline_slider.value(), 4)
        self.assertTrue(self.messages)

    def test_failed_open_preserves_previous_video(self):
        original_cap = Mock()
        self.viewer.cap = original_cap
        self.viewer.video_path = "original.mp4"
        candidate = Mock()
        candidate.isOpened.return_value = False
        with patch.object(QFileDialog, "getOpenFileName",
                          return_value=("missing.mp4", "")), \
                patch("osvev_app.cv2.VideoCapture", return_value=candidate):
            self.viewer.open_video()
        self.assertIs(self.viewer.cap, original_cap)
        self.assertEqual(self.viewer.video_path, "original.mp4")
        original_cap.release.assert_not_called()
        candidate.release.assert_called_once()
        self.assertTrue(self.messages)

    def test_lens_stays_inside_small_image(self):
        self.viewer.current_frame = np.zeros((8, 320, 3), dtype=np.uint8)
        self.viewer.process_cache.clear()
        self.viewer.update_frame()
        lens = self.viewer.main_label.lens_rect
        bounds = QRect(QPoint(0, 0), self.viewer.main_label.pixmap.size())
        self.assertTrue(bounds.contains(lens), (bounds, lens))

    def test_reset_keeps_neutral_cache_and_frame_info(self):
        self.viewer.brightness_slider.setValue(20)
        self.viewer.reset_settings()
        self.assertTrue(self.viewer.process_cache)
        np.testing.assert_array_equal(
            next(iter(self.viewer.process_cache.values())),
            self.viewer.current_frame,
        )
        self.assertGreater(self.viewer.frame_info_table.rowCount(), 0)

    def test_zoom_interpolation_and_highlights_never_change_frame_data(self):
        original = self.viewer.current_frame.copy()
        processed = self.viewer._get_processed_frame().copy()
        native_row = self.viewer.pixel_values_widget.item(0, 1).text()
        flags = {"Nearest": cv2.INTER_NEAREST, "Bilinear": cv2.INTER_LINEAR,
                 "Bicubic": cv2.INTER_CUBIC, "Lanczos": cv2.INTER_LANCZOS4}
        for interpolation in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
            self.viewer.interpolation_dropdown.setCurrentText(interpolation)
            for zoom in ("1x", "3x", "15x", "30x"):
                self.viewer.zoom_dropdown.setCurrentText(zoom)
                for extra in (1, 50):
                    self.viewer.nn_zoom_slider.setValue(extra)
                    self.viewer.green_checkbox.setChecked(True)
                    self.viewer.original_checkbox.setChecked(True)
                    np.testing.assert_array_equal(self.viewer.current_frame, original)
                    np.testing.assert_array_equal(self.viewer._get_processed_frame(), processed)
                    self.assertEqual(self.viewer.pixel_values_widget.item(0, 1).text(),
                                     native_row)
                    factor = int(zoom[:-1])
                    tx, ty, reported_zoom = self.viewer._crosshair_target(processed)
                    self.assertEqual(reported_zoom, factor)
                    full = cv2.resize(processed, None, fx=factor, fy=factor,
                                      interpolation=flags[interpolation])
                    b, g, r = map(int, full[ty, tx])
                    sb, sg, sr = map(int, original[ty // factor, tx // factor])
                    self.assertEqual(self.viewer.pixel_values_widget.item(1, 1).text(),
                                     f"{r}, {g}, {b} (source RGB: {sr}, {sg}, {sb})")
        self.assertEqual(self.messages, [])

    def test_cancelled_load_keeps_old_capture_and_stops_worker(self):
        original_cap = Mock()
        self.viewer.cap = original_cap
        self.viewer.video_path = "original.mp4"
        candidate = Mock()
        candidate.get.side_effect = lambda prop: 100 if prop == cv2.CAP_PROP_FRAME_COUNT else 30
        candidate.read.return_value = (True, self.viewer.current_frame.copy())
        responsive_ticks = []

        def scan(path, cancelled, **kwargs):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if cancelled():
                    raise MetadataCancelled()
                time.sleep(0.01)
            raise RuntimeError("Cancellation did not reach the metadata worker.")

        def cancel():
            responsive_ticks.append(True)
            self.viewer._loading_progress.findChild(QPushButton).click()

        QTimer.singleShot(30, cancel)
        with patch.object(QFileDialog, "getOpenFileName", return_value=("new.mp4", "")), \
                patch("osvev_app.cv2.VideoCapture", return_value=candidate), \
                patch.object(FrameInfoHandler, "get_frame_metadata", side_effect=scan):
            self.viewer.open_video()
        self.assertEqual(responsive_ticks, [True])
        self.assertIs(self.viewer.cap, original_cap)
        self.assertEqual(self.viewer.video_path, "original.mp4")
        self.assertIsNone(self.viewer._metadata_worker)
        original_cap.release.assert_not_called()
        candidate.release.assert_called_once()
        self.assertEqual(self.messages, [])

    def test_new_load_clears_old_metadata_and_resets_timeline(self):
        self.viewer.actual_speed_label.setText("Actual: 0.98×")
        self.viewer.skipped_frames_label.setText("Skipped: 9")
        self.viewer.skipped_frames_label.setStyleSheet("font-weight: bold;")
        self.viewer._source_paint_samples.append((100, 5))
        original_cap = Mock()
        self.viewer.cap = original_cap
        self.viewer.frame_metadata = [{"pict_type": "I", "pkt_size": 999}]
        self.viewer.timeline_slider.setValue(25)
        candidate = Mock()
        candidate.get.side_effect = lambda prop: 10 if prop == cv2.CAP_PROP_FRAME_COUNT else 24
        new_frame = np.zeros((16, 24, 3), dtype=np.uint8)
        candidate.read.return_value = (True, new_frame)
        with patch.object(QFileDialog, "getOpenFileName", return_value=("new.mp4", "")), \
                patch("osvev_app.cv2.VideoCapture", return_value=candidate), \
                patch.object(FrameInfoHandler, "get_frame_metadata",
                             side_effect=RuntimeError("No metadata")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.viewer.open_video()
        self.assertIs(self.viewer.cap, candidate)
        self.assertEqual(self.viewer.frame_metadata, [])
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.assertEqual(self.viewer.timeline_slider.value(), 0)
        self.assertEqual(self.viewer.frame_count, 10)
        self.assertEqual(self.viewer.frame_rate, 24)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: —")
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")
        self.assertEqual(self.viewer.skipped_frames_label.styleSheet(), "")
        self.assertEqual(len(self.viewer._source_paint_samples), 0)
        original_cap.release.assert_called_once()
        rows = {
            self.viewer.frame_info_table.item(i, 0).text():
            self.viewer.frame_info_table.item(i, 1).text()
            for i in range(self.viewer.frame_info_table.rowCount())
        }
        self.assertEqual(rows["Source Packet Size"], "Unknown")
        self.assertEqual(rows["Source Resolution"], "24x16")
        self.assertEqual(self.messages[0][0], "Metadata Unavailable")

    def test_closing_during_load_cancels_and_releases_candidate(self):
        candidate = Mock()
        candidate.get.side_effect = lambda prop: 100 if prop == cv2.CAP_PROP_FRAME_COUNT else 30
        candidate.read.return_value = (True, self.viewer.current_frame.copy())

        def scan(path, cancelled, **kwargs):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if cancelled():
                    raise MetadataCancelled()
                time.sleep(0.01)
            raise RuntimeError("Window close did not cancel the metadata worker.")

        QTimer.singleShot(30, self.viewer.close)
        with patch.object(QFileDialog, "getOpenFileName", return_value=("new.mp4", "")), \
                patch("osvev_app.cv2.VideoCapture", return_value=candidate), \
                patch.object(FrameInfoHandler, "get_frame_metadata", side_effect=scan):
            self.viewer.open_video()
        self.assertIsNone(self.viewer.cap)
        self.assertIsNone(self.viewer._metadata_worker)
        candidate.release.assert_called_once()
        self.assertFalse(self.viewer.timer.isActive())
        self.assertEqual(self.messages, [])

    def test_lens_preserves_normalized_position_on_resize(self):
        self.viewer.main_label.lens_rect.moveTo(200, 30)
        lens = self.viewer.main_label.lens_rect
        old_width = self.viewer.main_label.pixmap.width()
        old_height = self.viewer.main_label.pixmap.height()
        before = (
            (lens.x() + lens.width() // 2) / old_width,
            (lens.y() + lens.height() // 2) / old_height,
        )
        self.viewer.resize(1500, 1000)
        self.app.processEvents()
        lens = self.viewer.main_label.lens_rect
        after = (
            (lens.x() + lens.width() // 2) / self.viewer.main_label.pixmap.width(),
            (lens.y() + lens.height() // 2) / self.viewer.main_label.pixmap.height(),
        )
        self.assertAlmostEqual(before[0], after[0], delta=0.01)
        self.assertAlmostEqual(before[1], after[1], delta=0.01)


if __name__ == "__main__":
    unittest.main()
