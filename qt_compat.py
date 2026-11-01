# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Qt compatibility layer for PyQt6."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView, QFrame, QSlider


class QtCompat:
    """Provides PyQt6 enums in a unified interface."""
    
    AlignCenter = Qt.AlignmentFlag.AlignCenter
    Horizontal = Qt.Orientation.Horizontal
    Vertical = Qt.Orientation.Vertical
    ScrollBarAlwaysOff = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    ScrollBarAlwaysOn = Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    SmoothTransformation = Qt.TransformationMode.SmoothTransformation
    FastTransformation = Qt.TransformationMode.FastTransformation
    DashLine = Qt.PenStyle.DashLine
    SolidLine = Qt.PenStyle.SolidLine
    RoundCap = Qt.PenCapStyle.RoundCap
    white = Qt.GlobalColor.white
    black = Qt.GlobalColor.black
    transparent = Qt.GlobalColor.transparent
    WindowModal = Qt.WindowModality.WindowModal
    LeftButton = Qt.MouseButton.LeftButton
    ResizeToContents = QHeaderView.ResizeMode.ResizeToContents
    Fixed = QHeaderView.ResizeMode.Fixed
    Stretch = QHeaderView.ResizeMode.Stretch
    Panel = QFrame.Shape.Panel
    Sunken = QFrame.Shadow.Sunken
    Raised = QFrame.Shadow.Raised
    TicksBelow = QSlider.TickPosition.TicksBelow
    PassThrough = Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
