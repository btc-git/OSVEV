# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 CrimLawTech LLC
"""Small modal workflow; conversion and all verification run off the GUI thread."""

from fractions import Fraction
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QLabel, QMessageBox, QProgressBar, QScrollArea, QVBoxLayout, QWidget,
)

from color_interpretation import ColorChoices
from working_copy import WorkingCopyWorker


class WorkingCopyDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path, self.source, self.result_path = path, None, None
        self.worker = None
        self.cancelling = False
        self.setWindowTitle("Create Compatible Working Copy")
        self.setMinimumWidth(490)
        outer = QVBoxLayout(self)
        options = QWidget()
        layout = QVBoxLayout(options)
        self.options_scroll = QScrollArea()
        self.options_scroll.setWidgetResizable(True)
        self.options_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.options_scroll.setWidget(options)
        outer.addWidget(self.options_scroll, 1)
        notice = QLabel(
            "This creates a new video-only copy; the source is only read and checked for changes. "
            "Audio, subtitles, and other streams are left out. It can take time and use much more "
            "disk space than the original. Existing files are never overwritten."
        )
        notice.setWordWrap(True)
        notice.setToolTip(
            "The copy converts decoded pixels to FFV1/MKV and verifies every frame independently. "
            "It preserves the recorded color interpretation, not native YUV samples. The estimate "
            "shown after inspection is uncompressed pixel data, not a prediction of the compressed "
            "output size. Staging and verification require additional free space."
        )
        layout.addWidget(notice)
        self.display_note = QLabel()
        self.display_note.setWordWrap(True)
        self.display_note.setVisible(False)
        layout.addWidget(self.display_note)
        self.color_note = QLabel()
        self.color_note.setWordWrap(True)
        layout.addWidget(self.color_note)
        self.color_fields = QFormLayout()
        self.matrix, self.range_combo = QComboBox(), QComboBox()
        self.matrix.addItems(["BT.601", "BT.709"])
        self.range_combo.addItems(["Limited", "Full"])
        self.color_fields.addRow("Missing matrix", self.matrix)
        self.color_fields.addRow("Missing range", self.range_combo)
        layout.addLayout(self.color_fields)
        self.matrix.setEnabled(False)
        self.range_combo.setEnabled(False)
        self.derived = QCheckBox("Use estimated timestamps (not original)")
        self.derived.setToolTip("Use complete, nondecreasing FFmpeg best-effort timestamps only; "
                                "existing original PTS must agree. No frame rate is assumed.")
        self.derived.setVisible(False)
        self.derived.toggled.connect(self._enable_create)
        layout.addWidget(self.derived)
        self.count_note = QLabel()
        self.count_note.setWordWrap(True)
        self.count_note.setVisible(False)
        layout.addWidget(self.count_note)
        self.count_permission = QCheckBox("Use decoded frames despite the frame-count mismatch")
        self.count_permission.setToolTip(
            "The copy includes every recovered frame and adds or removes none. "
            "A count mismatch does not establish whether the original recording is complete."
        )
        self.count_permission.setVisible(False)
        self.count_permission.toggled.connect(self._enable_create)
        layout.addWidget(self.count_permission)
        self.reorder_note = QLabel()
        self.reorder_note.setWordWrap(True)
        self.reorder_note.setVisible(False)
        layout.addWidget(self.reorder_note)
        self.reorder_permission = QCheckBox("Put decoded frames in timestamp order")
        self.reorder_permission.setToolTip(
            "Only eligible local order reversals are accepted. Pixels and original timestamps "
            "remain unchanged; the conversion log records the frame mapping."
        )
        self.reorder_permission.setVisible(False)
        self.reorder_permission.toggled.connect(self._enable_create)
        layout.addWidget(self.reorder_permission)
        self.precision_note = QLabel()
        self.precision_note.setWordWrap(True)
        self.precision_note.setVisible(False)
        layout.addWidget(self.precision_note)
        self.precision_permission = QCheckBox("Allow timing to be rounded to milliseconds")
        self.precision_permission.setToolTip(
            "Each absolute timestamp is rounded independently by at most 0.5 ms. "
            "Reported durations, intervals, endpoint and overall span may differ by at most 1 ms. "
            "No new timestamp ties or gap repairs are allowed; all deviations are logged."
        )
        self.precision_permission.setVisible(False)
        self.precision_permission.toggled.connect(self._enable_create)
        layout.addWidget(self.precision_permission)
        layout.addStretch()
        self.status = QLabel("Inspecting source compatibility...")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        outer.addWidget(self.progress)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create = buttons.addButton("Create Working Copy...", QDialogButtonBox.ButtonRole.ActionRole)
        self.create.setEnabled(False)
        self.create.clicked.connect(self._create)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        available = self.screen().availableGeometry()
        self.resize(min(760, available.width() - 40), min(760, available.height() - 60))
        QTimer.singleShot(0, self._prepare)

    def _prepare(self):
        if not self.cancelling:
            self._start(WorkingCopyWorker(self.path, self))

    def _start(self, worker):
        self.worker = worker
        self.create.setEnabled(False)
        self.matrix.setEnabled(False)
        self.range_combo.setEnabled(False)
        self.derived.setEnabled(False)
        self.count_permission.setEnabled(False)
        self.precision_permission.setEnabled(False)
        self.reorder_permission.setEnabled(False)
        self.progress.setRange(0, 0)
        worker.progress.connect(self._progress)
        worker.finished.connect(self._finished)
        worker.start()

    def _progress(self, percent, message):
        if self.cancelling:
            return
        self.status.setText(message)
        self.progress.setRange(0, 100 if percent else 0)
        if percent:
            self.progress.setValue(percent)

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.wait()
        result, error, cancelled = worker.result, worker.error, worker.cancelled
        worker.deleteLater()
        if cancelled or self.cancelling:
            super().reject()
            return
        if error:
            self.status.setText(error)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            QMessageBox.warning(self, "Working copy not created", error)
            self._enable_create()
            return
        if self.source is not None:
            self.result_path = result
            super().accept()
            return
        self.source = result
        if self.source.display_rotation:
            self.display_note.setText(
                f"The source has a {self.source.display_rotation}° counter-clockwise display rotation. "
                "This copy will apply that rotation to its pixels and remove the display-matrix metadata. "
                "The source file remains unchanged."
            )
            self.display_note.setVisible(True)
        self.precision_note.setVisible(self.source.needs_rounding)
        self.precision_permission.setVisible(self.source.needs_rounding)
        self.precision_note.setText(
            "This output format cannot store the source's exact timing. The copy will use "
            "millisecond-rounded timing, so it will not have the original timing precision. "
            "Leave unchecked to cancel this conversion."
        )
        self.precision_note.setToolTip(self.precision_permission.toolTip())
        self.count_note.setVisible(self.source.count_mismatch)
        self.count_permission.setVisible(self.source.count_mismatch)
        audit = self.source.count_audit or {}
        packets = f" in {audit['packet_count']} packets" if "packet_count" in audit else ""
        explained = " ".join(audit.get("assumptions", [])) if not audit.get("avi", True) else ""
        self.count_note.setText(
            f"The file declares {self.source.declared_count} frames{packets}; "
            f"OSVEV decoded {len(self.source.frames)}. This copy includes only decoded frames. "
            "It cannot confirm whether the recording is complete."
        )
        self.count_note.setToolTip(
            (explained + " " if explained else "")
            + "Every recovered frame is independently verified. No frames are added or removed "
            "to match the declared count. The discrepancy does not prove the original recording is complete."
        )
        self.derived.setVisible(self.source.derived_timing)
        self.reorder_note.setVisible(self.source.reordered)
        self.reorder_permission.setVisible(self.source.reordered)
        if self.source.reordered:
            reorder = self.source.reorder_audit
            self.reorder_note.setText(
                "The decoder returned some frames out of timestamp order. If you allow it, "
                "the copy will put them in timestamp order without changing their pixels. "
                "The log will record the changes."
            )
            self.reorder_note.setToolTip(
                f"{reorder['reversal_count']} local timestamp reversal(s); "
                f"{reorder['repositioned_frame_count']} frame position(s) change, with a maximum "
                f"displacement of {reorder['max_displacement_frames']}. Original PTS and durations "
                "are unchanged; no frame is added, duplicated or removed. Every source/output mapping "
                "is verified and logged."
            )
        self.derived.setEnabled(True)
        missing = self.source.missing_color
        self.matrix.setEnabled("color_space" in missing)
        self.range_combo.setEnabled("color_range" in missing)
        if missing:
            self.color_note.setText(
                "Some color information is missing. Your choices fill only the blanks; they are "
                "assumptions, not verified facts. The conversion log records them."
            )
            self.color_note.setToolTip(
                "Missing fields: " + ", ".join(missing) + ". Selections fill missing fields only. "
                "Missing primaries/transfer assume SDR matching the effective matrix; missing chroma "
                "assumes left. These assumptions are not verified source properties."
            )
        else:
            self.color_note.setText(
                "The supported color information is present; no color assumptions are needed."
            )
            self.color_note.setToolTip("")
        raw_gib = (self.source.frames[0]["width"] * self.source.frames[0]["height"]
                   * 4 * len(self.source.frames) / 1024 ** 3)
        self.status.setText(
            f"Ready: {len(self.source.frames):,} frames. Rough uncompressed-pixel estimate: "
            f"{raw_gib:.2f} GiB. This is not a prediction of the FFV1 file size; actual size varies "
            "with the video, and temporary conversion/verification files need extra disk space. "
            "The log records timing and any assumptions."
        )
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._enable_create()

    def _enable_create(self):
        if self.source is not None and self.worker is None and not self.cancelling:
            self.matrix.setEnabled("color_space" in self.source.missing_color)
            self.range_combo.setEnabled("color_range" in self.source.missing_color)
            self.derived.setEnabled(True)
            self.count_permission.setEnabled(True)
            self.precision_permission.setEnabled(True)
            self.reorder_permission.setEnabled(True)
        self.create.setEnabled(self.source is not None and self.worker is None and not self.cancelling
                               and (not self.source.derived_timing or self.derived.isChecked())
                               and (not self.source.count_mismatch or self.count_permission.isChecked())
                               and (not self.source.reordered or self.reorder_permission.isChecked())
                               and (not self.source.needs_rounding or self.precision_permission.isChecked()))

    def _create(self):
        if not self.create.isEnabled():
            return
        default = str(Path(self.path).with_name(Path(self.path).stem + "-working.mkv"))
        destination, _ = QFileDialog.getSaveFileName(self, "Save Compatible Working Copy",
                                                     default, "FFV1 Matroska (*.mkv)")
        if not destination:
            return
        choices = (ColorChoices("smpte170m" if self.matrix.currentIndex() == 0 else "bt709",
                                "tv" if self.range_combo.currentIndex() == 0 else "pc")
                   if self.source.missing_color else None)
        self._start(WorkingCopyWorker(
            self.source, self, destination=destination, choices=choices,
            authorize_derived=self.derived.isChecked(),
            authorize_count_mismatch=self.count_permission.isChecked(),
            authorize_precision_rounding=self.precision_permission.isChecked(),
            authorize_pts_reordering=self.reorder_permission.isChecked()))

    def reject(self):
        self.cancelling = True
        if self.worker is not None:
            self.status.setText("Cancelling and cleaning up incomplete working-copy files...")
            self.worker.requestInterruption()
        else:
            super().reject()

    def closeEvent(self, event):
        self.reject()
        if self.worker is not None:
            event.ignore()
        else:
            event.accept()
