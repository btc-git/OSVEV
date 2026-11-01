# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
"""Magnifier label widget for inspecting video frames."""

from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import QRect, QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QColor

from qt_compat import QtCompat


class MagnifierLabel(QLabel):
    """A label widget that displays a frame with a draggable magnifying lens."""

    framePainted = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lens_rect = QRect(100, 100, 100, 100)
        self.dragging = False
        self.drag_offset = QPoint()
        self.click_centered = False
        self._press_pos = None
        self._mouse_moved = False
        self.setMouseTracking(True)
        self.pixmap = None
        self.presentation_token = None
        self._painted_token = None

    def image_size(self):
        """Displayed image size in widget coordinates; the pixmap may be high-DPI."""
        if self.pixmap is None or self.pixmap.isNull():
            return QSize()
        size = self.pixmap.deviceIndependentSize()
        return QSize(round(size.width()), round(size.height()))
        
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        
        # Draw dashed orange rectangle for lens outline
        pen = QPen(QColor(255, 165, 0))
        pen.setWidth(2)
        pen.setStyle(QtCompat.DashLine)
        painter.setPen(pen)
        painter.drawRect(self.lens_rect)
        
        # Set clipping rect for crosshair
        painter.setClipRect(self.lens_rect)
        
        # Draw center point crosshair
        center_x = self.lens_rect.x() + self.lens_rect.width() // 2
        center_y = self.lens_rect.y() + self.lens_rect.height() // 2
        
        pen.setColor(QtCompat.white)
        pen.setWidth(1)
        pen.setStyle(QtCompat.SolidLine)
        painter.setPen(pen)
        painter.drawLine(center_x - 3, center_y, center_x + 3, center_y)
        painter.drawLine(center_x, center_y - 3, center_x, center_y + 3)
        
        painter.setClipping(False)
        painter.end()
        if self.presentation_token is not None and self.presentation_token != self._painted_token:
            self._painted_token = self.presentation_token
            self.framePainted.emit(self.presentation_token)
    
    def mousePressEvent(self, event):
        if event.button() == QtCompat.LeftButton:
            self._press_pos = None
            self._mouse_moved = False
            if (self.pixmap is None or self.pixmap.isNull()
                    or not QRect(QPoint(), self.image_size()).contains(event.pos())):
                return
            self._press_pos = event.pos()
            if self.lens_rect.contains(event.pos()):
                self.dragging = True
                self.drag_offset = event.pos() - self.lens_rect.topLeft()
                
    def mouseMoveEvent(self, event):
        if self._press_pos is not None and event.pos() != self._press_pos:
            self._mouse_moved = True
        if self.dragging:
            if self.pixmap and not self.pixmap.isNull():
                new_pos = event.pos() - self.drag_offset
                half_width = self.lens_rect.width() // 2
                half_height = self.lens_rect.height() // 2
                image_size = self.image_size()
                new_pos.setX(max(0, min(new_pos.x() + half_width,
                                        image_size.width() - 1)) - half_width)
                new_pos.setY(max(0, min(new_pos.y() + half_height,
                                        image_size.height() - 1)) - half_height)

                self.click_centered = True
                self.lens_rect.moveTopLeft(new_pos)
                self.update()
                
                # Update zoomed view
                main_window = self.window()
                if hasattr(main_window, 'update_zoomed_view'):
                    main_window.update_zoomed_view()
    
    def mouseReleaseEvent(self, event):
        if event.button() == QtCompat.LeftButton:
            self.dragging = False
            if (self._press_pos is not None and not self._mouse_moved
                    and event.pos() == self._press_pos
                    and self.pixmap is not None and not self.pixmap.isNull()
                    and QRect(QPoint(), self.image_size()).contains(event.pos())):
                self.lens_rect.moveTopLeft(event.pos() - QPoint(
                    self.lens_rect.width() // 2, self.lens_rect.height() // 2
                ))
                self.click_centered = True
                self.update()
                main_window = self.window()
                if hasattr(main_window, 'update_zoomed_view'):
                    main_window.update_zoomed_view()
            self._press_pos = None
