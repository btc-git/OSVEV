# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Exact 90-degree rotation and the preview-only Inspection Aids group (synthetic data only)."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QMessageBox

from crop_utils import (ROTATION_LABELS, ROTATIONS, rotate_image, rotated_size,
                        rotation_filter, unrotate_point)
from frame_processor import FrameProcessor
from osvev_app import OSVEV

AVAILABLE = (importlib.util.find_spec("av") is not None and shutil.which("ffmpeg")
             and shutil.which("ffprobe"))


class RotationGeometryTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.random.default_rng(11).integers(0, 256, (9, 14, 3), dtype=np.uint8)

    def test_exact_rotations_and_inverse_mapping(self):
        self.assertEqual(ROTATIONS, (0, 90, 180, 270))
        self.assertEqual(list(ROTATION_LABELS.values()),
                         ["0° (Original)", "90° clockwise", "180°", "270° clockwise"])
        np.testing.assert_array_equal(rotate_image(self.frame, 90), np.rot90(self.frame, -1))
        np.testing.assert_array_equal(rotate_image(self.frame, 180), self.frame[::-1, ::-1])
        np.testing.assert_array_equal(rotate_image(self.frame, 270), np.rot90(self.frame, 1))
        # 90 clockwise: the top-left source pixel moves to the top-right corner.
        np.testing.assert_array_equal(rotate_image(self.frame, 90)[0, -1], self.frame[0, 0])
        for rotation in ROTATIONS:
            rotated = rotate_image(self.frame, rotation)
            self.assertTrue(rotated.flags.c_contiguous)
            self.assertEqual(rotated.shape[1::-1], rotated_size(14, 9, rotation))
            self.assertEqual(sorted(rotated.reshape(-1, 3).tolist()),
                             sorted(self.frame.reshape(-1, 3).tolist()))
            for y in range(rotated.shape[0]):
                for x in range(rotated.shape[1]):
                    cx, cy = unrotate_point(x, y, 14, 9, rotation)
                    np.testing.assert_array_equal(rotated[y, x], self.frame[cy, cx])
        self.assertEqual(rotation_filter(0), "")
        for invalid in (45, -90, 360, True, 90.0, "90", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                rotate_image(self.frame, invalid)

    def test_processor_rotates_after_crop_and_zero_is_unchanged(self):
        processor = FrameProcessor()
        crop = {"crop_top": 1, "crop_bottom": 2, "crop_left": 3, "crop_right": 4}
        settings = {"brightness": 15, "contrast": 20, "sharpness": 30, **crop}
        unrotated = processor.process(self.frame, **settings)
        self.assertEqual(unrotated.shape, (6, 7, 3))
        np.testing.assert_array_equal(processor.process(self.frame, **settings, rotation=0),
                                      unrotated)
        np.testing.assert_array_equal(FrameProcessor.process_frame(self.frame), self.frame)
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                np.testing.assert_array_equal(
                    processor.process(self.frame, **settings, rotation=rotation),
                    rotate_image(unrotated, rotation))
        with self.assertRaises(ValueError):
            processor.process(self.frame, rotation=45)


class RotationViewerTests(unittest.TestCase):
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
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.viewer = OSVEV(decoder_backend="opencv")
        self.addCleanup(self.viewer.close)
        self.viewer.show()
        self.app.processEvents()
        self.viewer.current_frame = np.random.default_rng(74).integers(
            0, 256, (137, 193, 3), np.uint8)
        self.viewer.frame_count = 1
        self.viewer.update_frame()

    def control_order(self):
        """Top-level Control Panel rows, each named by its first widget's text."""
        layout = self.viewer.rotation_dropdown.parentWidget().layout()
        rows = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widgets = [item.widget()] if item.widget() else [
                item.layout().itemAt(i).widget() for i in range(item.layout().count())
                if item.layout().itemAt(i).widget() is not None]
            rows.append(widgets)
        return rows

    def row_index(self, widget):
        return next(i for i, row in enumerate(self.control_order()) if widget in row)

    def test_rotate_dropdown_position_choices_and_default(self):
        dropdown = self.viewer.rotation_dropdown
        self.assertEqual([dropdown.itemText(i) for i in range(dropdown.count())],
                         ["0° (Original)", "90° clockwise", "180°", "270° clockwise"])
        self.assertEqual([dropdown.itemData(i) for i in range(dropdown.count())], [0, 90, 180, 270])
        self.assertEqual(dropdown.currentData(), 0)
        crop_row = self.row_index(self.viewer.crop_spins["crop_right"])
        rotate_row = self.row_index(dropdown)
        self.assertEqual(rotate_row, crop_row + 1)
        self.assertEqual(self.row_index(self.viewer.zoom_dropdown), rotate_row + 1)
        labels = [w.text() for w in self.control_order()[rotate_row] if isinstance(w, QLabel)]
        self.assertEqual(labels, ["Rotate:"])

    def test_inspection_aids_heading_groups_preview_only_controls(self):
        rows = self.control_order()
        heading = self.row_index(self.viewer.inspection_aids_label)
        self.assertEqual(self.viewer.inspection_aids_label.text(),
                         "<b>Inspection Aids (Preview Only)</b>")
        separator = rows[heading - 1][0]
        self.assertIsInstance(separator, QFrame)
        self.assertEqual(separator.frameShape(), QFrame.Shape.HLine)
        self.assertEqual(heading - 2, self.row_index(self.viewer.interpolation_dropdown))
        self.assertEqual(self.row_index(self.viewer.original_checkbox), heading + 1)
        self.assertEqual(self.row_index(self.viewer.green_checkbox), heading + 1)
        self.assertGreater(self.row_index(self.viewer.compression_slider), heading + 1)
        self.assertEqual(self.row_index(self.viewer.compression_slider), len(rows) - 1)

    def test_rotation_applies_to_main_and_magnified_views_and_inspector(self):
        viewer = self.viewer
        source = viewer.current_frame
        for name, value in (("crop_top", 7), ("crop_bottom", 4),
                            ("crop_left", 11), ("crop_right", 6)):
            viewer.crop_spins[name].setValue(value)
        unrotated = viewer._get_processed_frame().copy()
        self.assertEqual(unrotated.shape, (126, 176, 3))
        for rotation in (90, 180, 270, 0):
            with self.subTest(rotation=rotation):
                viewer.rotation_dropdown.setCurrentIndex(ROTATIONS.index(rotation))
                self.app.processEvents()
                processed = viewer._get_processed_frame()
                np.testing.assert_array_equal(processed, rotate_image(unrotated, rotation))
                pixmap = viewer.main_label.pixmap
                self.assertAlmostEqual(pixmap.width() / pixmap.height(),
                                       processed.shape[1] / processed.shape[0], delta=0.02)
                roi = viewer.current_zoomed_roi
                lens = viewer.main_label.lens_rect
                scale_x = processed.shape[1] / pixmap.width()
                scale_y = processed.shape[0] / pixmap.height()
                x = max(0, int(lens.x() * scale_x))
                y = max(0, int(lens.y() * scale_y))
                np.testing.assert_array_equal(roi, processed[y:y + roi.shape[0], x:x + roi.shape[1]])
                tx, ty, _ = viewer._crosshair_target(processed)
                cx, cy = unrotate_point(tx, ty, 176, 126, rotation)
                sx, sy = cx + 11, cy + 7
                np.testing.assert_array_equal(processed[ty, tx], source[sy, sx])
                b, g, r = map(int, source[sy, sx])
                item = viewer.pixel_values_widget.item(1, 1)
                self.assertTrue(item.text().startswith(f"{r}, {g}, {b} (source RGB: {r}, {g}, {b})"),
                                item.text())
                self.assertIn(f"maps to source ({sx}, {sy})", item.toolTip())
                if rotation:
                    self.assertIn(ROTATION_LABELS[rotation], item.toolTip())
                    self.assertIn(f"Rotate: {ROTATION_LABELS[rotation]}",
                                  viewer.pixel_values_widget.item(2, 1).text())
        self.assertNotIn("rotated pixel", viewer.pixel_values_widget.item(1, 1).toolTip())
        viewer.rotation_dropdown.setCurrentIndex(1)
        viewer.reset_settings()
        self.assertEqual(viewer.rotation_dropdown.currentData(), 0)
        np.testing.assert_array_equal(viewer._get_processed_frame(), source)
        self.assertEqual(self.messages, [])

    def test_rotation_is_part_of_export_and_preview_settings(self):
        viewer = self.viewer
        viewer.rotation_dropdown.setCurrentIndex(3)
        params = viewer.get_processing_params()
        self.assertEqual(params["rotation"], 270)
        self.assertNotEqual(viewer._processing_cache_key(0, params),
                            viewer._processing_cache_key(0, {**params, "rotation": 0}))


@unittest.skipUnless(AVAILABLE, "Optional PyAV and FFmpeg/FFprobe are required")
class RotationExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from frame_info import FrameInfoHandler
        cls.temp = tempfile.TemporaryDirectory(prefix="osvev-rotation-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        encoded, cls.source = root / "encoded.mp4", root / "source.mp4"
        frames = np.random.default_rng(202).integers(0, 256, (3, 48, 64, 3), dtype=np.uint8)
        for arguments, data in (
            (["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "64x48", "-r", "5", "-i", "pipe:0",
              "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
              "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709",
              str(encoded)], frames.tobytes()),
            (["-i", str(encoded), "-c:v", "copy", "-bsf:v",
              "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
              "matrix_coefficients=1:video_full_range_flag=0", str(cls.source)], None),
        ):
            subprocess.run(["ffmpeg", "-v", "error", *arguments], input=data,
                           check=True, capture_output=True, timeout=60)
        cls.metadata = FrameInfoHandler.get_frame_metadata(str(cls.source))

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="osvev-rotation-out-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def plan(self, kind, rotation, name, *, zoom=1, interpolation="Nearest", **extra):
        from ffmpeg_export import build_export_plan
        extension = {"png": ".png", "ffv1": ".mkv", "h264": ".mp4"}[kind]
        settings = {"contrast": 20, "crop_top": 1, "crop_bottom": 3, "crop_left": 5,
                    "crop_right": 7, **extra}
        if rotation is not None:
            settings["rotation"] = rotation
        return build_export_plan(str(self.source), str(self.root / (name + extension)), kind,
                                 1, 2 if kind != "png" else 1, settings, self.metadata, "pyav",
                                 zoom_level=zoom, interpolation=interpolation)

    def test_rotated_exports_are_exact_verified_and_documented(self):
        from export_runner import execute_export
        reference = self.plan("png", 0, "reference")
        execute_export(reference, self.metadata)
        unrotated = cv2.imread(str(reference.output_path(0)))
        self.assertEqual(unrotated.shape, (44, 52, 3))
        for rotation in (90, 180, 270):
            for kind, zoom, interpolation in (("png", 1, "Nearest"), ("ffv1", 2, "Bicubic"),
                                              ("h264", 1, "Nearest")):
                with self.subTest(rotation=rotation, kind=kind):
                    plan = self.plan(kind, rotation, f"{kind}-{rotation}", zoom=zoom,
                                     interpolation=interpolation)
                    width, height = rotated_size(52, 44, rotation)
                    self.assertEqual((plan.width, plan.height), (width * zoom, height * zoom))
                    graph = plan.filter_graph
                    self.assertLess(graph.index("crop=w="), graph.index(rotation_filter(rotation)))
                    if zoom > 1:
                        self.assertLess(graph.index(rotation_filter(rotation)),
                                        graph.index("scale=w="))
                    report = execute_export(plan, self.metadata)
                    self.assertEqual(report["comparison"]["filter_mismatch_frames"], 0)
                    if kind != "h264":
                        self.assertEqual(report["comparison"]["output_mismatch_frames"], 0)
                    self.assertEqual(report["settings"]["rotation"], rotation)
                    self.assertEqual(report["rotation"]["clockwise_degrees"], rotation)
                    self.assertEqual(report["rotation"]["ffmpeg_filter"], rotation_filter(rotation))
                    text = plan.log_path.read_text(encoding="utf-8")
                    self.assertIn("crop -> rotation -> export scaling", text)
                    self.assertIn(f"Rotation: {ROTATION_LABELS[rotation]}; exact 90-degree", text)
                    self.assertIn(f"rotation: {rotation}", text)
                    if kind == "png":
                        # Independent of the processor: exact rotation of the unrotated export.
                        np.testing.assert_array_equal(
                            cv2.imread(str(plan.output_path(0))), rotate_image(unrotated, rotation))

    def test_zero_rotation_preserves_existing_plan_and_invalid_rotation_is_rejected(self):
        from ffmpeg_export import ExportError
        for kind in ("png", "ffv1", "h264"):
            legacy = self.plan(kind, None, f"legacy-{kind}")
            zero = self.plan(kind, 0, f"legacy-{kind}")
            self.assertEqual(zero.command(), legacy.command())
            self.assertEqual(zero.filter_graph, legacy.filter_graph)
            self.assertNotIn("transpose", zero.filter_graph)
            self.assertNotIn("flip", zero.filter_graph)
        for invalid in (45, -90, 360, True, 90.0, "90"):
            with self.subTest(invalid=invalid), self.assertRaises(ExportError):
                self.plan("ffv1", invalid, "invalid")
        # Rotation swaps dimensions before the H.264 even-size check (52x43 -> 43x52).
        with self.assertRaisesRegex(ExportError, "43x52"):
            self.plan("h264", 90, "odd", crop_bottom=4)


if __name__ == "__main__":
    unittest.main()
