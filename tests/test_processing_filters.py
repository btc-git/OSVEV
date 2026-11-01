# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import itertools
import shutil
import subprocess
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from frame_processor import FrameProcessor
from processing_filters import enhancement_filter, enhancement_stages


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class SharedProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ramp = np.tile(np.arange(256, dtype=np.uint8)[None, :, None], (16, 1, 3))
        rng = np.random.default_rng(174)
        cls.frames = np.stack((
            ramp, np.zeros_like(ramp), np.full_like(ramp, 255),
            rng.integers(0, 256, ramp.shape, dtype=np.uint8),
        ))
        cls.frames[3, 0, :] = [0, 0, 255]
        cls.frames[3, -1, :] = [255, 0, 0]
        cls.frames[3, :, 0] = [0, 255, 0]

    def compare(self, settings, frames=None):
        frames = self.frames if frames is None else frames
        height, width = frames.shape[1:3]
        result = subprocess.run([
            "ffmpeg", "-v", "error", "-filter_threads", "1", "-f", "rawvideo",
            "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-i", "pipe:0",
            "-vf", enhancement_filter(**settings), "-threads", "1",
            "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
        ], input=frames.tobytes(), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        processor = FrameProcessor()
        results = [processor.process(frame, **settings) for frame in frames]
        self.assertTrue(all(frame.flags.c_contiguous for frame in results))
        actual = b"".join(frame.tobytes() for frame in results)
        self.assertEqual(actual, result.stdout)

    def test_all_brightness_settings_clip_and_round_exactly(self):
        for brightness in range(-100, 101):
            with self.subTest(brightness=brightness):
                self.compare({"brightness": brightness})

    def test_all_sharpening_amounts_and_colored_edges_match(self):
        for sharpness in range(101):
            with self.subTest(sharpness=sharpness):
                self.compare({"sharpness": sharpness})

    def test_combinations_extremes_and_rounding_sensitive_contrast(self):
        for brightness, contrast, sharpness in itertools.product(
            (-100, 0, 100), (-100, 32, 100), (0, 1, 100)
        ):
            with self.subTest(b=brightness, c=contrast, s=sharpness):
                self.compare(dict(brightness=brightness, contrast=contrast, sharpness=sharpness))
        self.compare(dict(brightness=20, contrast=20, sharpness=30))

    def test_small_odd_sizes_and_boundary_pixels(self):
        for height, width in ((1, 1), (2, 2), (3, 5), (7, 9)):
            with self.subTest(height=height, width=width):
                frame = np.random.default_rng(width).integers(
                    0, 256, (3, height, width, 3), dtype=np.uint8
                )
                self.compare(dict(brightness=20, contrast=32, sharpness=100), frame)

    def test_graph_reuse_slider_changes_geometry_and_seek_order(self):
        processor = FrameProcessor()
        settings = dict(brightness=20, contrast=32, sharpness=30)
        expected = processor.process(self.frames[0], **settings)
        graph = processor._graph
        for index in (3, 2, 0, 1, 0):
            actual = processor.process(self.frames[index], **settings)
            self.assertIs(processor._graph, graph)
            if index == 0:
                np.testing.assert_array_equal(actual, expected)
        processor.process(self.frames[0], brightness=21)
        self.assertIsNot(processor._graph, graph)
        processor.process(self.frames[0, :7, :9], **settings)
        neutral = processor.process(self.frames[0])
        np.testing.assert_array_equal(neutral, self.frames[0])
        self.assertFalse(np.shares_memory(neutral, self.frames))
        self.assertIsNone(processor._graph)

    def test_jpeg_is_explicit_post_core_teaching_simulation(self):
        processor = FrameProcessor()
        settings = dict(brightness=20, contrast=32, sharpness=30)
        core = processor.process(self.frames[3], **settings)
        ok, encoded = cv2.imencode(".jpg", core, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        self.assertTrue(ok)
        expected = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        np.testing.assert_array_equal(
            processor.process(self.frames[3], compression=20, **settings), expected
        )

    def test_invalid_inputs_and_filter_errors_do_not_fall_back(self):
        processor = FrameProcessor()
        for settings in ({"brightness": 101}, {"sharpness": -1},
                         {"contrast": True}, {"compression": 100}):
            with self.assertRaises(ValueError):
                processor.process(self.frames[0], **settings)
        with self.assertRaises(ValueError):
            processor.process(np.zeros((4, 4), dtype=np.uint8))
        import av
        with patch("av.filter.Graph", side_effect=av.FFmpegError(22, "test graph failure")):
            with self.assertRaisesRegex(RuntimeError, "processing failed"):
                processor.process(self.frames[0], brightness=1)
        self.assertIsNone(processor._graph)

    def test_processing_order_and_explicit_color_domains(self):
        stages = enhancement_stages(20, 32, 30)
        names = [name for name, _ in stages]
        self.assertEqual(names, ["scale", "format", "eq", "unsharp", "scale", "format"])
        self.assertEqual(dict(stages)["eq"], "brightness=0.20:contrast=1.32")
        for name, options in stages:
            if name == "scale":
                self.assertIn("in_range=full:out_range=full", options)
                self.assertIn("in_color_matrix=bt709:out_color_matrix=bt709", options)
        self.assertEqual(stages[-1], ("format", "pix_fmts=bgr24"))
        self.assertEqual(enhancement_stages(), ())

    def test_standard_eq_slider_mappings_and_neutral_bypass(self):
        for brightness, contrast in ((-100, -100), (100, 100), (-1, 1), (0, 32)):
            with self.subTest(brightness=brightness, contrast=contrast):
                nodes = enhancement_stages(brightness, contrast)
                self.assertEqual(
                    nodes[2], ("eq", f"brightness={brightness / 100:.2f}:contrast={1 + contrast / 100:.2f}")
                )
                self.assertEqual(sum(name == "scale" for name, _ in nodes), 2)
        self.assertNotIn("eq", dict(enhancement_stages(sharpness=30)))
        processor = FrameProcessor()
        with patch("av.filter.Graph", side_effect=AssertionError("Neutral must not use a graph")):
            np.testing.assert_array_equal(processor.process(self.frames[3]), self.frames[3])

    def test_crop_is_exact_after_enhancements_without_rebuilding_graph(self):
        from crop_utils import crop_rectangle
        processor = FrameProcessor()
        settings = dict(brightness=20, contrast=32, sharpness=100)
        frame = self.frames[3]
        full = processor.process(frame, **settings)
        graph = processor._graph
        for crop in (
            dict(crop_top=1, crop_bottom=2, crop_left=3, crop_right=4),
            dict(crop_left=255, crop_top=15),
        ):
            with self.subTest(crop=crop):
                rectangle = crop_rectangle(256, 16, **crop)
                actual = processor.process(frame, **settings, **crop)
                self.assertIs(processor._graph, graph)
                np.testing.assert_array_equal(actual, full[
                    rectangle.top:rectangle.top + rectangle.height,
                    rectangle.left:rectangle.left + rectangle.width,
                ])
                result = subprocess.run([
                    "ffmpeg", "-v", "error", "-filter_threads", "1", "-f", "rawvideo",
                    "-pix_fmt", "bgr24", "-s", "256x16", "-i", "pipe:0",
                    "-vf", enhancement_filter(**settings) + "," + rectangle.filter,
                    "-threads", "1", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
                ], input=frame.tobytes(), capture_output=True, timeout=30, check=True)
                self.assertEqual(actual.tobytes(), result.stdout)
                self.assertTrue(actual.flags.c_contiguous)
        for crop in (dict(crop_left=256), dict(crop_top=-1), dict(crop_left=True)):
            with self.subTest(crop=crop), self.assertRaises(ValueError):
                processor.process(frame, **crop)
