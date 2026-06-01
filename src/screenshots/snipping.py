from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QPoint, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QGuiApplication, QCursor, QImage
from PySide6.QtWidgets import QDialog, QRubberBand


class SnippingOverlay(QDialog):
    """
    Dialog de selección de región de pantalla con soporte correcto para HiDPI.
    Utiliza la misma metodología que test_screenshots.py.
    """
    def __init__(self, on_region_selected, output_dir: Path = None, parent=None):
        super().__init__(parent)
        self.on_region_selected = on_region_selected
        self.output_dir = output_dir or Path("captures")
        self.output_dir.mkdir(exist_ok=True)

        self.start_point = QPoint()
        self.end_point = QPoint()
        self.is_selecting = False

        # Obtener la pantalla actual donde está el cursor
        self.screen = QGuiApplication.screenAt(QCursor.pos())

        if self.screen is None:
            self.screen = QGuiApplication.primaryScreen()

        if self.screen is None:
            raise RuntimeError("No se pudo acceder a la pantalla.")

        self.screen_geometry = self.screen.geometry()

        # Captura real de pantalla
        pixmap = self.screen.grabWindow(0)

        # Convertimos a QImage y neutralizamos el DPR
        # Esto evita el zoom raro en pantallas con 125%, 150%, etc.
        self.full_image = pixmap.toImage()
        self.full_image.setDevicePixelRatio(1)

        self.image_width = self.full_image.width()
        self.image_height = self.full_image.height()

        self.preview_width = self.screen_geometry.width()
        self.preview_height = self.screen_geometry.height()

        self.scale_x = self.image_width / self.preview_width
        self.scale_y = self.image_height / self.preview_height

        # Usar QRubberBand para mostrar la selección
        self.rubber_band = None
        self.selection_rect = QRect()

        # Window flags para pantalla completa sin bordes
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )

        # Hacer el dialog modal para que bloquee hasta que el usuario seleccione
        self.setModal(True)

        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)

        # La ventana usa coordenadas lógicas
        self.setGeometry(self.screen_geometry)

    def showEvent(self, event):
        """Override para asegurar que el dialog se muestre en pantalla completa."""
        super().showEvent(event)
        self.showFullScreen()
        self.activateWindow()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)

        # Dibujamos la captura completa ajustada al tamaño visible
        painter.drawImage(self.rect(), self.full_image)

        # Overlay oscuro
        overlay = QColor(0, 0, 0, 120)
        painter.fillRect(self.rect(), overlay)

        if self.is_selecting:
            selection_rect = QRect(self.start_point, self.end_point).normalized()

            # Convertimos el rectángulo lógico seleccionado a coordenadas reales
            source_rect = self.logical_to_image_rect(selection_rect)

            # Dibujamos la parte seleccionada sin overlay
            painter.drawImage(selection_rect, self.full_image, source_rect)

            pen = QPen(QColor(255, 255, 255), 2)
            painter.setPen(pen)
            painter.drawRect(selection_rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_point = event.position().toPoint()
            self.end_point = self.start_point
            self.is_selecting = True

            # Crear rubber band
            self.rubber_band = QRubberBand(QRubberBand.Rectangle, self)
            self.rubber_band.setGeometry(QRect(self.start_point, self.end_point))
            self.rubber_band.show()

            self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_point = event.position().toPoint()
            self.rubber_band.setGeometry(QRect(self.start_point, self.end_point).normalized())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.end_point = event.position().toPoint()
            self.is_selecting = False

            if self.rubber_band:
                self.rubber_band.hide()

            selection_rect = QRect(self.start_point, self.end_point).normalized()

            if selection_rect.width() > 5 and selection_rect.height() > 5:
                # Convertir coordenadas lógicas a coordenadas de imagen real
                image_rect = self.logical_to_image_rect(selection_rect)
                self.on_region_selected(image_rect, self.full_image, self.output_dir)

            self.accept()  # Cerrar el dialog

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()  # Cancelar

    def logical_to_image_rect(self, rect: QRect) -> QRect:
        """
        Convierte coordenadas de la preview visible a coordenadas reales
        de la imagen capturada.
        """
        x = int(rect.x() * self.scale_x)
        y = int(rect.y() * self.scale_y)
        w = int(rect.width() * self.scale_x)
        h = int(rect.height() * self.scale_y)

        x = max(0, min(x, self.image_width - 1))
        y = max(0, min(y, self.image_height - 1))
        w = max(1, min(w, self.image_width - x))
        h = max(1, min(h, self.image_height - y))

        return QRect(x, y, w, h)

    def save_crop(self, image_rect: QRect, output_dir: Path = None) -> Path:
        """Guarda la región seleccionada como imagen."""
        if output_dir is None:
            output_dir = self.output_dir

        cropped = self.full_image.copy(image_rect)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"capture_{timestamp}.png"

        cropped.save(str(output_path), "PNG")
        return output_path
