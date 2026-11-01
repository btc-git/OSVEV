# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Loading progress, responsive cancellation, and candidate ownership."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QPushButton

from frame_info import FrameInfoHandler, FrameMetadataWorker, MetadataCancelled
from osvev_app import OSVEV


class ProbeProgressTests(unittest.TestCase):
    def scan(self, script, cancelled=None):
        updates = []
        processes = []
        real_popen = subprocess.Popen
        real_directory = tempfile.TemporaryDirectory
        with tempfile.TemporaryDirectory() as root:
            def directory(**kwargs):
                return real_directory(dir=root, **kwargs)

            def start(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                return process

            try:
                with patch("frame_info.tempfile.TemporaryDirectory", side_effect=directory), \
                        patch("frame_info.subprocess.Popen", side_effect=start):
                    result = FrameInfoHandler._scan_with_progress(
                        [sys.executable, "-u", "-c", script], cancelled,
                        lambda *args: updates.append(args), 4,
                    )
                return result, updates
            finally:
                self.assertEqual(list(Path(root).iterdir()), [])
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())

    def test_live_frame_counts_and_output_are_preserved(self):
        script = (
            "import time\n"
            "print('{\"frames\": [')\n"
            "for i in range(4):\n"
            " print('{\"key_frame\": 1}' + (',' if i < 3 else ''))\n"
            " time.sleep(0.12)\n"
            "print(']}')\n"
        )
        result, updates = self.scan(script)
        self.assertIn('"frames"', result)
        self.assertTrue(any(0 < count < 4 for _, count, _ in updates), updates)
        self.assertEqual(updates[-1][1:], (4, 4))
        self.assertEqual(sorted(count for _, count, _ in updates),
                         [count for _, count, _ in updates])

    def test_cancel_interrupts_running_process_and_removes_temporary_output(self):
        started = time.monotonic()
        with self.assertRaises(MetadataCancelled):
            self.scan(
                "import time; print('{\"frames\": [', flush=True); time.sleep(30)",
                lambda: time.monotonic() - started > 0.2,
            )
        self.assertLess(time.monotonic() - started, 1.5)

    def test_fast_probe_output_is_drained_after_process_exits(self):
        _, updates = self.scan(
            "print('{\"frames\": ['); "
            "[print('{\"key_frame\": 1},') for _ in range(4)]; print(']}')"
        )
        self.assertEqual(updates[-1][1:], (4, 4))

    def test_failed_probe_reports_stderr_and_cleans_up(self):
        with self.assertRaisesRegex(RuntimeError, "broken input"):
            self.scan("import sys; print('broken input', file=sys.stderr); sys.exit(1)")

    def test_unresponsive_termination_escalates_to_kill_and_reaps_process(self):
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired("probe", 0.5), ("", "")]
        FrameInfoHandler._stop_probe(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)

    def test_worker_discards_metadata_if_cancelled_after_scan(self):
        worker = FrameMetadataWorker("source.mp4")

        def scan(*args, **kwargs):
            worker.requestInterruption()
            return [{"pts": 1}]

        with patch.object(FrameInfoHandler, "get_frame_metadata", side_effect=scan):
            worker.start()
            self.assertTrue(worker.wait(2000))
        self.assertEqual(worker.metadata, [])
        self.assertIsNone(worker.error)


class LoadingDialogTests(unittest.TestCase):
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
        self.viewer = OSVEV()
        self.viewer.show()
        self.app.processEvents()
        self.previous = Mock()
        self.viewer.cap = self.previous
        self.viewer.video_path = "previous.mp4"
        self.candidate = Mock()
        self.candidate.get.side_effect = lambda prop: 4 if prop == cv2.CAP_PROP_FRAME_COUNT else 30
        self.candidate.read.return_value = (True, np.zeros((16, 24, 3), dtype=np.uint8))
        self.candidate.native_samples = None

    def tearDown(self):
        self.viewer.close()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()

    def test_percentage_unknown_count_and_stage_transitions(self):
        progress = QProgressDialog(self.viewer)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self.viewer._loading_progress = progress
        self.viewer._update_loading_progress("Reading source frame metadata", 25, 100)
        self.assertEqual((progress.maximum(), progress.value()), (100, 25))
        self.assertIn("25%", progress.labelText())
        self.viewer._update_loading_progress("Reading source frame metadata", 50, 0)
        self.assertEqual(progress.maximum(), 0)
        self.assertIn("50 processed", progress.labelText())
        self.viewer._update_loading_progress("Reading source frame metadata", 101, 100)
        self.assertEqual(progress.maximum(), 0)  # Never display >100% on a count mismatch.
        self.viewer._update_loading_progress("Validating the source frame index...", 0, 0)
        self.assertEqual(progress.maximum(), 0)
        self.assertIn("Validating", progress.labelText())
        self.viewer._loading_progress = None
        progress.deleteLater()

    def test_success_installs_only_completed_candidate(self):
        metadata = [{"pts": i, "pict_type": "I", "best_effort_timestamp_time": str(i / 30)}
                    for i in range(4)]
        worker_threads = []

        def opened(path):
            worker_threads.append(threading.get_ident())
            return self.candidate

        with patch.object(self.viewer, "_open_input_capture", side_effect=opened), \
                patch.object(FrameInfoHandler, "get_frame_metadata", return_value=metadata):
            self.viewer._load_video("new.mp4")
        self.assertNotEqual(worker_threads, [threading.get_ident()])
        self.assertIs(self.viewer.cap, self.candidate)
        self.assertEqual(self.viewer.frame_metadata, metadata)
        self.assertEqual(self.viewer.video_path, "new.mp4")
        self.assertIsNone(self.viewer._metadata_worker)
        self.assertIsNone(self.viewer._loading_progress)
        self.previous.release.assert_called_once()
        self.candidate.release.assert_not_called()
        self.assertEqual(self.messages, [])

    def test_cancel_during_open_keeps_gui_responsive_and_previous_video(self):
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start(10)
        self.addCleanup(timer.stop)

        def opened(path):
            time.sleep(0.2)  # A non-interruptible native open, not GUI work.
            return self.candidate

        def cancel():
            self.viewer._loading_progress.findChild(QPushButton).click()

        QTimer.singleShot(50, cancel)
        with patch.object(self.viewer, "_open_input_capture", side_effect=opened), \
                patch.object(FrameInfoHandler, "get_frame_metadata") as scan:
            self.viewer._load_video("new.mp4")
        self.assertGreater(len(ticks), 5)
        self.assertIs(self.viewer.cap, self.previous)
        self.assertEqual(self.viewer.video_path, "previous.mp4")
        self.previous.release.assert_not_called()
        self.candidate.release.assert_called_once()
        scan.assert_not_called()
        self.assertIsNone(self.viewer._metadata_worker)
        self.assertIsNone(self.viewer._loading_progress)
        self.assertEqual(self.messages, [])

    def test_index_failure_releases_candidate_without_replacing_previous_video(self):
        self.candidate.configure_index.side_effect = RuntimeError("Invalid index")
        with patch.object(self.viewer, "_open_input_capture", return_value=self.candidate), \
                patch.object(FrameInfoHandler, "get_frame_metadata", return_value=[{"pts": 1}]):
            self.viewer._load_video("new.mp4")
        self.assertIs(self.viewer.cap, self.previous)
        self.previous.release.assert_not_called()
        self.candidate.release.assert_called_once()
        self.assertIn("Invalid index", self.messages[0][1])

    def test_dialog_cancel_reaps_running_probe_and_keeps_previous_video(self):
        processes = []
        real_popen = subprocess.Popen

        def start(*args, **kwargs):
            process = real_popen(
                [sys.executable, "-u", "-c",
                 "import time; print('{\"frames\": [', flush=True); time.sleep(30)"],
                **kwargs,
            )
            processes.append(process)
            return process

        def cancel_when_running():
            if processes:
                self.viewer._loading_progress.findChild(QPushButton).click()
            else:
                QTimer.singleShot(10, cancel_when_running)

        QTimer.singleShot(10, cancel_when_running)
        started = time.monotonic()
        with patch.object(self.viewer, "_open_input_capture", return_value=self.candidate), \
                patch("frame_info.os.path.isfile", return_value=True), \
                patch("frame_info.subprocess.Popen", side_effect=start):
            self.viewer._load_video("new.mp4")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertIs(self.viewer.cap, self.previous)
        self.previous.release.assert_not_called()
        self.candidate.release.assert_called_once()
        self.assertIsNone(self.viewer._metadata_worker)
        self.assertEqual(self.messages, [])

    def test_cancel_interrupts_index_validation(self):
        def configure(*args, cancelled, **kwargs):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if cancelled():
                    raise MetadataCancelled()
                time.sleep(0.01)
            raise RuntimeError("Index validation did not cancel.")

        self.candidate.configure_index.side_effect = configure
        QTimer.singleShot(50, self.viewer._cancel_metadata_load)
        with patch.object(self.viewer, "_open_input_capture", return_value=self.candidate), \
                patch.object(FrameInfoHandler, "get_frame_metadata", return_value=[{"pts": 1}]):
            self.viewer._load_video("new.mp4")
        self.assertIs(self.viewer.cap, self.previous)
        self.candidate.release.assert_called_once()
        self.assertEqual(self.messages, [])


if __name__ == "__main__":
    unittest.main()
