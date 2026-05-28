from PySide6.QtWidgets import QWidget, QRubberBand
from PySide6.QtCore import Qt, QRect


class SnippingOverlay(QWidget):
    def __init__(self, on_region_selected):
        super().__init__()
        self.on_region_selected = on_region_selected
        self.origin = None
        self.rubber_band = None

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.showFullScreen()

    def mousePressEvent(self, event):
        self.origin = event.position().toPoint()
        self.rubber_band = QRubberBand(QRubberBand.Rectangle, self)
        self.rubber_band.setGeometry(
            QRect(self.origin, event.position().toPoint()).normalized()
        )
        self.rubber_band.show()

    def mouseMoveEvent(self, event):
        if self.rubber_band is not None:
            self.rubber_band.setGeometry(
                QRect(self.origin, event.position().toPoint()).normalized()
            )

    def mouseReleaseEvent(self, event):
        if self.rubber_band is not None:
            self.rubber_band.hide()
            rect = QRect(self.origin, event.position().toPoint()).normalized()
            self.close()
            self.on_region_selected(rect.x(), rect.y(), rect.width(), rect.height())
