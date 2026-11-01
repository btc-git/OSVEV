# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Generate and save the OSVEV magnifying glass icon."""

from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor, QIcon
from PyQt6.QtWidgets import QApplication
from qt_compat import QtCompat
from PyQt6.QtCore import QPoint
import sys

def create_and_save_icon(output_path='osvev_icon.ico'):
    """Create magnifying glass icon and save as ICO file."""
    # Need QApplication for QPixmap
    app = QApplication.instance() or QApplication(sys.argv)
    
    icon_size = 256  # Larger for better quality in ico
    pixmap = QPixmap(icon_size, icon_size)
    pixmap.fill(QtCompat.transparent)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Draw magnifying glass lens (circle)
    lens_color = QColor(50, 150, 255, 220)
    painter.setBrush(lens_color)
    painter.setPen(QPen(QtCompat.black, 4))
    lens_radius = int(icon_size * 0.4)
    lens_center = QPoint(int(icon_size * 0.4), int(icon_size * 0.4))
    painter.drawEllipse(lens_center, lens_radius, lens_radius)
    
    # Draw handle
    handle_width = int(icon_size * 0.15)
    painter.setPen(QPen(QtCompat.black, handle_width, QtCompat.SolidLine, QtCompat.RoundCap))
    handle_start = QPoint(int(lens_center.x() + lens_radius * 0.7), int(lens_center.y() + lens_radius * 0.7))
    handle_end = QPoint(int(icon_size * 0.85), int(icon_size * 0.85))
    painter.drawLine(handle_start, handle_end)
    
    # Draw cross inside lens
    painter.setPen(QPen(QtCompat.black, 2, QtCompat.SolidLine))
    cross_size = int(lens_radius * 0.4)
    painter.drawLine(lens_center.x() - cross_size, lens_center.y(), lens_center.x() + cross_size, lens_center.y())
    painter.drawLine(lens_center.x(), lens_center.y() - cross_size, lens_center.x(), lens_center.y() + cross_size)
    
    painter.end()
    
    # Save as ICO
    pixmap.save(output_path, 'ICO')
    print(f"Icon saved to {output_path}")

if __name__ == '__main__':
    create_and_save_icon()
