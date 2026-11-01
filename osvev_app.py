# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Main OSVEV application class."""

import cv2
import numpy as np
import subprocess
import json
import os
import sys
import tempfile
import ctypes
import traceback
import time
from fractions import Fraction
from collections import deque

from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, 
                            QHBoxLayout, QSlider, QComboBox, QCheckBox, QFileDialog,
                            QScrollArea, QProgressDialog, QMessageBox, QSplitter, 
                            QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
                            QGridLayout, QProgressBar, QFrame, QAbstractScrollArea, QSpinBox,
                            QDialog, QDialogButtonBox, QFormLayout, QLineEdit)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QSize, QEventLoop, QEvent
from PyQt6.QtGui import (QImage, QPixmap, QCursor, QPainter, QPen, QColor, QFont,
                         QIcon, QKeySequence, QShortcut)

from qt_compat import QtCompat
from magnifier_label import MagnifierLabel
from magnifier_exact import ExactMagnifier, MagnifierRequest
from frame_processor import FrameProcessor
from frame_info import FrameInfoHandler, FrameMetadataWorker, parse_time
from playback_timing import PlaybackTimeline, PlaybackClock
from audio_preview import AudioPreview
from video_summary import VideoSummaryProbe, summary_text, indexed_bounds, clock_text, reported_bounds
from media_info import FileNameLabel, MediaInfoDialog, MediaInfoProbe, reveal_in_folder, summary_line
from color_interpretation import MissingColorMetadata, InputCompatibilityError
from color_dialog import ColorAssumptionsDialog
from timeline_slider import TimelineSlider
from scrub_preview import PreviewSource, ScrubPreview
from crop_utils import (CROP_KEYS, ROTATION_LABELS, ROTATIONS, crop_rectangle,
                        unrotate_point)
from ffmpeg_tools import tool_path
from version import (APP_NAME, COPYRIGHT, LICENSE_NAME, SOURCE_URL, __version__,
                     git_commit, resource_path)

VIDEO_INPUT_FILTER = "Video Files (*.mp4 *.mov *.avi *.mkv *.m4v)"


class TimeRangeDialog(QDialog):
    """Ask for a half-open source presentation-time interval [start, end)."""

    def __init__(self, parent=None, choose_scope=False, allow_range=True, video_path=None):
        super().__init__(parent)
        self.setWindowTitle("Open Video" if choose_scope else "Open Time Range")
        layout = QVBoxLayout(self)
        self.scope_dropdown = QComboBox()
        self.scope_dropdown.addItems(["Entire Video", "Selected Time Range"])
        self.scope_dropdown.setCurrentIndex(0 if choose_scope else 1)
        self.scope_dropdown.setVisible(choose_scope)
        self.scope_dropdown.setEnabled(allow_range)
        self.scope_dropdown.setToolTip(
            "Entire Video uses full indexing. Selected Time Range requires the "
            "PyAV decoder and a source eligible for exact partial indexing."
        )
        layout.addWidget(self.scope_dropdown)
        self.summary_label = QLabel("Reported duration: unknown | unknown | unknown")
        self.summary_label.setWordWrap(True)
        self.summary_label.setFixedHeight(self.fontMetrics().lineSpacing() * 2)
        self.summary_label.setVisible(video_path is not None)
        layout.addWidget(self.summary_label)
        note = QLabel("Source timestamps: seconds or HH:MM:SS.sss. End is exclusive.")
        note.setWordWrap(True)
        note.setToolTip(
            "Frames with start <= source presentation timestamp < end are indexed. "
            "Earlier frames are counted from demuxed packets without decoding; "
            "frame numbers and exports refer to the unchanged original file. "
            "Reported container duration is not verified frame timing."
        )
        self.range_fields = QWidget()
        self.range_fields.setStyleSheet(
            "QLabel:disabled, QLineEdit:disabled { color: #808080; }"
            "QLineEdit:disabled { background-color: palette(window); }"
        )
        range_layout = QVBoxLayout(self.range_fields)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(note)
        form = QFormLayout()
        self.start_edit, self.end_edit = QLineEdit("00:00:00.000"), QLineEdit("00:01:00.000")
        form.addRow("Start", self.start_edit)
        form.addRow("End (exclusive)", self.end_edit)
        range_layout.addLayout(form)
        self.bounds_note = QLabel("Editable defaults; exact boundaries require a complete frame index.")
        self.bounds_note.setWordWrap(True)
        self.bounds_note.setFixedHeight(self.fontMetrics().lineSpacing() * 2)
        range_layout.addWidget(self.bounds_note)
        layout.addWidget(self.range_fields)
        self.range_fields.setEnabled(self.scope_dropdown.currentIndex() == 1)
        self.scope_dropdown.currentIndexChanged.connect(
            lambda index: self.range_fields.setEnabled(index == 1)
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.time_range = None
        self._known_bounds = None
        self.probe = None
        if (video_path and parent is not None and parent.video_path
                and os.path.normcase(os.path.abspath(video_path))
                == os.path.normcase(os.path.abspath(parent.video_path))
                and parent.decoder_backend == "pyav"
                and parent.source_range is None
                and len(parent.frame_metadata) == parent.frame_count):
            self._known_bounds = indexed_bounds(parent.frame_metadata)
        if self._known_bounds is not None:
            self.start_edit.setText(clock_text(self._known_bounds[0], exact=True))
            self.end_edit.setText(clock_text(self._known_bounds[1], exact=True))
            self.bounds_note.setText("Defaults use the current complete source-frame index.")
        if video_path:
            self.probe = VideoSummaryProbe(video_path, self)
            self.probe.ready.connect(self._summary_ready)
            self.probe.start()
        self.setFixedWidth(450)
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())

    def _summary_ready(self, data, size, error):
        self.summary_label.setText(summary_text(data, size))
        self.summary_label.setToolTip(error or "Header-reported values, not verified frame timing.")
        if error and self._known_bounds is None:
            self.bounds_note.setText("Header unavailable; editable defaults are not verified boundaries.")
        if self._known_bounds is not None or self.start_edit.isModified() or self.end_edit.isModified():
            return
        if error:
            return
        start, end = reported_bounds(data)
        self.start_edit.setText(clock_text(start))
        self.end_edit.setText(clock_text(end))
        self.bounds_note.setText(
            "Time range defaults are based on reported metadata and may be approximate."
        )

    def done(self, result):
        if self.probe is not None:
            self.probe.stop()
        super().done(result)

    def accept(self):
        if self.scope_dropdown.currentIndex() == 0:
            self.time_range = None
            super().accept()
            return
        try:
            start, end = parse_time(self.start_edit.text()), parse_time(self.end_edit.text())
            if end <= start:
                raise ValueError("The end time must be after the start time.")
        except ValueError as error:
            QMessageBox.warning(self, "Open Time Range", str(error))
            return
        self.time_range = (start, end)
        super().accept()

    @staticmethod
    def get_range(parent=None):
        dialog = TimeRangeDialog(parent)
        return dialog.time_range if dialog.exec() else None

    @staticmethod
    def get_open_choice(parent=None, allow_range=True, video_path=None):
        dialog = TimeRangeDialog(parent, choose_scope=True, allow_range=allow_range,
                                 video_path=video_path)
        return (True, dialog.time_range) if dialog.exec() else (False, None)


class AboutDialog(QDialog):
    """Version, copyright, license and bundled third-party notices."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QPlainTextEdit, QTabWidget
        import PyQt6.QtCore as qt_core
        self.setWindowTitle(f"About {APP_NAME}")
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        revision = git_commit()
        build_state = ("Development build (uncommitted source changes)"
                       if revision.endswith("+dirty") else
                       "Source revision available in Build details"
                       if revision != "unknown" else "Source revision unavailable")
        ffmpeg_path = tool_path("ffmpeg")
        ffmpeg_error = ""
        try:
            ffmpeg_details = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-version"], capture_output=True, text=True, check=True,
                timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip()
            if not ffmpeg_details:
                raise ValueError("FFmpeg returned no version information.")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            ffmpeg_details = f"FFmpeg version query failed: {error}"
            ffmpeg_error = "<br>FFmpeg information unavailable; see Build details."
        try:
            import av
            pyav_version = av.__version__
        except ImportError:
            pyav_version = "not installed"
        header = QLabel(
            f"<b>{APP_NAME} {__version__}</b><br>"
            "Open Source Video Enhancement Visualizer<br>"
            f"{COPYRIGHT}<br>{build_state}<br><br>"
            f"OSVEV source is licensed under the {LICENSE_NAME}. Packaged distributions "
            "combine components under GPL version 3; see Third-party notices. "
            "This program comes with ABSOLUTELY NO WARRANTY.<br>"
            f'Source code: <a href="{SOURCE_URL}" style="color: #0057b8;">{SOURCE_URL}</a>'
            f"{ffmpeg_error}"
        )
        header.setWordWrap(True)
        header.setOpenExternalLinks(True)
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(header)
        tabs = QTabWidget()
        for title, name in (("License (GPL-3.0)", "LICENSE"),
                            ("Third-party notices", "THIRD_PARTY_NOTICES.md")):
            text = QPlainTextEdit()
            text.setReadOnly(True)
            try:
                text.setPlainText(resource_path(name).read_text(encoding="utf-8"))
            except OSError:
                text.setPlainText(f"{name} was not found. See {SOURCE_URL}.")
            tabs.addTab(text, title)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(
            f"{APP_NAME} {__version__}\nSource revision: {revision}\n{build_state}\n\n"
            f"PyAV: {pyav_version}\nQt: {qt_core.QT_VERSION_STR}\n"
            f"PyQt: {qt_core.PYQT_VERSION_STR}\nOpenCV: {cv2.__version__}\nNumPy: {np.__version__}\n\n"
            f"FFmpeg executable: {ffmpeg_path}\n\n{ffmpeg_details}"
        )
        tabs.addTab(details, "Build details")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

class OSVEV(QWidget):
    """Main video analysis application."""
    
    def __init__(self, decoder_backend="pyav"):
        super().__init__()
        if decoder_backend not in ("opencv", "pyav"):
            raise ValueError(f"Unknown input decoder: {decoder_backend}")
        self.decoder_backend = decoder_backend
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setMinimumSize(1200, 800)
        
        # Create and set application icon
        self.create_app_icon()
        self.set_windows_taskbar_icon()
        self.set_global_font_size()

        # Check for required dependencies
        self._check_dependencies()

        # Create UI
        self._setup_ui()
        
        # Initialize video state
        self.cap = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._playback_tick)
        self.is_playing = False
        self.frame_rate = 30
        self.frame_count = 0
        self.current_frame_index = 0
        self.was_playing_before_timeline = False
        
        self.video_path = None
        self._media_probe = None
        self._media_info = None
        self.current_frame = None
        self.current_native_samples = None
        self._exact_sample_pending = None
        self._exact_sample = None
        self.frame_metadata = []
        self.source_range = None
        self.current_pixel_pos = QPoint(0, 0)
        self.current_zoomed_roi = None
        self.shown = False
        
        # Processing cache
        self.process_cache = {}
        self.frame_processor = FrameProcessor()
        self._display_map_key = self._display_map = None
        self._magnifier_closing = False
        self._exact_magnifier = ExactMagnifier(self)
        self._exact_magnifier.ready.connect(self._show_exact_magnifier)
        self._exact_magnifier.sampled.connect(self._show_exact_sample)
        self._exact_magnifier.failed.connect(self._exact_magnifier_error)
        self.last_processing_params = {}
        self.frame_skip = 1
        self._last_decoded_index = -1
        self._scrub_preview = ScrubPreview(self)
        self._scrub_preview.ready.connect(self._show_scrub_preview)
        self._scrub_preview.failed.connect(self._scrub_preview_error)
        self._scrub_active = False
        self._scrub_has_preview = False
        self._scrub_failed = False
        self._scrub_requested = None
        self._metadata_worker = None
        self._loading_progress = None
        self._load_cancelled = False
        self._export_dialog = None
        self._working_copy_dialog = None
        self._playback_timeline = None
        self._playback_clock = None
        self._audio = AudioPreview(self)
        self._audio.notice.connect(self.audio_notice.setText)
        self._audio.status.connect(self._show_audio_status)
        self.volume_slider.valueChanged.connect(self._audio.set_volume)
        self.volume_slider.valueChanged.connect(
            lambda value: self.volume_value_label.setText(f"{value}%"))
        self.mute_checkbox.toggled.connect(self._audio.set_muted)
        self._audio_source_offset = None
        self._playback_generation = 0
        self._paint_times = deque(maxlen=120)
        self._paint_intervals = deque(maxlen=120)
        self._paint_lateness = deque(maxlen=120)
        self._source_paint_samples = deque(maxlen=120)
        self._recent_paints = deque()
        self._recent_skips = deque()
        self._last_paint_position = None
        self._intentional_skips = 0
        self._late_skips = 0
        self._missed_paint_slots = 0
        self._last_diagnostic_update = 0.0
        self._diagnostic_timer = QTimer(self)
        self._diagnostic_timer.setInterval(250)
        self._diagnostic_timer.timeout.connect(self._refresh_performance_labels)
        self.main_label.framePainted.connect(self._record_frame_painted)

    def _setup_ui(self):
        """Setup the user interface."""
        # Main horizontal splitter
        main_splitter = QSplitter(QtCompat.Horizontal)
        main_splitter.setHandleWidth(0)
        main_splitter.setStyleSheet(self._get_splitter_style())
        
        # Left side - video and controls
        left_splitter = QSplitter(QtCompat.Vertical)
        left_splitter.setHandleWidth(0)
        left_splitter.setStyleSheet(self._get_splitter_style())
        
        # Video widget
        video_widget = self._create_video_widget()
        
        # Controls widget
        controls_container = self._create_controls_widget()
        
        left_splitter.addWidget(video_widget)
        left_splitter.addWidget(controls_container)
        left_splitter.setSizes([600, 200])
        
        # Right side - zoom and info
        right_splitter = QSplitter(QtCompat.Vertical)
        right_splitter.setHandleWidth(0)
        right_splitter.setStyleSheet(self._get_splitter_style())
        
        zoom_widget = self._create_zoom_widget()
        lower_right_widget = self._create_frame_info_widget()
        
        right_splitter.addWidget(zoom_widget)
        right_splitter.addWidget(lower_right_widget)
        right_splitter.setSizes([500, 300])
        
        # Main layout
        main_splitter.addWidget(left_splitter)
        main_splitter.addWidget(right_splitter)
        main_splitter.setSizes([int(self.width() * 0.6), int(self.width() * 0.4)])
        
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.addWidget(main_splitter)
        self.setLayout(main_layout)

    def _get_splitter_style(self):
        """Get splitter stylesheet."""
        return """
            QSplitter::handle {
                background-color: #c0c0c0; 
                border: 1px solid #a0a0a0;
                border-radius: 2px;
                margin: 1px;
            }
            QSplitter::handle:hover {
                background-color: #a0a0a0;
            }
            QSplitter::handle:pressed {
                background-color: #808080;
            }
        """

    def _create_video_widget(self):
        """Create video display widget."""
        video_widget = QWidget()
        video_layout = QVBoxLayout(video_widget)
        video_layout.setContentsMargins(10, 10, 10, 10)
        
        # Scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(QtCompat.AlignCenter)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll_area.setVerticalScrollBarPolicy(QtCompat.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCompat.ScrollBarAlwaysOff)
        
        # Main label
        self.main_label = MagnifierLabel()
        self.main_label.setAlignment(QtCompat.AlignCenter)
        self.main_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.main_label.setMinimumSize(640, 480)
        self.scroll_area.setWidget(self.main_label)
        video_layout.addWidget(self.scroll_area)
        
        # Buttons
        buttons_layout = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.setToolTip(
            "Choose a video, then open the entire video (default) or a selected time range."
        )
        self.reset_button = QPushButton("Reset")
        self.prev_frame_button = QPushButton("◀ Frame")
        self.prev_iframe_button = QPushButton("◀ I-Frame")
        self.play_pause_button = QPushButton("Play")
        self.next_iframe_button = QPushButton("I-Frame ▶")
        self.next_frame_button = QPushButton("Frame ▶")
        self.prev_frame_button.setToolTip("Pause and move back one frame (Left).")
        self.next_frame_button.setToolTip("Pause and move forward one frame (Right).")
        self.play_pause_button.setToolTip("Play or pause (Space).")
        self.prev_iframe_button.setToolTip(
            "Pause and go to the nearest preceding I-frame in the loaded video/range, "
            "using source frame-type metadata (not keyframe flags). No wraparound. "
            "Shortcut: Shift+Left."
        )
        self.next_iframe_button.setToolTip(
            "Pause and go to the nearest following I-frame in the loaded video/range, "
            "using source frame-type metadata (not keyframe flags). No wraparound. "
            "Shortcut: Shift+Right."
        )
        self.export_button = QPushButton("Export")
        self.about_button = QPushButton("About")
        
        buttons_layout.addWidget(self.open_button)
        buttons_layout.addWidget(self.reset_button)
        buttons_layout.addWidget(self.prev_frame_button)
        buttons_layout.addWidget(self.prev_iframe_button)
        buttons_layout.addWidget(self.play_pause_button)
        buttons_layout.addWidget(self.next_iframe_button)
        buttons_layout.addWidget(self.next_frame_button)
        buttons_layout.addWidget(self.export_button)
        buttons_layout.addWidget(self.about_button)
        video_layout.addLayout(buttons_layout)
        
        # Timeline
        timeline_layout = QHBoxLayout()
        timeline_layout.addWidget(QLabel("Timeline:"))
        self.timeline_slider = TimelineSlider()
        self.timeline_slider.setEnabled(False)
        self.timeline_slider.setToolTip(
            "Click to seek. Drag for live previews when decoding permits; "
            "release to seek accurately to the final frame."
        )
        timeline_layout.addWidget(self.timeline_slider)
        video_layout.addLayout(timeline_layout)
        
        # Connect signals
        self.open_button.clicked.connect(self.choose_video)
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        self.prev_frame_button.clicked.connect(self.previous_frame)
        self.next_frame_button.clicked.connect(self.manual_next_frame)
        self.prev_iframe_button.clicked.connect(lambda: self.navigate_iframe(-1))
        self.next_iframe_button.clicked.connect(lambda: self.navigate_iframe(1))
        self.timeline_slider.sliderPressed.connect(self.on_timeline_pressed)
        self.timeline_slider.previewRequested.connect(self._request_scrub_preview)
        self.timeline_slider.seekRequested.connect(self.set_frame_position)
        self.timeline_slider.sliderReleased.connect(self.on_timeline_released)
        self.reset_button.clicked.connect(self.reset_settings)
        self.export_button.clicked.connect(self.open_export_dialog)
        self.about_button.clicked.connect(lambda: AboutDialog(self).exec())
        for key, button in (
            ("Space", self.play_pause_button),
            ("Left", self.prev_frame_button),
            ("Right", self.next_frame_button),
            ("Shift+Left", self.prev_iframe_button),
            ("Shift+Right", self.next_iframe_button),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setAutoRepeat(False)
            if key in ("Left", "Right"):
                shortcut.activated.connect(
                    lambda b=button, d=-1 if key == "Left" else 1: self._arrow_pressed(b, d))
            else:
                shortcut.activated.connect(button.click)
        # A held arrow repeats via a single-shot timer restarted only after each
        # step completes, so slow decodes never accumulate a backlog of steps.
        self._arrow_hold_direction = 0
        self._arrow_hold_timer = QTimer(self)
        self._arrow_hold_timer.setSingleShot(True)
        self._arrow_hold_timer.timeout.connect(self._arrow_hold_step)
        
        return video_widget

    ARROW_HOLD_DELAY_MS = 350
    ARROW_HOLD_INTERVAL_MS = 33

    def _arrow_pressed(self, button, direction):
        self._stop_arrow_hold()
        before = self.current_frame_index
        button.click()
        if self.current_frame_index != before:
            self._arrow_hold_direction = direction
            QApplication.instance().installEventFilter(self)
            self._arrow_hold_timer.start(self.ARROW_HOLD_DELAY_MS)

    def _stop_arrow_hold(self):
        if self._arrow_hold_direction:
            QApplication.instance().removeEventFilter(self)
        self._arrow_hold_direction = 0
        self._arrow_hold_timer.stop()

    def _arrow_hold_step(self):
        direction = self._arrow_hold_direction
        if (not direction or QApplication.activeModalWidget() is not None
                or not self.isActiveWindow()
                or not self.cap or not self.cap.isOpened() or self.frame_count <= 0):
            self._stop_arrow_hold()
            return
        before = self.current_frame_index
        # Holding stops at the loaded boundaries instead of wrapping around.
        if (direction < 0 and before <= 0) or (direction > 0 and before >= self.frame_count - 1):
            self._stop_arrow_hold()
            return
        (self.previous_frame if direction < 0 else self.manual_next_frame)()
        if self._arrow_hold_direction != direction or self.current_frame_index == before:
            self._stop_arrow_hold()
            return
        self._arrow_hold_timer.start(self.ARROW_HOLD_INTERVAL_MS)

    def eventFilter(self, obj, event):
        if self._arrow_hold_direction:
            kind = event.type()
            if kind == event.Type.KeyRelease and not event.isAutoRepeat():
                if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                    self._stop_arrow_hold()
            elif (kind in (event.Type.KeyPress, event.Type.ShortcutOverride)
                  and not event.isAutoRepeat()):
                if event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                    self._stop_arrow_hold()
            elif kind == event.Type.ApplicationDeactivate:
                self._stop_arrow_hold()
        return super().eventFilter(obj, event)

    def open_export_dialog(self):
        if self.cap is None or not self.cap.isOpened() or self.current_frame is None:
            QMessageBox.warning(self, "Export", "Open a video before exporting.")
            return
        self.pause_video()
        from export_dialog import ExportDialog
        dialog = ExportDialog(
            self.video_path, self.current_frame_index, self.frame_metadata,
            self.get_processing_params(), self.decoder_backend, self,
            playback_speed=self.speed_multiplier,
            zoom_level=int(self.zoom_dropdown.currentText().replace("x", "")),
            interpolation=self.interpolation_dropdown.currentText(),
            source_range=self.source_range,
            color_choices=getattr(self.cap, "color_choices", None),
        )
        self._export_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._export_dialog = None
            dialog.deleteLater()

    def _create_controls_widget(self):
        """Create controls widget."""
        controls_container = QScrollArea()
        controls_container.setWidgetResizable(True)
        controls_container.setVerticalScrollBarPolicy(QtCompat.ScrollBarAlwaysOn)
        controls_container.setHorizontalScrollBarPolicy(QtCompat.ScrollBarAlwaysOff)
        
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(10, 10, 10, 10)

        file_layout = QHBoxLayout()
        file_layout.setSpacing(6)
        file_layout.addWidget(QLabel("File:"))
        self.file_name_label = FileNameLabel()
        self.file_name_label.linkActivated.connect(self._reveal_loaded_file)
        file_layout.addWidget(self.file_name_label)
        file_layout.addSpacing(12)
        self.media_summary_label = QLabel()
        self.media_summary_label.setWordWrap(True)
        self.media_summary_label.setToolTip(
            "Codec, source resolution, average frame rate, and file size reported by "
            "FFprobe when loaded; not verified against decoded frames. Resolution is "
            "before crop/rotation/zoom. Average FPS does not imply a constant frame rate "
            "and is not playback speed. Profile/level and more details are in FFprobe Info.")
        file_layout.addWidget(self.media_summary_label, 1)
        self.media_info_button = QPushButton("FFprobe Info")
        self.media_info_button.setToolTip(
            "Show FFprobe-reported container and stream header information for the loaded file.")
        self.media_info_button.setEnabled(False)
        self.media_info_button.clicked.connect(self._show_media_info)
        file_layout.addWidget(self.media_info_button)
        controls_layout.addLayout(file_layout)

        # Playback speed
        speed_layout = QHBoxLayout()
        speed_layout.setSpacing(6)
        speed_layout.addWidget(QLabel("Play Speed:"))
        self.playback_speed_dropdown = QComboBox()
        self.playback_speed_dropdown.addItems(["0.05x", "0.1x", "0.25x", "0.5x", "0.75x", "1x", "2x", "3x", "4x", "5x"])
        self.playback_speed_dropdown.setCurrentText("1x")
        self.playback_speed_dropdown.setFixedWidth(100)
        speed_layout.addWidget(self.playback_speed_dropdown)
        self.video_time_label = QLabel("Time: — / unknown")
        self.video_time_label.setToolTip(
            "Elapsed source presentation time, not frame number/FPS. Total includes "
            "only an explicitly indexed final-frame duration; otherwise unknown. "
            "Loaded ranges use their first indexed frame as zero, not the whole video.")
        speed_layout.addWidget(self.video_time_label)
        speed_layout.addSpacing(16)
        self.actual_speed_label = QLabel("Actual: —")
        self.actual_speed_label.setToolTip(
            "Observed source-frame index progression per monotonic wall-clock second, "
            "from distinct completed Main View Qt paints over about 2 seconds "
            "(up to 120 samples; at least 0.5 seconds required). Supports VFR and loops. "
            "Includes skipped source frames, including intentional fast-forward selection; "
            "not decoded or displayed FPS. VFR can change this rate even at correct "
            "real-time speed. Timestamp-based speed remains in the Play/Pause tooltip. "
            "Not monitor/vsync timing or a latency measurement. — means insufficient "
            "samples or playback is paused."
        )
        self.displayed_fps_label = QLabel("Displayed: —")
        self.displayed_fps_label.setToolTip(
            "Distinct completed Main View video paints per second over the preceding "
            "5 seconds (at least 0.5 seconds and two paints required). Includes VFR; "
            "not source average FPS, decoder throughput, or monitor/vsync scanout. "
            "— means unavailable or paused."
        )
        self.skipped_frames_label = QLabel("Skipped: 0 (0 recent)")
        self.skipped_frames_label.setToolTip(
            "Source frames selected for presentation but not observed in completed "
            "Main View Qt paints, including overload catch-up and paint coalescing. "
            "Excludes intentional frame selection at faster speeds. Overlapping late "
            "and missed-paint diagnostics are not added together. Not monitor scanout "
            "or a count of undecoded frames. Recent counts cover the preceding 5 seconds "
            "and expire even while paused. Cumulative count retained on pause; reset on resume, "
            "speed changes, successful seeks/steps, or opening a new video."
        )
        speed_layout.addWidget(self.actual_speed_label)
        speed_layout.addWidget(self.displayed_fps_label)
        speed_layout.addWidget(self.skipped_frames_label)
        speed_layout.addStretch()
        controls_layout.addLayout(speed_layout)

        self.audio_controls = QWidget()
        audio_layout = QHBoxLayout(self.audio_controls)
        audio_layout.setContentsMargins(0, 0, 0, 0)
        audio_layout.addWidget(QLabel("Volume:"))
        self.volume_slider = QSlider(QtCompat.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)
        self.volume_slider.setMaximumWidth(110)
        self.volume_slider.setToolTip("Original audio for convenient listening at 1x only; not forensic synchronization.")
        self.mute_checkbox = QCheckBox("Mute")
        self.volume_value_label = QLabel("50%")
        self.volume_value_label.setMinimumWidth(34)
        self.audio_status_label = QLabel()
        self.audio_track_dropdown = QComboBox()
        self.audio_track_dropdown.setMaximumWidth(110)
        self.audio_track_dropdown.setToolTip(
            "Select a Qt-discovered audio track. Qt track numbers are not container stream indices.")
        self.audio_track_dropdown.hide()
        self.audio_track_dropdown.activated.connect(self._select_audio_track)
        self.audio_status_label.setToolTip(
            "Container streams and Qt-discovered tracks are counted separately. "
            "For multiple streams, the selected codec comes from Qt; ch ? and Hz ? "
            "mean Qt does not expose channels/sample rate. No container/Qt track "
            "mapping is assumed. A stream may contain silence. Playback is 1x only "
            "and silent while scrubbing."
        )
        self.audio_notice = QLabel()
        self.audio_notice.setWordWrap(True)
        audio_layout.addWidget(self.volume_slider)
        audio_layout.addWidget(self.volume_value_label)
        audio_layout.addWidget(self.mute_checkbox)
        audio_layout.addWidget(self.audio_track_dropdown)
        audio_layout.addWidget(self.audio_status_label)
        audio_layout.addWidget(self.audio_notice)
        audio_layout.addStretch()
        self.audio_controls.hide()
        controls_layout.addWidget(self.audio_controls)
        
        # Brightness
        controls_layout.addWidget(QLabel("Brightness:"))
        self.brightness_slider = QSlider(QtCompat.Horizontal)
        self.brightness_slider.setRange(-100, 100)
        self.brightness_slider.setValue(0)
        self.brightness_slider.setToolTip(
            "Full-range BT.709 YUV444 luma offset (-100 to +100), before contrast."
        )
        controls_layout.addWidget(self.brightness_slider)
        
        # Contrast
        controls_layout.addWidget(QLabel("Contrast:"))
        self.contrast_slider = QSlider(QtCompat.Horizontal)
        self.contrast_slider.setRange(-100, 100)
        self.contrast_slider.setValue(0)
        controls_layout.addWidget(self.contrast_slider)
        
        # Sharpness
        controls_layout.addWidget(QLabel("Sharpness:"))
        self.sharpness_slider = QSlider(QtCompat.Horizontal)
        self.sharpness_slider.setRange(0, 100)
        self.sharpness_slider.setValue(0)
        self.sharpness_slider.setToolTip(
            "FFmpeg 5x5 luma unsharp, amount 0.00 to 1.00, after contrast."
        )
        controls_layout.addWidget(self.sharpness_slider)

        crop_layout = QGridLayout()
        crop_layout.addWidget(QLabel("Crop (pixels, after enhancements):"), 0, 0, 1, 4)
        self.crop_spins = {}
        for index, name in enumerate(CROP_KEYS):
            spin = QSpinBox()
            spin.setRange(0, 100000)
            spin.setToolTip(
                "Exact pixels removed from this edge. Crop must leave a nonempty image. "
                "Odd dimensions are allowed; H.264 YUV420 exports require even dimensions."
            )
            self.crop_spins[name] = spin
            crop_layout.addWidget(QLabel(name.replace("crop_", "").title()), 1 + index // 2,
                                  (index % 2) * 2)
            crop_layout.addWidget(spin, 1 + index // 2, (index % 2) * 2 + 1)
            spin.valueChanged.connect(self.update_frame)
        controls_layout.addLayout(crop_layout)

        # Rotation (exact, after crop and before zoom)
        rotate_layout = QHBoxLayout()
        rotate_layout.addWidget(QLabel("Rotate:"))
        self.rotation_dropdown = QComboBox()
        for degrees in ROTATIONS:
            self.rotation_dropdown.addItem(ROTATION_LABELS[degrees], degrees)
        self.rotation_dropdown.setToolTip(
            "Exact 90-degree pixel rotation after crop and before Zoom Level; no "
            "interpolation. Applies to the Main View, Magnified View and verified exports."
        )
        rotate_layout.addWidget(self.rotation_dropdown)
        rotate_layout.addStretch()
        controls_layout.addLayout(rotate_layout)
        
        # Zoom level
        zoom_layout = QHBoxLayout()
        zoom_layout.addWidget(QLabel("Zoom Level:"))
        self.zoom_dropdown = QComboBox()
        self.zoom_dropdown.addItems(["1x", "2x", "3x", "4x", "5x", "8x", "10x", "15x", "20x", "25x", "30x"])
        self.zoom_dropdown.setCurrentText("1x")
        self.zoom_dropdown.setFixedWidth(100)
        zoom_layout.addWidget(self.zoom_dropdown)
        zoom_layout.addStretch()
        controls_layout.addLayout(zoom_layout)
        
        # Interpolation
        interp_layout = QHBoxLayout()
        interp_layout.addWidget(QLabel("Interpolation Method:"))
        self.interpolation_dropdown = QComboBox()
        self.interpolation_dropdown.addItems(["Nearest", "Bilinear", "Bicubic", "Lanczos"])
        self.interpolation_dropdown.setFixedWidth(100)
        interp_layout.addWidget(self.interpolation_dropdown)
        self.match_export_checkbox = QCheckBox("Match FFmpeg Export")
        self.match_export_checkbox.setToolTip(
            "Use FFmpeg interpolation to match an export of the same selected crop "
            "and zoom. Exact rendering runs in the background after movement stops "
            "or playback pauses. Dragging and playback use the fast preview. "
            "Additional Zoom and highlights remain display-only."
        )
        self.match_export_checkbox.toggled.connect(self.update_zoomed_view)
        interp_layout.addWidget(self.match_export_checkbox)
        interp_layout.addStretch()
        controls_layout.addLayout(interp_layout)

        # Preview-only aids: excluded from verified exports and working copies.
        aids_separator = QFrame()
        aids_separator.setFrameShape(QFrame.Shape.HLine)
        aids_separator.setFrameShadow(QFrame.Shadow.Sunken)
        controls_layout.addWidget(aids_separator)
        self.inspection_aids_label = QLabel("<b>Inspection Aids (Preview Only)</b>")
        self.inspection_aids_label.setToolTip(
            "Display aids only; never included in verified exports or working copies.")
        controls_layout.addWidget(self.inspection_aids_label)
        
        # Highlighting
        highlight_layout = QHBoxLayout()
        self.green_checkbox = QCheckBox("Highlight Interpolated Pixels")
        self.original_checkbox = QCheckBox("Highlight Original Pixels")
        grid_help = (
            "Schematic grid positions, not proof of unchanged pixel values. "
            "Interpolation can change colors at source-grid positions too. "
            "Highlights are display-only; the inspector reads unhighlighted pixels."
        )
        self.green_checkbox.setToolTip(grid_help)
        self.original_checkbox.setToolTip(grid_help)
        highlight_layout.addWidget(self.original_checkbox)
        highlight_layout.addWidget(self.green_checkbox)
        controls_layout.addLayout(highlight_layout)
        
        # Compression
        controls_layout.addWidget(QLabel("JPEG Compression Simulation:"))
        self.compression_slider = QSlider(QtCompat.Horizontal)
        self.compression_slider.setRange(0, 99)
        self.compression_slider.setValue(0)
        self.compression_slider.setToolTip(
            "0 bypasses JPEG completely. Higher values simulate lossy "
            "recompression after the core enhancements; they do not describe the "
            "source codec. This teaching simulation is not FFmpeg-reproducible "
            "and must be zero for verified export."
        )
        controls_layout.addWidget(self.compression_slider)
        
        # Connect signals
        self.playback_speed_dropdown.currentIndexChanged.connect(self.adjust_playback_speed)
        self.interpolation_dropdown.currentIndexChanged.connect(self.update_frame)
        self.zoom_dropdown.currentIndexChanged.connect(self.update_frame)
        self.rotation_dropdown.currentIndexChanged.connect(self.update_frame)
        self.green_checkbox.stateChanged.connect(self.update_frame)
        self.original_checkbox.stateChanged.connect(self.update_frame)
        self.compression_slider.valueChanged.connect(self.update_frame)
        self.brightness_slider.valueChanged.connect(self.update_frame)
        self.contrast_slider.valueChanged.connect(self.update_frame)
        self.sharpness_slider.valueChanged.connect(self.update_frame)
        
        controls_container.setWidget(controls_widget)
        return controls_container

    def _create_zoom_widget(self):
        """Create zoom display widget."""
        zoom_widget = QWidget()
        zoom_layout = QVBoxLayout(zoom_widget)
        zoom_layout.setContentsMargins(10, 10, 10, 10)
        
        zoom_layout.addWidget(QLabel("<b>Magnified View</b>"))
        
        self.zoom_label = QLabel()
        self.zoom_label.setAlignment(QtCompat.AlignCenter)
        self.zoom_label.setMinimumSize(400, 100)
        self.zoom_label.setFrameStyle(QtCompat.Panel | QtCompat.Sunken)
        self.zoom_label.setMouseTracking(True)
        self.zoom_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        zoom_layout.addWidget(self.zoom_label)
        
        # Additional zoom slider
        nn_zoom_layout = QHBoxLayout()
        nn_zoom_layout.addWidget(QLabel("Additional Zoom:"))
        self.nn_zoom_slider = QSlider(QtCompat.Horizontal)
        self.nn_zoom_slider.setRange(1, 50)
        self.nn_zoom_slider.setValue(1)
        self.nn_zoom_slider.setToolTip(
            "Nearest-neighbor enlargement after the selected interpolation. "
            "Changes only the magnified view, not the underlying frame."
        )
        self.nn_zoom_slider.setTickPosition(QtCompat.TicksBelow)
        self.nn_zoom_slider.setTickInterval(1)
        nn_zoom_layout.addWidget(self.nn_zoom_slider)
        zoom_layout.addLayout(nn_zoom_layout)
        
        self.nn_zoom_slider.valueChanged.connect(self.update_zoomed_view)
        
        return zoom_widget

    def _create_frame_info_widget(self):
        """Create frame info and pixel inspector widget."""
        lower_right_widget = QWidget()
        lower_right_layout = QVBoxLayout(lower_right_widget)
        lower_right_layout.setContentsMargins(10, 10, 10, 10)
        
        # Pixel inspector
        pixel_inspector_layout = QHBoxLayout()
        
        pixel_view_layout = QVBoxLayout()
        pixel_view_layout.addWidget(QLabel("<b>Pixel Inspector</b>"))
        
        self.pixel_display = QLabel()
        self.pixel_display.setFixedSize(65, 65)
        self.pixel_display.setFrameStyle(QtCompat.Panel | QtCompat.Raised)
        self.pixel_display.setAlignment(QtCompat.AlignCenter)
        pixel_view_layout.addWidget(self.pixel_display)
        pixel_view_layout.setAlignment(self.pixel_display, QtCompat.AlignCenter)
        
        self.pixel_values_widget = QTableWidget(3, 2)
        self.pixel_values_widget.horizontalHeader().setVisible(False)
        self.pixel_values_widget.verticalHeader().setVisible(False)
        self.pixel_values_widget.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.pixel_values_widget.setVerticalScrollBarPolicy(QtCompat.ScrollBarAlwaysOff)
        self.pixel_values_widget.setHorizontalScrollBarPolicy(QtCompat.ScrollBarAlwaysOff)
        self.pixel_values_widget.setStyleSheet("QTableWidget { border: 1px solid #d0d0d0; }")
        
        total_width = self.pixel_values_widget.width() or 400
        first_column_width = int(total_width * 0.35)
        self.pixel_values_widget.setColumnWidth(0, first_column_width)
        self.pixel_values_widget.horizontalHeader().setSectionResizeMode(0, QtCompat.Fixed)
        self.pixel_values_widget.horizontalHeader().setSectionResizeMode(1, QtCompat.Stretch)
        
        value_rows = ["Native Source YCbCr", "Processed RGB", "Image Adjustments"]
        self.pixel_values_widget.setToolTip(
            "Native Source: decoded source samples at the source pixel under the Main "
            "View crosshair (Y′/Cb/Cr, or R/G/B for native RGB sources), unchanged by "
            "enhancements. Processed RGB: the enhanced pixel at the crosshair after "
            "crop, Zoom Level and the selected interpolation (FFmpeg-matched when "
            "Match FFmpeg Export is on), before highlights, Additional Zoom or window fit."
            " Parenthesized source RGB is the interpreted decoded source pixel before "
            "enhancements, zoom or interpolation; it is not native YCbCr or an "
            "interpolated source value."
        )
        for i, label in enumerate(value_rows):
            self.pixel_values_widget.setItem(i, 0, QTableWidgetItem(label))
            self.pixel_values_widget.setItem(i, 1, QTableWidgetItem("-"))
        
        self.pixel_values_widget.verticalHeader().setSectionResizeMode(QtCompat.ResizeToContents)
        self.pixel_values_widget.resizeRowsToContents()
        
        total_height = sum(self.pixel_values_widget.rowHeight(i) for i in range(self.pixel_values_widget.rowCount()))
        self.pixel_values_widget.setFixedHeight(total_height + 4)
        
        pixel_inspector_layout.addLayout(pixel_view_layout)
        pixel_inspector_layout.addWidget(self.pixel_values_widget)
        lower_right_layout.addLayout(pixel_inspector_layout)
        
        # Frame info
        lower_right_layout.addWidget(QLabel("<b>Frame Information</b>"))
        
        self.frame_info_table = QTableWidget(0, 2)
        self.frame_info_table.horizontalHeader().setVisible(False)
        self.frame_info_table.verticalHeader().setVisible(False)
        self.frame_info_table.horizontalHeader().setSectionResizeMode(QtCompat.Stretch)
        self.frame_info_table.setToolTip(
            "Source attributes come from FFprobe; inspection uses the selected "
            "decoder's 8-bit BGR baseline. Matching neutral BGR hashes is not proof "
            "of native-YUV or color-managed equivalence."
        )
        lower_right_layout.addWidget(self.frame_info_table)
        
        return lower_right_widget

    def set_global_font_size(self):
        """Set global font size."""
        font_size = 12
        stylesheet = f"""
            QLabel, QPushButton, QComboBox, QCheckBox, QTableWidget {{
                font-size: {font_size}pt;
            }}
            QSlider::sub-page:horizontal {{
                font-size: {font_size}pt;
            }}
            QLabel[styleSheet*="bold"] {{
                font-size: {font_size + 2}pt;
            }}
            QHeaderView::section {{
                font-size: {font_size}pt;
            }}
        """
        self.setStyleSheet(stylesheet)
        default_font = QFont()
        default_font.setPointSize(font_size)
        QApplication.setFont(default_font)

    def _check_dependencies(self):
        """Check for required dependencies and exit if missing."""
        missing_deps = []
        
        # Check for ffprobe
        if not FrameInfoHandler.check_ffprobe():
            missing_deps.append("ffprobe")
        
        # Check for ffmpeg
        if not self._check_ffmpeg():
            missing_deps.append("ffmpeg")
        
        # Exit if any dependencies are missing
        if missing_deps:
            deps_str = " and ".join(missing_deps)
            message = (
                f"⚠️ {deps_str.upper()} not found\n\n"
                f"OSVEV could not run its FFmpeg/FFprobe tools.\n\n"
                f"Packaged builds include them; when running from source, "
                f"activate the OSVEV conda environment (environment-eq.yml) "
                f"or install a GPL FFmpeg 9 build on PATH, then restart OSVEV."
            )
            
            QMessageBox.critical(
                self,
                "Missing Required Dependencies",
                message
            )
            sys.exit(1)
    
    def _check_ffmpeg(self):
        """Check if ffmpeg is available."""
        try:
            startupinfo = None
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            
            subprocess.run(
                [tool_path("ffmpeg"), "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                startupinfo=startupinfo
            )
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def get_processing_params(self):
        """Get current processing parameters."""
        return {
            'brightness': self.brightness_slider.value(),
            'contrast': self.contrast_slider.value(),
            'sharpness': self.sharpness_slider.value(),
            'compression': self.compression_slider.value(),
            **self.get_crop_params(),
            'rotation': self.rotation_dropdown.currentData(),
            'interpolation': self.interpolation_dropdown.currentText()
        }

    def get_crop_params(self):
        return {name: spin.value() for name, spin in self.crop_spins.items()}

    def _get_processed_frame(self):
        """Share one unhighlighted processed frame across all inspection views."""
        params = self.get_processing_params()
        cache_key = self._processing_cache_key(self.current_frame_index, params)
        if cache_key not in self.process_cache:
            frame = self.frame_processor.process(
                self.current_frame,
                brightness=params['brightness'], contrast=params['contrast'],
                sharpness=params['sharpness'], compression=params['compression'],
                **self.get_crop_params(), rotation=params['rotation'],
            )
            self.process_cache = {cache_key: frame}
            self.last_processing_params = params.copy()
        return self.process_cache[cache_key]

    @staticmethod
    def _processing_cache_key(index, params):
        return (
            index, params['brightness'], params['contrast'],
            params['sharpness'], params['compression'],
            *(params[name] for name in CROP_KEYS), params.get('rotation', 0),
        )

    def _clear_pixel_inspector(self):
        self.pixel_display.clear()
        for row in range(self.pixel_values_widget.rowCount()):
            self.pixel_values_widget.setItem(row, 1, QTableWidgetItem("-"))

    def update_frame(self):
        """Update the current frame display."""
        self._update_video_time()
        if self._scrub_active:
            self._request_scrub_preview(self.timeline_slider.sliderPosition())
        self._exact_magnifier.invalidate()
        if not hasattr(self, 'current_frame') or self.current_frame is None:
            return

        try:
            self.display_processed_frame(self._get_processed_frame())

        except (cv2.error, ValueError, TypeError, RuntimeError) as e:
            print(f"Error updating frame: {e}")
            traceback.print_exc()
            self.pause_video()
            self.main_label.clear()
            self.main_label.pixmap = None
            self.zoom_label.clear()
            self._clear_pixel_inspector()
            QMessageBox.warning(self, "Processing Error", str(e))

    def display_processed_frame(self, frame):
        """Fit the processed frame at the screen's physical pixel density (display only)."""
        try:
            height, width, channel = frame.shape
            avail_width = max(1, self.scroll_area.viewport().width())
            avail_height = max(1, self.scroll_area.viewport().height())
            # Qt's own aspect-fit keeps framing and crosshair mapping unchanged.
            target = QSize(width, height).scaled(
                avail_width, avail_height, QtCompat.KeepAspectRatio)
            target_width, target_height = max(1, target.width()), max(1, target.height())
            # Render physical pixels so Qt does not rescale the image again when painting.
            ratio = max(1.0, self.main_label.devicePixelRatioF())
            # Round half up like Qt's qRound; Python's round() would leave a 1-pixel gap.
            pixel_width = max(1, int(target_width * ratio + 0.5))
            pixel_height = max(1, int(target_height * ratio + 0.5))
            frame = self._display_resize(frame, pixel_width, pixel_height)
            q_img = QImage(frame.data, pixel_width, pixel_height, 3 * pixel_width,
                           QImage.Format.Format_BGR888)
            scaled_pixmap = QPixmap.fromImage(q_img)
            scaled_pixmap.setDevicePixelRatio(ratio)
            current_lens = self.main_label.lens_rect
            previous = self.main_label.image_size()
            if not previous.isEmpty():
                center_x = (current_lens.x() + current_lens.width() // 2) / previous.width()
                center_y = (current_lens.y() + current_lens.height() // 2) / previous.height()
            else:
                center_x = center_y = 0.5
            width, height = target_width, target_height
            lens_width, lens_height = min(100, width), min(100, height)
            lens_x = max(0, min(round(center_x * width) - lens_width // 2, width - lens_width))
            lens_y = max(0, min(round(center_y * height) - lens_height // 2, height - lens_height))
            if self.main_label.click_centered:
                lens_x = max(0, min(round(center_x * width), width - 1)) - lens_width // 2
                lens_y = max(0, min(round(center_y * height), height - 1)) - lens_height // 2
            self.main_label.setFixedSize(target_width, target_height)
            self.main_label.setPixmap(scaled_pixmap)
            self.main_label.pixmap = scaled_pixmap
            self.main_label.lens_rect = QRect(lens_x, lens_y, lens_width, lens_height)
            self.update_zoomed_view()
            
        except (cv2.error, ValueError, TypeError, RuntimeError) as e:
            print(f"Error displaying frame: {e}")
            self.pause_video()
            self.main_label.clear()
            self.main_label.pixmap = None
            self.zoom_label.clear()
            self._clear_pixel_inspector()
            QMessageBox.warning(self, "Display Error", str(e))

    def _display_resize(self, frame, width, height):
        """Main View only: area-average when shrinking, so every source pixel contributes.

        Enlarging at 1x or with Nearest repeats whole pixels using the inspector's exact
        integer mapping (x * source // displayed); otherwise the selected interpolation.
        """
        source_height, source_width = frame.shape[:2]
        if (width, height) == (source_width, source_height):
            return frame
        if width <= source_width and height <= source_height:
            return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        interpolation = (cv2.INTER_NEAREST if self.zoom_dropdown.currentText() == "1x"
                         else self._INTERPOLATION_FLAGS.get(
                             self.interpolation_dropdown.currentText(), cv2.INTER_NEAREST))
        if interpolation != cv2.INTER_NEAREST:
            return cv2.resize(frame, (width, height), interpolation=interpolation)
        key = (source_width, source_height, width, height)
        if self._display_map_key != key:
            map_x, map_y = np.meshgrid(
                ((np.arange(width) * source_width) // width).astype(np.float32),
                ((np.arange(height) * source_height) // height).astype(np.float32))
            # Compact integer maps hold coordinates up to 32767 only.
            self._display_map = ((map_x, map_y) if max(source_width, source_height) > 32767
                                 else cv2.convertMaps(map_x, map_y, cv2.CV_16SC2,
                                                      nninterpolation=True))
            self._display_map_key = key
        return cv2.remap(frame, *self._display_map, cv2.INTER_NEAREST)

    def _cancel_metadata_load(self):
        self._load_cancelled = True
        if self._metadata_worker is not None:
            self._metadata_worker.requestInterruption()
            if self._loading_progress is not None and not self._magnifier_closing:
                self._loading_progress.setRange(0, 0)
                self._loading_progress.setLabelText("Cancelling loading...")
                self._loading_progress.setCancelButton(None)
                self._loading_progress.show()

    def _update_loading_progress(self, stage, processed, total):
        progress = self._loading_progress
        if progress is None or self._load_cancelled:
            return
        if total > 0 and processed <= total:
            percent = processed * 100 // total
            progress.setRange(0, 100)
            progress.setValue(percent)
            progress.setLabelText(f"{stage}: {processed:,} / {total:,} frames ({percent}%)")
        else:
            progress.setRange(0, 0)
            suffix = f": {processed:,} processed" if processed else ""
            progress.setLabelText(stage + suffix)

    def _open_input_capture(self, file_path, *, color_choices=None):
        if self.decoder_backend == "opencv":
            return cv2.VideoCapture(file_path)
        try:
            from pyav_input import PyAVInputCapture
        except ImportError as error:
            raise RuntimeError(
                "PyAV input is unavailable. Install the documented PyAV "
                "dependency or explicitly launch with --decoder opencv. "
                "No automatic decoder fallback is performed."
            ) from error
        return PyAVInputCapture(file_path, color_choices=color_choices)

    def choose_video(self):
        """Choose scope before invoking the unchanged full/partial loading paths."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", VIDEO_INPUT_FILTER)
        if not file_path:
            return
        accepted, time_range = TimeRangeDialog.get_open_choice(
            self, allow_range=self.decoder_backend == "pyav", video_path=file_path
        )
        if accepted:
            if time_range is None:
                self._load_video(file_path)
            else:
                self._load_video(file_path, time_range)

    def open_video(self):
        """Open a video file."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", VIDEO_INPUT_FILTER)
        if not file_path:
            return
        self._load_video(file_path)

    def open_time_range(self):
        """Index only a source presentation-time interval of a long video."""
        if self.decoder_backend != "pyav":
            QMessageBox.warning(self, "Open Time Range",
                                "Exact partial indexing requires the PyAV decoder.")
            return
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video Time Range", "",
                                                   VIDEO_INPUT_FILTER)
        if not file_path:
            return
        time_range = TimeRangeDialog.get_range(self)
        if time_range is not None:
            self._load_video(file_path, time_range)

    def _load_video(self, file_path, time_range=None, *, color_choices=None):
        if self._metadata_worker is not None or self._working_copy_dialog is not None:
            return
        self._cancel_scrub_preview()
        self.pause_video()
        self._load_cancelled = False
        candidate = None
        retry_choices = None
        compatibility_error = None
        create_copy = False
        try:
            progress = QProgressDialog(
                "Opening video...",
                "Cancel", 0, 0, self)
            self._loading_progress = progress
            progress.setWindowModality(QtCompat.WindowModal)
            progress.setWindowTitle("Loading")
            progress.setWindowIcon(self.app_icon)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            opener = self._open_input_capture
            if color_choices is not None:
                opener = lambda path: self._open_input_capture(path, color_choices=color_choices)
            worker = FrameMetadataWorker(file_path, self, time_range, opener, self.decoder_backend)
            self._metadata_worker = worker
            progress.canceled.connect(self._cancel_metadata_load)
            worker.progress.connect(self._update_loading_progress)
            loop = QEventLoop(self)
            worker.finished.connect(loop.quit)
            progress.show()
            worker.start()
            loop.exec()
            worker.wait()
            loop.deleteLater()
            candidate, worker.candidate = worker.candidate, None
            metadata, metadata_error = worker.metadata, worker.metadata_error
            source_range = worker.source_range
            frame_count, frame_rate, frame = worker.frame_count, worker.frame_rate, worker.frame
            error = worker.error
            missing_color = worker.missing_color
            compatibility_error = worker.compatibility_error
            self._metadata_worker = None
            worker.deleteLater()
            if self._load_cancelled:
                return
            if error is not None:
                if missing_color is not None:
                    raise missing_color
                if compatibility_error is not None:
                    raise InputCompatibilityError(compatibility_error)
                raise RuntimeError(error)
            if metadata_error is not None:
                print(f"Metadata unavailable: {metadata_error}")
                QMessageBox.warning(self, "Metadata Unavailable", metadata_error)
            progress.setRange(0, 100)
            progress.setValue(100)

            native = candidate.native_samples if self.decoder_backend == "pyav" else None
            playback_timeline = PlaybackTimeline.from_metadata(
                metadata, frame_count, frame_rate,
                time_base=None if native is None else native.frame.time_base,
            )
            if self.cap is not None:
                self.cap.release()
            self.cap, candidate = candidate, None
            self.video_path = file_path
            self._show_loaded_file()
            self.source_range = source_range
            self._reset_playback_diagnostics()
            self.frame_count, self.frame_rate = frame_count, frame_rate
            self.frame_metadata = metadata
            self._playback_timeline = playback_timeline
            self._load_audio_preview()
            self.current_frame_index = 0
            self._last_decoded_index = 0
            self.current_frame = frame
            self.current_native_samples = (
                self.cap.native_samples if self.decoder_backend == "pyav" else None
            )
            self.current_zoomed_roi = None
            for spin in self.crop_spins.values():
                spin.blockSignals(True)
                spin.setValue(0)
                spin.blockSignals(False)
            self.process_cache.clear()
            self.timeline_slider.setMaximum(frame_count - 1)
            self.timeline_slider.setValue(0)
            self.timeline_slider.setEnabled(True)
            self.adjust_playback_speed()
            self.update_frame()
            discrepancy = getattr(self.cap, "count_discrepancy", None)
            if self.decoder_backend == "pyav" and isinstance(discrepancy, dict):
                QMessageBox.warning(
                    self, "Frame-count discrepancy",
                    f"The container declares {discrepancy['declared_count']} frames in "
                    f"{discrepancy['packet_count']} packets, but {discrepancy['decoded_count']} frames "
                    "decode. " + " ".join(discrepancy["assumptions"]) + " Only the decoded frames "
                    "and their original timestamps are shown; none were added or renumbered.")
        except MissingColorMetadata as error:
            if self._loading_progress is not None:
                self._loading_progress.hide()
            retry_choices = ColorAssumptionsDialog.get_choices(error, self, allow_working_copy=True)
            if retry_choices == "working-copy":
                retry_choices, create_copy = None, True
        except InputCompatibilityError as error:
            compatibility_error = str(error)
        except (cv2.error, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
            QMessageBox.warning(self, "Error", f"An error occurred: {str(e)}")
        finally:
            if self._metadata_worker is not None:
                self._metadata_worker.requestInterruption()
                self._metadata_worker.wait()
                self._metadata_worker.deleteLater()
                self._metadata_worker = None
            if self._loading_progress is not None:
                self._loading_progress.close()
                self._loading_progress.deleteLater()
                self._loading_progress = None
            if candidate is not None:
                candidate.release()
        if retry_choices is not None:
            self._load_video(file_path, time_range, color_choices=retry_choices)
        elif not self._magnifier_closing and (
                create_copy or (compatibility_error and self._offer_working_copy(compatibility_error))):
            self._create_working_copy(file_path)

    def _offer_working_copy(self, reason):
        message = QMessageBox(self)
        message.setWindowTitle("Video compatibility")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(reason)
        message.setInformativeText(
            "You can try creating a separately verified video-only working copy. "
            "The original remains untouched. Unsupported or unverifiable conversions are refused."
        )
        create = message.addButton("Create Compatible Working Copy", QMessageBox.ButtonRole.ActionRole)
        message.addButton(QMessageBox.StandardButton.Close)
        message.exec()
        return message.clickedButton() is create

    def _create_working_copy(self, file_path):
        from working_copy_dialog import WorkingCopyDialog
        dialog = WorkingCopyDialog(file_path, self)
        self._working_copy_dialog = dialog
        try:
            completed = dialog.exec()
            result = dialog.result_path
        finally:
            self._working_copy_dialog = None
            dialog.deleteLater()
        if completed and result and not self._magnifier_closing:
            if QMessageBox.question(
                self, "Working copy verified",
                "A verified video-only working copy and conversion log were created. "
                "Keep the conversion log with the MKV: it records color assumptions and any "
                "authorized timing changes. Audio and other nonvideo streams are excluded. "
                "Open the working copy?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes:
                self._load_video(result)

    def get_frame(self, frame_idx, *, sequential_playback=False):
        """Get frame by index."""
        self._cancel_scrub_preview()
        if not self.cap or not self.cap.isOpened() or frame_idx < 0 or frame_idx >= self.frame_count:
            return None
        try:
            if frame_idx == self._last_decoded_index and self.current_frame is not None:
                return self.current_frame
            gap = frame_idx - self._last_decoded_index
            forward = sequential_playback and self._last_decoded_index >= 0 and 0 < gap <= 12
            if not forward and (self._last_decoded_index < 0 or gap != 1):
                if not self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx):
                    self._last_decoded_index = -1
                    return None
            # Decode small playback skips forward; seeking each skipped frame
            # repeatedly re-decodes the GOP, especially costly with PyAV.
            for _ in range(gap if forward else 1):
                ret, frame = self.cap.read()
                if not ret or frame is None:
                    self._last_decoded_index = -1
                    return None
            if ret and frame is not None:
                self.current_frame = frame
                self.current_native_samples = (
                    self.cap.native_samples if self.decoder_backend == "pyav" else None
                )
                self._last_decoded_index = frame_idx
                return frame
        except (cv2.error, OSError, ValueError, RuntimeError) as error:
            self.pause_video()
            print(f"Input decoding failed: {error}")
            self._last_decoded_index = -1
            raise RuntimeError(f"Input decoding failed: {error}") from error
        self._last_decoded_index = -1
        return None

    def toggle_play_pause(self):
        """Toggle play/pause."""
        if self.is_playing:
            self.pause_video()
        else:
            self.resume_video()

    def pause_video(self):
        """Pause playback."""
        was_playing = self.is_playing
        self.timer.stop()
        self.play_pause_button.setText("Play")
        self.is_playing = False
        self._audio.pause()
        self._refresh_performance_labels()
        if was_playing and self.match_export_checkbox.isChecked():
            QTimer.singleShot(0, self._refresh_exact_on_pause)

    def _refresh_exact_on_pause(self):
        if (not self._magnifier_closing and not self.is_playing
                and self.match_export_checkbox.isChecked()):
            self.update_zoomed_view()

    def resume_video(self):
        """Resume playback."""
        self._cancel_scrub_preview()
        if self.cap and self.cap.isOpened():
            self.adjust_playback_speed()
            self.play_pause_button.setText("Pause")
            self.is_playing = True
            if self.match_export_checkbox.isChecked():
                self.update_zoomed_view()
            self._start_playback_clock()

    def _start_playback_clock(self):
        if self._playback_timeline is None:
            self._playback_timeline = PlaybackTimeline.from_metadata(
                self.frame_metadata, self.frame_count, self.frame_rate,
                time_base=(self.current_native_samples.frame.time_base
                           if self.current_native_samples is not None else None),
            )
        now = time.perf_counter()
        self._playback_clock = PlaybackClock(
            self._playback_timeline, self.current_frame_index, self.speed_multiplier, now
        )
        self._playback_generation += 1
        self._reset_playback_diagnostics()
        self._last_diagnostic_update = now
        self.play_pause_button.setToolTip(
            "Play or pause (Space).\nPlayback timing: collecting completed Qt paints..."
            + ("\nDuplicate PTS: frames are submitted in source order; tied frames have "
               "no independently established display interval. Playback may lag under load."
               if self._playback_timeline.preserve_order else "")
        )
        self._diagnostic_timer.start()
        self.timer.start(self._playback_clock.delay_ms(now))
        if self._audio_source_offset is not None:
            self._audio.follow(self._playback_clock, self._audio_source_offset)

    def _update_video_time(self):
        prefix = "Time (loaded range)" if self.source_range is not None else "Time"
        elapsed, total = "—", "unknown"
        if (len(self.frame_metadata) == self.frame_count
                and 0 <= self.current_frame_index < self.frame_count):
            try:
                first, last = self.frame_metadata[0], self.frame_metadata[-1]
                current = self.frame_metadata[self.current_frame_index]
                native = self.current_native_samples
                base = native.frame.time_base if native is not None else None
                if base is not None:
                    start = first["pts"] * base
                    position = current["pts"] * base
                    end_frame = last["pts"] * base
                else:
                    start = Fraction(first["best_effort_timestamp_time"])
                    position = Fraction(current["best_effort_timestamp_time"])
                    end_frame = Fraction(last["best_effort_timestamp_time"])
                if position < start or end_frame < position:
                    raise ValueError("Invalid presentation-time bounds.")
                elapsed = clock_text(position - start)
                tail = last.get("duration_time", last.get("pkt_duration_time"))
                if tail is not None and Fraction(tail) > 0:
                    total = clock_text(end_frame + Fraction(tail) - start)
            except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
                elapsed, total = "—", "unknown"
        self.video_time_label.setText(f"{prefix}: {elapsed} / {total}")

    def _select_audio_track(self, index):
        try:
            self._audio.select_track(index)
        except ValueError as error:
            QMessageBox.warning(self, "Audio Track", str(error))
            self._show_audio_status()

    def _audio_info_text(self):
        streams = getattr(self.cap, "audio_streams", None)
        if not isinstance(streams, (tuple, list)):
            return "Audio stream info unavailable"
        if not streams:
            return "No audio streams"
        tracks = self._audio.available_tracks()
        selected = self._audio.active_track()
        if len(streams) == 1 and len(tracks) <= 1:
            first = streams[0]
            details = [first.get("codec") or "unknown codec"]
            if first.get("channels"):
                details.append(f"{first['channels']} ch")
            if first.get("sample_rate"):
                details.append(f"{first['sample_rate']} Hz")
        elif 0 <= selected < len(tracks):
            details = [f"Qt track {selected + 1}: {tracks[selected]}",
                       "ch ?, Hz ?"]
        else:
            details = ["no selected Qt audio track"]
        count = f"{len(streams)} audio streams" if len(streams) > 1 else "1 audio stream"
        return f"{count}: " + ", ".join(details) + f" | {len(tracks)} Qt tracks"

    def _show_audio_status(self, status=None):
        tracks = self._audio.available_tracks()
        self.audio_track_dropdown.blockSignals(True)
        self.audio_track_dropdown.clear()
        self.audio_track_dropdown.addItems(
            [f"Track {index + 1}" for index in range(len(tracks))])
        self.audio_track_dropdown.setCurrentIndex(self._audio.active_track())
        self.audio_track_dropdown.blockSignals(False)
        self.audio_track_dropdown.setVisible(len(tracks) > 1 and not self._audio.failed)
        info = self._audio_info_text()
        has_audio = getattr(self.cap, "has_audio", False) is True
        if not has_audio:
            self.audio_status_label.setText(info)
        else:
            self.audio_status_label.setText(f"{info} | {status or self._audio.status_text()}")

    def _load_audio_preview(self):
        self._audio.clear()
        self._audio_source_offset = None
        has_audio = getattr(self.cap, "has_audio", False) is True
        self.audio_controls.setVisible(self.cap is not None)
        self.volume_slider.setEnabled(has_audio)
        self.mute_checkbox.setEnabled(has_audio)
        self._show_audio_status()
        if not has_audio:
            return
        origin = getattr(self.cap, "media_start_seconds", None)
        if (isinstance(origin, (int, float)) and np.isfinite(origin)
                and self._playback_timeline.mode.startswith("source timestamps")):
            self._audio_source_offset = (
                float(self.frame_metadata[0]["best_effort_timestamp_time"]) - origin
            )
        self._audio.load(self.video_path, self._audio_source_offset)

    def _reset_playback_diagnostics(self):
        self._paint_times.clear()
        self._paint_intervals.clear()
        self._paint_lateness.clear()
        self._source_paint_samples.clear()
        self._recent_paints.clear()
        self._recent_skips.clear()
        self._diagnostic_timer.stop()
        self._last_paint_position = None
        self._intentional_skips = self._late_skips = self._missed_paint_slots = 0
        self.actual_speed_label.setText("Actual: —")
        self.displayed_fps_label.setText("Displayed: —")
        self.skipped_frames_label.setText("Skipped: 0 (0 recent)")
        self.skipped_frames_label.setStyleSheet("")

    def _playback_tick(self):
        if not self.is_playing or self._playback_clock is None:
            return
        clock = self._playback_clock
        target = clock.due(time.perf_counter())
        if target is not None:
            try:
                frame = self.get_frame(target.index, sequential_playback=True)
                if frame is None:
                    raise RuntimeError("Could not decode the scheduled playback frame.")
                self.current_frame_index = target.index
                self.timeline_slider.setValue(target.index)
                self.main_label.presentation_token = (
                    self._playback_generation, target.position, target.deadline
                )
                self.update_frame()
                if not self.is_playing:
                    return
                clock.presented(target)
                self._intentional_skips += target.intentional_skips
                self._late_skips += target.late_skips
            except (cv2.error, OSError, ValueError, RuntimeError) as error:
                self.pause_video()
                print(f"Playback input error: {error}")
                QMessageBox.warning(self, "Playback Error", str(error))
                return
        if self.is_playing:
            self.timer.start(clock.delay_ms(time.perf_counter()))

    def _record_frame_painted(self, token):
        generation, position, deadline = token
        if not self.is_playing or generation != self._playback_generation:
            return
        if self._last_paint_position is not None and position <= self._last_paint_position:
            return
        now = time.perf_counter()
        if self._paint_times:
            self._paint_intervals.append(now - self._paint_times[-1])
        previous = (self._last_paint_position if self._last_paint_position is not None
                    else self._playback_clock.anchor)
        slots = (position - previous) // self._playback_clock.stride
        # Count missing selected presentations once, including before the first paint.
        missed = max(0, slots - 1)
        self._missed_paint_slots += missed
        if missed:
            self._recent_skips.append((now, missed))
        self._last_paint_position = position
        self._paint_times.append(now)
        self._recent_paints.append(now)
        self._paint_lateness.append(now - deadline)
        self._source_paint_samples.append((
            now, self._playback_clock.timeline.source_time(position), position
        ))
        while (len(self._source_paint_samples) > 1
               and self._source_paint_samples[0][0] < now - 2):
            self._source_paint_samples.popleft()
        self._refresh_performance_labels(now)
        if now - self._last_diagnostic_update >= 1:
            report = self.playback_diagnostics()
            speed = report["actual_speed"]
            speed_text = "—" if speed is None else f"{speed:.2f}×"
            self.play_pause_button.setToolTip(
                f"Play or pause (Space).\n"
                f"Completed Qt paints (not monitor/vsync): {report['paint_fps']:.2f} FPS\n"
                f"Actual speed (source timestamps): {speed_text}\n"
                f"Intervals median/p95/max: {report['interval_median_ms']:.1f}/"
                f"{report['interval_p95_ms']:.1f}/{report['interval_max_ms']:.1f} ms\n"
                f"Intentional source skips: {report['intentional_source_skips']}; "
                f"late slots: {report['late_presentation_slots']}; "
                f"missed paint slots: {report['missed_paint_slots']}\n"
                f"Timing: {report['timing_mode']}"
                + ("\nTied frames have no independently established display interval; "
                   "all source frames are submitted in order, without catch-up skipping."
                   if self._playback_timeline.preserve_order else "")
            )
            self._last_diagnostic_update = now

    def _refresh_performance_labels(self, now=None):
        now = time.perf_counter() if now is None else now
        while self._recent_paints and self._recent_paints[0] <= now - 5:
            self._recent_paints.popleft()
        while self._recent_skips and self._recent_skips[0][0] <= now - 5:
            self._recent_skips.popleft()
        while self._source_paint_samples and self._source_paint_samples[0][0] < now - 2:
            self._source_paint_samples.popleft()
        actual_fps = self._observed_source_fps() if self.is_playing else None
        actual_text = "Actual: —" if actual_fps is None else f"Actual: {actual_fps:.1f} FPS"
        if self.actual_speed_label.text() != actual_text:
            self.actual_speed_label.setText(actual_text)
        displayed = None
        if self.is_playing and len(self._recent_paints) >= 2:
            elapsed = now - self._recent_paints[0]
            if elapsed >= 0.5:
                displayed = (len(self._recent_paints) - 1) / elapsed
        displayed_text = "Displayed: —" if displayed is None else f"Displayed: {displayed:.1f} FPS"
        if self.displayed_fps_label.text() != displayed_text:
            self.displayed_fps_label.setText(displayed_text)
        recent = sum(count for _, count in self._recent_skips)
        skipped_text = f"Skipped: {self._missed_paint_slots} ({recent} recent)"
        if self.skipped_frames_label.text() != skipped_text:
            self.skipped_frames_label.setText(skipped_text)
        if not self.is_playing and not self._recent_skips:
            self._diagnostic_timer.stop()

    def _observed_playback_speed(self):
        if (self._playback_timeline is None
                or not self._playback_timeline.mode.startswith("source timestamps")
                or len(self._source_paint_samples) < 2):
            return None
        first, last = self._source_paint_samples[0], self._source_paint_samples[-1]
        elapsed = last[0] - first[0]
        return (last[1] - first[1]) / elapsed if elapsed >= 0.5 else None

    def _observed_source_fps(self):
        if len(self._source_paint_samples) < 2:
            return None
        first, last = self._source_paint_samples[0], self._source_paint_samples[-1]
        elapsed = last[0] - first[0]
        return (last[2] - first[2]) / elapsed if elapsed >= 0.5 else None

    def playback_diagnostics(self):
        """Bounded paint observations; excludes repeated control/lens repaints."""
        intervals = np.asarray(self._paint_intervals, dtype=float) * 1000
        lateness = np.asarray(self._paint_lateness, dtype=float) * 1000
        elapsed = self._paint_times[-1] - self._paint_times[0] if len(self._paint_times) > 1 else 0
        return {
            "actual_speed": self._observed_playback_speed(),
            "actual_fps": self._observed_source_fps(),
            "overload_skipped_frames": self._missed_paint_slots,
            "paint_fps": (len(self._paint_times) - 1) / elapsed if elapsed > 0 else 0.0,
            "interval_median_ms": float(np.median(intervals)) if intervals.size else 0.0,
            "interval_p95_ms": float(np.percentile(intervals, 95)) if intervals.size else 0.0,
            "interval_max_ms": float(intervals.max()) if intervals.size else 0.0,
            "interval_std_ms": float(intervals.std()) if intervals.size else 0.0,
            "lateness_p95_ms": float(np.percentile(lateness, 95)) if lateness.size else 0.0,
            "intentional_source_skips": self._intentional_skips,
            "late_presentation_slots": self._late_skips,
            "missed_paint_slots": self._missed_paint_slots,
            "timing_mode": self._playback_timeline.mode if self._playback_timeline else "not started",
            "paint_samples": len(self._paint_times),
        }

    def on_timeline_pressed(self):
        """Timeline pressed."""
        self._cancel_scrub_preview()
        self._scrub_has_preview = False
        self._scrub_failed = False
        self.was_playing_before_timeline = self.is_playing
        if self.is_playing:
            self.pause_video()

    def on_timeline_released(self):
        """Timeline released; scrubbing intentionally leaves playback paused."""
        position = self.timeline_slider.sliderPosition()
        self._cancel_scrub_preview()
        if self._scrub_has_preview or position != self.current_frame_index:
            self.set_frame_position(position)
        self._scrub_has_preview = False

    def _cancel_scrub_preview(self):
        if self._scrub_active:
            self._scrub_preview.cancel()
            self._scrub_active = False
            self._scrub_requested = None

    def _request_scrub_preview(self, position):
        if (not self.timeline_slider.isSliderDown() or self.is_playing
                or self._scrub_failed or not self.video_path
                or self.cap is None or not self.cap.isOpened()):
            return
        settings = self.get_processing_params()
        settings.pop("interpolation")
        if self._scrub_requested == (position, settings):
            return
        if not self._scrub_active:
            self._scrub_preview.begin(PreviewSource(
                self.video_path, self.decoder_backend, self.frame_metadata,
                partial=self.source_range is not None,
                color_choices=getattr(self.cap, "color_choices", None),
            ))
            self._scrub_active = True
        self._scrub_requested = (position, settings)
        self._scrub_preview.request(position, settings)

    def _show_scrub_preview(self, result):
        if (not self._scrub_active or not self.timeline_slider.isSliderDown()
                or self.is_playing):
            return
        settings = self.get_processing_params()
        effective = {name: value for name, value in settings.items() if name != "interpolation"}
        if result.settings != effective:
            self._request_scrub_preview(self.timeline_slider.sliderPosition())
            return
        self.current_frame_index = result.index
        self.current_frame = result.frame
        self.current_native_samples = result.native
        # Preview frames belong to another decoder, not the main decoder's cursor/cache.
        self._last_decoded_index = -1
        self.process_cache = {
            self._processing_cache_key(result.index, settings): result.processed,
        }
        self.last_processing_params = settings.copy()
        self._scrub_has_preview = True
        self._reset_playback_diagnostics()
        self.update_frame()

    def _scrub_preview_error(self, error):
        self._cancel_scrub_preview()
        self._scrub_failed = True
        QMessageBox.warning(
            self, "Timeline Preview", f"Live preview unavailable: {error}\n"
            "Release the timeline to seek using the normal decoder.",
        )

    def set_frame_position(self, position=None):
        """Set frame position from timeline."""
        if self.cap and self.cap.isOpened():
            self.pause_video()
            frame_index = self.timeline_slider.value() if position is None else position
            try:
                frame = self.get_frame(frame_index)
            except RuntimeError as error:
                self.timeline_slider.setValue(self.current_frame_index)
                QMessageBox.warning(self, "Input Error", str(error))
                return
            if frame is not None:
                self.current_frame_index = frame_index
                self._reset_playback_diagnostics()
                self.update_frame()
            else:
                self.pause_video()
                self.timeline_slider.setValue(self.current_frame_index)
                QMessageBox.warning(self, "Warning", "Could not read the selected frame.")

    def next_frame(self):
        """Advance to next frame."""
        if not self.cap or not self.cap.isOpened() or self.frame_count <= 0:
            return
        
        try:
            next_idx = (self.current_frame_index + self.frame_skip) % self.frame_count
            
            frame = self.get_frame(next_idx)
            if frame is not None:
                self.current_frame_index = next_idx
                self.timeline_slider.setValue(self.current_frame_index)
                
                self.update_frame()
            else:
                self.pause_video()
                QMessageBox.warning(self, "Warning", "Error reading video frame.")
                
        except (cv2.error, ValueError, TypeError, RuntimeError) as e:
            print(f"Error in next_frame: {e}")
            traceback.print_exc()
            self.pause_video()
            QMessageBox.warning(self, "Playback Error", str(e))

    def previous_frame(self):
        """Go to previous frame."""
        if self.cap and self.cap.isOpened():
            was_playing = self.is_playing
            if was_playing:
                self.pause_video()
            
            prev_idx = (self.current_frame_index - 1) % self.frame_count
            try:
                frame = self.get_frame(prev_idx)
            except RuntimeError as error:
                QMessageBox.warning(self, "Input Error", str(error))
                return
            if frame is not None:
                self.current_frame_index = prev_idx
                self._reset_playback_diagnostics()
                self.timeline_slider.setValue(self.current_frame_index)
                self.update_frame()
            else:
                QMessageBox.warning(self, "Warning", "Error reading previous frame.")

    def navigate_iframe(self, direction):
        """Navigate presentation-order pict_type metadata, within the loaded index."""
        if not self.cap or not self.cap.isOpened() or self.frame_count <= 0:
            return
        self.pause_video()
        indices = (range(self.current_frame_index - 1, -1, -1) if direction < 0
                   else range(self.current_frame_index + 1, self.frame_count))
        if len(self.frame_metadata) != self.frame_count:
            QMessageBox.warning(self, "I-Frame Navigation",
                                "Complete source frame-type metadata is unavailable.")
            return
        target = None
        for index in indices:
            pict_type = self.frame_metadata[index].get("pict_type")
            if not pict_type or pict_type == "unknown":
                QMessageBox.warning(self, "I-Frame Navigation",
                                    "Source frame-type metadata is missing along this path; "
                                    "the nearest I-frame cannot be established.")
                return
            if pict_type == "I":
                target = index
                break
        if target is None:
            QMessageBox.information(
                self, "I-Frame Navigation",
                "No preceding I-frame in the loaded video/range." if direction < 0 else
                "No following I-frame in the loaded video/range."
            )
            return
        try:
            frame = self.get_frame(target)
        except RuntimeError as error:
            QMessageBox.warning(self, "Input Error", str(error))
            return
        if frame is None:
            QMessageBox.warning(self, "I-Frame Navigation", "Could not read the selected I-frame.")
            return
        self.current_frame_index = target
        self._reset_playback_diagnostics()
        self.timeline_slider.setValue(target)
        self.update_frame()

    def manual_next_frame(self):
        """Manually advance frame."""
        if self.cap and self.cap.isOpened():
            was_playing = self.is_playing
            if was_playing:
                self.pause_video()
            
            next_idx = (self.current_frame_index + 1) % self.frame_count
            try:
                frame = self.get_frame(next_idx)
            except RuntimeError as error:
                QMessageBox.warning(self, "Input Error", str(error))
                return
            if frame is not None:
                self.current_frame_index = next_idx
                self._reset_playback_diagnostics()
                self.timeline_slider.setValue(self.current_frame_index)
                self.update_frame()
            else:
                QMessageBox.warning(self, "Warning", "Error reading next frame.")

    def adjust_playback_speed(self):
        """Adjust playback speed."""
        speed_map = {
            "0.05x": 0.05, "0.1x": 0.1, "0.25x": 0.25, "0.5x": 0.5,
            "0.75x": 0.75, "1x": 1, "2x": 2, "3x": 3, "4x": 4, "5x": 5
        }
        
        self.speed_multiplier = speed_map.get(self.playback_speed_dropdown.currentText(), 1)
        
        self.frame_skip = max(1, round(self.speed_multiplier))
        
        if self.is_playing:
            self._start_playback_clock()
        else:
            self._reset_playback_diagnostics()
        
        self.update_frame_info_table()

    def reset_settings(self):
        """Reset all settings."""
        for spin in self.crop_spins.values():
            spin.blockSignals(True)
            spin.setValue(0)
            spin.blockSignals(False)
        self.brightness_slider.setValue(0)
        self.contrast_slider.setValue(0)
        self.sharpness_slider.setValue(0)
        self.compression_slider.setValue(0)
        self.rotation_dropdown.setCurrentIndex(0)
        self.zoom_dropdown.setCurrentText("1x")
        self.interpolation_dropdown.setCurrentText("Nearest")
        self.match_export_checkbox.setChecked(False)
        self.playback_speed_dropdown.setCurrentText("1x")
        self.green_checkbox.setChecked(False)
        self.original_checkbox.setChecked(False)
        self.nn_zoom_slider.setValue(1)
        
        self.adjust_playback_speed()
        
        self.process_cache = {}
        self.last_processing_params = {}
        if hasattr(self, 'current_frame') and self.current_frame is not None:
            self.update_frame()

    def update_frame_info_table(self):
        """Update frame information table."""
        if not hasattr(self, 'current_frame') or self.current_frame is None:
            self.frame_info_table.setRowCount(0)
            return
        
        try:
            if isinstance(self.frame_metadata, list) and self.current_frame_index < len(self.frame_metadata):
                frame_data = self.frame_metadata[self.current_frame_index].copy()
            else:
                frame_data = {}
            frame_data['width'] = self.current_frame.shape[1]
            frame_data['height'] = self.current_frame.shape[0]
            
            row_data = FrameInfoHandler.format_frame_data(
                frame_data,
                self.current_frame_index,
                self.frame_count,
                self.frame_rate,
                self.zoom_dropdown.currentText(),
                self.playback_speed_dropdown.currentText(),
                self.compression_slider.value(),
                self.source_range,
            )
            crop = crop_rectangle(self.current_frame.shape[1], self.current_frame.shape[0],
                                  **self.get_crop_params())
            row_data.append(("Processed Crop", f"{crop.width}x{crop.height}; source origin "
                             f"({crop.left}, {crop.top}); exact pixels, no output resize"))
            rotation = self.rotation_dropdown.currentData()
            if rotation:
                row_data.append(("Rotation", f"{ROTATION_LABELS[rotation]} after crop, before "
                                 "Zoom Level; exact pixels, no interpolation"))
            if self.decoder_backend == "pyav" and self.current_native_samples is not None:
                native = self.current_native_samples
                row_data = [
                    (name, ("8-bit BGR (exact native RGB channel packing)" if native.matrix == "rgb"
                            else "8-bit BGR (explicit PyAV conversion)")
                     if name == "Inspection Format" else value)
                    for name, value in row_data
                ]
            
            structure_changed = self.frame_info_table.rowCount() != len(row_data)
            self.frame_info_table.setRowCount(len(row_data))
            for i, (prop, value) in enumerate(row_data):
                for column, text in enumerate((prop, str(value))):
                    item = self.frame_info_table.item(i, column)
                    if item is None:
                        self.frame_info_table.setItem(i, column, QTableWidgetItem(text))
                    elif item.text() != text:
                        item.setText(text)
            
            if structure_changed:
                self.frame_info_table.resizeRowsToContents()
            
            if self.frame_info_table.columnCount() == 2:
                total_width = self.frame_info_table.width()
                if total_width > 0:
                    first_column_width = int(total_width * 0.35)
                    self.frame_info_table.horizontalHeader().setSectionResizeMode(0, QtCompat.Fixed)
                    self.frame_info_table.horizontalHeader().setSectionResizeMode(1, QtCompat.Stretch)
                    self.frame_info_table.setColumnWidth(0, first_column_width)
                    
        except (ValueError, TypeError, RuntimeError) as e:
            print(f"Error updating frame info table: {e}")
            self.frame_info_table.setRowCount(0)
            QMessageBox.warning(self, "Metadata Display Error", str(e))

    def update_zoomed_view(self):
        """Update the zoomed view."""
        self._exact_magnifier.invalidate()
        self.zoom_label.setToolTip(
            "Fast preview; exact FFmpeg rendering pending while paused."
            if self.match_export_checkbox.isChecked() else ""
        )
        if not hasattr(self, 'current_frame') or self.current_frame is None:
            return
        
        try:
            frame = self._get_processed_frame()
            
            lens = self.main_label.lens_rect
            image_size = self.main_label.image_size()
            
            if not image_size.isEmpty():
                pixmap_width = image_size.width()
                pixmap_height = image_size.height()
                frame_height, frame_width = frame.shape[:2]
                
                scale_x = frame_width / pixmap_width
                scale_y = frame_height / pixmap_height
                
                selection = lens.intersected(QRect(0, 0, pixmap_width, pixmap_height))
                if selection.isEmpty():
                    raise ValueError("The magnifier does not cover any source pixels.")
                # Outward integer rounding includes partially covered pixels, even a 1x1 crop.
                x = selection.x() * frame_width // pixmap_width
                y = selection.y() * frame_height // pixmap_height
                right = ((selection.x() + selection.width()) * frame_width
                         + pixmap_width - 1) // pixmap_width
                bottom = ((selection.y() + selection.height()) * frame_height
                          + pixmap_height - 1) // pixmap_height
                w, h = right - x, bottom - y
                
                roi = frame[y:y+h, x:x+w]
                self.current_zoomed_roi = roi.copy()
                
                zoom_level = int(self.zoom_dropdown.currentText().replace('x', ''))
                interp_method = self.interpolation_dropdown.currentText()
                
                interp_map = {
                    "Nearest": cv2.INTER_NEAREST,
                    "Bilinear": cv2.INTER_LINEAR,
                    "Bicubic": cv2.INTER_CUBIC,
                    "Lanczos": cv2.INTER_LANCZOS4
                }
                interp = interp_map.get(interp_method, cv2.INTER_NEAREST)
                
                zoom_label_width = self.zoom_label.width()
                zoom_label_height = self.zoom_label.height()
                nn_zoom_factor = self.nn_zoom_slider.value()
                total_zoom = zoom_level * nn_zoom_factor
                
                max_visible_width = max(1, (zoom_label_width + total_zoom - 1) // total_zoom)
                max_visible_height = max(1, (zoom_label_height + total_zoom - 1) // total_zoom)
                max_visible_width = min(max_visible_width, w)
                max_visible_height = min(max_visible_height, h)
                
                center_x = min(w - 1, int((lens.x() + lens.width() // 2) * scale_x) - x)
                center_y = min(h - 1, int((lens.y() + lens.height() // 2) * scale_y) - y)
                roi_x1 = max(0, min(center_x - max_visible_width // 2, w - max_visible_width))
                roi_y1 = max(0, min(center_y - max_visible_height // 2, h - max_visible_height))
                roi_x2 = roi_x1 + max_visible_width
                roi_y2 = roi_y1 + max_visible_height

                # Retain interpolation neighbors while rendering only the visible area.
                pad = 4
                ctx_x1, ctx_y1 = max(0, x + roi_x1 - pad), max(0, y + roi_y1 - pad)
                ctx_x2 = min(frame_width, x + roi_x2 + pad)
                ctx_y2 = min(frame_height, y + roi_y2 + pad)
                context = frame[ctx_y1:ctx_y2, ctx_x1:ctx_x2]
                interpolated = cv2.resize(
                    context, None, fx=zoom_level, fy=zoom_level, interpolation=interp
                )
                start_x = (x + roi_x1 - ctx_x1) * zoom_level
                start_y = (y + roi_y1 - ctx_y1) * zoom_level
                zoomed_roi = interpolated[
                    start_y:start_y + max_visible_height * zoom_level,
                    start_x:start_x + max_visible_width * zoom_level,
                ].copy()

                if self.green_checkbox.isChecked() or self.original_checkbox.isChecked():
                    mask = np.zeros(zoomed_roi.shape[:2], dtype=bool)
                    mask[::zoom_level, ::zoom_level] = True
                    if self.green_checkbox.isChecked():
                        zoomed_roi[~mask] = (0, 255, 0)
                    if self.original_checkbox.isChecked():
                        zoomed_roi[mask] = (0, 255, 0)

                if nn_zoom_factor > 1:
                    zoomed_roi = cv2.resize(
                        zoomed_roi, None, fx=nn_zoom_factor, fy=nn_zoom_factor,
                        interpolation=cv2.INTER_NEAREST,
                    )
                height, width = zoomed_roi.shape[:2]
                crop_x = max(0, (width - zoom_label_width) // 2)
                crop_y = max(0, (height - zoom_label_height) // 2)
                zoomed_roi = np.ascontiguousarray(zoomed_roi[
                    crop_y:crop_y + zoom_label_height,
                    crop_x:crop_x + zoom_label_width,
                ])
                height, width = zoomed_roi.shape[:2]
                q_img = QImage(
                    zoomed_roi.data, width, height, zoomed_roi.strides[0],
                    QImage.Format.Format_BGR888,
                )
                self.zoom_label.setPixmap(QPixmap.fromImage(q_img))

                if self.match_export_checkbox.isChecked() and not self.is_playing:
                    target_x, target_y, _ = self._crosshair_target(frame)
                    sample = None
                    if (x <= target_x // zoom_level < x + w
                            and y <= target_y // zoom_level < y + h):
                        sample = (target_x - x * zoom_level, target_y - y * zoom_level)
                    self._exact_sample_pending = self._inspector_key(
                        target_x, target_y, zoom_level, interp_method)
                    self._exact_magnifier.request(MagnifierRequest(
                        roi=roi.copy(), zoom=zoom_level, interpolation=interp_method,
                        visible=(roi_x1, roi_y1, max_visible_width, max_visible_height),
                        viewport=(zoom_label_width, zoom_label_height),
                        additional_zoom=nn_zoom_factor,
                        highlight_added=self.green_checkbox.isChecked(),
                        highlight_source=self.original_checkbox.isChecked(),
                        sample=sample,
                    ))
                
                self.update_pixel_inspector()
                self.update_frame_info_table()
                
        except (cv2.error, ValueError, TypeError, RuntimeError) as e:
            print(f"Error updating zoomed view: {e}")
            traceback.print_exc()
            self.zoom_label.clear()
            self._clear_pixel_inspector()
            self.pause_video()
            QMessageBox.warning(self, "Magnifier Error", str(e))

    def _show_exact_magnifier(self, frame):
        height, width = frame.shape[:2]
        image = QImage(frame.data, width, height, frame.strides[0],
                       QImage.Format.Format_BGR888)
        self.zoom_label.setPixmap(QPixmap.fromImage(image))
        self.zoom_label.setToolTip(
            "FFmpeg-matched selected crop and zoom; Additional Zoom and highlights "
            "are display-only. The inspector still reads processed source pixels."
        )

    def _exact_magnifier_error(self, error):
        print(f"Exact magnifier rendering failed: {error}")
        self.match_export_checkbox.setChecked(False)
        QMessageBox.warning(self, "Exact Magnifier Error", error)

    _INTERPOLATION_FLAGS = {
        "Nearest": cv2.INTER_NEAREST, "Bilinear": cv2.INTER_LINEAR,
        "Bicubic": cv2.INTER_CUBIC, "Lanczos": cv2.INTER_LANCZOS4,
    }

    def _crosshair_target(self, frame):
        """Map the Main View crosshair to a pixel of the processed image at Zoom Level."""
        image_size, lens = self.main_label.image_size(), self.main_label.lens_rect
        zoom = int(self.zoom_dropdown.currentText().replace("x", ""))
        height, width = frame.shape[:2]
        center_x = lens.x() + lens.width() // 2
        center_y = lens.y() + lens.height() // 2
        target_x = max(0, min(center_x * width * zoom // image_size.width(), width * zoom - 1))
        target_y = max(0, min(center_y * height * zoom // image_size.height(), height * zoom - 1))
        return target_x, target_y, zoom

    @classmethod
    def _interpolated_pixel(cls, frame, target_x, target_y, zoom, interpolation):
        """One pixel of the OpenCV Zoom Level image, from a local context of the frame.

        Integer-factor OpenCV resizing reads at most four source pixels on each side,
        so the context result equals the full-frame resize at that pixel.
        """
        if zoom == 1:
            return frame[target_y, target_x].copy()
        source_x, source_y, pad = target_x // zoom, target_y // zoom, 4
        x1, y1 = max(0, source_x - pad), max(0, source_y - pad)
        x2 = min(frame.shape[1], source_x + pad + 1)
        y2 = min(frame.shape[0], source_y + pad + 1)
        scaled = cv2.resize(frame[y1:y2, x1:x2], None, fx=zoom, fy=zoom,
                            interpolation=cls._INTERPOLATION_FLAGS.get(
                                interpolation, cv2.INTER_NEAREST))
        return scaled[target_y - y1 * zoom, target_x - x1 * zoom].copy()

    def _inspector_key(self, target_x, target_y, zoom, interpolation):
        params = self.get_processing_params()
        return (self._processing_cache_key(self.current_frame_index, params),
                target_x, target_y, zoom, interpolation)

    def _show_exact_sample(self, pixel):
        if self._exact_sample_pending is not None:
            self._exact_sample = (self._exact_sample_pending, pixel)
            self.update_pixel_inspector()

    def update_pixel_inspector(self):
        """Report native source samples and the processed pixel under the crosshair."""
        if not hasattr(self, 'current_frame') or self.current_frame is None:
            return
        pixmap = getattr(self.main_label, 'pixmap', None)
        if not pixmap or pixmap.isNull():
            return
        try:
            frame = self._get_processed_frame()
            target_x, target_y, zoom = self._crosshair_target(frame)
            interpolation = self.interpolation_dropdown.currentText()
            orig_x, orig_y = target_x // zoom, target_y // zoom
            crop = crop_rectangle(self.current_frame.shape[1], self.current_frame.shape[0],
                                  **self.get_crop_params())
            rotation = self.rotation_dropdown.currentData()
            cropped_x, cropped_y = unrotate_point(orig_x, orig_y, crop.width, crop.height,
                                                  rotation)
            source_x, source_y = cropped_x + crop.left, cropped_y + crop.top

            b, g, r = map(int, self._interpolated_pixel(
                frame, target_x, target_y, zoom, interpolation))
            method = "OpenCV"
            if self.match_export_checkbox.isChecked() and zoom > 1:
                key = self._inspector_key(target_x, target_y, zoom, interpolation)
                if (not self.is_playing and self._exact_sample is not None
                        and self._exact_sample[0] == key):
                    b, g, r = map(int, self._exact_sample[1])
                    method = "FFmpeg"
            processed_text = f"{r}, {g}, {b}"
            source_b, source_g, source_r = map(
                int, self.current_frame[source_y, source_x])
            processed_text += f" (source RGB: {source_r}, {source_g}, {source_b})"
            if method == "FFmpeg":
                processed_text += " (FFmpeg-matched)"
            elif self.match_export_checkbox.isChecked() and zoom > 1:
                processed_text += (" (OpenCV preview during playback)" if self.is_playing
                                   else " (OpenCV preview; FFmpeg value pending)")

            swatch = QPixmap(65, 65)
            painter = QPainter(swatch)
            painter.fillRect(0, 0, 65, 65, QColor(r, g, b))
            painter.end()
            self.pixel_display.setPixmap(swatch)

            processed_item = QTableWidgetItem(processed_text)
            position = (f"Zoom Level {zoom}x pixel ({target_x}, {target_y}) of the processed "
                        f"image" + (f", {interpolation} interpolation ({method})"
                                    if zoom > 1 else "")
                        + (f"; lies within cropped pixel ({orig_x}, {orig_y}), which maps "
                           if not rotation else
                           f"; lies within rotated pixel ({orig_x}, {orig_y}) "
                           f"({ROTATION_LABELS[rotation]}), cropped pixel "
                           f"({cropped_x}, {cropped_y}), which maps ")
                        + f"to source ({source_x}, {source_y}).")
            processed_item.setToolTip(
                position + "\nRead before highlights, Additional Zoom and window fit."
                "\nSource RGB: the corresponding actual decoded source pixel after "
                "its documented color interpretation, before enhancements, zoom or "
                "interpolation. No interpolated source RGB is fabricated.")

            native = self.current_native_samples
            label = "Native Source YCbCr"
            if native is None:
                native_item = QTableWidgetItem(
                    "Unavailable (legacy OpenCV input)" if self.decoder_backend == "opencv"
                    else "Unavailable")
                native_item.setToolTip(
                    "Native decoded samples are unavailable; no values are reconstructed "
                    "from RGB.")
            else:
                values = [int(value) for value in native.pixel(source_x, source_y)]
                if native.matrix == "rgb":
                    label = "Native Source RGB"
                    native_item = QTableWidgetItem("R {}, G {}, B {}".format(*values))
                    sample_text = (
                        f"Native source R/G/B at ({source_x}, {source_y}): "
                        f"{values[0]}, {values[1]}, {values[2]}\n"
                        f"{native.frame.format.name}, full-range RGB; no native Y/U/V. "
                    )
                else:
                    native_item = QTableWidgetItem("Y′ {}, Cb {}, Cr {}".format(*values))
                    sample_text = (
                        f"Native source Y/U/V at ({source_x}, {source_y}): "
                        f"{values[0]}, {values[1]}, {values[2]}\n"
                        f"U/V shared sample at ({source_x // 2}, {source_y // 2}); "
                        f"{native.matrix}, {native.range_name} range; "
                        f"chroma location: {native.chroma_location}. "
                    )
                tooltip = ("Decoded source samples, not values reconstructed from RGB.\n"
                           + position + "\n" + sample_text
                           + "Unchanged by enhancements or overlays.")
                audit = getattr(self.cap, "color_audit", None)
                if audit is not None:
                    tooltip += "\n" + audit["source_color_intent"] + "\n" + "\n".join(
                        f"{field}: {value['effective']} ({value['provenance']})"
                        for field, value in audit["interpretation"].items()
                    )
                native_item.setToolTip(tooltip)

            effect_parts = []
            if self.brightness_slider.value() != 0:
                effect_parts.append(f"Brightness: {self.brightness_slider.value():+}")
            if self.contrast_slider.value() != 0:
                effect_parts.append(f"Contrast: {self.contrast_slider.value():+.0f}%")
            if self.sharpness_slider.value() > 0:
                effect_parts.append(f"Sharpness: +{self.sharpness_slider.value():.0f}%")
            if self.compression_slider.value() > 0:
                effect_parts.append(
                    f"JPEG Compression Simulation: {self.compression_slider.value()}%")
            if any(self.get_crop_params().values()):
                effect_parts.append(f"Crop: {crop.width}x{crop.height}")
            if rotation:
                effect_parts.append(f"Rotate: {ROTATION_LABELS[rotation]}")
            if zoom > 1:
                effect_parts.append(f"Zoom: {zoom}x {interpolation}")
            effect_text = ", ".join(effect_parts) if effect_parts else "No adjustments"

            label_item = self.pixel_values_widget.item(0, 0)
            if label_item is None or label_item.text() != label:
                self.pixel_values_widget.setItem(0, 0, QTableWidgetItem(label))
            self.pixel_values_widget.setItem(0, 1, native_item)
            self.pixel_values_widget.setItem(1, 1, processed_item)
            self.pixel_values_widget.setItem(2, 1, QTableWidgetItem(effect_text))

            for i in range(self.pixel_values_widget.rowCount()):
                self.pixel_values_widget.setRowHeight(i, 20)
            total_height = sum(self.pixel_values_widget.rowHeight(i)
                               for i in range(self.pixel_values_widget.rowCount()))
            self.pixel_values_widget.setFixedHeight(total_height + 2)

        except (cv2.error, ValueError, TypeError, RuntimeError) as e:
            print(f"Error updating pixel inspector: {e}")
            traceback.print_exc()
            self._clear_pixel_inspector()
            QMessageBox.warning(self, "Pixel Inspection Error", str(e))

    def create_app_icon(self):
        """Create application icon."""
        icon_size = 64
        pixmap = QPixmap(icon_size, icon_size)
        pixmap.fill(QtCompat.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        lens_color = QColor(50, 150, 255, 220)
        painter.setBrush(lens_color)
        painter.setPen(QPen(QtCompat.black, 2))
        lens_radius = int(icon_size * 0.4)
        lens_center = QPoint(int(icon_size * 0.4), int(icon_size * 0.4))
        painter.drawEllipse(lens_center, lens_radius, lens_radius)
        
        handle_width = int(icon_size * 0.15)
        painter.setPen(QPen(QtCompat.black, handle_width, QtCompat.SolidLine, QtCompat.RoundCap))
        handle_start = QPoint(int(lens_center.x() + lens_radius * 0.7), int(lens_center.y() + lens_radius * 0.7))
        handle_end = QPoint(int(icon_size * 0.85), int(icon_size * 0.85))
        painter.drawLine(handle_start, handle_end)
        
        painter.setPen(QPen(QtCompat.black, 1, QtCompat.SolidLine))
        cross_size = int(lens_radius * 0.4)
        painter.drawLine(lens_center.x() - cross_size, lens_center.y(), lens_center.x() + cross_size, lens_center.y())
        painter.drawLine(lens_center.x(), lens_center.y() - cross_size, lens_center.x(), lens_center.y() + cross_size)
        
        painter.end()
        
        self.app_icon = QIcon(pixmap)
        self.setWindowIcon(self.app_icon)
        QApplication.setWindowIcon(self.app_icon)

    def set_windows_taskbar_icon(self):
        """Set Windows taskbar icon."""
        if sys.platform == 'win32':
            try:
                app_id = 'OSVEV.VideoAnalysis.1.0'
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
                
                try:
                    tmp_icon_path = os.path.join(tempfile.gettempdir(), 'osvev_icon.png')
                    if hasattr(self, 'app_icon'):
                        pixmap = self.app_icon.pixmap(64, 64)
                        pixmap.save(tmp_icon_path, 'PNG')
                        
                        if os.path.exists(tmp_icon_path):
                            icon = QIcon(tmp_icon_path)
                            self.setWindowIcon(icon)
                            QApplication.setWindowIcon(icon)
                except Exception as e:
                    print(f"Minor issue setting taskbar icon: {e}")
                    
            except Exception as e:
                print(f"Failed to set Windows taskbar icon: {e}")

    def closeEvent(self, event):
        """Handle close event."""
        self._stop_arrow_hold()
        self._cancel_scrub_preview()
        self._magnifier_closing = True
        self._exact_magnifier.close()
        self.pause_video()
        self._audio.clear()
        self._diagnostic_timer.stop()
        if self._working_copy_dialog is not None:
            self._working_copy_dialog.reject()
            if self._working_copy_dialog.worker is not None:
                self._working_copy_dialog.worker.wait()
        if self._export_dialog is not None and self._export_dialog.worker is not None:
            self._export_dialog.worker.requestInterruption()
            self._export_dialog.worker.wait()
            self._export_dialog.done(0)
        self._cancel_metadata_load()
        self._stop_media_probe()
        if self._loading_progress is not None:
            self._loading_progress.cancel()
        if self.cap is not None:
            self.cap.release()
        self.current_native_samples = None
        self.frame_processor.close()
        event.accept()

    def _stop_media_probe(self):
        if self._media_probe is not None:
            self._media_probe.stop()
            self._media_probe.deleteLater()
            self._media_probe = None

    def _show_loaded_file(self):
        self._stop_media_probe()
        self._media_info = None
        self.file_name_label.set_path(self.video_path)
        self.media_summary_label.setText("Reading FFprobe info…")
        self.media_info_button.setEnabled(False)
        probe = MediaInfoProbe(self.video_path, self)
        probe.ready.connect(lambda data, size, error: self._media_info_ready(probe, data, size, error))
        self._media_probe = probe
        probe.start()

    def _media_info_ready(self, probe, data, size, error):
        if probe is not self._media_probe:
            return
        self._media_info = (data, size, error, probe.modified)
        self.media_summary_label.setText(summary_line(data, size, error))
        self.media_info_button.setEnabled(True)

    def _reveal_loaded_file(self, _link=None):
        try:
            reveal_in_folder(self.video_path)
        except OSError as error:
            QMessageBox.warning(self, "Show in Folder", str(error))

    def _show_media_info(self):
        if self._media_info is None:
            return
        dialog = MediaInfoDialog(self.video_path, *self._media_info, parent=self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def event(self, event):
        handled = super().event(event)
        # Moving to a monitor with different scaling may not resize the window.
        if (event.type() == getattr(QEvent.Type, "DevicePixelRatioChange", None)
                and getattr(self, "current_frame", None) is not None):
            self.update_frame()
        return handled

    def resizeEvent(self, event):
        """Handle resize event."""
        super().resizeEvent(event)
        
        if hasattr(self, 'current_frame') and self.current_frame is not None:
            self.update_frame()
        
        if hasattr(self, 'pixel_values_widget'):
            total_width = self.pixel_values_widget.width()
            if total_width > 0:
                first_column_width = int(total_width * 0.35)
                self.pixel_values_widget.setColumnWidth(0, first_column_width)
            
            self.pixel_values_widget.resizeRowsToContents()
            for i in range(self.pixel_values_widget.rowCount()):
                self.pixel_values_widget.setRowHeight(i, 20)
            
            total_height = sum(self.pixel_values_widget.rowHeight(i) for i in range(self.pixel_values_widget.rowCount()))
            self.pixel_values_widget.setFixedHeight(total_height + 2)
        
        if hasattr(self, 'frame_info_table'):
            total_width = self.frame_info_table.width()
            if total_width > 0:
                first_column_width = int(total_width * 0.35)
                self.frame_info_table.setColumnWidth(0, first_column_width)

    def showEvent(self, event):
        """Handle show event."""
        super().showEvent(event)
        self.shown = True
        QTimer.singleShot(100, self.update_layout_and_views)

    def update_layout_and_views(self):
        """Update layout and views."""
        self.update_zoomed_view()
        
        if hasattr(self, 'pixel_values_widget'):
            self.pixel_values_widget.resizeRowsToContents()
            total_height = sum(self.pixel_values_widget.rowHeight(i) for i in range(self.pixel_values_widget.rowCount()))
            self.pixel_values_widget.setFixedHeight(total_height + 4)
            
            for i in range(self.pixel_values_widget.rowCount()):
                self.pixel_values_widget.setRowHeight(i, 20)
            
            total_height = sum(self.pixel_values_widget.rowHeight(i) for i in range(self.pixel_values_widget.rowCount()))
            self.pixel_values_widget.setFixedHeight(total_height + 2)
