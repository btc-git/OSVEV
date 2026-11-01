# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Loaded-file row and header-only FFprobe Info window, using synthetic sources."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QTextDocument
from PyQt6.QtWidgets import QApplication, QMessageBox

import media_info
from media_info import (FileNameLabel, MediaInfoDialog, media_info_text, reveal_in_folder,
                        size_text, summary_line)
from osvev_app import OSVEV

AVAILABLE = importlib.util.find_spec("av") is not None and shutil.which("ffmpeg") and shutil.which("ffprobe")


def ffmpeg(arguments):
    result = subprocess.run(["ffmpeg", "-v", "error", *map(str, arguments)],
                            capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))


def wait_for(condition, timeout=20):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not reached")
        QApplication.processEvents()
        time.sleep(0.01)


VIDEO = {
    "index": 0, "codec_type": "video", "codec_name": "h264",
    "codec_long_name": "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10", "profile": "High",
    "level": 40, "codec_tag_string": "avc1", "width": 1920, "height": 1080,
    "sample_aspect_ratio": "1:1", "display_aspect_ratio": "16:9", "pix_fmt": "yuv420p",
    "color_range": "tv", "color_space": "bt709", "color_primaries": "bt709",
    "color_transfer": "bt709", "r_frame_rate": "30/1", "avg_frame_rate": "30/1",
    "time_base": "1/15360", "start_time": "0.000000", "duration": "828.933000",
    "bit_rate": "666000", "nb_frames": "24868", "bits_per_raw_sample": "8",
    "disposition": {"default": 1, "attached_pic": 0},
}
AUDIO = {
    "index": 1, "codec_type": "audio", "codec_name": "aac",
    "codec_long_name": "AAC (Advanced Audio Coding)", "profile": "LC",
    "codec_tag_string": "mp4a", "sample_rate": "48000", "channels": 2,
    "channel_layout": "stereo", "sample_fmt": "fltp", "r_frame_rate": "0/0",
    "avg_frame_rate": "0/0", "bit_rate": "192000", "duration": "828.928000",
    "disposition": {"default": 1}, "tags": {"title": "simple_aac_recording0"},
}
FORMAT = {
    "format_name": "mov,mp4,m4a,3gp,3g2,mj2", "format_long_name": "QuickTime / MOV",
    "duration": "828.933000", "bit_rate": "902000", "nb_streams": 2,
    "tags": {"major_brand": "isom", "compatible_brands": "isomiso2avc1mp41",
             "encoder": "Lavf61.7.100"},
}


def values(text):
    rows = {}
    for line in text.splitlines():
        key, separator, value = line.partition(" : ")
        if separator:
            rows.setdefault(key.strip(), value)
    return rows


class FormattingTests(unittest.TestCase):
    def test_summary_line_labels_source_attributes(self):
        data = {"streams": [VIDEO, AUDIO], "format": FORMAT}
        size = 93_532_979
        self.assertEqual(summary_line(data, size),
                         "Codec: H.264 | Resolution: 1920x1080 | Avg FPS: 30 | Size: 89.20 MiB")
        self.assertEqual(summary_line({}, size, "Header information unavailable: x"),
                         "FFprobe info unavailable | Size: 89.20 MiB")
        self.assertEqual(summary_line({"streams": [AUDIO]}, None), "No video stream reported")
        hevc = dict(VIDEO, codec_name="hevc", codec_long_name="H.265 / HEVC (High Efficiency "
                    "Video Coding)", profile="Main", level=93)
        self.assertEqual(summary_line({"streams": [hevc]}, None),
                         "Codec: H.265 | Resolution: 1920x1080 | Avg FPS: 30")

    def test_summary_does_not_guess_missing_resolution_or_average_frame_rate(self):
        video = {"codec_type": "video", "codec_name": "h264", "r_frame_rate": "30/1",
                 "avg_frame_rate": "0/0"}
        self.assertEqual(summary_line({"streams": [video]}, None),
                         "Codec: h264 | Resolution: not reported | Avg FPS: not reported")
        video.update(width=640, height=480, avg_frame_rate="30000/1001")
        self.assertEqual(summary_line({"streams": [video]}, 1024),
                         "Codec: h264 | Resolution: 640x480 | Avg FPS: 29.97 | Size: 1.00 KiB")

    def test_size_text_units(self):
        self.assertEqual(size_text(1023), "1023 bytes")
        self.assertEqual(size_text(1024), "1.00 KiB")
        self.assertEqual(size_text(5 * 1024 ** 2), "5.00 MiB")
        self.assertEqual(size_text(3 * 1024 ** 3 // 2), "1.50 GiB")

    def test_media_info_text_reports_headers_and_omits_unreported_values(self):
        path = os.path.join("C:\\", "cases", "clip.mp4")
        text = media_info_text(path, {"streams": [VIDEO, AUDIO], "format": FORMAT}, 93_532_979)
        rows = values(text)
        self.assertIn("not verified against decoded frames", text.splitlines()[0])
        self.assertEqual(rows["Complete name"], os.path.normpath(path))
        self.assertEqual(rows["File size"], "93,532,979 bytes (89.20 MiB)")
        self.assertEqual(rows["Container format"], "QuickTime / MOV (mov,mp4,m4a,3gp,3g2,mj2)")
        self.assertEqual(rows["Writing application"], "Lavf61.7.100")
        self.assertEqual(rows["Duration (reported)"], "00:13:48.933 (828.933000 s)")
        self.assertEqual(rows["Level"], "4")
        self.assertEqual(rows["Width x height"], "1920 x 1080 pixels")
        self.assertEqual(rows["Average frame rate"], "30/1 (30.000 fps)")
        self.assertEqual(rows["Source color primaries"], "bt709")
        self.assertEqual(rows["Overall bit rate (reported)"], "902,000 b/s (902.0 kb/s)")
        self.assertIn("\nAudio\n", text)
        self.assertIn("Sample rate                      : 48000 Hz", text)
        self.assertIn("Channels                         : 2 (stereo)", text)
        self.assertIn("Title                            : simple_aac_recording0", text)
        self.assertNotIn("0/0", text)
        self.assertNotIn("Creation time", text)
        self.assertNotIn("Probe error", text)
        self.assertNotIn("File modified", text)

    def test_unreported_tags_colors_and_repeated_streams(self):
        video = {key: value for key, value in VIDEO.items()
                 if not key.startswith("color_")}
        video["codec_tag_string"] = "[0][0][0][0]"
        second = dict(AUDIO, index=2, tags={"LANGUAGE": "eng"})
        text = media_info_text("clip.mkv", {"streams": [video, AUDIO, second]}, None,
                               modified=0)
        self.assertNotIn("[0][0][0][0]", text)
        self.assertIn("Source color matrix              : not reported", text)
        self.assertIn("\nAudio #1\n", text)
        self.assertIn("\nAudio #2\n", text)
        self.assertIn("Language                         : eng", text)
        self.assertIn("File modified (file system)", text)
        self.assertNotIn("File size", text)

    def test_probe_error_is_shown(self):
        text = media_info_text("clip.mp4", {}, 10, "Header information unavailable: timed out")
        self.assertIn("Probe error                      : Header information unavailable: "
                      "timed out", text)
        self.assertIn("File size                        : 10 bytes (10 bytes)", text)


class RevealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="osvev-media-info-")
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "clip & copy.mp4")
        Path(self.path).write_bytes(b"synthetic")

    def test_windows_selects_file_with_full_explorer_path(self):
        explorer = os.path.join(r"C:\Windows", "explorer.exe")
        with patch.object(media_info.sys, "platform", "win32"), \
                patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}), \
                patch.object(media_info.subprocess, "Popen") as popen:
            reveal_in_folder(self.path)
        popen.assert_called_once_with(f'"{explorer}" /select,"{os.path.normpath(self.path)}"')

    def test_macos_reveals_and_other_platforms_open_folder(self):
        with patch.object(media_info.sys, "platform", "darwin"), \
                patch.object(media_info.subprocess, "Popen") as popen:
            reveal_in_folder(self.path)
        popen.assert_called_once_with(["open", "-R", os.path.normpath(self.path)])
        with patch.object(media_info.sys, "platform", "linux"), \
                patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
            reveal_in_folder(self.path)
        self.assertEqual(os.path.normpath(open_url.call_args.args[0].toLocalFile()),
                         os.path.dirname(os.path.normpath(self.path)))

    def test_missing_file_is_reported_without_launching(self):
        os.remove(self.path)
        with patch.object(media_info.subprocess, "Popen") as popen, \
                self.assertRaisesRegex(FileNotFoundError, "no longer at"):
            reveal_in_folder(self.path)
        popen.assert_not_called()

    def test_label_elides_escapes_and_tooltips_full_path(self):
        label = FileNameLabel()
        self.addCleanup(label.deleteLater)
        label.show()
        self.assertEqual(label.text(), "No video loaded")
        self.assertEqual(label.toolTip(), "")
        label.set_path(self.path)
        label.resize(label.sizeHint())
        self.assertIn("clip &amp; copy.mp4", label.text())
        self.assertIn('href="reveal"', label.text())
        document = QTextDocument()
        document.setHtml(label.text())
        fragment = document.begin().begin().fragment()
        self.assertEqual(fragment.charFormat().foreground().color().name(), "#0057b8")
        self.assertIn(os.path.normpath(self.path), label.toolTip())
        label.resize(label.minimumSizeHint())
        self.assertIn("…", label.text())
        self.assertLess(label.minimumSizeHint().width(), label.sizeHint().width())

    def test_dialog_copy_and_show_in_folder(self):
        dialog = MediaInfoDialog(self.path, {"streams": [VIDEO]}, 9)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.windowTitle(), "FFprobe Info - clip & copy.mp4")
        dialog.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), dialog.text.toPlainText())
        self.assertIn(os.path.normpath(self.path), dialog.text.toPlainText())
        os.remove(self.path)
        with patch.object(QMessageBox, "warning") as warning:
            dialog.show_button.click()
        self.assertIn("no longer at", warning.call_args.args[2])


@unittest.skipUnless(AVAILABLE, "PyAV and FFmpeg/FFprobe are required")
class ViewerFileRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        directory = tempfile.TemporaryDirectory(prefix="osvev-media-row-")
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name)
        cls.source = cls.root / "synthetic clip.mp4"
        ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=5:duration=1.2",
                "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=1.2",
                "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
                "matrix_coefficients=1:video_full_range_flag=0",
                "-c:a", "aac", "-shortest", cls.source])

    def viewer(self):
        self.enterContext(patch.object(OSVEV, "_check_dependencies"))
        self.enterContext(patch.object(OSVEV, "set_windows_taskbar_icon"))
        self.warning = self.enterContext(patch.object(QMessageBox, "warning"))
        viewer = OSVEV()
        self.addCleanup(viewer.close)
        viewer.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        viewer.show()
        return viewer

    def test_file_row_fits_supported_window_widths(self):
        viewer = self.viewer()
        path = str(self.root / ("long synthetic recording name " * 4 + ".mp4"))
        viewer.file_name_label.set_path(path)
        viewer.media_summary_label.setText(summary_line({"streams": [VIDEO]}, 93_532_979))
        for width in (1200, 1400, 1600):
            with self.subTest(width=width):
                viewer.resize(width, 900)
                for _ in range(3):
                    self.app.processEvents()
                self.assertEqual(viewer.width(), width)
                name, summary, button = (viewer.file_name_label, viewer.media_summary_label,
                                         viewer.media_info_button)
                self.assertGreaterEqual(name.width(), name.minimumSizeHint().width())
                self.assertLess(name.geometry().right(), summary.geometry().left())
                self.assertLess(summary.geometry().right(), button.geometry().left())
                self.assertLessEqual(button.geometry().right(), button.parentWidget().width())
                self.assertGreaterEqual(summary.height(), summary.heightForWidth(summary.width()))
                self.assertGreaterEqual(button.width(), button.minimumSizeHint().width())
                self.assertIn(path, name.toolTip())

    def test_file_attributes_use_available_width_before_wrapping(self):
        viewer = self.viewer()
        viewer.resize(1600, 900)
        viewer.file_name_label.set_path(str(self.root / "synthetic_clip_01.MP4"))
        viewer.media_summary_label.setText(summary_line(
            {"streams": [dict(VIDEO, width=1280, height=720, avg_frame_rate="24/1")]},
            16_683_663))
        summary = viewer.media_summary_label
        required = summary.fontMetrics().horizontalAdvance(summary.text())
        viewer.resize(max(1600, required * 3), 900)
        for _ in range(3):
            self.app.processEvents()
        self.assertGreaterEqual(summary.width(), required)
        self.assertLessEqual(summary.heightForWidth(summary.width()),
                             summary.fontMetrics().lineSpacing() + 4)

    def test_row_identifies_loaded_file_and_opens_media_info(self):
        viewer = self.viewer()
        self.assertEqual(viewer.file_name_label.text(), "No video loaded")
        self.assertEqual(viewer.media_info_button.text(), "FFprobe Info")
        self.assertFalse(viewer.media_info_button.isEnabled())
        viewer._load_video(str(self.source))
        self.warning.assert_not_called()
        self.assertIn(str(self.source), viewer.file_name_label.toolTip())
        self.assertIn("synthetic clip.mp4", viewer.file_name_label.text())
        wait_for(viewer.media_info_button.isEnabled)
        size = self.source.stat().st_size
        self.assertEqual(viewer.media_summary_label.text(),
                         f"Codec: H.264 | Resolution: 64x48 | Avg FPS: 5 | Size: {size_text(size)}")
        summary = viewer.media_summary_label.text()
        viewer.crop_spins["crop_left"].setValue(8)
        viewer.playback_speed_dropdown.setCurrentText("2x")
        self.assertEqual(viewer.media_summary_label.text(), summary)
        self.assertTrue(viewer.media_summary_label.wordWrap())

        with patch.object(media_info.subprocess, "Popen") as popen, \
                patch.object(QDesktopServices, "openUrl", return_value=True):
            viewer.file_name_label.linkActivated.emit("reveal")
        if sys.platform == "win32":
            self.assertIn(f'/select,"{self.source}"', popen.call_args.args[0])

        shown = []
        with patch.object(MediaInfoDialog, "exec",
                          lambda dialog: shown.append(dialog.text.toPlainText()) or 0):
            viewer.media_info_button.click()
        rows = values(shown[0])
        self.assertEqual(rows["Complete name"], str(self.source))
        self.assertEqual(rows["File size"], f"{size:,} bytes ({size_text(size)})")
        self.assertEqual(rows["Width x height"], "64 x 48 pixels")
        self.assertIn("AAC", shown[0])

    def test_failed_load_keeps_previous_file_row(self):
        viewer = self.viewer()
        viewer._load_video(str(self.source))
        wait_for(viewer.media_info_button.isEnabled)
        viewer._load_video(str(self.root / "missing.mp4"))
        self.warning.assert_called_once()
        self.assertIn(str(self.source), viewer.file_name_label.toolTip())
        self.assertTrue(viewer.media_info_button.isEnabled())

    def test_missing_file_click_warns(self):
        viewer = self.viewer()
        viewer.video_path = str(self.root / "moved.mp4")
        viewer._reveal_loaded_file()
        self.assertIn("no longer at", self.warning.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
