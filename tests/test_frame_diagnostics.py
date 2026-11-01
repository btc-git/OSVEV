# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import json
import unittest
from fractions import Fraction
from unittest.mock import Mock, patch

from frame_info import FrameInfoHandler, SourceRange


class FrameDiagnosticTests(unittest.TestCase):
    def rows(self, frame, source_range=None):
        return dict(FrameInfoHandler.format_frame_data(
            frame, 0, 10, 30, "1x", "1x", 0, source_range
        ))

    def test_keyframe_flag_is_independent_of_picture_type(self):
        for flag, kind, expected in ((1, "P", "Yes"), (0, "I", "No"),
                                      (None, "I", "—"), ("N/A", "P", "—")):
            self.assertEqual(self.rows({"key_frame": flag, "pict_type": kind})["Keyframe"],
                             expected)

    def test_iframe_distance_is_index_spacing_not_keyframe_spacing(self):
        frames = [
            {"pict_type": kind, "key_frame": key}
            for kind, key in (("P", 1), ("I", 0), ("B", 1), ("P", 0),
                              ("I", 0), ("P", 1))
        ]
        FrameInfoHandler.annotate_iframe_distances(frames)
        self.assertEqual([frame["frames_since_previous_iframe"] for frame in frames],
                         [None, 0, 1, 2, 0, 1])
        self.assertEqual(self.rows(frames[3])["Frames Since Previous I-Frame"], "2")
        # A partial index cannot certify an I-frame preceding the range.
        selected = [frame.copy() for frame in frames[2:]]
        FrameInfoHandler.annotate_iframe_distances(selected)
        self.assertEqual([frame["frames_since_previous_iframe"] for frame in selected],
                         [None, None, 0, 1])
        source_range = SourceRange(100, 300, Fraction(2), Fraction(3))
        self.assertEqual(self.rows(selected[0], source_range)["Frames Since Previous I-Frame"],
                         "—")
        self.assertEqual(self.rows(selected[3], source_range)["Frames Since Previous I-Frame"],
                         "1")

    def test_unknown_type_breaks_certified_distance(self):
        frames = [{"pict_type": kind} for kind in ("I", "P", None, "B", "I")]
        FrameInfoHandler.annotate_iframe_distances(frames)
        self.assertEqual([frame["frames_since_previous_iframe"] for frame in frames],
                         [0, 1, None, None, 0])

    def test_bitrate_formats_only_reported_video_stream_value(self):
        for value, expected in (
            ("12345678", "12.346 Mbps"), ("512000", "512.0 kbps"),
            (None, "—"), ("N/A", "—"), (0, "—"), (-1, "—"),
        ):
            self.assertEqual(self.rows({
                "source_stream_bit_rate": value, "pkt_size": 900000,
                "duration_time": "0.01",
            })["Source Bitrate (reported)"], expected)
        rows = self.rows({})
        self.assertEqual(rows["Frames Since Previous I-Frame"], "—")
        self.assertEqual(rows["Keyframe"], "—")
        self.assertIn("Source Average FPS", rows)
        self.assertIn("Target Playback FPS", rows)
        self.assertIn("JPEG Compression Simulation", rows)
        self.assertFalse({"Source FPS (average)", "Playback FPS (target)",
                          "JPEG Simulation", "Native Source Samples"} & set(rows))

    def test_existing_probe_supplies_bitrate_without_additional_process(self):
        process = Mock()
        process.returncode = 0
        process.communicate.return_value = (json.dumps({
            "streams": [{"bit_rate": "1500000"}],
            "frames": [{"pict_type": "I"}, {"pict_type": "P"}],
        }), "")
        context = Mock()
        context.__enter__ = Mock(return_value=process)
        context.__exit__ = Mock(return_value=False)
        with patch("frame_info.os.path.isfile", return_value=True), \
                patch("frame_info.subprocess.Popen", return_value=context) as popen:
            frames = FrameInfoHandler.get_frame_metadata("source.mp4")
        popen.assert_called_once()
        self.assertIn("stream=bit_rate", popen.call_args.args[0][
            popen.call_args.args[0].index("-show_entries") + 1])
        self.assertEqual(self.rows(frames[1])["Source Bitrate (reported)"], "1.500 Mbps")
        self.assertEqual(self.rows(frames[1])["Frames Since Previous I-Frame"], "1")
