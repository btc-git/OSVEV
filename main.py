"""OSVEV - Video Analysis Application.

Copyright © 2026 CrimLawTech LLC
SPDX-License-Identifier: GPL-3.0-or-later

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. This program is distributed WITHOUT ANY WARRANTY; see LICENSE.

Main entry point for the application.
"""

import argparse
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from osvev_app import OSVEV
from version import APP_NAME, __version__, version_string


def main():
    """Main application entry point."""
    parser = argparse.ArgumentParser(description="OSVEV teaching viewer")
    parser.add_argument(
        "--decoder", choices=("opencv", "pyav"), default="pyav",
        help="Verified PyAV input (default), or explicitly selected legacy OpenCV; no fallback.",
    )
    parser.add_argument("--version", action="version", version=version_string())
    options, qt_args = parser.parse_known_args()
    # Set high DPI settings BEFORE creating QApplication
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    # Create application
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    
    # Set Fusion style for consistent appearance
    app.setStyle("Fusion")
    
    # Apply light theme palette
    palette = app.palette()
    palette.setColor(palette.ColorRole.Window, QColor(240, 240, 240))
    palette.setColor(palette.ColorRole.WindowText, QColor(0, 0, 0))
    palette.setColor(palette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(palette.ColorRole.AlternateBase, QColor(245, 245, 245))
    palette.setColor(palette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(palette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(palette.ColorRole.Text, QColor(0, 0, 0))
    palette.setColor(palette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(palette.ColorRole.ButtonText, QColor(0, 0, 0))
    palette.setColor(palette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(palette.ColorRole.Highlight, QColor(76, 163, 224))
    palette.setColor(palette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)
    
    # Create and show application
    viewer = OSVEV(decoder_backend=options.decoder)
    viewer.show()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
