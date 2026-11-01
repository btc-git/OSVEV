# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Convenience listening follows video time without driving the video clock."""

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtMultimedia import QMediaDevices, QMediaMetaData, QMediaPlayer
from PyQt6.QtWidgets import QApplication, QMessageBox

from audio_preview import AudioPreview
from ffmpeg_tools import tool_path
from playback_timing import PlaybackClock, PlaybackTimeline
from osvev_app import OSVEV
from sample_media import requires_samples


class AudioPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.audio = AudioPreview()
        self.player = Mock(spec=QMediaPlayer)
        self.player.mediaStatus.return_value = QMediaPlayer.MediaStatus.LoadedMedia
        self.player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        self.player.duration.return_value = 100000
        self.player.position.return_value = 0
        self.audio.player = self.player
        self.timeline = PlaybackTimeline((0, .04, .12, .20), .25, "source timestamps")

    def tearDown(self):
        self.audio.pause()
        self.audio.player = None

    def test_vfr_nonzero_partial_offset_seek_and_loop_follow_source_time(self):
        clock = PlaybackClock(self.timeline, 2, 1, 100)
        with patch("audio_preview.time.perf_counter", return_value=100):
            self.audio.follow(clock, 5.5)
        self.player.setPosition.assert_called_once_with(5620)
        self.player.reset_mock()
        with patch("audio_preview.time.perf_counter", return_value=100.14):
            self.audio.synchronize()
        self.player.setPosition.assert_called_once_with(5510)
        self.assertEqual(clock.presented_position, 2)  # Audio cannot select video frames.

    def test_pause_and_non_one_speeds_cannot_start_audio(self):
        for speed in (.05, .75, 2, 5):
            self.player.reset_mock()
            self.audio.follow(PlaybackClock(self.timeline, 0, speed, 100), 0)
            self.player.pause.assert_called_once()
            self.player.play.assert_not_called()
            self.player.setPosition.assert_not_called()
            self.assertFalse(self.audio.timer.isActive())

    def test_drift_correction_is_bounded_and_does_not_seek_every_tick(self):
        clock = PlaybackClock(self.timeline, 0, 1, 100)
        with patch("audio_preview.time.perf_counter", return_value=100):
            self.audio.follow(clock, 2)
        self.player.reset_mock()
        # Same cycle, large offset, but allow asynchronous seek/start to settle.
        with patch("audio_preview.time.perf_counter", return_value=100.1):
            self.audio.synchronize()
        self.player.setPosition.assert_not_called()
        long_timeline = PlaybackTimeline((0, 1, 2), 3, "source timestamps")
        self.audio.clock = PlaybackClock(long_timeline, 0, 1, 100)
        with patch("audio_preview.time.perf_counter", return_value=101):
            self.audio.synchronize()
        self.player.setPosition.assert_called_once_with(3000)

    def test_status_reports_actual_output_state_without_inferring_sound(self):
        statuses = []
        self.audio.status.connect(statuses.append)
        self.player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState
        self.audio.pause()
        self.assertEqual(statuses[-1], "Paused")
        self.audio.set_muted(True)
        self.assertEqual(statuses[-1], "Paused, muted")
        self.audio.set_muted(False)
        self.audio.follow(PlaybackClock(self.timeline, 0, 2, 100), 0)
        self.assertEqual(statuses[-1], "Off (1x playback only)")
        self.player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        with patch("audio_preview.time.perf_counter", return_value=100):
            self.audio.follow(PlaybackClock(self.timeline, 0, 1, 100), 0)
        self.assertEqual(self.audio.status_text(), "Playing")
        self.player.mediaStatus.return_value = QMediaPlayer.MediaStatus.LoadingMedia
        self.assertEqual(self.audio.status_text(), "Loading")
        self.audio._fail("Audio unavailable: test")
        self.assertEqual(statuses[-1], "Unavailable")
        self.audio.player = None
        self.audio.failed = False
        self.assertEqual(self.audio.status_text(), "Not loaded")

    def test_unavailable_audio_notice_does_not_raise_or_start_video(self):
        notices = []
        self.audio.notice.connect(notices.append)
        self.audio._error(QMediaPlayer.Error.FormatError, "Unsupported audio")
        self.assertTrue(self.audio.failed)
        self.assertIsNone(self.audio.clock)
        self.assertEqual(notices, ["Audio unavailable: Unsupported audio"])

    def test_unseekable_audio_fails_explicitly(self):
        notices = []
        self.audio.notice.connect(notices.append)
        self.player.hasAudio.return_value = True
        self.player.isSeekable.return_value = False
        self.audio._status(QMediaPlayer.MediaStatus.LoadedMedia)
        self.assertTrue(self.audio.failed)
        self.assertIn("seekable", notices[0])

    def test_unknown_timing_and_missing_device_are_explicit_and_safe(self):
        self.audio.player = None
        notices = []
        self.audio.notice.connect(notices.append)
        self.audio.load("source.mp4", None)
        self.assertTrue(self.audio.failed)
        self.assertIn("timing", notices[-1])
        with patch("audio_preview.QMediaDevices.defaultAudioOutput") as device:
            device.return_value.isNull.return_value = True
            self.audio.load("source.mp4", 0)
        self.assertIn("no output device", notices[-1])
        self.assertIsNone(self.audio.player)

    def test_end_of_source_is_silent_until_video_loops(self):
        self.player.duration.return_value = 1000
        with patch("audio_preview.time.perf_counter", return_value=100):
            self.audio.follow(PlaybackClock(self.timeline, 0, 1, 100), 2)
        self.player.play.assert_not_called()
        self.player.setPosition.assert_not_called()
        self.assertIsNone(self.audio._cycle)

    def test_volume_and_mute_change_only_audio_output(self):
        self.audio.output = Mock()
        self.audio.set_volume(23)
        self.audio.set_muted(True)
        self.audio.output.setVolume.assert_called_once_with(.23)
        self.audio.output.setMuted.assert_called_once_with(True)

    def test_track_selection_uses_qt_indices_and_resynchronizes_without_changing_volume(self):
        tracks = [QMediaMetaData(), QMediaMetaData()]
        self.player.audioTracks.return_value = tracks
        self.player.activeAudioTrack.return_value = 1
        self.assertEqual(self.audio.available_tracks(), ["unknown codec", "unknown codec"])
        self.assertEqual(self.audio.active_track(), 1)
        self.audio.volume, self.audio.muted = 37, True
        self.audio.clock = PlaybackClock(self.timeline, 0, 1, 100)
        with patch("audio_preview.time.perf_counter", return_value=100):
            self.audio.select_track(1)
        self.player.setActiveAudioTrack.assert_called_once_with(1)
        self.player.setPosition.assert_called_once_with(0)
        self.assertEqual((self.audio.volume, self.audio.muted), (37, True))
        self.audio.pause()
        self.player.reset_mock()
        self.audio.select_track(0)
        self.player.play.assert_not_called()
        for index in (-1, 2):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                self.audio.select_track(index)
        self.audio.failed = True
        with self.assertRaises(ValueError):
            self.audio.select_track(0)


class RealAudioPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if QMediaDevices.defaultAudioOutput().isNull():
            raise unittest.SkipTest("Real playback tests require an audio output device")
        cls.temp = tempfile.TemporaryDirectory(prefix="osvev-audio-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.paths = []
        for suffix, codec, vfr, offset in (
            ("mp4", "aac", False, 0),
            ("mov", "pcm_s16le", False, 0),
            ("mkv", "aac", True, 2),
        ):
            path = Path(cls.temp.name) / f"source.{suffix}"
            command = [
                tool_path("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=96x64:rate=30:duration=6", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000:duration=6",
            ]
            if vfr:
                command += ["-vf", "select='not(mod(n,2))+not(mod(n,5))'", "-fps_mode", "vfr"]
            command += [
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_range", "tv", "-color_primaries",
                "bt709", "-color_trc", "bt709", "-chroma_sample_location", "left",
                "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
                "matrix_coefficients=1:video_full_range_flag=0",
                "-c:a", codec, "-output_ts_offset", str(offset), str(path),
            ]
            subprocess.run(command, capture_output=True, check=True, timeout=30)
            cls.paths.append(path)
        cls.silent_path = Path(cls.temp.name) / "silent.mp4"
        subprocess.run([
            tool_path("ffmpeg"), "-v", "error", "-i", str(cls.paths[0]),
            "-map", "0:v:0", "-c:v", "copy", "-an", str(cls.silent_path),
        ], capture_output=True, check=True, timeout=30)
        cls.multi_path = Path(cls.temp.name) / "two-tracks.mp4"
        subprocess.run([
            tool_path("ffmpeg"), "-v", "error", "-i", str(cls.paths[0]),
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:duration=2",
            "-map", "0:v:0", "-map", "0:a:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-t", "2", str(cls.multi_path),
        ], capture_output=True, check=True, timeout=30)

    def pump(self, seconds):
        until = time.perf_counter() + seconds
        while time.perf_counter() < until:
            self.app.processEvents()
            time.sleep(.003)

    def wait_for(self, predicate, timeout=4):
        until = time.perf_counter() + timeout
        while not predicate() and time.perf_counter() < until:
            self.pump(.01)
        self.assertTrue(predicate())

    def test_real_player_seek_pause_resume_loop_and_responsiveness(self):
        self.measurements = []
        for path in self.paths:
            with self.subTest(path=path.name):
                audio = AudioPreview()
                audio.set_muted(True)
                notices = []
                audio.notice.connect(notices.append)
                try:
                    audio.load(str(path), 0)
                    self.wait_for(lambda: audio.failed or audio.player.hasAudio())
                    self.assertFalse(audio.failed, notices)
                    timeline = PlaybackTimeline((0, .1, .3, .7, 1.1), 1.5, "source timestamps")
                    clock = PlaybackClock(timeline, 2, 1, time.perf_counter())
                    ticks = []
                    timer = QTimer()
                    timer.timeout.connect(lambda: ticks.append(True))
                    timer.start(10)
                    audio.follow(clock, 1)
                    errors = []
                    for _ in range(5):
                        self.pump(.16)
                        target = round((1 + (clock.source_origin + time.perf_counter()
                                             - clock.origin) % timeline.duration) * 1000)
                        errors.append(abs(audio.player.position() - target))
                    timer.stop()
                    self.assertGreater(len(ticks), 30)
                    self.assertLess(max(errors), 200, (path, errors, notices))
                    audio.pause()
                    self.pump(.1)
                    position = audio.player.position()
                    self.pump(.2)
                    self.assertEqual(audio.player.position(), position)
                    audio.follow(PlaybackClock(timeline, 4, 1, time.perf_counter()), 2)
                    self.pump(.15)
                    self.assertLess(abs(audio.player.position() - 3250), 200)
                    self.pump(.65)  # Cross the loaded range's boundary.
                    target = 2 + (audio.clock.source_origin + time.perf_counter()
                                  - audio.clock.origin) % timeline.duration
                    self.assertLess(abs(audio.player.position() - round(target * 1000)), 200)
                    self.assertEqual(notices, [""])
                    self.measurements.append((path.name, errors))
                finally:
                    audio.clear()
                    self.pump(.05)
        print("Qt convenience-audio position errors (ms):", self.measurements)

    def test_real_osvev_navigation_modes_and_video_without_audio(self):
        with patch.object(OSVEV, "_check_dependencies"), \
                patch.object(OSVEV, "set_windows_taskbar_icon"), \
                patch.object(QMessageBox, "warning") as warning:
            viewer = OSVEV()
            viewer.show()
            try:
                viewer._load_video(str(self.paths[0]))
                warning.assert_not_called()
                self.assertTrue(viewer.audio_controls.isVisible())
                viewer.mute_checkbox.setChecked(True)
                self.wait_for(lambda: viewer._audio.failed or viewer._audio.player.hasAudio())
                self.assertFalse(viewer._audio.failed, viewer.audio_notice.text())
                stream = viewer.cap.audio_streams[0]
                self.assertEqual(len(viewer.cap.audio_streams), 1)
                self.assertTrue(stream["codec"] and stream["channels"] and stream["sample_rate"])
                self.assertTrue(viewer.audio_status_label.text().startswith(
                    f"1 audio stream: {stream['codec']}, {stream['channels']} ch, "
                    f"{stream['sample_rate']} Hz | "))
                self.assertTrue(viewer.audio_status_label.text().endswith("Paused, muted"),
                                viewer.audio_status_label.text())
                viewer.volume_slider.setValue(73)
                self.assertEqual(viewer.volume_value_label.text(), "73%")
                self.assertEqual(viewer._audio.volume, 73)
                viewer.set_frame_position(45)
                viewer.resume_video()
                self.pump(.7)
                self.assertTrue(viewer.is_playing)
                self.assertTrue(viewer.audio_status_label.text().endswith("Playing, muted"),
                                viewer.audio_status_label.text())
                target = 1500 + 700
                self.assertLess(abs(viewer._audio.player.position() - target), 200)
                displayed_time = float(viewer.frame_metadata[viewer.current_frame_index][
                    "best_effort_timestamp_time"]) - viewer.cap.media_start_seconds
                self.assertLess(abs(viewer._audio.player.position() - displayed_time * 1000), 200)
                viewer.manual_next_frame()
                self.assertFalse(viewer.is_playing)
                self.assertFalse(viewer._audio.timer.isActive())
                self.assertEqual(viewer._audio.player.playbackState(),
                                 QMediaPlayer.PlaybackState.PausedState)
                viewer.resume_video()
                viewer.playback_speed_dropdown.setCurrentText("2x")
                self.assertIsNone(viewer._audio.clock)
                self.assertTrue(viewer.audio_status_label.text().endswith(
                    "Off (1x playback only), muted"), viewer.audio_status_label.text())
                viewer.playback_speed_dropdown.setCurrentText("1x")
                self.assertIsNotNone(viewer._audio.clock)
                viewer.pause_video()
                viewer._load_video(str(self.silent_path))
                self.assertTrue(viewer.audio_controls.isVisible())
                self.assertEqual(viewer.audio_status_label.text(), "No audio streams")
                self.assertFalse(viewer.volume_slider.isEnabled())
                self.assertIsNone(viewer._audio.player)
                warning.assert_not_called()
            finally:
                viewer.close()
                self.pump(.05)

    def test_actual_vfr_nonzero_start_and_partial_range_alignment(self):
        from fractions import Fraction
        with patch.object(OSVEV, "_check_dependencies"), \
                patch.object(OSVEV, "set_windows_taskbar_icon"), \
                patch.object(QMessageBox, "warning") as warning:
            viewer = OSVEV()
            viewer.show()
            viewer.mute_checkbox.setChecked(True)
            try:
                viewer._load_video(str(self.paths[2]))
                warning.assert_not_called()
                self.assertGreaterEqual(float(viewer.frame_metadata[0][
                    "best_effort_timestamp_time"]), 2)
                self.assertAlmostEqual(
                    viewer._audio_source_offset,
                    float(viewer.frame_metadata[0]["best_effort_timestamp_time"])
                    - viewer.cap.media_start_seconds,
                )
                viewer.set_frame_position(12)
                viewer.resume_video()
                self.pump(.8)
                self.assertFalse(viewer._audio.failed, viewer.audio_notice.text())
                clock = viewer._playback_clock
                target = (viewer._audio_source_offset + clock.source_origin
                          + time.perf_counter() - clock.origin) * 1000
                self.assertLess(abs(viewer._audio.player.position() - target), 200)
                displayed_time = float(viewer.frame_metadata[viewer.current_frame_index][
                    "best_effort_timestamp_time"]) - viewer.cap.media_start_seconds
                self.assertLess(abs(viewer._audio.player.position() - displayed_time * 1000), 200)
                # MP4 has a declared frame count and supports exact partial indexing.
                viewer._load_video(str(self.paths[0]), (Fraction(2), Fraction(3)))
                warning.assert_not_called()
                self.assertIsNotNone(viewer.source_range)
                self.assertEqual(viewer.video_time_label.text(),
                                 "Time (loaded range): 00:00:00.000 / 00:00:01.000")
                self.assertAlmostEqual(viewer._audio_source_offset, 2)
                viewer.resume_video()
                self.pump(1.2)
                self.assertFalse(viewer._audio.failed, viewer.audio_notice.text())
                target = (2 + (time.perf_counter() - viewer._playback_clock.origin) % 1) * 1000
                self.assertLess(abs(viewer._audio.player.position() - target), 200)
            finally:
                viewer.close()
                self.pump(.05)

    def test_real_multiple_qt_tracks_selection_and_container_count_distinction(self):
        with patch.object(OSVEV, "_check_dependencies"), \
                patch.object(OSVEV, "set_windows_taskbar_icon"), \
                patch.object(QMessageBox, "warning") as warning:
            viewer = OSVEV()
            viewer.show()
            try:
                viewer._load_video(str(self.multi_path))
                viewer.volume_slider.setValue(37)
                viewer.mute_checkbox.setChecked(True)
                self.wait_for(lambda: viewer._audio.failed
                              or viewer.audio_track_dropdown.count() == 2)
                self.assertFalse(viewer._audio.failed, viewer.audio_notice.text())
                self.assertTrue(viewer.audio_track_dropdown.isVisible())
                self.assertIn("2 audio streams", viewer.audio_status_label.text())
                self.assertIn("2 Qt tracks", viewer.audio_status_label.text())
                viewer._select_audio_track(1)
                self.wait_for(lambda: viewer._audio.active_track() == 1)
                self.assertEqual(viewer.audio_track_dropdown.currentIndex(), 1)
                self.assertIn("Qt track 2", viewer.audio_status_label.text())
                self.assertIn("ch ?, Hz ?", viewer.audio_status_label.text())
                viewer.resume_video()
                self.pump(.2)
                viewer._select_audio_track(0)
                self.pump(.2)
                self.assertEqual(viewer._audio.active_track(), 0)
                self.assertIn("Playing, muted", viewer.audio_status_label.text())
                self.assertAlmostEqual(viewer._audio.output.volume(), .37, places=5)
                self.assertTrue(viewer._audio.output.isMuted())
                viewer.pause_video()
                viewer._load_video(str(self.paths[0]))
                self.wait_for(lambda: len(viewer._audio.available_tracks()) == 1)
                self.assertFalse(viewer.audio_track_dropdown.isVisible())
                viewer._load_video(str(self.silent_path))
                self.assertFalse(viewer.audio_track_dropdown.isVisible())
                self.assertEqual(viewer.audio_status_label.text(), "No audio streams")
                warning.assert_not_called()
            finally:
                viewer.close()
                self.pump(.05)

    @requires_samples("gas-station.mp4")
    def test_existing_recording_with_enhancements_and_export_notice(self):
        from PyQt6.QtWidgets import QLabel
        from export_dialog import ExportDialog
        with patch.object(OSVEV, "_check_dependencies"), \
                patch.object(OSVEV, "set_windows_taskbar_icon"), \
                patch.object(QMessageBox, "warning") as warning:
            viewer = OSVEV()
            viewer.show()
            viewer.mute_checkbox.setChecked(True)
            try:
                source = Path(__file__).resolve().parent.parent / "gas-station.mp4"
                viewer._load_video(str(source))
                warning.assert_not_called()
                self.assertTrue(viewer.cap.has_audio)
                self.wait_for(lambda: viewer._audio.failed or viewer._audio.player.hasAudio())
                self.assertFalse(viewer._audio.failed, viewer.audio_notice.text())
                viewer.brightness_slider.setValue(20)
                viewer.sharpness_slider.setValue(20)
                viewer.resume_video()
                gaps = []
                for _ in range(5):
                    self.pump(.2)
                    displayed_time = float(viewer.frame_metadata[viewer.current_frame_index][
                        "best_effort_timestamp_time"]) - viewer.cap.media_start_seconds
                    gaps.append(abs(viewer._audio.player.position() - displayed_time * 1000))
                self.assertLess(max(gaps), 200, gaps)
                print("Existing enhanced recording audio/displayed-frame errors (ms):",
                      [round(gap, 1) for gap in gaps])
                viewer.pause_video()
                dialog = ExportDialog(
                    viewer.video_path, viewer.current_frame_index, viewer.frame_metadata,
                    viewer.get_processing_params(), viewer.decoder_backend, viewer,
                )
                try:
                    self.assertTrue(any("NOT included in exports" in label.text()
                                        for label in dialog.findChildren(QLabel)))
                finally:
                    dialog.deleteLater()
            finally:
                viewer.close()
                self.pump(.05)


if __name__ == "__main__":
    unittest.main()
