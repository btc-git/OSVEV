# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Lightweight header information, never a verified source-frame index."""

from fractions import Fraction
import json
import math
import os

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from ffmpeg_tools import tool_path


def positive_number(value):
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def reported_time(value):
    try:
        return Fraction(str(value))
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def reported_bounds(data):
    streams = data.get("streams", [])
    stream = streams[0] if streams else {}
    container = data.get("format", {})
    stream_start = reported_time(stream.get("start_time"))
    format_start = reported_time(container.get("start_time"))
    start = stream_start if stream_start is not None else format_start
    start = max(Fraction(0), start if start is not None else Fraction(0))
    duration = reported_time(stream.get("duration"))
    if duration is not None and duration > 0:
        origin = stream_start if stream_start is not None else start
        end = origin + duration
    else:
        duration = reported_time(container.get("duration"))
        origin = format_start if format_start is not None else start
        end = origin + duration if duration is not None and duration > 0 else start + 60
    if end <= start:
        end = start + 60
    # Keep the suggested half-open interval nonempty and avoid rounding its end down.
    return Fraction(math.floor(start * 1000), 1000), Fraction(math.ceil(end * 1000), 1000)


def clock_text(value, *, exact=False):
    milliseconds = math.floor(value * 1000 + Fraction(1, 2))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    if exact and value * 1000 != milliseconds:
        hours, remainder = divmod(value, 3600)
        minutes, seconds = divmod(remainder, 60)
        second_text = timestamp_text(seconds)
        if seconds < 10:
            second_text = "0" + second_text
        return f"{int(hours):02d}:{int(minutes):02d}:{second_text}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def summary_text(data, size):
    streams = data.get("streams", [])
    stream = streams[0] if streams else {}
    duration = positive_number(stream.get("duration"))
    if duration is None:
        duration = positive_number(data.get("format", {}).get("duration"))
    duration_text = clock_text(Fraction(str(duration))) if duration is not None else "unknown"
    width, height = stream.get("width"), stream.get("height")
    resolution = (f"{width}x{height}" if type(width) is int and width > 0
                  and type(height) is int and height > 0 else "unknown")
    size_text = f"{size / (1024 * 1024):.2f} MiB" if size is not None else "unknown"
    return f"Reported duration: {duration_text} | {resolution} | {size_text}"


def indexed_bounds(metadata):
    """Only explicit final-frame duration establishes a known exclusive end."""
    try:
        first = Fraction(str(metadata[0]["best_effort_timestamp_time"]))
        last = Fraction(str(metadata[-1]["best_effort_timestamp_time"]))
        duration = Fraction(str(metadata[-1].get(
            "duration_time", metadata[-1].get("pkt_duration_time")
        )))
    except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if first < 0 or last < first or duration <= 0:
        return None
    return first, last + duration


def timestamp_text(value):
    # Decimal source timestamps/durations retain all their supplied precision.
    from decimal import Decimal, localcontext
    with localcontext() as context:
        context.prec = 40
        return format(Decimal(value.numerator) / Decimal(value.denominator), "f")


class VideoSummaryProbe(QObject):
    ready = pyqtSignal(dict, object, str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.process = QProcess(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self._timeout)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.done = False
        try:
            self.size = os.path.getsize(path)
        except OSError:
            self.size = None

    def start(self):
        self.timer.start()
        self.process.start(tool_path("ffprobe"), [
            "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=width,height,duration,start_time:format=duration,start_time", "-of", "json",
            self.path,
        ])

    def stop(self):
        self.done = True
        self.timer.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)

    def _complete(self, data, error):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        self.ready.emit(data, self.size, error)

    def _timeout(self):
        self._complete({}, "Header information unavailable: metadata probe timed out.")
        self.stop()

    def _error(self, error):
        self._complete({}, f"Header information unavailable: {self.process.errorString()}")

    def _finished(self, code, status):
        if self.done:
            return
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            detail = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip()
            self._complete({}, f"Header information unavailable: {detail or 'probe failed'}")
            return
        try:
            data = json.loads(bytes(self.process.readAllStandardOutput()))
            if not isinstance(data, dict) or not isinstance(data.get("streams", []), list):
                raise ValueError("invalid header information")
            if any(not isinstance(stream, dict) for stream in data.get("streams", [])):
                raise ValueError("invalid video stream information")
            if not isinstance(data.get("format", {}), dict):
                raise ValueError("invalid format information")
        except (ValueError, UnicodeError) as error:
            self._complete({}, f"Header information unavailable: {error}")
            return
        self._complete(data, "")
