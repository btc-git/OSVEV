# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Source frame metadata, separate from display-only magnification."""

from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
import subprocess
import sys
import tempfile

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from ffmpeg_tools import tool_path
from color_interpretation import MissingColorMetadata, InputCompatibilityError


class MetadataCancelled(Exception):
    """The user cancelled a metadata scan."""


@dataclass(frozen=True)
class SourceRange:
    """Partially indexed working range; positions map to original source frames."""

    offset: int
    source_frame_count: int
    start_seconds: Fraction
    end_seconds: Fraction

    def source_index(self, position: int) -> int:
        return self.offset + position


def parse_time(text: str) -> Fraction:
    """Parse seconds or [[HH:]MM:]SS[.fraction] exactly, without float rounding."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or any(not part.strip() for part in parts):
        raise ValueError(f"Invalid time: {text!r}. Use seconds or HH:MM:SS.sss.")
    try:
        values = [Fraction(part.strip()) for part in parts]
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f"Invalid time: {text!r}. Use seconds or HH:MM:SS.sss.") from error
    if any(value < 0 for value in values) or any(value >= 60 for value in values[1:]):
        raise ValueError(f"Invalid time: {text!r}.")
    seconds = Fraction(0)
    for value in values:
        seconds = seconds * 60 + value
    return seconds


class FrameMetadataWorker(QThread):
    """Read metadata without blocking the GUI event loop."""

    progress = pyqtSignal(str, int, int)

    def __init__(self, video_path, parent=None, time_range=None, opener=None, backend="pyav"):
        super().__init__(parent)
        self.video_path = video_path
        self.time_range = time_range
        self.metadata = []
        self.source_range = None
        self.error = None
        self.opener = opener
        self.backend = backend
        self.candidate = None
        self.frame = None
        self.frame_count = 0
        self.frame_rate = 0
        self.metadata_error = None
        self.missing_color = None
        self.compatibility_error = None

    def _check_cancelled(self):
        if self.isInterruptionRequested():
            raise MetadataCancelled()

    def run(self):
        try:
            self._check_cancelled()
            if self.opener is not None:
                import numpy as np

                self.progress.emit("Opening video...", 0, 0)
                self.candidate = self.opener(self.video_path)
                self._check_cancelled()
                if not self.candidate.isOpened():
                    raise ValueError("Could not open the video file.")
                self.frame_count = int(self.candidate.get(cv2.CAP_PROP_FRAME_COUNT))
                self.frame_rate = self.candidate.get(cv2.CAP_PROP_FPS)
                if self.frame_count <= 0 and (self.backend != "pyav" or self.time_range is not None):
                    raise ValueError(
                        "Open Time Range requires a declared source frame count; open the entire video instead."
                        if self.backend == "pyav" else
                        "The decoder did not provide a usable frame count."
                    )
                if not np.isfinite(self.frame_rate) or self.frame_rate <= 0:
                    raise ValueError("The decoder did not provide a usable frame rate.")
                self.progress.emit("Reading the first source frame...", 0, 0)
                ret, self.frame = self.candidate.read()
                if not ret or self.frame is None:
                    raise ValueError("Could not read the first frame.")
                self._check_cancelled()
            try:
                self._read_metadata()
            except (cv2.error, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                if self.backend == "pyav" or self.opener is None:
                    raise RuntimeError(f"PyAV fidelity mode requires source metadata: {error}") from error
                self.metadata_error = str(error)
            self._check_cancelled()
            if self.opener is not None and self.backend == "pyav":
                self.progress.emit("Validating the source frame index...", 0, 0)
                self.candidate.configure_index(
                    self.metadata, partial=self.source_range is not None,
                    cancelled=self.isInterruptionRequested,
                )
                self.frame_count = int(self.candidate.get(cv2.CAP_PROP_FRAME_COUNT))
                self._check_cancelled()
                if self.source_range is not None or self.candidate.chroma_location == "center":
                    self.progress.emit("Reading the first indexed source frame...", 0, 0)
                    ret, self.frame = self.candidate.read()
                    if not ret or self.frame is None:
                        raise ValueError("Could not read the first frame of the time range.")
            self._check_cancelled()
        except MetadataCancelled:
            self.metadata = []
            self.source_range = None
        except MissingColorMetadata as error:
            self.missing_color = error
            self.error = str(error)
        except InputCompatibilityError as error:
            self.compatibility_error = str(error)
            self.error = str(error)
        except (cv2.error, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            self.error = str(error)
        finally:
            if self.candidate is not None and (self.error is not None or self.isInterruptionRequested()):
                self.candidate.release()
                self.candidate = None
                self.frame = None
                self.metadata = []
                self.source_range = None

    def _read_metadata(self):
        if self.time_range is None:
            self.metadata = FrameInfoHandler.get_frame_metadata(
                self.video_path, self.isInterruptionRequested,
                progress=self.progress.emit, expected_frames=self.frame_count,
            )
        else:
            self.metadata, self.source_range = FrameInfoHandler.get_range_metadata(
                self.video_path, *self.time_range, self.isInterruptionRequested,
                progress=self.progress.emit,
            )


class FrameInfoHandler:
    """Handles source metadata retrieval and formatting."""

    SPEED_MAP = {
        "0.05x": 20, "0.1x": 10, "0.25x": 4, "0.5x": 2,
        "0.75x": 4 / 3, "1x": 1, "2x": 0.5, "3x": 1 / 3,
        "4x": 0.25, "5x": 0.2,
    }

    FRAME_TYPES = {
        "I": "I-frame (Key frame, full image)",
        "P": "P-frame (Predicted from previous frame)",
        "B": "B-frame (Bi-directionally predicted)",
        "unknown": "Unknown",
    }

    @staticmethod
    def _startupinfo():
        if sys.platform != "win32":
            return None
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo

    @staticmethod
    def check_ffprobe():
        """Check if ffprobe is available."""
        try:
            subprocess.run(
                [tool_path("ffprobe"), "-version"], capture_output=True, text=True,
                check=True, startupinfo=FrameInfoHandler._startupinfo(),
            )
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    @staticmethod
    def validate_display_transforms(record):
        """A display matrix can encode flips/shears even when rotation is zero."""
        if (
            record.get("rotation", 0) not in (0, "0")
            or record.get("tags", {}).get("rotate", "0") not in (0, "0")
            or any(side.get("side_data_type") == "Display Matrix"
                   for side in record.get("side_data_list", []))
        ):
            raise ValueError(
                "Unsupported source rotation/display matrix. OSVEV does not apply "
                "display transforms; open an explicitly normalized source instead."
            )

    @staticmethod
    def get_frame_metadata(video_path, cancelled=None, read_intervals=None, *,
                           progress=None, expected_frames=0):
        """Read presentation-order source metadata; errors are not empty success."""
        if not video_path or not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        if cancelled is not None and cancelled():
            raise MetadataCancelled()
        cmd = [tool_path("ffprobe"), "-v", "error", "-select_streams", "v:0"]
        if read_intervals is not None:
            cmd += ["-read_intervals", read_intervals]
        cmd += [
            "-show_frames", "-show_streams", "-show_entries",
            "frame=pict_type,pts,best_effort_timestamp_time,key_frame,width,height,"
            "pkt_size,pkt_duration_time,duration_time,pix_fmt,color_range,color_space,"
            "color_primaries,color_transfer,chroma_location,interlaced_frame:"
            "frame_side_data=side_data_type,rotation:"
            "stream=bit_rate:stream_side_data=side_data_type,rotation:stream_tags=rotate",
            "-of", "json", video_path,
        ]
        if progress is not None:
            stdout = FrameInfoHandler._scan_with_progress(
                cmd, cancelled, progress, expected_frames
            )
        else:
            stdout = FrameInfoHandler._scan_probe(cmd, cancelled)
        if cancelled is not None and cancelled():
            raise MetadataCancelled()
        if progress is not None:
            progress("Validating source metadata...", 0, 0)
        data = json.loads(stdout)
        if not isinstance(data, dict):
            raise ValueError("FFprobe returned invalid metadata.")
        frames = data.get("frames")
        if not isinstance(frames, list) or not frames or not all(
            isinstance(frame, dict) for frame in frames
        ):
            raise ValueError("FFprobe returned no video frame metadata.")
        for number, record in enumerate(data.get("streams", []) + frames):
            if number % 256 == 0 and cancelled is not None and cancelled():
                raise MetadataCancelled()
            FrameInfoHandler.validate_display_transforms(record)
        streams = data.get("streams", [])
        bitrate = streams[0].get("bit_rate") if streams else None
        for number, frame in enumerate(frames):
            if number % 256 == 0 and cancelled is not None and cancelled():
                raise MetadataCancelled()
            frame["source_stream_bit_rate"] = bitrate
        FrameInfoHandler.annotate_iframe_distances(frames, cancelled)
        return frames

    @staticmethod
    def _stop_probe(process):
        process.terminate()
        try:
            process.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    @staticmethod
    def _scan_probe(cmd, cancelled):
        with subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            startupinfo=FrameInfoHandler._startupinfo(),
        ) as process:
            while True:
                if cancelled is not None and cancelled():
                    FrameInfoHandler._stop_probe(process)
                    raise MetadataCancelled()
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode != 0:
                raise RuntimeError(f"FFprobe metadata scan failed: {stderr.strip()}")
        return stdout

    @staticmethod
    def _scan_with_progress(cmd, cancelled, progress, expected_frames):
        # Separate reader/writer handles avoid sharing FFprobe's file offset.
        with tempfile.TemporaryDirectory(prefix="osvev-metadata-") as directory:
            output = os.path.join(directory, "frames.json")
            with open(output, "wb") as sink, tempfile.TemporaryFile() as errors:
                with subprocess.Popen(
                    cmd, stdout=sink, stderr=errors,
                    startupinfo=FrameInfoHandler._startupinfo(),
                ) as process:
                    try:
                        with open(output, encoding="utf-8") as reader:
                            count, pending = 0, ""
                            progress("Reading source frame metadata", 0, expected_frames)
                            while True:
                                if cancelled is not None and cancelled():
                                    raise MetadataCancelled()
                                finished = process.poll() is not None
                                chunk = reader.read(256 * 1024)
                                lines = (pending + chunk).split("\n")
                                pending = lines.pop()
                                count += sum('"key_frame":' in line for line in lines)
                                progress("Reading source frame metadata", count, expected_frames)
                                if finished and not chunk:
                                    break
                                try:
                                    process.wait(timeout=0.1)
                                except subprocess.TimeoutExpired:
                                    continue
                    finally:
                        if process.poll() is None:
                            FrameInfoHandler._stop_probe(process)
                    if process.returncode != 0:
                        errors.seek(0)
                        raise RuntimeError(
                            "FFprobe metadata scan failed: "
                            + errors.read().decode("utf-8", errors="replace").strip()
                        )
                sink.flush()
            with open(output, encoding="utf-8") as reader:
                return reader.read()

    @staticmethod
    def annotate_iframe_distances(frames, cancelled=None):
        """Only establish distances inside this contiguous, available index."""
        previous = None
        for index, frame in enumerate(frames):
            if index % 256 == 0 and cancelled is not None and cancelled():
                raise MetadataCancelled()
            kind = frame.get("pict_type")
            if kind == "I":
                previous = index
            elif kind not in ("P", "B"):
                previous = None
            frame["frames_since_previous_iframe"] = (
                index - previous if previous is not None else None
            )

    # Packet reordering (B-frames) never spans this much presentation time in practice;
    # the interval/packet cross-check below rejects any incomplete result.
    RANGE_MARGIN_SECONDS = 5

    @staticmethod
    def get_range_metadata(video_path, start, end, cancelled=None, *, progress=None):
        """Index only source frames with presentation time in [start, end) seconds.

        Earlier frames are counted from demuxed packets (no decoding) so every
        position keeps its exact original source frame number; frame metadata
        is decoded by FFprobe only around the interval.
        """
        import av

        if cancelled is not None and cancelled():
            raise MetadataCancelled()
        if progress is not None:
            progress("Counting source packets for the selected time range...", 0, 0)
        start, end = Fraction(start), Fraction(end)
        if start < 0 or end <= start:
            raise ValueError("The range end must be after its start.")
        if not video_path or not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        with av.open(video_path) as container:
            if not container.streams.video:
                raise ValueError("The file contains no video stream.")
            stream = container.streams.video[0]
            if stream.codec_context.name == "ffv1":
                raise ValueError("FFV1 currently requires full-video opening, not Open Time Range.")
            time_base = Fraction(stream.time_base)
            total = stream.frames
            if total <= 0:
                raise ValueError(
                    "The container does not declare its frame count; open the entire video instead."
                )
            start_pts = math.ceil(start / time_base)
            end_pts = math.ceil(end / time_base)
            stop_dts = end_pts + math.ceil(FrameInfoHandler.RANGE_MARGIN_SECONDS / time_base)
            packets = []
            for number, packet in enumerate(container.demux(stream)):
                if number % 256 == 0 and cancelled is not None and cancelled():
                    raise MetadataCancelled()
                if number % 256 == 0 and progress is not None:
                    progress("Counting source packets for the selected time range", number, 0)
                if packet.size == 0 and packet.pts is None:
                    continue
                if packet.pts is None:
                    raise ValueError("Partial indexing requires presentation timestamps on every packet.")
                if packet.dts is not None and packet.dts > stop_dts:
                    break
                if not packet.is_discard:
                    packets.append(packet.pts)
        if len(set(packets)) != len(packets):
            raise ValueError(
                "Duplicate packet timestamps require the complete verified frame index; "
                "open Entire Video instead of Selected Time Range."
            )
        wanted = sorted(pts for pts in packets if start_pts <= pts < end_pts)
        if not wanted:
            raise ValueError(
                f"No source frames have presentation times from {float(start):.6f} "
                f"to before {float(end):.6f} seconds."
            )
        offset = sum(1 for pts in packets if pts < start_pts)
        margin = FrameInfoHandler.RANGE_MARGIN_SECONDS
        first = start - 2
        interval = ((f"{float(first):.6f}" if first > 0 else "")
                    + f"%{float(end + margin):.6f}")
        frames = FrameInfoHandler.get_frame_metadata(
            video_path, cancelled, interval, **({"progress": progress} if progress else {})
        )
        selected = [frame for frame in frames
                    if isinstance(frame.get("pts"), int) and start_pts <= frame["pts"] < end_pts]
        if [frame["pts"] for frame in selected] != wanted:
            raise ValueError(
                "Partial frame metadata does not match the demuxed source packets; "
                "open the entire video instead."
            )
        if offset + len(selected) > total:
            raise ValueError("Partial index exceeds the declared source frame count.")
        FrameInfoHandler.annotate_iframe_distances(selected)
        return selected, SourceRange(offset, total, start, end)

    @staticmethod
    def get_frame_type_description(pict_type):
        return FrameInfoHandler.FRAME_TYPES.get(
            pict_type, f"{pict_type} (Unknown type)"
        )

    @staticmethod
    def get_speed_factor(playback_speed_text):
        return FrameInfoHandler.SPEED_MAP[playback_speed_text]

    @staticmethod
    def format_frame_data(frame_data, frame_index, frame_count, frame_rate,
                          zoom_level, playback_speed, compression, source_range=None):
        """Report source properties without implying zoom modifies the file."""
        speed_factor = FrameInfoHandler.get_speed_factor(playback_speed)
        flag = frame_data.get("key_frame")
        keyframe = "Yes" if flag == 1 else "No" if flag == 0 else "—"
        distance = frame_data.get("frames_since_previous_iframe")
        distance_text = str(distance) if type(distance) is int and distance >= 0 else "—"
        bitrate = frame_data.get("source_stream_bit_rate")
        try:
            bitrate = int(bitrate)
        except (TypeError, ValueError, OverflowError):
            bitrate = 0
        bitrate_text = ("—" if bitrate <= 0 else
                        f"{bitrate / 1_000_000:.3f} Mbps" if bitrate >= 1_000_000 else
                        f"{bitrate / 1000:.1f} kbps")
        range_rows = []
        if source_range is not None:
            range_rows = [("Loaded Range Frame", f"{frame_index + 1} of {frame_count} "
                           f"(source time {float(source_range.start_seconds):.3f}"
                           f"-{float(source_range.end_seconds):.3f} s)")]
            frame_index, frame_count = (source_range.source_index(frame_index),
                                        source_range.source_frame_count)
        width = frame_data.get("width")
        height = frame_data.get("height")
        if width is not None and height is not None:
            width, height = int(width), int(height)
            resolution = f"{width}x{height}"
            pixels = f"{width * height:,}"
        else:
            resolution = pixels = "Unknown"
        packet_size = frame_data.get("pkt_size")
        packet_text = f"{int(packet_size):,} bytes" if packet_size is not None else "Unknown"
        timestamp = frame_data.get("best_effort_timestamp_time")
        timestamp_text = f"{float(timestamp):.6f} sec" if timestamp is not None else "Unknown"
        duration = frame_data.get("duration_time", frame_data.get("pkt_duration_time"))
        if duration is not None:
            duration_text = f"{float(duration):.6f} sec"
        else:
            duration_text = "Unknown"
        return [
            ("Source Frame", f"{frame_index + 1} of {frame_count}"),
            *range_rows,
            ("Frame Type", FrameInfoHandler.get_frame_type_description(
                frame_data.get("pict_type", "unknown"))),
            ("Keyframe", keyframe),
            ("Frames Since Previous I-Frame", distance_text),
            ("Source Bitrate (reported)", bitrate_text),
            ("Source Packet Size", packet_text),
            ("Source Resolution", resolution),
            ("Source Pixels", pixels),
            ("Source Pixel Format", str(frame_data.get("pix_fmt", "Unknown"))),
            ("Source Color Matrix", str(frame_data.get("color_space", "Unknown"))),
            ("Source Color Range", str(frame_data.get("color_range", "Unknown"))),
            ("Inspection Format", "8-bit BGR (OpenCV decode)"),
            ("Magnifier Zoom", f"{zoom_level} (view only)"),
            ("Source Average FPS", f"{frame_rate:.3f}"),
            ("Target Playback FPS", f"{frame_rate / speed_factor:.3f}"),
            ("Source Timestamp", timestamp_text),
            ("Source Frame Duration", duration_text),
            ("JPEG Compression Simulation", f"{compression}% (not source compression)"),
        ]
