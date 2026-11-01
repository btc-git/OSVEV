# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Loaded-file identification and an FFprobe summary of header-reported values.

Values come from FFprobe's container and stream headers, read once when the video
is loaded. They are not verified against decoded frames.
"""

from datetime import datetime, timezone
import html
import os
import subprocess
import sys

from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox,
                             QPlainTextEdit, QSizePolicy, QVBoxLayout)

from ffmpeg_tools import tool_path
from video_summary import VideoSummaryProbe, clock_text, reported_time

NOTE = ("Reported by FFprobe from container and stream headers when the video was loaded; "
        "not verified against decoded frames. Per-frame values are in Frame Information.")
KEY_WIDTH = 32


class MediaInfoProbe(VideoSummaryProbe):
    """Header probe of the container and every stream."""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.timer.setInterval(15000)
        try:
            self.modified = os.stat(path).st_mtime
        except OSError:
            self.modified = None

    def start(self):
        self.timer.start()
        self.process.start(tool_path("ffprobe"), [
            "-v", "error", "-show_format", "-show_streams", "-of", "json", self.path,
        ])


def size_text(size):
    for unit, scale in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if size >= scale:
            return f"{size / scale:.2f} {unit}"
    return f"{size} bytes"


def _tag(item, name):
    # Matroska reports some tags in upper case.
    for key, value in (item.get("tags") or {}).items():
        if key.lower() == name and str(value).strip():
            return str(value)
    return None


def _codec(stream):
    long_name, name = stream.get("codec_long_name"), stream.get("codec_name")
    if long_name and name:
        return f"{long_name} ({name})"
    return long_name or name


def _short_codec(stream):
    long_name = stream.get("codec_long_name")
    return long_name.split(" / ")[0].strip() if long_name else stream.get("codec_name")


def _level(stream):
    level = stream.get("level")
    if type(level) is not int or level <= 0:
        return None
    if stream.get("codec_name") == "h264":
        return "1b" if level == 9 else f"{level / 10:g}"
    if stream.get("codec_name") == "hevc":
        return f"{level / 30:g}"
    return str(level)


def _duration(value):
    duration = reported_time(value)
    if duration is None or duration < 0:
        return None
    return f"{clock_text(duration)} ({value} s)"


def _seconds(value):
    return f"{value} s" if reported_time(value) is not None else None


def _bit_rate(value):
    try:
        rate = int(value)
    except (TypeError, ValueError):
        return None
    return f"{rate:,} b/s ({rate / 1000:.1f} kb/s)" if rate > 0 else None


def _frame_rate(value):
    rate = reported_time(value)
    if rate is None or rate <= 0:
        return None
    return f"{value} ({float(rate):.3f} fps)"


def _ratio(value):
    return value if isinstance(value, str) and value not in ("0:1", "N/A") else None


def _positive(value, suffix=""):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return f"{number:,}{suffix}" if number > 0 else None


def _codec_id(stream):
    # FFprobe prints unprintable or absent tags as bracketed byte values.
    tag = stream.get("codec_tag_string")
    return tag if isinstance(tag, str) and tag.strip() and "[" not in tag else None


def _sample_rate(value):
    try:
        rate = int(value)
    except (TypeError, ValueError):
        return None
    return f"{rate} Hz" if rate > 0 else None


def _flag(stream, name):
    value = (stream.get("disposition") or {}).get(name)
    return {1: "Yes", 0: "No"}.get(value)


def _modified_text(timestamp):
    if timestamp is None:
        return None
    try:
        local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone()
    except (OverflowError, OSError, ValueError):
        return None
    return local.isoformat(sep=" ", timespec="seconds")


def summary_line(data, size, error=""):
    """Header-reported source attributes, not processed dimensions or playback FPS."""
    parts = []
    if error and not data:
        parts.append("FFprobe info unavailable")
    else:
        video = next((stream for stream in data.get("streams", [])
                      if stream.get("codec_type") == "video"), None)
        if video is None:
            parts.append("No video stream reported")
        else:
            parts.append(f"Codec: {_short_codec(video) or 'not reported'}")
            width, height = video.get("width"), video.get("height")
            resolution = (f"{width}x{height}" if type(width) is int and width > 0
                          and type(height) is int and height > 0 else "not reported")
            parts.append(f"Resolution: {resolution}")
            rate = reported_time(video.get("avg_frame_rate"))
            fps = (f"{float(rate):.3f}".rstrip("0").rstrip(".")
                   if rate is not None and rate > 0 else "not reported")
            parts.append(f"Avg FPS: {fps}")
    if size is not None:
        parts.append(f"Size: {size_text(size)}")
    return " | ".join(parts)


def _video_rows(stream):
    width, height = stream.get("width"), stream.get("height")
    colors = [(label, stream.get(key) or "not reported") for label, key in (
        ("Source color range", "color_range"), ("Source color matrix", "color_space"),
        ("Source color primaries", "color_primaries"),
        ("Source color transfer", "color_transfer"))]
    return [
        ("Codec", _codec(stream)),
        ("Profile", stream.get("profile")),
        ("Level", _level(stream)),
        ("Codec ID", _codec_id(stream)),
        ("Width x height", f"{width} x {height} pixels"
         if type(width) is int and type(height) is int else None),
        ("Sample aspect ratio", _ratio(stream.get("sample_aspect_ratio"))),
        ("Display aspect ratio", _ratio(stream.get("display_aspect_ratio"))),
        ("Average frame rate", _frame_rate(stream.get("avg_frame_rate"))),
        ("Base frame rate (r_frame_rate)", _frame_rate(stream.get("r_frame_rate"))),
        ("Frame count (reported)", _positive(stream.get("nb_frames"))),
        ("Duration (reported)", _duration(stream.get("duration"))),
        ("Start time", _seconds(stream.get("start_time"))),
        ("Time base", stream.get("time_base")),
        ("Bit rate (reported)", _bit_rate(stream.get("bit_rate"))),
        ("Pixel format", stream.get("pix_fmt")),
        ("Bit depth", _positive(stream.get("bits_per_raw_sample"), " bits")),
        *colors,
        ("Chroma location", stream.get("chroma_location")),
        ("Field order", stream.get("field_order")),
        ("Attached picture", "Yes" if _flag(stream, "attached_pic") == "Yes" else None),
    ]


def _audio_rows(stream):
    channels, layout = _positive(stream.get("channels")), stream.get("channel_layout")
    return [
        ("Codec", _codec(stream)),
        ("Profile", stream.get("profile")),
        ("Codec ID", _codec_id(stream)),
        ("Sample rate", _sample_rate(stream.get("sample_rate"))),
        ("Channels", f"{channels} ({layout})" if channels and layout else channels),
        ("Sample format", stream.get("sample_fmt")),
        ("Bit rate (reported)", _bit_rate(stream.get("bit_rate"))),
        ("Duration (reported)", _duration(stream.get("duration"))),
        ("Start time", _seconds(stream.get("start_time"))),
        ("Default", _flag(stream, "default")),
    ]


def media_info_text(path, data, size, error="", modified=None):
    """FFprobe information as plain text; rows without a reported value are omitted."""
    container = data.get("format") or {}
    format_name = container.get("format_long_name")
    if format_name and container.get("format_name"):
        format_name += f" ({container['format_name']})"
    general = [
        ("Complete name", os.path.normpath(path)),
        ("Probe error", error or None),
        ("Container format", format_name),
        ("Major brand", _tag(container, "major_brand")),
        ("Compatible brands", _tag(container, "compatible_brands")),
        ("File size", f"{size:,} bytes ({size_text(size)})" if size is not None else None),
        ("File modified (file system)", _modified_text(modified)),
        ("Duration (reported)", _duration(container.get("duration"))),
        ("Overall bit rate (reported)", _bit_rate(container.get("bit_rate"))),
        ("Streams", _positive(container.get("nb_streams"))),
        ("Title", _tag(container, "title")),
        ("Creation time (tag)", _tag(container, "creation_time")),
        ("Writing application", _tag(container, "encoder")),
        ("Comment", _tag(container, "comment")),
    ]
    sections = [("General", general)]
    counts, totals = {}, {}
    streams = data.get("streams", [])
    for stream in streams:
        kind = str(stream.get("codec_type") or "unknown").capitalize()
        totals[kind] = totals.get(kind, 0) + 1
    for stream in streams:
        kind = str(stream.get("codec_type") or "unknown").capitalize()
        counts[kind] = counts.get(kind, 0) + 1
        title = kind if totals[kind] == 1 else f"{kind} #{counts[kind]}"
        rows = [("Stream index", str(stream.get("index", "?")))]
        if kind == "Video":
            rows += _video_rows(stream)
        elif kind == "Audio":
            rows += _audio_rows(stream)
        else:
            rows += [("Codec", _codec(stream)), ("Default", _flag(stream, "default"))]
        rows += [("Language", _tag(stream, "language")), ("Title", _tag(stream, "title"))]
        sections.append((title, rows))
    lines = [NOTE]
    for title, rows in sections:
        lines += ["", title]
        lines += [f"{key:<{KEY_WIDTH}} : {value}" for key, value in rows if value]
    return "\n".join(lines) + "\n"


def reveal_in_folder(path):
    """Open the file's folder in the system file manager with the file selected."""
    path = os.path.normpath(os.path.abspath(path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"The loaded video is no longer at:\n{path}")
    if sys.platform == "win32":
        # Full path so a same-named program in the working directory is never run.
        explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
        subprocess.Popen(f'"{explorer}" /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif not QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))):
        raise OSError(f"Could not open the folder:\n{os.path.dirname(path)}")


class FileNameLabel(QLabel):
    """Loaded file name as a link, elided to fit; the tooltip shows the full path."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = None
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.set_path(None)

    def set_path(self, path):
        self._path = path
        self.setToolTip(f"{os.path.normpath(path)}\nClick to show the file in its folder."
                        if path else "")
        self.updateGeometry()
        self._refresh()

    def _name(self):
        return os.path.basename(self._path) if self._path else "No video loaded"

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self._name()) + 4,
                     super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(min(self.sizeHint().width(), self.fontMetrics().horizontalAdvance("M" * 6)),
                     super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        shown = html.escape(self.fontMetrics().elidedText(
            self._name(), Qt.TextElideMode.ElideMiddle, max(1, self.contentsRect().width() - 2)))
        self.setText(f'<a href="reveal" style="color: #0057b8;">{shown}</a>' if self._path else shown)


class MediaInfoDialog(QDialog):
    def __init__(self, path, data, size, error="", modified=None, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"FFprobe Info - {os.path.basename(path)}")
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setPlainText(media_info_text(path, data, size, error, modified))
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.show_button = buttons.addButton("Show in Folder", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_button = buttons.addButton("Copy", QDialogButtonBox.ButtonRole.ActionRole)
        self.show_button.clicked.connect(self._reveal)
        self.copy_button.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reveal(self):
        try:
            reveal_in_folder(self.path)
        except OSError as error:
            QMessageBox.warning(self, "Show in Folder", str(error))
