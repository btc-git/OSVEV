# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Small command-first export dialog; no live preview or rendering architecture."""

from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from crop_utils import ROTATION_LABELS
from export_runner import ExportWorker
from ffmpeg_export import ExportError, build_export_plan, powershell_command


class ExportDialog(QDialog):
    def __init__(self, source, current_index, metadata, settings, decoder_backend, parent=None,
                 *, playback_speed=1.0, zoom_level=1, interpolation="Nearest", source_range=None,
                 color_choices=None):
        super().__init__(parent)
        self.source, self.metadata, self.settings = source, metadata, dict(settings)
        self.settings["compression"] = 0
        self.source_range = source_range
        self.color_choices = color_choices
        self.offset = 0 if source_range is None else source_range.offset
        # current_index is a working-timeline position; plans use original source indices.
        self.decoder_backend, self.current_index = decoder_backend, self.offset + current_index
        self.playback_speed = playback_speed
        self.zoom_level, self.interpolation = zoom_level, interpolation
        self.worker = None
        self.plan = None
        self.setWindowTitle("FFmpeg export and verification")
        self.resize(820, 590)
        layout = QVBoxLayout(self)
        label = QLabel(
            "Original video -> combined brightness/contrast (eq) -> sharpening -> exact crop -> exact rotation -> export scaling.\n"
            f"Viewer settings: {playback_speed:g}x playback, {zoom_level}x zoom, {interpolation} interpolation, "
            f"{ROTATION_LABELS.get(self.settings.get('rotation', 0), self.settings.get('rotation'))} rotation.\n"
            "PNG/FFV1 preserve processed RGB pixels; compatibility H.264 is LOSSY.\n"
            "Export-only FFmpeg interpolation may differ from OpenCV/Qt display pixels; neither viewer changes.\n"
            "Audio is for listening only and is NOT included in exports.\n"
            "Video-only; all selected frames retained. JPEG compression simulation, pixel highlighting, Magnified View,\n"
            "Additional Zoom and inspection overlays are excluded. PNGs have no playback timing."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems(["PNG (lossless RGB)", "FFV1 MKV (lossless RGB)", "H.264 MP4 (lossy compatibility)"])
        self.selection_combo = QComboBox()
        self.selection_combo.addItems(["Current frame", "Selected range",
                                       "Whole video" if source_range is None else "Whole loaded time range"])
        self.first_spin, self.last_spin = QSpinBox(), QSpinBox()
        for spin in (self.first_spin, self.last_spin):
            spin.setRange(self.offset + 1, self.offset + max(1, len(metadata)))
            spin.setValue(self.current_index + 1)
        range_row = QHBoxLayout()
        range_row.addWidget(self.first_spin)
        range_row.addWidget(QLabel("through (inclusive)"))
        range_row.addWidget(self.last_spin)
        self.range_widget = QWidget()
        self.range_widget.setLayout(range_row)
        self.range_label = QLabel("Original source frame numbers (1-based)")
        self.path_edit = QLineEdit()
        self.browse_button = QPushButton("Choose output...")
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.browse_button)
        form.addRow("Format", self.format_combo)
        self.ffv1_playback_note = QLabel(
            'FFV1/MKV is lossless, but some video players may not support it. '
            'Use <a href="https://www.videolan.org/vlc/">VLC</a> or another compatible player.'
        )
        self.ffv1_playback_note.setWordWrap(True)
        self.ffv1_playback_note.setOpenExternalLinks(True)
        form.addRow(self.ffv1_playback_note)
        form.addRow("Selection", self.selection_combo)
        form.addRow(self.range_label, self.range_widget)
        form.addRow("New output name", path_row)
        self.diagnostics_check = QCheckBox("Keep extra verification details (optional)")
        self.diagnostics_check.setToolTip(
            "Adds a .osvev.json summary and .frames.jsonl per-frame records. "
            "Useful for detailed audits and diagnosing failures; verification is unchanged.")
        form.addRow(self.diagnostics_check)
        self.diagnostics_note = QLabel(
            "Verification runs for every export, with or without this option. Successful exports "
            "always include a readable .txt processing log with settings, commands and file hashes.\n"
            "Troubleshooting or detailed audits: extra JSON/JSONL files retain per-frame hashes, "
            "timing checks and failure details."
        )
        self.diagnostics_note.setWordWrap(True)
        form.addRow(self.diagnostics_note)
        layout.addLayout(form)
        self.command_text = QPlainTextEdit()
        self.command_text.setReadOnly(True)
        self.command_text.setPlaceholderText("Choose a destination to generate a PowerShell-ready ordinary FFmpeg command.")
        layout.addWidget(self.command_text)
        self.status_label = QLabel("Existing outputs will never be overwritten.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy command")
        self.execute_button = QPushButton("Export")
        self.cancel_button = QPushButton("Close")
        for button in (self.copy_button, self.execute_button, self.cancel_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.browse_button.clicked.connect(self._browse)
        self.copy_button.clicked.connect(self._copy)
        self.execute_button.clicked.connect(self._execute)
        self.cancel_button.clicked.connect(self.reject)
        for combo in (self.format_combo, self.selection_combo):
            combo.currentIndexChanged.connect(self._refresh)
        for spin in (self.first_spin, self.last_spin):
            spin.valueChanged.connect(self._refresh)
        self.path_edit.textChanged.connect(self._refresh)
        self._refresh()

    def _browse(self):
        filters = ["PNG images (*.png)", "Matroska FFV1 (*.mkv)", "H.264 MP4 (*.mp4)"]
        path, _ = QFileDialog.getSaveFileName(
            self, "Choose a NEW output name", "", filters[self.format_combo.currentIndex()]
        )
        if path:
            self.path_edit.setText(path)

    def _refresh(self):
        if self.worker is not None:
            return
        self.ffv1_playback_note.setVisible(self.format_combo.currentIndex() == 1)
        selected = self.selection_combo.currentIndex()
        self.range_widget.setVisible(selected == 1)
        self.range_label.setVisible(selected == 1)
        self.first_spin.setEnabled(selected == 1)
        self.last_spin.setEnabled(selected == 1)
        self.plan = None
        if selected == 0:
            first = last = self.current_index
        elif selected == 1:
            first, last = self.first_spin.value() - 1, self.last_spin.value() - 1
        else:
            first, last = self.offset, self.offset + len(self.metadata) - 1
        try:
            if not self.path_edit.text():
                raise ExportError("Choose a new output name. PNG ranges produce a numbered sequence.")
            self.plan = build_export_plan(
                self.source, self.path_edit.text(), ("png", "ffv1", "h264")[self.format_combo.currentIndex()],
                first, last, self.settings, self.metadata, self.decoder_backend,
                allow_existing_output=True,
                playback_speed=self.playback_speed,
                zoom_level=self.zoom_level, interpolation=self.interpolation,
                source_range=self.source_range,
                color_choices=self.color_choices,
            )
            self.command_text.setPlainText(powershell_command(self.plan.command()))
            self.status_label.setText(
                f"{self.plan.count} frames; "
                + f"{self.plan.width}x{self.plan.height}; "
                + ("PNG: no playback timing; " if self.plan.kind == "png" else
                   f"{self.plan.playback_speed:g}x; expected duration {float(self.plan.expected_duration):.6f} s; ")
                + ("lossless processed RGB" if self.plan.lossless else "LOSSY H.264; pixel differences will be measured")
                + ". Every frame is verified; successful exports receive one .txt processing log."
            )
        except (OSError, ValueError, RuntimeError) as error:
            self.command_text.clear()
            self.status_label.setText(str(error))
        self.execute_button.setEnabled(self.plan is not None)
        self.copy_button.setEnabled(self.plan is not None)

    def _copy(self):
        if self.plan is not None:
            QApplication.clipboard().setText(self.command_text.toPlainText())

    def _execute(self, checked=False, *, verify_only=False):
        self._refresh()
        if self.plan is None:
            return
        if not verify_only and (
            max(self.plan.width, self.plan.height) > 8192
            or self.plan.width * self.plan.height > 33_177_600
        ):
            answer = QMessageBox.warning(
                self, "Large export resolution",
                f"The scaled export is {self.plan.width}x{self.plan.height} "
                f"({self.plan.width * self.plan.height / 1_000_000:.1f} megapixels per frame).\n"
                "Export and verification may require substantial memory, disk space and time. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.worker = ExportWorker(
            self.plan, self.metadata, self, verify_only=verify_only,
            detailed_diagnostics=self.diagnostics_check.isChecked(),
        )
        for widget in (
            self.format_combo, self.selection_combo, self.first_spin, self.last_spin,
            self.path_edit, self.browse_button, self.execute_button,
            self.diagnostics_check,
        ):
            widget.setEnabled(False)
        self.cancel_button.setText("Cancel")
        self.progress_bar.setValue(0)
        self.worker.progressChanged.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _progress(self, percent, message):
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)

    def _finished(self):
        worker = self.worker
        self.worker = None
        if worker.cancelled:
            message = "Cancelled. Partial output is NOT VERIFIED; no processing log created. Choose a new name to retry."
        elif worker.error is not None:
            message = "Export/verification failed: " + worker.error
            QMessageBox.warning(self, "Export failed", message)
        else:
            message = (
                "VERIFIED lossless: every output frame matches the export processing pipeline."
                if worker.plan.lossless else
                "Lossy comparison complete: reference processing matches the export pipeline; output pixel differences are in the log."
            )
            message += f"\nProcessing log: {worker.plan.log_path}"
            if worker.detailed_diagnostics:
                message += f"\nDetailed diagnostics: {worker.plan.report_path}"
            if worker.plan.kind != "png":
                message += "\nAll selected frames and speed-scaled timing/duration verified."
        worker.deleteLater()
        self.format_combo.setEnabled(True)
        self.selection_combo.setEnabled(True)
        self.path_edit.setEnabled(True)
        self.browse_button.setEnabled(True)
        self.diagnostics_check.setEnabled(True)
        self.cancel_button.setText("Close")
        self.cancel_button.setEnabled(True)
        self._refresh()
        self.status_label.setText(message)

    def reject(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling; waiting for FFmpeg to stop. Partial files will be retained.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            event.accept()
