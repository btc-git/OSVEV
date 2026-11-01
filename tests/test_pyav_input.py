# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from frame_info import FrameInfoHandler, MetadataCancelled
from frame_processor import FrameProcessor
from osvev_app import OSVEV
from sample_media import requires_samples

HAS_PYAV = importlib.util.find_spec("av") is not None
if HAS_PYAV:
    import av
    from pyav_input import InputDecodeError, PyAVInputCapture

ROOT = Path(__file__).resolve().parent.parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ffmpeg(args, data=None):
    process = subprocess.run(
        ["ffmpeg", "-v", "error", *map(str, args)], input=data,
        capture_output=True, timeout=60,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode(errors="replace"))
    return process.stdout


def open_indexed(path):
    capture = PyAVInputCapture(str(path))
    try:
        ok, first = capture.read()
        if not ok:
            raise RuntimeError("No first frame.")
        capture.configure_index(FrameInfoHandler.get_frame_metadata(str(path)))
        return capture, first
    except (RuntimeError, OSError, ValueError):
        capture.release()
        raise


@unittest.skipUnless(HAS_PYAV and shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Optional PyAV and FFmpeg/FFprobe are required")
class PyAVInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="osvev-pyav-tests-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.paths = {}
        yy, xx = np.indices((96, 128))
        uv_y, uv_x = np.indices((48, 64))
        cls.known_planes = []
        for i in range(6):
            y = ((xx * 3 + yy * 2 + i * 7) % 256).astype(np.uint8)
            u = ((uv_x * 9 + uv_y * 5 + i * 3) % 256).astype(np.uint8)
            v = ((uv_x * 5 + uv_y * 11 + i * 13) % 256).astype(np.uint8)
            y[:16, :64] = np.repeat(np.array([16, 64, 128, 235], dtype=np.uint8), 16)
            u[:8, :32] = v[:8, :32] = 128
            y[:16, 64:80], u[:8, 32:40], v[:8, 32:40] = 100, 90, 180
            cls.known_planes.append(y.tobytes() + u.tobytes() + v.tobytes())
        base = Path(cls.directory.name) / "base.mp4"
        ffmpeg([
            "-f", "rawvideo", "-pix_fmt", "yuv420p", "-video_size", "128x96",
            "-framerate", "6", "-i", "pipe:0", "-c:v", "libx264", "-crf", "0",
            "-preset", "ultrafast", "-g", "1", "-pix_fmt", "yuv420p", base,
        ], b"".join(cls.known_planes))
        for matrix, code in (("bt601", 6), ("bt709", 1)):
            for range_name in ("limited", "full"):
                path = Path(cls.directory.name) / f"{matrix}-{range_name}.mp4"
                full = int(range_name == "full")
                ffmpeg([
                    "-i", base, "-map", "0:v:0", "-c:v", "copy", "-bsf:v",
                    f"h264_metadata=colour_primaries={code}:transfer_characteristics={code}:"
                    f"matrix_coefficients={code}:video_full_range_flag={full}", path,
                ])
                cls.paths[matrix, range_name] = path

    def test_controls_match_known_native_and_independent_ffmpeg(self):
        established = {
            ("bt601", "limited"): "e285549f24b9cd0af7f334b0a45f5c530c7d4048e566f72038a690dd04d0974f",
            ("bt601", "full"): "3a33b67b37ccef5007409786ddd39945226a7ba353ef3aec123fc84c055faddb",
            ("bt709", "limited"): "74a8a04a0b1856d2ccade87dd15ad84355551eab7fef955ea80017edc01b9644",
            ("bt709", "full"): "b0d8ac8116b01af34a060e824e3d1bd932b10e68cfe50105294b40e6b6d4e47c",
        }
        expected_patch = {
            ("bt601", "limited"): (20, 68, 179),
            ("bt709", "limited"): (16, 77, 190),
            ("bt601", "full"): (32, 75, 172),
            ("bt709", "full"): (29, 82, 181),
        }
        for (matrix, range_name), path in self.paths.items():
            with self.subTest(matrix=matrix, range_name=range_name):
                before = digest(path.read_bytes())
                capture, first = open_indexed(path)
                self.addCleanup(capture.release)
                frames = [first]
                self.assertEqual(capture.native_samples.packed_bytes(), self.known_planes[0])
                saved_native = capture.native_samples
                for index in range(1, 6):
                    ok, frame = capture.read()
                    self.assertTrue(ok)
                    frames.append(frame)
                    self.assertEqual(capture.native_samples.packed_bytes(), self.known_planes[index])
                self.assertEqual(capture.read(), (False, None))
                self.assertEqual(saved_native.packed_bytes(), self.known_planes[0])
                self.assertFalse(saved_native.y.flags.writeable)
                with self.assertRaises(ValueError):
                    saved_native.y[0, 0] = 42
                native_before = saved_native.packed_bytes()
                FrameProcessor.process_frame(first, brightness=25, contrast=20, sharpness=30)
                self.assertEqual(saved_native.packed_bytes(), native_before)
                self.assertEqual(saved_native.pixel(72, 4), (100, 90, 180))
                self.assertEqual(tuple(map(int, first[4, 72])), expected_patch[matrix, range_name])
                reference = ffmpeg([
                    "-threads", "1", "-i", path, "-map", "0:v:0",
                    "-fps_mode", "passthrough", "-vf",
                    f"scale=in_color_matrix={matrix}:out_color_matrix={matrix}:"
                    f"in_range={range_name}:out_range=full:flags=bicubic",
                    "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
                ])
                actual = b"".join(frame.tobytes() for frame in frames)
                self.assertEqual(actual, reference)
                self.assertEqual(digest(actual), established[matrix, range_name])
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.assertEqual(digest(capture.read()[1].tobytes()), digest(first.tobytes()))
                for index in (5, 2, 1, 4, 0):
                    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                    self.assertEqual(digest(capture.read()[1].tobytes()), digest(frames[index].tobytes()))
                    self.assertEqual(capture.native_samples.packed_bytes(), self.known_planes[index])
                self.assertEqual(digest(path.read_bytes()), before)

    def test_index_rejects_missing_contradictory_or_unsupported_metadata(self):
        path = self.paths["bt709", "limited"]
        metadata = FrameInfoHandler.get_frame_metadata(str(path))
        for field, value in (
            ("pts", None), ("color_space", "unknown"), ("color_range", "pc"),
            ("pix_fmt", "yuv420p10le"), ("width", 130), ("chroma_location", "center"),
            ("interlaced_frame", 1), ("color_transfer", "smpte2084"),
            ("color_primaries", "unknown"),
        ):
            with self.subTest(field=field):
                capture = PyAVInputCapture(str(path))
                self.addCleanup(capture.release)
                capture.read()
                altered = [dict(entry) for entry in metadata]
                altered[-1][field] = value
                with self.assertRaises(InputDecodeError):
                    capture.configure_index(altered)
                self.assertTrue(capture.isOpened())
        capture = PyAVInputCapture(str(path))
        self.addCleanup(capture.release)
        capture.read()
        with self.assertRaises(InputDecodeError):
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        with self.assertRaises(InputDecodeError):
            capture.configure_index([])
        duplicate = [dict(entry) for entry in metadata]
        duplicate[1]["pts"] = duplicate[0]["pts"] - 1
        with self.assertRaisesRegex(InputDecodeError, "nondecreasing"):
            capture.configure_index(duplicate)

    def test_unknown_native_tags_are_rejected_before_conversion(self):
        capture, first = open_indexed(self.paths["bt709", "limited"])
        self.addCleanup(capture.release)
        original = capture.native_samples
        bad = av.VideoFrame(128, 96, "yuv420p")
        bad.pts, bad.time_base = original.frame.pts, original.frame.time_base
        with self.assertRaises(InputDecodeError):
            capture._validate_frame(bad)
        self.assertIs(capture.native_samples, original)
        with self.assertRaises(InputDecodeError):
            capture.set(cv2.CAP_PROP_POS_FRAMES, 6)
        with self.assertRaises(InputDecodeError):
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0.5)
        capture.release()
        self.assertFalse(capture.isOpened())
        self.assertIsNone(capture.native_samples)
        with self.assertRaises(InputDecodeError):
            capture.read()

    def test_exact_seeking_with_nonzero_and_irregular_pts(self):
        path = Path(self.directory.name) / "nonzero-irregular.mp4"
        ffmpeg([
            "-i", self.paths["bt709", "limited"], "-vf",
            "setpts=PTS+2/TB+if(gte(N\\,3)\\,1/TB\\,0)",
            "-fps_mode", "passthrough", "-c:v", "libx264", "-crf", "0",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv", path,
        ])
        metadata = FrameInfoHandler.get_frame_metadata(str(path))
        self.assertGreater(metadata[0]["pts"], 0)
        deltas = [b["pts"] - a["pts"] for a, b in zip(metadata, metadata[1:])]
        self.assertGreater(len(set(deltas)), 1)
        capture, first = open_indexed(path)
        self.addCleanup(capture.release)
        for index in (5, 0, 3, 1, 4, 2, 0):
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            self.assertTrue(ok)
            self.assertEqual(capture.native_samples.frame.pts, metadata[index]["pts"])
            self.assertEqual(capture.native_samples.packed_bytes(), self.known_planes[index])

    @requires_samples()
    def test_complete_samples_match_established_ordered_hash_manifests(self):
        cases = [
            ("short-clip.mp4", 2493,
             "d5c35e135edf654e63f4c9d5da253b520cc2b7ad1e039662473cd56e96aa229d",
             "67285f105f19d06f9ff62fc962ebc1584710577a113f0b1aedcc1c2be93e940a",
             "c0d4ffb1911d8b37a63b07ba707073a2f4a69d25ea1aa2454e00eb9adc1ccbae"),
            ("gas-station.mp4", 2900,
             "bebdb46f71a772aca3cfb33948c0b18e1d0e4376bf63bfce6749203af2ba802d",
             "d3b801b0a04eadcfecafaa2bdeaa2472fad17c9c2cb9ca96aebc2ec1a157ab5d",
             "be72df5a1e49e2404c7af0822425cbca8d6607cad53aaf02dd98a5f902f9e737"),
        ]
        for filename, count, source_hash, native_hash, bgr_hash in cases:
            with self.subTest(filename=filename):
                path = ROOT / filename
                self.assertEqual(digest(path.read_bytes()), source_hash)
                metadata = FrameInfoHandler.get_frame_metadata(str(path))
                if filename == "gas-station.mp4":
                    self.assertIn("B", {entry["pict_type"] for entry in metadata})
                capture, first = open_indexed(path)
                self.addCleanup(capture.release)
                native_manifest, bgr_manifest = hashlib.sha256(), hashlib.sha256()
                selected = {}
                for index in range(count):
                    if index == 0:
                        frame = first
                    else:
                        ok, frame = capture.read()
                        self.assertTrue(ok, index)
                    native = capture.native_samples
                    nh, bh = digest(native.packed_bytes()), digest(frame.tobytes())
                    identity = f"{index}|{native.frame.pts}|{native.frame.time_base}|"
                    native_manifest.update(f"{identity}{nh}\n".encode())
                    bgr_manifest.update(f"{identity}{bh}\n".encode())
                    if index in (0, 1, 17, count // 2, count - 1):
                        selected[index] = (nh, bh)
                    self.assertEqual(digest(FrameProcessor.process_frame(frame).tobytes()), bh)
                self.assertEqual(capture.read(), (False, None))
                self.assertEqual(native_manifest.hexdigest(), native_hash)
                self.assertEqual(bgr_manifest.hexdigest(), bgr_hash)
                for index in (count - 1, 0, count // 2, 17, 1, 0):
                    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                    ok, frame = capture.read()
                    self.assertTrue(ok, index)
                    self.assertEqual(
                        (digest(capture.native_samples.packed_bytes()), digest(frame.tobytes())),
                        selected[index],
                    )
                self.assertEqual(digest(path.read_bytes()), source_hash)


@unittest.skipUnless(HAS_PYAV and shutil.which("ffprobe"), "Optional PyAV/FFprobe required")
@requires_samples()
class PyAVViewerTests(unittest.TestCase):
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
        self.viewer = OSVEV(decoder_backend="pyav")
        self.viewer.show()
        self.app.processEvents()
        self.addCleanup(self.viewer.close)

    def load_sample(self):
        with patch.object(QFileDialog, "getOpenFileName",
                          return_value=(str(ROOT / "gas-station.mp4"), "")):
            self.viewer.open_video()
        self.assertEqual(self.messages, [])
        self.assertIsInstance(self.viewer.cap, PyAVInputCapture)

    def test_loading_navigation_inspection_controls_and_playback(self):
        self.load_sample()
        self.assertEqual(digest(self.viewer.current_frame.tobytes()),
                         "dbf8e15ebbcf0b45a6d82e106f0f5525aa6231cd9455f15552c8d2566a88b36e")
        native = self.viewer.current_native_samples.packed_bytes()
        self.viewer.brightness_slider.setValue(20)
        self.viewer.zoom_dropdown.setCurrentText("30x")
        self.viewer.nn_zoom_slider.setValue(50)
        self.viewer.interpolation_dropdown.setCurrentText("Lanczos")
        self.viewer.green_checkbox.setChecked(True)
        self.assertEqual(self.viewer.current_native_samples.packed_bytes(), native)
        tooltip = self.viewer.pixel_values_widget.item(0, 1).toolTip()
        self.assertIn("Native source Y/U/V", tooltip)
        self.assertIn("not values reconstructed from RGB", tooltip)
        self.viewer.reset_settings()
        self.viewer.manual_next_frame()
        self.assertEqual(self.viewer.current_frame_index, 1)
        self.viewer.previous_frame()
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.viewer.previous_frame()
        self.assertEqual(self.viewer.current_frame_index, 2899)
        self.viewer.manual_next_frame()
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.viewer.timeline_slider.setSliderDown(True)
        self.viewer.timeline_slider.setSliderPosition(17)
        self.viewer.timeline_slider.setSliderDown(False)
        self.assertEqual(self.viewer.current_frame_index, 17)
        self.assertIs(self.viewer.current_native_samples, self.viewer.cap.native_samples)
        self.assertEqual(self.viewer.current_native_samples.frame.pts, 17017)
        ticks = []
        QTimer.singleShot(140, lambda: (ticks.append(True), self.viewer.pause_video()))
        self.viewer.resume_video()
        deadline = time.monotonic() + 2
        while self.viewer.is_playing and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        self.assertEqual(ticks, [True])
        self.assertGreater(self.viewer.current_frame_index, 17)
        self.assertFalse(self.viewer.timer.isActive())
        self.assertEqual(self.messages, [])
        self.viewer.close()
        self.assertFalse(self.viewer.cap.isOpened())
        self.assertIsNone(self.viewer.current_native_samples)

    def test_default_is_pyav_and_cropped_inspector_maps_native_source_coordinates(self):
        self.assertEqual(OSVEV.__init__.__defaults__, ("pyav",))
        self.load_sample()
        source = self.viewer.current_frame.copy()
        native = self.viewer.current_native_samples
        packed = native.packed_bytes()
        self.viewer.brightness_slider.setValue(20)
        self.viewer.contrast_slider.setValue(32)
        self.viewer.sharpness_slider.setValue(30)
        for name, value in {"crop_top": 37, "crop_bottom": 9,
                            "crop_left": 101, "crop_right": 7}.items():
            self.viewer.crop_spins[name].setValue(value)
        processed = self.viewer._get_processed_frame()
        self.assertEqual(processed.shape, (652, 1172, 3))
        lens = self.viewer.main_label.lens_rect
        image = self.viewer.main_label.pixmap
        x = min(1171, int((lens.x() + lens.width() // 2) * 1172 / image.width()))
        y = min(651, int((lens.y() + lens.height() // 2) * 652 / image.height()))
        source_x, source_y = x + 101, y + 37
        b, g, r = map(int, processed[y, x])
        sb, sg, sr = map(int, source[source_y, source_x])
        self.assertEqual(self.viewer.pixel_values_widget.item(1, 1).text(),
                         f"{r}, {g}, {b} (source RGB: {sr}, {sg}, {sb})")
        tooltip = self.viewer.pixel_values_widget.item(0, 1).toolTip()
        ny, nu, nv = native.pixel(source_x, source_y)
        self.assertEqual(self.viewer.pixel_values_widget.item(0, 0).text(), "Native Source YCbCr")
        self.assertEqual(self.viewer.pixel_values_widget.item(0, 1).text(),
                         f"Y′ {ny}, Cb {nu}, Cr {nv}")
        rows = {self.viewer.frame_info_table.item(i, 0).text()
                for i in range(self.viewer.frame_info_table.rowCount())}
        self.assertNotIn("Native Source Samples", rows)
        self.assertIn("Inspection Format", rows)
        self.assertEqual(self.viewer.original_checkbox.text(), "Highlight Original Pixels")
        self.assertEqual(self.viewer.green_checkbox.text(), "Highlight Interpolated Pixels")
        native_text = self.viewer.pixel_values_widget.item(0, 1).text()
        for zoom, interpolation in (("2x", "Bilinear"), ("5x", "Lanczos")):
            self.viewer.zoom_dropdown.setCurrentText(zoom)
            self.viewer.interpolation_dropdown.setCurrentText(interpolation)
            tx, ty, factor = self.viewer._crosshair_target(processed)
            self.assertEqual((tx // factor, ty // factor), (x, y))
            self.assertEqual(self.viewer.pixel_values_widget.item(0, 1).text(), native_text)
            self.assertIn(f"maps to source ({source_x}, {source_y})",
                          self.viewer.pixel_values_widget.item(1, 1).toolTip())
            self.assertIn(f"(source RGB: {sr}, {sg}, {sb})",
                          self.viewer.pixel_values_widget.item(1, 1).text())
        self.viewer.zoom_dropdown.setCurrentText("1x")
        self.assertIn(f"source ({source_x}, {source_y})", tooltip)
        self.assertIn(f"{ny}, {nu}, {nv}", tooltip)
        self.assertIn(f"({source_x // 2}, {source_y // 2})", tooltip)
        expected_hash = digest(processed.tobytes())
        for interpolation in ("Nearest", "Bilinear", "Bicubic", "Lanczos"):
            self.viewer.interpolation_dropdown.setCurrentText(interpolation)
            self.viewer.zoom_dropdown.setCurrentText("30x")
            self.viewer.nn_zoom_slider.setValue(50)
            self.viewer.green_checkbox.setChecked(True)
            self.viewer.original_checkbox.setChecked(True)
            self.viewer.playback_speed_dropdown.setCurrentText("5x")
            self.viewer.update_frame()
            self.assertEqual(digest(self.viewer._get_processed_frame().tobytes()), expected_hash)
            self.assertEqual(native.packed_bytes(), packed)
        self.viewer.manual_next_frame()
        self.assertEqual(self.viewer.current_frame_index, 1)
        self.viewer.set_frame_position(17)
        self.assertEqual(self.viewer.current_frame_index, 17)
        self.viewer.previous_frame()
        self.assertEqual(self.viewer.current_frame_index, 16)
        self.viewer.reset_settings()
        self.assertTrue(all(spin.value() == 0 for spin in self.viewer.crop_spins.values()))
        np.testing.assert_array_equal(self.viewer._get_processed_frame(), self.viewer.current_frame)
        self.assertEqual(self.messages, [])

    def test_decode_error_preserves_display_and_native_identity(self):
        self.load_sample()
        frame, native = self.viewer.current_frame, self.viewer.current_native_samples
        with patch.object(self.viewer.cap, "read", side_effect=InputDecodeError("test input failure")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.viewer.manual_next_frame()
            self.viewer.previous_frame()
            self.viewer.set_frame_position(20)
            self.viewer.next_frame()
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.assertIs(self.viewer.current_frame, frame)
        self.assertIs(self.viewer.current_native_samples, native)
        self.assertEqual(len(self.messages), 4)
        self.assertFalse(self.viewer.is_playing)
        self.messages.clear()
        self.viewer.set_frame_position(0)
        self.assertEqual(digest(self.viewer.current_frame.tobytes()), digest(frame.tobytes()))
        self.assertEqual(self.messages, [])

    def test_fast_playback_keeps_native_and_bgr_identity_without_seeking(self):
        self.load_sample()
        reference = PyAVInputCapture(str(ROOT / "gas-station.mp4"))
        self.addCleanup(reference.release)
        for _ in range(6):
            ok, expected = reference.read()
            self.assertTrue(ok)
        self.viewer.playback_speed_dropdown.setCurrentText("5x")
        self.viewer.resume_video()
        clock = self.viewer._playback_clock
        with patch("osvev_app.time.perf_counter", return_value=clock.origin + 0.034), \
                patch.object(self.viewer.cap, "set", wraps=self.viewer.cap.set) as seek:
            self.viewer._playback_tick()
        self.viewer.pause_video()
        seek.assert_not_called()
        self.assertEqual(self.viewer.current_frame_index, 5)
        self.assertEqual(self.viewer.current_native_samples.frame.pts, 5005)
        self.assertEqual(self.viewer.current_native_samples.packed_bytes(),
                         reference.native_samples.packed_bytes())
        np.testing.assert_array_equal(self.viewer.current_frame, expected)
        self.viewer.update_frame()
        np.testing.assert_array_equal(self.viewer._get_processed_frame(), expected)

    def test_metadata_failure_keeps_old_video_without_opencv_fallback(self):
        self.load_sample()
        original, native = self.viewer.cap, self.viewer.current_native_samples
        with patch.object(QFileDialog, "getOpenFileName",
                          return_value=(str(ROOT / "short-clip.mp4"), "")), \
                patch.object(FrameInfoHandler, "get_frame_metadata",
                             side_effect=RuntimeError("required metadata missing")), \
                patch("osvev_app.cv2.VideoCapture") as fallback:
            self.viewer.open_video()
        self.assertIs(self.viewer.cap, original)
        self.assertIs(self.viewer.current_native_samples, native)
        self.assertTrue(original.isOpened())
        self.assertEqual(len(self.messages), 1)
        self.assertIn("requires source metadata", self.messages[0][1])
        fallback.assert_not_called()

    def test_missing_pyav_dependency_does_not_switch_decoders(self):
        with patch.dict("sys.modules", {"pyav_input": None}), \
                patch("osvev_app.cv2.VideoCapture") as fallback:
            with self.assertRaisesRegex(RuntimeError, "No automatic decoder fallback"):
                self.viewer._open_input_capture(str(ROOT / "gas-station.mp4"))
        fallback.assert_not_called()
        self.assertEqual(self.viewer.decoder_backend, "pyav")

    def test_cancel_and_close_during_pyav_load_release_candidate(self):
        for close in (False, True):
            with self.subTest(close=close):
                candidate = PyAVInputCapture(str(ROOT / "gas-station.mp4"))
                def scan(path, cancelled, **kwargs):
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if cancelled():
                            raise MetadataCancelled()
                        time.sleep(0.005)
                    raise RuntimeError("Load cancellation was not delivered.")
                def interrupt():
                    if close:
                        self.viewer.close()
                    else:
                        self.viewer._loading_progress.findChild(QPushButton).click()
                QTimer.singleShot(30, interrupt)
                with patch.object(QFileDialog, "getOpenFileName",
                                  return_value=(str(ROOT / "gas-station.mp4"), "")), \
                        patch.object(self.viewer, "_open_input_capture", return_value=candidate), \
                        patch.object(FrameInfoHandler, "get_frame_metadata", side_effect=scan):
                    self.viewer.open_video()
                self.assertFalse(candidate.isOpened())
                self.assertIsNone(self.viewer._metadata_worker)
                self.assertEqual(self.messages, [])


if __name__ == "__main__":
    unittest.main()
