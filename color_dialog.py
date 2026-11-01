# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""One-time permission to interpret unspecified source color properties."""

from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from color_interpretation import ColorChoices


class ColorAssumptionsDialog(QDialog):
    def __init__(self, reason, parent=None, *, allow_working_copy=False):
        super().__init__(parent)
        self.setWindowTitle("Unspecified source color")
        self.setFixedWidth(460)
        layout = QVBoxLayout(self)
        note = QLabel(
            "This source has unspecified color metadata. Open Video uses the assumptions below; "
            "a working copy is optional when direct opening succeeds.\n\n"
            "Selections fill missing fields only; reported values are never overridden. "
            "Missing primaries/transfer are assumed SDR matching the effective matrix; "
            "missing chroma location is assumed left. These are not verified source "
            "properties. Native samples remain unchanged, but displayed colors depend "
            "on these choices. Assumptions are recorded in export logs."
        )
        note.setWordWrap(True)
        note.setToolTip(str(reason))
        layout.addWidget(note)
        form = QFormLayout()
        self.matrix = QComboBox()
        self.matrix.addItems(["BT.601", "BT.709"])
        self.range_combo = QComboBox()
        self.range_combo.addItems(["Limited", "Full"])
        form.addRow("Missing matrix", self.matrix)
        form.addRow("Missing range", self.range_combo)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.working_copy_requested = False
        if allow_working_copy:
            copy = buttons.addButton("Create Compatible Working Copy", QDialogButtonBox.ButtonRole.ActionRole)
            copy.setToolTip(
                "Optional video-only conversion of the entire source, not just a selected time range. "
                "It can be much larger and takes time to verify. It does not establish missing metadata.")
            copy.clicked.connect(self._working_copy)
        buttons.addButton("Open Video", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _working_copy(self):
        self.working_copy_requested = True
        self.accept()

    @staticmethod
    def get_choices(reason, parent=None, *, allow_working_copy=False):
        dialog = ColorAssumptionsDialog(reason, parent, allow_working_copy=allow_working_copy)
        if not dialog.exec():
            return None
        if dialog.working_copy_requested:
            return "working-copy"
        return ColorChoices(
            "smpte170m" if dialog.matrix.currentIndex() == 0 else "bt709",
            "tv" if dialog.range_combo.currentIndex() == 0 else "pc",
        )
