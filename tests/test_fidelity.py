# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import hashlib
import shutil
import subprocess
import unittest
from pathlib import Path

import cv2
import numpy as np

from frame_processor import FrameProcessor
from sample_media import requires_samples
from verify_fidelity import inspect_fidelity


def digest(frame):
    return hashlib.sha256(frame.tobytes()).hexdigest()


def ffmpeg(arguments, data=None):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", *arguments],
        input=data, capture_output=True, timeout=60,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class FidelityTests(unittest.TestCase):
    def setUp(self):
        self.frames = np.random.default_rng(5).integers(
            0, 256, (5, 32, 48, 3), dtype=np.uint8
        )
        self.raw = self.frames.tobytes()
        self.raw_input = [
            "-f", "rawvideo", "-pixel_format", "bgr24",
            "-video_size", "48x32", "-framerate", "5", "-i", "pipe:0",
        ]

    def decode(self, encoded):
        return ffmpeg([
            "-i", "pipe:0", "-map", "0:v:0",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
        ], encoded)

    def test_ffv1_preserves_the_decoded_bgr_baseline_exactly(self):
        encoded = ffmpeg([
            *self.raw_input, "-c:v", "ffv1", "-level", "3",
            "-pix_fmt", "bgr0", "-f", "nut", "pipe:1",
        ], self.raw)
        self.assertEqual(self.decode(encoded), self.raw)

    def test_rgb_h264_lossless_preserves_bgr_bytes(self):
        encoded = ffmpeg([
            *self.raw_input, "-c:v", "libx264rgb", "-crf", "0",
            "-preset", "ultrafast", "-f", "nut", "pipe:1",
        ], self.raw)
        self.assertEqual(self.decode(encoded), self.raw)

    def test_yuv420_h264_lossless_does_not_preserve_bgr_bytes(self):
        encoded = ffmpeg([
            *self.raw_input, "-c:v", "libx264", "-crf", "0",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-f", "nut", "pipe:1",
        ], self.raw)
        decoded = self.decode(encoded)
        self.assertEqual(len(decoded), len(self.raw))
        self.assertNotEqual(decoded, self.raw)

    def test_yuv420_h264_lossless_preserves_its_native_yuv_input(self):
        yuv = ffmpeg([
            *self.raw_input, "-pix_fmt", "yuv420p", "-f", "rawvideo", "pipe:1",
        ], self.raw)
        encoded = ffmpeg([
            "-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", "48x32",
            "-framerate", "5", "-i", "pipe:0", "-c:v", "libx264", "-crf", "0",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-f", "nut", "pipe:1",
        ], yuv)
        decoded = ffmpeg([
            "-i", "pipe:0", "-map", "0:v:0",
            "-pix_fmt", "yuv420p", "-f", "rawvideo", "pipe:1",
        ], encoded)
        self.assertEqual(decoded, yuv)

    @requires_samples()
    def test_sample_source_and_neutral_frames_are_unchanged(self):
        root = Path(__file__).resolve().parent.parent
        for filename in ("short-clip.mp4", "gas-station.mp4"):
            source = root / filename
            with self.subTest(filename=filename):
                before = hashlib.sha256(source.read_bytes()).hexdigest()
                capture = cv2.VideoCapture(str(source))
                self.assertTrue(capture.isOpened())
                hashes = []
                try:
                    for index in range(8):
                        ok, frame = capture.read()
                        self.assertTrue(ok, index)
                        original = frame.copy()
                        processed = FrameProcessor.process_frame(frame)
                        self.assertEqual(digest(processed), digest(frame))
                        np.testing.assert_array_equal(frame, original)
                        hashes.append(digest(frame))
                finally:
                    capture.release()
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
                self.assertEqual(len(set(hashes)), 8)

    @requires_samples("gas-station.mp4")
    def test_read_only_report_scopes_its_hash_guarantees(self):
        source = Path(__file__).resolve().parent.parent / "gas-station.mp4"
        report = inspect_fidelity(source, frame_limit=2)
        self.assertTrue(report["source_file_unchanged"])
        self.assertTrue(report["neutral_processing_pixel_identical"])
        self.assertEqual(len(report["frames"]), 2)
        self.assertIn("not native-YUV", report["scope"])
        self.assertEqual(report["opencv_ffmpeg_pixel_identical"], all(
            record["opencv_bgr_sha256"] == record["ffmpeg_bgr_sha256"]
            for record in report["frames"]
        ))
        for record in report["frames"]:
            self.assertEqual(record["shape"], [698, 1280, 3])
            self.assertEqual(record["dtype"], "uint8")

    def test_report_rejects_unbounded_frame_request(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 32"):
            inspect_fidelity("unused.mp4", frame_limit=1000)


if __name__ == "__main__":
    unittest.main()
