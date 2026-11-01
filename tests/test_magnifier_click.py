# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import os
import math
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import QImage, QMouseEvent, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from magnifier_label import MagnifierLabel
from osvev_app import OSVEV
from test_magnifier_exact import ffmpeg_crop_scale, pixmap_bgr


def click_mouse(label, point, button=Qt.MouseButton.LeftButton):
    # QTest treats QPoint(0, 0) as "use widget center", not the image corner.
    for event_type in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
        QApplication.sendEvent(label, QMouseEvent(
            event_type, QPointF(point), QPointF(label.mapToGlobal(point)),
            button, button if event_type == QMouseEvent.Type.MouseButtonPress
            else Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        ))


def move_mouse(label, point):
    label.mouseMoveEvent(QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(point), QPointF(point),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))


class MouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.update_zoomed_view = Mock()
        self.label = MagnifierLabel(self.window)
        self.label.setFixedSize(320, 240)
        self.label.pixmap = QPixmap(320, 240)
        self.label.setPixmap(self.label.pixmap)
        self.label.lens_rect = QRect(100, 80, 100, 100)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()

    def test_click_inside_or_outside_old_box_centers_without_changing_dimensions(self):
        for point in (QPoint(120, 100), QPoint(260, 160)):
            click_mouse(self.label, point)
            lens = self.label.lens_rect
            self.assertEqual(lens.topLeft() + QPoint(50, 50), point)
            self.assertEqual((lens.width(), lens.height()), (100, 100))
            self.assertTrue(self.label.click_centered)
        self.assertEqual(self.window.update_zoomed_view.call_count, 2)

    def test_edges_and_corners_clip_box_not_crosshair(self):
        for point in (QPoint(0, 0), QPoint(319, 0), QPoint(0, 239), QPoint(319, 239),
                      QPoint(0, 120), QPoint(319, 120), QPoint(160, 0), QPoint(160, 239)):
            click_mouse(self.label, point)
            lens = self.label.lens_rect
            self.assertEqual(lens.topLeft() + QPoint(50, 50), point)
            self.assertEqual((lens.width(), lens.height()), (100, 100))
            self.assertFalse(QRect(0, 0, 320, 240).contains(lens))

    def test_drag_retains_offset_and_boundary_clamping_without_release_recenter(self):
        QTest.mousePress(self.label, Qt.MouseButton.LeftButton, pos=QPoint(110, 90))
        move_mouse(self.label, QPoint(150, 120))
        self.assertEqual(self.label.lens_rect, QRect(140, 110, 100, 100))
        move_mouse(self.label, QPoint(500, 500))
        self.assertEqual(self.label.lens_rect, QRect(269, 189, 100, 100))
        QTest.mouseRelease(self.label, Qt.MouseButton.LeftButton, pos=QPoint(300, 220))
        self.assertEqual(self.label.lens_rect, QRect(269, 189, 100, 100))
        self.assertTrue(self.label.click_centered)
        self.assertFalse(self.label.dragging)
        self.assertEqual(self.window.update_zoomed_view.call_count, 2)

    def test_drag_from_clipped_box_retains_grab_offset(self):
        click_mouse(self.label, QPoint(0, 0))
        QTest.mousePress(self.label, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        move_mouse(self.label, QPoint(15, 15))
        QTest.mouseRelease(self.label, Qt.MouseButton.LeftButton, pos=QPoint(15, 15))
        self.assertEqual(self.label.lens_rect, QRect(-40, -40, 100, 100))
        self.assertTrue(self.label.click_centered)

    def test_drag_clamps_crosshair_at_all_edges_and_returns_without_offset_drift(self):
        press = QPoint(110, 90)
        QTest.mousePress(self.label, Qt.MouseButton.LeftButton, pos=press)
        offset = QPoint(self.label.drag_offset)
        half_size = QPoint(50, 50)
        for pointer in (
            QPoint(-500, -500), QPoint(800, -500), QPoint(-500, 800), QPoint(800, 800),
            QPoint(-500, 70), QPoint(800, 70), QPoint(120, -500), QPoint(120, 800),
            QPoint(140, 110),
        ):
            with self.subTest(pointer=pointer):
                move_mouse(self.label, pointer)
                wanted = pointer - offset + half_size
                expected = QPoint(max(0, min(wanted.x(), 319)),
                                  max(0, min(wanted.y(), 239)))
                lens = self.label.lens_rect
                self.assertEqual(lens.topLeft() + half_size, expected)
                self.assertEqual((lens.width(), lens.height()), (100, 100))
                self.assertEqual(self.label.drag_offset, offset)
                self.assertTrue(self.label.dragging)
        self.assertEqual(self.label.lens_rect.topLeft(), QPoint(130, 100))
        QTest.mouseRelease(self.label, Qt.MouseButton.LeftButton, pos=QPoint(140, 110))
        self.assertEqual(self.label.lens_rect.topLeft(), QPoint(130, 100))
        self.assertEqual(self.window.update_zoomed_view.call_count, 9)

    def test_small_image_drag_keeps_dimensions_and_valid_crosshair(self):
        self.label.pixmap = QPixmap(7, 5)
        self.label.lens_rect = QRect(0, 0, 7, 5)
        QTest.mousePress(self.label, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        move_mouse(self.label, QPoint(-50, -50))
        self.assertEqual(self.label.lens_rect, QRect(-3, -2, 7, 5))
        move_mouse(self.label, QPoint(50, 50))
        self.assertEqual(self.label.lens_rect, QRect(3, 2, 7, 5))
        QTest.mouseRelease(self.label, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))

    def test_movement_starting_outside_box_is_not_a_click_or_drag(self):
        original = QRect(self.label.lens_rect)
        QTest.mousePress(self.label, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        move_mouse(self.label, QPoint(40, 40))
        QTest.mouseRelease(self.label, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
        self.assertEqual(self.label.lens_rect, original)
        self.window.update_zoomed_view.assert_not_called()

    def test_outside_video_right_click_and_no_video_are_ignored(self):
        original = QRect(self.label.lens_rect)
        for point in (QPoint(-1, 50), QPoint(320, 50), QPoint(50, 240)):
            click_mouse(self.label, point)
        click_mouse(self.label, QPoint(20, 20), Qt.MouseButton.RightButton)
        self.label.pixmap = None
        click_mouse(self.label, QPoint(20, 20))
        self.assertEqual(self.label.lens_rect, original)
        self.window.update_zoomed_view.assert_not_called()

    def test_high_dpi_pixmap_uses_logical_image_bounds(self):
        self.label.pixmap = QPixmap(640, 480)
        self.label.pixmap.setDevicePixelRatio(2.0)
        self.label.setPixmap(self.label.pixmap)
        self.assertEqual(self.label.image_size(), QSize(320, 240))
        click_mouse(self.label, QPoint(319, 239))
        self.assertEqual(self.label.lens_rect.topLeft() + QPoint(50, 50), QPoint(319, 239))
        original = QRect(self.label.lens_rect)
        for point in (QPoint(320, 50), QPoint(50, 240)):
            click_mouse(self.label, point)
        self.assertEqual(self.label.lens_rect, original)
        click_mouse(self.label, QPoint(100, 80))
        QTest.mousePress(self.label, Qt.MouseButton.LeftButton, pos=QPoint(100, 80))
        move_mouse(self.label, QPoint(800, 800))
        self.assertEqual(self.label.lens_rect.topLeft() + QPoint(50, 50), QPoint(319, 239))
        QTest.mouseRelease(self.label, Qt.MouseButton.LeftButton, pos=QPoint(800, 800))


class ClickCoordinateTests(unittest.TestCase):
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
        self.viewer.current_frame = np.random.default_rng(74).integers(
            0, 256, (137, 193, 3), np.uint8
        )
        self.viewer.frame_count = 1
        self.viewer.update_frame()

    def tearDown(self):
        self.viewer.close()
        deadline = time.monotonic() + 5
        while self.viewer._exact_magnifier._task is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.assertIsNone(self.viewer._exact_magnifier._task)
        for item in reversed(self.patches):
            item.stop()

    def assert_selected_pixel(self, point):
        viewer = self.viewer
        frame = viewer._get_processed_frame()
        pixmap = viewer.main_label.pixmap
        x = int(point.x() * frame.shape[1] / pixmap.width())
        y = int(point.y() * frame.shape[0] / pixmap.height())
        source_x = x + viewer.crop_spins["crop_left"].value()
        source_y = y + viewer.crop_spins["crop_top"].value()
        b, g, r = map(int, viewer.current_frame[source_y, source_x])
        item = viewer.pixel_values_widget.item(1, 1)
        if viewer.zoom_dropdown.currentText() == "1x":
            self.assertTrue(item.text().startswith(f"{r}, {g}, {b}"), item.text())
        self.assertIn(f"maps to source ({source_x}, {source_y})", item.toolTip())
        lens = viewer.main_label.lens_rect
        self.assertEqual(lens.topLeft() + QPoint(lens.width() // 2, lens.height() // 2),
                         point)

    def wait_exact(self):
        deadline = time.monotonic() + 5
        while not self.viewer.zoom_label.toolTip().startswith("FFmpeg-matched"):
            self.assertLess(time.monotonic(), deadline)
            self.app.processEvents()
            time.sleep(0.002)
        self.assertEqual(self.messages, [])

    def test_click_mapping_with_crop_upscale_and_downscale_in_both_modes(self):
        for crop in (False, True):
            if crop:
                for name, value in (("crop_top", 7), ("crop_bottom", 4),
                                    ("crop_left", 11), ("crop_right", 6)):
                    self.viewer.crop_spins[name].setValue(value)
            frame = self.viewer._get_processed_frame()
            for size in ((581, 413), (101, 79)):
                # Exercise unequal X/Y display ratios using the existing mapping.
                image = QImage(frame.data, frame.shape[1], frame.shape[0],
                               frame.strides[0], QImage.Format.Format_BGR888)
                pixmap = QPixmap.fromImage(image).scaled(*size)
                label = self.viewer.main_label
                label.setFixedSize(pixmap.size())
                label.setPixmap(pixmap)
                label.pixmap = pixmap
                label.lens_rect = QRect(0, 0, 100, 70)
                for exact in (False, True):
                    self.viewer.match_export_checkbox.setChecked(exact)
                    for point in (
                        QPoint(0, 0), QPoint(size[0] - 1, 0),
                        QPoint(0, size[1] - 1), QPoint(size[0] - 1, size[1] - 1),
                        QPoint(0, size[1] // 2), QPoint(size[0] // 2, 0),
                        QPoint(size[0] - 1, size[1] // 2),
                        QPoint(size[0] // 2, size[1] - 1),
                        QPoint(size[0] // 2, size[1] // 2),
                    ):
                        with self.subTest(crop=crop, size=size, exact=exact, point=point):
                            click_mouse(label, point)
                            self.assert_selected_pixel(point)
                            if exact:
                                self.wait_exact()
                            self.assertEqual(self.messages, [])

    def test_tiny_crops_keep_nonempty_lens_and_correct_inspected_pixels(self):
        viewer = self.viewer
        original = viewer.current_frame.copy()
        for size in (1, 4, 8):
            for name, value in (("crop_top", 7), ("crop_left", 11),
                                ("crop_bottom", 137 - 7 - size),
                                ("crop_right", 193 - 11 - size)):
                spin = viewer.crop_spins[name]
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
            viewer.update_frame()
            image_size = viewer.main_label.image_size()
            for zoom in ("1x", "30x"):
                viewer.zoom_dropdown.setCurrentText(zoom)
                for exact in (False, True):
                    viewer.match_export_checkbox.setChecked(exact)
                    for point in (QPoint(0, 0), QPoint(image_size.width() // 2, image_size.height() // 2),
                                  QPoint(image_size.width() - 1, image_size.height() - 1)):
                        with self.subTest(size=size, zoom=zoom, exact=exact, point=point):
                            click_mouse(viewer.main_label, point)
                            if exact:
                                self.wait_exact()
                            self.assertEqual(self.messages, [])
                            roi = viewer.current_zoomed_roi
                            self.assertGreater(min(roi.shape[:2]), 0)
                            self.assert_selected_pixel(point)
                            x = point.x() * size // image_size.width()
                            y = point.y() * size // image_size.height()
                            b, g, r = map(int, original[y + 7, x + 11])
                            self.assertTrue(viewer.pixel_values_widget.item(1, 1).text()
                                            .startswith(f"{r}, {g}, {b}"))
                            self.assertTrue(np.any(np.all(pixmap_bgr(viewer.zoom_label)
                                                          == original[y + 7, x + 11], axis=2)))
        np.testing.assert_array_equal(viewer.current_frame, original)

    def test_tiny_fast_roi_uses_same_neighbors_as_processed_pixel_inspector(self):
        viewer = self.viewer
        for name, value in (("crop_top", 7), ("crop_left", 11),
                            ("crop_bottom", 126), ("crop_right", 178)):
            spin = viewer.crop_spins[name]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        viewer.zoom_dropdown.setCurrentText("3x")
        for method, interpolation in (("Nearest", cv2.INTER_NEAREST),
                                      ("Bilinear", cv2.INTER_LINEAR),
                                      ("Bicubic", cv2.INTER_CUBIC),
                                      ("Lanczos", cv2.INTER_LANCZOS4)):
            with self.subTest(method=method):
                viewer.interpolation_dropdown.setCurrentText(method)
                viewer.update_frame()
                frame = viewer._get_processed_frame()
                label = viewer.main_label
                image_size = label.image_size()
                point = QPoint(image_size.width() // 2, image_size.height() // 2)
                click_mouse(label, point)
                selection = label.lens_rect.intersected(QRect(QPoint(), image_size))
                x = selection.x() * frame.shape[1] // image_size.width()
                y = selection.y() * frame.shape[0] // image_size.height()
                height, width = viewer.current_zoomed_roi.shape[:2]
                scaled = cv2.resize(frame, None, fx=3, fy=3, interpolation=interpolation)
                np.testing.assert_array_equal(pixmap_bgr(viewer.zoom_label),
                                              scaled[y * 3:(y + height) * 3, x * 3:(x + width) * 3])
                target_x, target_y, _ = viewer._crosshair_target(frame)
                b, g, r = map(int, scaled[target_y, target_x])
                self.assertTrue(viewer.pixel_values_widget.item(1, 1).text()
                                .startswith(f"{r}, {g}, {b}"))
        self.assertEqual(self.messages, [])

    def test_clipped_region_has_only_valid_pixels_without_stretching(self):
        self.viewer.zoom_dropdown.setCurrentText("3x")
        label = self.viewer.main_label
        frame = self.viewer._get_processed_frame()
        for point in (QPoint(0, 0), QPoint(label.pixmap.width() - 1,
                                          label.pixmap.height() - 1)):
            click_mouse(label, point)
            selection = label.lens_rect.intersected(QRect(QPoint(), label.pixmap.size()))
            sx, sy = frame.shape[1] / label.pixmap.width(), frame.shape[0] / label.pixmap.height()
            x, y = int(selection.x() * sx), int(selection.y() * sy)
            right = math.ceil((selection.x() + selection.width()) * sx)
            bottom = math.ceil((selection.y() + selection.height()) * sy)
            expected_roi = frame[y:bottom, x:right]
            np.testing.assert_array_equal(self.viewer.current_zoomed_roi, expected_roi)
            for extra in (1, 5):
                self.viewer.nn_zoom_slider.setValue(extra)
                for added, source in ((False, False), (True, False), (False, True)):
                    self.viewer.green_checkbox.setChecked(added)
                    self.viewer.original_checkbox.setChecked(source)
                    # Highlight toggles redisplay the frame; the clicked edge must persist.
                    self.assert_selected_pixel(point)
                    for exact in (False, True):
                        self.viewer.match_export_checkbox.setChecked(exact)
                        if exact:
                            self.wait_exact()
                        output = self.viewer.zoom_label.pixmap()
                        roi = self.viewer.current_zoomed_roi
                        self.assertEqual(output.width(), min(
                            roi.shape[1] * 3 * extra, self.viewer.zoom_label.width()))
                        self.assertEqual(output.height(), min(
                            roi.shape[0] * 3 * extra, self.viewer.zoom_label.height()))
                        visible_w = min(roi.shape[1], (self.viewer.zoom_label.width()
                                                      + 3 * extra - 1) // (3 * extra))
                        visible_h = min(roi.shape[0], (self.viewer.zoom_label.height()
                                                      + 3 * extra - 1) // (3 * extra))
                        cx = min(roi.shape[1] - 1, int(point.x() * sx) - x)
                        cy = min(roi.shape[0] - 1, int(point.y() * sy) - y)
                        vx = max(0, min(cx - visible_w // 2, roi.shape[1] - visible_w))
                        vy = max(0, min(cy - visible_h // 2, roi.shape[0] - visible_h))
                        expected = np.repeat(np.repeat(
                            roi[vy:vy + visible_h, vx:vx + visible_w], 3, axis=0
                        ), 3, axis=1)
                        mask = np.zeros(expected.shape[:2], bool)
                        mask[::3, ::3] = True
                        if added:
                            expected[~mask] = (0, 255, 0)
                        if source:
                            expected[mask] = (0, 255, 0)
                        expected = np.repeat(np.repeat(expected, extra, axis=0),
                                             extra, axis=1)
                        ox = max(0, (expected.shape[1] - self.viewer.zoom_label.width()) // 2)
                        oy = max(0, (expected.shape[0] - self.viewer.zoom_label.height()) // 2)
                        expected = expected[oy:oy + output.height(), ox:ox + output.width()]
                        image = output.toImage().convertToFormat(QImage.Format.Format_BGR888)
                        bits = image.bits()
                        bits.setsize(image.sizeInBytes())
                        actual = np.frombuffer(bits, np.uint8).reshape(
                            image.height(), image.bytesPerLine()
                        )[:, :image.width() * 3].reshape(image.height(), image.width(), 3)
                        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(self.messages, [])

    def test_clicked_edge_is_preserved_on_frame_refresh(self):
        label = self.viewer.main_label
        point = QPoint(label.pixmap.width() - 1, label.pixmap.height() - 1)
        click_mouse(label, point)
        self.viewer.update_frame()
        self.assert_selected_pixel(point)
        self.assertEqual((label.lens_rect.width(), label.lens_rect.height()), (100, 100))

    def test_dragged_crop_boundaries_match_clicks_in_both_modes(self):
        for name, value in (("crop_top", 7), ("crop_bottom", 4),
                            ("crop_left", 11), ("crop_right", 6)):
            self.viewer.crop_spins[name].setValue(value)
        self.viewer.zoom_dropdown.setCurrentText("3x")
        self.viewer.interpolation_dropdown.setCurrentText("Lanczos")
        label = self.viewer.main_label
        source = self.viewer.current_frame.copy()
        width, height = label.pixmap.width(), label.pixmap.height()
        for extra, added, original in ((1, False, False), (5, True, False),
                                       (5, False, True)):
            self.viewer.nn_zoom_slider.setValue(extra)
            self.viewer.green_checkbox.setChecked(added)
            self.viewer.original_checkbox.setChecked(original)
            for exact in (False, True):
                self.viewer.match_export_checkbox.setChecked(exact)
                for target in (
                    QPoint(0, 0), QPoint(width - 1, 0), QPoint(0, height - 1),
                    QPoint(width - 1, height - 1), QPoint(0, height // 2),
                    QPoint(width - 1, height // 2), QPoint(width // 2, 0),
                    QPoint(width // 2, height - 1),
                ):
                    with self.subTest(extra=extra, exact=exact, target=target):
                        start = QPoint(width // 2, height // 2)
                        click_mouse(label, start)
                        press = start + QPoint(7, 3)
                        QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=press)
                        pointer = target + QPoint(7, 3)
                        if target.x() == 0:
                            pointer.setX(-100)
                        elif target.x() == width - 1:
                            pointer.setX(width + 100)
                        if target.y() == 0:
                            pointer.setY(-100)
                        elif target.y() == height - 1:
                            pointer.setY(height + 100)
                        move_mouse(label, pointer)
                        QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=pointer)
                        self.assert_selected_pixel(target)
                        dragged_roi = self.viewer.current_zoomed_roi.copy()
                        if exact:
                            self.wait_exact()
                        dragged_pixels = pixmap_bgr(self.viewer.zoom_label)
                        click_mouse(label, target)
                        if exact:
                            self.wait_exact()
                        np.testing.assert_array_equal(self.viewer.current_zoomed_roi, dragged_roi)
                        np.testing.assert_array_equal(pixmap_bgr(self.viewer.zoom_label),
                                                      dragged_pixels)
                        self.viewer.update_frame()
                        self.assert_selected_pixel(target)
        np.testing.assert_array_equal(self.viewer.current_frame, source)
        self.assertEqual((label.lens_rect.width(), label.lens_rect.height()), (100, 100))
        self.assertEqual(self.messages, [])

    def test_clipped_exact_pixels_match_ffmpeg_for_all_interpolations(self):
        self.viewer.zoom_dropdown.setCurrentText("3x")
        self.viewer.match_export_checkbox.setChecked(True)
        label = self.viewer.main_label
        for point in (QPoint(0, 0), QPoint(label.pixmap.width() - 1,
                                          label.pixmap.height() - 1)):
            for method in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
                with self.subTest(point=point, method=method):
                    self.viewer.interpolation_dropdown.setCurrentText(method)
                    click_mouse(label, point)
                    roi = self.viewer.current_zoomed_roi.copy()
                    expected = ffmpeg_crop_scale(
                        roi, (0, 0, roi.shape[1], roi.shape[0]), 3, method
                    )
                    self.wait_exact()
                    np.testing.assert_array_equal(pixmap_bgr(self.viewer.zoom_label), expected)
                    self.assert_selected_pixel(point)
