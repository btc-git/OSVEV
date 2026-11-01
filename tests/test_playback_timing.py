# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import contextlib
import io
import math
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication, QMessageBox

from osvev_app import OSVEV
from playback_timing import PlaybackClock, PlaybackTimeline


class PlaybackTimingTests(unittest.TestCase):
    def timeline(self, count=300, fps=30000 / 1001):
        return PlaybackTimeline.from_metadata([], count, fps)

    def test_fractional_deadlines_do_not_accumulate_timer_rounding(self):
        clock = PlaybackClock(self.timeline(), 0, 1, 100)
        actual = []
        now = 100.0
        for i in range(1, 300):
            now += clock.delay_ms(now) / 1000
            target = clock.due(now)
            self.assertIsNotNone(target)
            self.assertEqual(target.index, i)
            self.assertEqual(target.late_skips, 0)
            actual.append(now)
            clock.presented(target)
            now += 0.007  # Decode/display work must not shift the absolute clock.
        error = actual[-1] - (100 + 299 * 1001 / 30000)
        self.assertGreaterEqual(error, 0)
        self.assertLess(error, 0.0011)

    def test_stall_chooses_current_source_time_without_bursting_old_frames(self):
        clock = PlaybackClock(self.timeline(fps=30), 0, 1, 0)
        target = clock.due(0.135)
        self.assertEqual(target.index, 4)
        self.assertEqual(target.late_skips, 3)
        self.assertEqual(target.intentional_skips, 0)
        clock.presented(target)
        self.assertIsNone(clock.due(0.140))
        self.assertAlmostEqual(clock.deadline(clock.next_position), 5 / 30)

    def test_high_speed_skips_are_separate_from_late_slots(self):
        clock = PlaybackClock(self.timeline(fps=30), 0, 5, 10)
        target = clock.due(10 + 1 / 30 + 0.0001)
        self.assertEqual(target.index, 5)
        self.assertEqual(target.intentional_skips, 4)
        self.assertEqual(target.late_skips, 0)
        clock.presented(target)
        late = clock.due(10.134)
        self.assertEqual(late.index, 20)
        self.assertEqual(late.intentional_skips, 12)
        self.assertEqual(late.late_skips, 2)

    def test_source_pts_nonzero_origin_variable_intervals_and_slow_speed(self):
        metadata = [
            {"best_effort_timestamp_time": 2.0},
            {"best_effort_timestamp_time": 2.02},
            {"best_effort_timestamp_time": 2.10, "duration_time": 0.04},
        ]
        timeline = PlaybackTimeline.from_metadata(metadata, 3, 30)
        self.assertEqual(timeline.mode, "source timestamps")
        self.assertAlmostEqual(timeline.duration, 0.14)
        clock = PlaybackClock(timeline, 0, 0.5, 20)
        self.assertAlmostEqual(clock.deadline(1), 20.04)
        self.assertAlmostEqual(clock.deadline(2), 20.20)
        self.assertAlmostEqual(clock.deadline(3), 20.28)
        self.assertIsNone(clock.due(20.039))

    def test_wraparound_uses_duration_and_keeps_absolute_clock(self):
        clock = PlaybackClock(self.timeline(count=3, fps=30), 2, 1, 0)
        target = clock.due(0.034)
        self.assertEqual((target.position, target.index), (3, 0))
        clock.presented(target)
        target = clock.due(0.067)
        self.assertEqual((target.position, target.index), (4, 1))

    def test_missing_and_invalid_metadata_are_labelled_fps_fallbacks(self):
        for metadata in (
            [], [{}] * 3,
            [{"best_effort_timestamp_time": value} for value in (0, 0, 1)],
            [{"best_effort_timestamp_time": value} for value in (0, math.nan, 1)],
        ):
            timeline = PlaybackTimeline.from_metadata(metadata, 3, 25)
            self.assertIn("fallback", timeline.mode)
            self.assertEqual(timeline.times, (0, 0.04, 0.08))
        for count, fps in ((0, 30), (3, 0), (3, math.inf)):
            with self.assertRaises(ValueError):
                PlaybackTimeline.from_metadata([], count, fps)


class PlaybackViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.messages = []
        self.patches = [
            patch.object(OSVEV, "_check_dependencies"),
            patch.object(OSVEV, "set_windows_taskbar_icon"),
            patch.object(QMessageBox, "warning", side_effect=lambda *a: self.messages.append(a[1:])),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.viewer = OSVEV(decoder_backend="opencv")
        self.addCleanup(self.viewer.close)
        self.viewer.show()
        self.app.processEvents()
        self.viewer.cap = Mock()
        self.viewer.cap.isOpened.return_value = True
        self.viewer.cap.set.return_value = True
        self.viewer.cap.read.side_effect = lambda: (True, np.zeros((24, 32, 3), np.uint8))
        self.viewer.current_frame = np.zeros((24, 32, 3), np.uint8)
        self.viewer.frame_count = 300
        self.viewer.frame_rate = 30
        self.viewer._last_decoded_index = 0
        self.viewer.timeline_slider.setMaximum(299)
        self.viewer.update_frame()

    def test_fast_playback_decodes_forward_without_seek_and_scrub_still_seeks(self):
        self.viewer.get_frame(5, sequential_playback=True)
        self.viewer.cap.set.assert_not_called()
        self.assertEqual(self.viewer.cap.read.call_count, 5)
        self.viewer.set_frame_position(17)
        self.viewer.cap.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 17)
        self.assertEqual(self.viewer.current_frame_index, 17)
        self.viewer.manual_next_frame()
        self.assertEqual(self.viewer.current_frame_index, 18)
        self.viewer.previous_frame()
        self.assertEqual(self.viewer.current_frame_index, 17)

    def test_resume_and_speed_change_do_not_redisplay_current_frame(self):
        with patch.object(self.viewer, "update_frame") as update:
            self.viewer.resume_video()
            self.viewer.playback_speed_dropdown.setCurrentText("5x")
            update.assert_not_called()
        self.assertTrue(self.viewer.timer.isSingleShot())
        self.assertTrue(self.viewer.timer.isActive())
        self.assertEqual(self.viewer._playback_clock.stride, 5)
        self.viewer.pause_video()
        self.assertFalse(self.viewer.timer.isActive())

    def test_tick_recovers_stall_and_manual_scrubbing_reanchors(self):
        self.viewer.resume_video()
        self.viewer.timer.stop()
        clock = self.viewer._playback_clock
        with patch("osvev_app.time.perf_counter", return_value=clock.origin + 0.135):
            self.viewer._playback_tick()
        self.assertEqual(self.viewer.current_frame_index, 4)
        self.assertEqual(self.viewer._late_skips, 3)
        self.viewer.cap.set.assert_not_called()
        self.assertEqual(self.viewer.cap.read.call_count, 4)
        self.viewer.set_frame_position(20)
        self.assertFalse(self.viewer.is_playing)
        self.assertFalse(self.viewer.timer.isActive())
        self.viewer.resume_video()
        self.assertEqual(self.viewer._playback_clock.anchor, 20)
        self.assertEqual(self.viewer._late_skips, 0)
        self.viewer.close()
        self.assertFalse(self.viewer.timer.isActive())

    def test_diagnostics_only_count_new_playback_paints_and_are_bounded(self):
        with patch("osvev_app.time.perf_counter", return_value=100):
            self.viewer.resume_video()
        self.viewer.timer.stop()
        generation = self.viewer._playback_generation
        with patch("osvev_app.time.perf_counter", return_value=100):
            self.viewer._record_frame_painted((generation - 1, 1, 100))
        self.assertEqual(self.viewer.playback_diagnostics()["paint_samples"], 0)
        for index in range(200):
            with patch("osvev_app.time.perf_counter", return_value=100 + index / 30):
                self.viewer._record_frame_painted((generation, index, 100 + index / 30))
        report = self.viewer.playback_diagnostics()
        self.assertEqual(report["paint_samples"], 120)
        self.assertAlmostEqual(report["paint_fps"], 30)
        self.assertAlmostEqual(report["interval_max_ms"], 1000 / 30)
        self.assertEqual(report["missed_paint_slots"], 0)
        self.assertIn("not monitor/vsync", self.viewer.play_pause_button.toolTip())
        self.viewer.pause_video()
        self.viewer._record_frame_painted((generation, 201, 110))
        self.assertEqual(self.viewer.playback_diagnostics()["paint_samples"], 120)

    def test_failed_tick_pauses_and_keeps_previous_frame(self):
        old = self.viewer.current_frame
        self.viewer.resume_video()
        self.viewer.cap.read.side_effect = RuntimeError("decode failure")
        clock = self.viewer._playback_clock
        with patch("osvev_app.time.perf_counter", return_value=clock.origin + 0.04), \
                contextlib.redirect_stdout(io.StringIO()):
            self.viewer._playback_tick()
        self.assertFalse(self.viewer.is_playing)
        self.assertIs(self.viewer.current_frame, old)
        self.assertEqual(self.viewer.current_frame_index, 0)
        self.assertEqual(len(self.messages), 1)

    def test_frame_information_cells_are_reused(self):
        item = self.viewer.frame_info_table.item(0, 1)
        self.viewer.current_frame_index = 1
        self.viewer.update_frame()
        self.assertIs(self.viewer.frame_info_table.item(0, 1), item)
        self.assertIn("2 of", item.text())

    def test_lens_repaints_do_not_count_as_new_frame_presentations(self):
        tokens = []
        self.viewer.main_label.framePainted.connect(tokens.append)
        self.viewer.main_label.presentation_token = (1, 1, 0)
        self.viewer.main_label.repaint()
        self.viewer.main_label.repaint()
        self.assertEqual(tokens, [(1, 1, 0)])
        self.viewer.main_label.presentation_token = (1, 2, 0.03)
        self.viewer.main_label.repaint()
        self.assertEqual(tokens, [(1, 1, 0), (1, 2, 0.03)])

    def start_observations(self, speed="1x", times=None, tail=0.1):
        self.viewer.playback_speed_dropdown.setCurrentText(speed)
        if times is None:
            times = [i / 30 for i in range(300)]
        self.viewer.frame_count = len(times)
        self.viewer.frame_metadata = [
            {"best_effort_timestamp_time": value + 7} for value in times
        ]
        self.viewer.frame_metadata[-1]["duration_time"] = tail
        self.viewer._playback_timeline = None
        with patch("osvev_app.time.perf_counter", return_value=100):
            self.viewer.resume_video()
        self.viewer.timer.stop()

    def observe(self, position, wall_time):
        clock = self.viewer._playback_clock
        with patch("osvev_app.time.perf_counter", return_value=100 + wall_time):
            self.viewer._record_frame_painted((
                self.viewer._playback_generation, position, clock.deadline(position)
            ))

    def test_actual_normal_playback_requires_samples_and_uses_timestamps(self):
        self.start_observations()
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: —")
        self.observe(1, 1 / 30)
        self.observe(2, 2 / 30)
        self.assertIsNone(self.viewer.playback_diagnostics()["actual_speed"])
        for position in range(3, 32):
            self.observe(position, position / 30)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 30.0 FPS")
        self.assertAlmostEqual(self.viewer.playback_diagnostics()["actual_speed"], 1)
        self.assertIn("Actual speed (source timestamps): 1.00×",
                      self.viewer.play_pause_button.toolTip())
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")
        self.assertEqual(self.viewer.skipped_frames_label.styleSheet(), "")

    def test_actual_slow_progression_is_not_inferred_from_fps(self):
        self.start_observations()
        self.observe(1, 0)
        for position in range(2, 32):
            self.observe(position, (position - 1) / 30 / 0.98)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 29.4 FPS")
        self.assertAlmostEqual(self.viewer.playback_diagnostics()["actual_speed"], 0.98)
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")

    def test_overload_before_first_paint_and_coalescing_are_counted_once(self):
        self.start_observations()
        self.viewer._late_skips = 3
        self.observe(4, 4 / 30)
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 3 (3 recent)")
        # Position 5 was scheduled but coalesced; 6 is the next completed paint.
        self.observe(6, 6 / 30)
        self.assertEqual(self.viewer.playback_diagnostics()["overload_skipped_frames"], 4)
        self.assertEqual(self.viewer.playback_diagnostics()["late_presentation_slots"], 3)
        self.assertEqual(self.viewer.skipped_frames_label.styleSheet(), "")
        self.observe(6, 0.5)  # Duplicate/control repaint must not change observations.
        self.assertEqual(self.viewer.playback_diagnostics()["paint_samples"], 2)
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 4 (4 recent)")

    def test_actual_can_be_one_x_while_overload_skips_increase(self):
        self.start_observations()
        self.observe(1, 1 / 30)
        self.observe(31, 31 / 30)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 30.0 FPS")
        self.assertEqual(self.viewer.displayed_fps_label.text(), "Displayed: 1.0 FPS")
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 29 (29 recent)")

    def test_intentional_fast_selection_is_excluded_but_missed_slots_count(self):
        self.start_observations(speed="5x")
        for position in range(5, 156, 5):
            self.observe(position, position / 150)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 150.0 FPS")
        self.assertEqual(self.viewer.displayed_fps_label.text(), "Displayed: 30.0 FPS")
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")
        # 160 and 165 were eligible presentations; intentional intervening frames
        # are excluded even when the next paint jumps to source frame 170.
        self.viewer._late_skips = 2
        self.observe(170, 170 / 150)
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 2 (2 recent)")

    def test_vfr_and_loop_use_unwrapped_source_time_not_frame_count(self):
        self.start_observations(times=[0, 0.02, 0.1, 0.8, 1.0], tail=0.2)
        for position, wall_time in ((1, 0.02), (2, 0.1), (3, 0.8), (4, 1),
                                    (5, 1.2), (6, 1.22), (7, 1.3), (8, 2.0)):
            self.observe(position, wall_time)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 3.5 FPS")
        self.assertAlmostEqual(self.viewer.playback_diagnostics()["actual_speed"], 1)
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")

    def test_actual_uses_short_window_and_index_fps_survives_timestamp_fallback(self):
        self.start_observations()
        self.observe(1, 0)
        self.observe(16, 1)  # Initially 0.5x.
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 15.0 FPS")
        for position in range(17, 137):
            self.observe(position, 1 + (position - 16) / 30)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 30.0 FPS")
        self.assertLessEqual(len(self.viewer._source_paint_samples), 120)
        self.assertLessEqual(self.viewer._source_paint_samples[-1][0]
                             - self.viewer._source_paint_samples[0][0], 2)
        self.viewer.pause_video()
        self.viewer.frame_metadata = []
        self.viewer._playback_timeline = None
        self.viewer.resume_video()
        self.viewer.timer.stop()
        self.observe(1, 0)
        self.observe(31, 1)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 30.0 FPS")
        self.assertIsNone(self.viewer.playback_diagnostics()["actual_speed"])

    def test_pause_retains_indicators_resume_and_speed_change_reset_them(self):
        self.start_observations()
        self.observe(1, 0)
        self.observe(31, 1)
        skipped = self.viewer.skipped_frames_label.text()
        with patch("osvev_app.time.perf_counter", return_value=101):
            self.viewer.pause_video()
        self.observe(50, 10)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: —")
        self.assertEqual(self.viewer.skipped_frames_label.text(), skipped)
        self.viewer.resume_video()
        self.viewer.timer.stop()
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: —")
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")
        self.observe(31, 1)
        self.viewer.playback_speed_dropdown.setCurrentText("2x")
        self.viewer.timer.stop()
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: —")
        self.viewer.pause_video()
        self.viewer.skipped_frames_label.setText("Skipped: 9")
        self.viewer.playback_speed_dropdown.setCurrentText("0.5x")
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")

    def test_seek_and_manual_navigation_reset_observations(self):
        for action in (
            lambda: self.viewer.set_frame_position(20),
            self.viewer.manual_next_frame, self.viewer.previous_frame,
            lambda: self.viewer.navigate_iframe(1),
        ):
            self.viewer.current_frame_index = 0
            self.start_observations()
            self.viewer.frame_metadata[20]["pict_type"] = "I"
            for entry in self.viewer.frame_metadata:
                entry.setdefault("pict_type", "P")
            self.observe(1, 0)
            self.observe(31, 1)
            action()
            self.assertFalse(self.viewer.is_playing)
            self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")
            self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: —")
            self.assertEqual(len(self.viewer._source_paint_samples), 0)

    def test_indicators_share_existing_speed_row(self):
        layout = self.viewer.playback_speed_dropdown.parentWidget().layout()
        row = next(layout.itemAt(i).layout() for i in range(layout.count())
                   if layout.itemAt(i).layout() is not None
                   and layout.itemAt(i).layout().indexOf(self.viewer.playback_speed_dropdown) >= 0)
        self.assertGreater(row.indexOf(self.viewer.actual_speed_label),
                           row.indexOf(self.viewer.playback_speed_dropdown))
        self.assertEqual(row.indexOf(self.viewer.skipped_frames_label),
                         row.indexOf(self.viewer.actual_speed_label) + 2)
        self.assertEqual(row.indexOf(self.viewer.video_time_label),
                         row.indexOf(self.viewer.playback_speed_dropdown) + 1)
        self.assertEqual(row.itemAt(row.indexOf(self.viewer.video_time_label) + 1)
                         .spacerItem().sizeHint().width() + row.spacing(), 22)
        for label in (self.viewer.video_time_label, self.viewer.actual_speed_label,
                      self.viewer.displayed_fps_label,
                      self.viewer.skipped_frames_label):
            self.assertEqual(label.styleSheet(), "")
            self.assertEqual(label.font().weight(), self.viewer.playback_speed_dropdown.font().weight())
            self.assertEqual(label.font().pointSize(), self.viewer.playback_speed_dropdown.font().pointSize())
        self.assertIn("Not monitor", self.viewer.actual_speed_label.toolTip())
        self.assertIn("Excludes intentional", self.viewer.skipped_frames_label.toolTip())

    def test_displayed_fps_uses_five_seconds_not_last_120_samples(self):
        self.start_observations()
        for position in range(1, 241):
            self.observe(position, position / 60)
        self.assertEqual(self.viewer.displayed_fps_label.text(), "Displayed: 60.0 FPS")
        self.assertEqual(len(self.viewer._recent_paints), 240)
        self.viewer._refresh_performance_labels(105.5)
        self.assertTrue(all(t > 100.5 for t in self.viewer._recent_paints))
        self.viewer._refresh_performance_labels(110)
        self.assertEqual(self.viewer.displayed_fps_label.text(), "Displayed: —")
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: —")

    def test_recent_skips_expire_exactly_and_cumulative_survives_pause(self):
        self.start_observations(speed="5x")
        self.observe(5, 0.1)
        self.observe(20, 0.2)  # 10 and 15 missed; intentional frames excluded.
        self.observe(30, 1.2)  # 25 missed.
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 3 (3 recent)")
        with patch("osvev_app.time.perf_counter", return_value=101.2):
            self.viewer.pause_video()
        self.assertEqual(self.viewer.displayed_fps_label.text(), "Displayed: —")
        self.viewer._refresh_performance_labels(105.2)
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 3 (1 recent)")
        self.viewer._refresh_performance_labels(106.2)
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 3 (0 recent)")
        self.assertFalse(self.viewer._diagnostic_timer.isActive())
        self.assertEqual(self.viewer.skipped_frames_label.styleSheet(), "")

    def test_live_refresh_expires_recent_without_more_paints(self):
        self.start_observations()
        self.observe(4, 0.2)
        with patch("osvev_app.time.perf_counter", return_value=105.2):
            self.viewer._diagnostic_timer.timeout.emit()
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 3 (0 recent)")
        self.assertTrue(self.viewer._diagnostic_timer.isActive())

    def test_vfr_displayed_rate_is_paint_rate_not_source_average(self):
        self.start_observations(times=[0, 0.02, 0.1, 0.8, 1.0], tail=0.2)
        for position, wall_time in ((1, 0.02), (2, 0.1), (3, 0.8), (4, 1)):
            self.observe(position, wall_time)
        self.assertEqual(self.viewer.displayed_fps_label.text(), "Displayed: 3.1 FPS")
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 3.1 FPS")
        self.assertAlmostEqual(self.viewer.playback_diagnostics()["actual_speed"], 1)

    def test_simulated_processing_delays_measure_progress_and_paints_separately(self):
        for speed, expected_fps, expected_intentional in (
            ("1x", 30, 0), ("5x", 150, 132),
        ):
            with self.subTest(speed=speed):
                self.viewer.pause_video()
                self.viewer.current_frame_index = 0
                self.start_observations(speed=speed)
                clock = self.viewer._playback_clock
                for tick in range(1, 12):
                    due_time = 100 + tick * 0.1 + 0.001
                    # Simulate 80 ms decode/enhancement/render work on each tick.
                    with patch("osvev_app.time.perf_counter", return_value=due_time), \
                            patch.object(self.viewer, "update_frame"):
                        self.viewer._playback_tick()
                    self.viewer.timer.stop()
                    token = self.viewer.main_label.presentation_token
                    with patch("osvev_app.time.perf_counter", return_value=due_time + 0.08):
                        self.viewer._record_frame_painted(token)
                report = self.viewer.playback_diagnostics()
                self.assertAlmostEqual(report["actual_fps"], expected_fps)
                self.assertEqual(self.viewer.actual_speed_label.text(),
                                 f"Actual: {expected_fps:.1f} FPS")
                self.assertEqual(self.viewer.displayed_fps_label.text(), "Displayed: 10.0 FPS")
                self.assertEqual(report["overload_skipped_frames"], 22)
                self.assertEqual(report["intentional_source_skips"], expected_intentional)
                self.assertEqual(self.viewer.skipped_frames_label.text(),
                                 "Skipped: 22 (22 recent)")
                self.assertAlmostEqual(report["actual_speed"], clock.speed)

    def test_partial_range_nonzero_start_loops_without_index_discontinuity(self):
        from fractions import Fraction
        from frame_info import SourceRange
        self.viewer.source_range = SourceRange(500, 1000, Fraction(7), Fraction(9))
        self.viewer.current_frame_index = 3
        self.start_observations(times=[0, 0.1, 0.3, 0.6, 0.9], tail=0.1)
        clock = self.viewer._playback_clock
        for position in range(4, 13):
            elapsed = clock.timeline.source_time(position) - clock.source_origin
            self.observe(position, elapsed)
        samples = self.viewer._source_paint_samples
        expected = (samples[-1][2] - samples[0][2]) / (samples[-1][0] - samples[0][0])
        self.assertAlmostEqual(self.viewer.playback_diagnostics()["actual_fps"], expected)
        self.assertAlmostEqual(self.viewer.playback_diagnostics()["actual_speed"], 1)
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 0 (0 recent)")

    def test_vfr_source_average_does_not_determine_actual_fps(self):
        self.start_observations(times=[0, 0.02, 0.1, 0.8, 1.0], tail=0.2)
        self.viewer.frame_rate = 999  # Metadata FPS must not enter either observation.
        self.observe(1, 0.02)
        self.observe(4, 1.0)
        expected = 3 / 0.98
        self.assertAlmostEqual(self.viewer.playback_diagnostics()["actual_fps"], expected)
        self.assertEqual(self.viewer.actual_speed_label.text(), "Actual: 3.1 FPS")
        self.assertEqual(self.viewer.displayed_fps_label.text(), "Displayed: 1.0 FPS")
        self.assertEqual(self.viewer.skipped_frames_label.text(), "Skipped: 2 (2 recent)")


if __name__ == "__main__":
    unittest.main()
