# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QTabWidget

import ffmpeg_tools
import version


class FFmpegToolResolutionTests(unittest.TestCase):
    def setUp(self):
        ffmpeg_tools.tool_path.cache_clear()
        self.addCleanup(ffmpeg_tools.tool_path.cache_clear)

    def test_bundled_tools_take_priority_over_environment_and_path(self):
        with tempfile.TemporaryDirectory() as bundle:
            suffix = ".exe" if sys.platform == "win32" else ""
            tools = Path(bundle)
            for name in ffmpeg_tools.TOOL_NAMES:
                (tools / (name + suffix)).write_bytes(b"")
            with patch.object(sys, "_MEIPASS", bundle, create=True), \
                    patch.dict(os.environ, {"PATH": "", "OSVEV_FFMPEG_DIR": ""}):
                self.assertEqual(ffmpeg_tools.tool_path("ffmpeg"), str(tools / ("ffmpeg" + suffix)))
                self.assertEqual(ffmpeg_tools.tool_path("ffprobe"), str(tools / ("ffprobe" + suffix)))
                self.assertTrue(ffmpeg_tools.is_bundled())

    def test_displayed_command_name_is_replaced_only_for_execution(self):
        displayed = ["ffmpeg", "-i", "ffmpeg", "out.mkv"]
        executed = ffmpeg_tools.resolve_arguments(displayed)
        self.assertEqual(displayed[0], "ffmpeg")
        self.assertEqual(executed[0], ffmpeg_tools.tool_path("ffmpeg"))
        self.assertEqual(executed[1:], displayed[1:])
        self.assertEqual(ffmpeg_tools.resolve_arguments(["other", "x"]), ["other", "x"])
        with self.assertRaises(ValueError):
            ffmpeg_tools.tool_path("ffplay")

    def test_resolved_tools_run_without_relying_on_path(self):
        ffmpeg = ffmpeg_tools.tool_path("ffmpeg")
        if not Path(ffmpeg).is_file():
            self.skipTest("FFmpeg is not installed beside this Python environment")
        result = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                                text=True, check=True, env={**os.environ, "PATH": ""})
        self.assertRegex(result.stdout, r"\beq\b")
        self.assertRegex(result.stdout, r"\bunsharp\b")


class VersionTests(unittest.TestCase):
    def test_version_commit_and_bundled_documents(self):
        self.assertRegex(version.__version__, r"^\d+\.\d+\.\d+$")
        self.assertNotEqual(version.git_commit(), "")
        self.assertIn(version.__version__, version.version_string())
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            self.assertTrue(version.resource_path(name).is_file(), name)
        self.assertIn("GNU GENERAL PUBLIC LICENSE",
                      version.resource_path("LICENSE").read_text(encoding="utf-8"))


class AboutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, *, error=None):
        from osvev_app import AboutDialog
        self.enterContext(patch("osvev_app.git_commit", return_value="123456789abc+dirty"))
        self.enterContext(patch("osvev_app.tool_path",
                                return_value=r"X:\osvev\synthetic-tools\ffmpeg.exe"))
        self.enterContext(patch("osvev_app.subprocess.run", side_effect=error,
                                return_value=subprocess.CompletedProcess(
                                    [], 0, "ffmpeg version test-runtime\nconfiguration: --enable-gpl\n", "")))
        dialog = AboutDialog()
        self.addCleanup(dialog.close)
        return dialog

    def test_about_keeps_revision_and_runtime_details_out_of_summary(self):
        dialog = self.dialog()
        header = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("uncommitted source changes", header)
        self.assertNotIn("+dirty", header)
        self.assertNotIn("synthetic-tools", header)
        tabs = dialog.findChild(QTabWidget)
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "Build details")
        details = tabs.widget(index).toPlainText()
        self.assertIn("123456789abc+dirty", details)
        self.assertIn(r"X:\osvev\synthetic-tools\ffmpeg.exe", details)
        self.assertIn("configuration: --enable-gpl", details)
        self.assertIn("PyAV", details)

    def test_about_reports_ffmpeg_query_failure(self):
        dialog = self.dialog(error=OSError("synthetic probe failure"))
        header = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("FFmpeg information unavailable", header)
        tabs = dialog.findChild(QTabWidget)
        details = next(tabs.widget(i).toPlainText() for i in range(tabs.count())
                       if tabs.tabText(i) == "Build details")
        self.assertIn("synthetic probe failure", details)


if __name__ == "__main__":
    unittest.main()
