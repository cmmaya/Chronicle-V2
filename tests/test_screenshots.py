import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QPoint, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QGuiApplication, QCursor
from PySide6.QtWidgets import QApplication, QWidget


OUTPUT_DIR = Path("captures")


class SnippingWidget(QWidget):
    def __init__(self):
        super().__init__()

        OUTPUT_DIR.mkdir(exist_ok=True)

        self.start_point = QPoint()
        self.end_point = QPoint()
        self.is_selecting = False

        self.screen = QGuiApplication.screenAt(QCursor.pos())

        if self.screen is None:
            self.screen = QGuiApplication.primaryScreen()

        if self.screen is None:
            raise RuntimeError("No se pudo acceder a la pantalla.")

        self.screen_geometry = self.screen.geometry()

        # La ventana usa coordenadas lógicas.
        self.setGeometry(self.screen_geometry)

        # Captura real de pantalla.
        pixmap = self.screen.grabWindow(0)

        # Convertimos a QImage y neutralizamos el DPR.
        # Esto evita el zoom raro en pantallas con 125%, 150%, etc.
        self.full_image = pixmap.toImage()
        self.full_image.setDevicePixelRatio(1)

        self.image_width = self.full_image.width()
        self.image_height = self.full_image.height()

        self.preview_width = self.screen_geometry.width()
        self.preview_height = self.screen_geometry.height()

        self.scale_x = self.image_width / self.preview_width
        self.scale_y = self.image_height / self.preview_height

        print("Screen geometry lógico:", self.screen_geometry)
        print("Imagen real:", self.image_width, "x", self.image_height)
        print("Preview lógico:", self.preview_width, "x", self.preview_height)
        print("Scale X:", self.scale_x)
        print("Scale Y:", self.scale_y)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )

        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)

        self.showFullScreen()
        self.activateWindow()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)

        # Dibujamos la captura completa ajustada exactamente al tamaño visible.
        # Esto elimina el efecto de zoom.
        painter.drawImage(self.rect(), self.full_image)

        overlay = QColor(0, 0, 0, 120)
        painter.fillRect(self.rect(), overlay)

        if self.is_selecting:
            selection_rect = QRect(self.start_point, self.end_point).normalized()

            # Convertimos el rectángulo lógico seleccionado a coordenadas reales.
            source_rect = self.logical_to_image_rect(selection_rect)

            # Dibujamos la parte seleccionada sin overlay.
            painter.drawImage(selection_rect, self.full_image, source_rect)

            pen = QPen(QColor(255, 255, 255), 2)
            painter.setPen(pen)
            painter.drawRect(selection_rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_point = event.position().toPoint()
            self.end_point = self.start_point
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_point = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.end_point = event.position().toPoint()
            self.is_selecting = False

            selection_rect = QRect(self.start_point, self.end_point).normalized()

            if selection_rect.width() > 5 and selection_rect.height() > 5:
                self.save_crop(selection_rect)

            self.finish()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.finish()

    def logical_to_image_rect(self, rect):
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

    def save_crop(self, logical_rect):
        image_rect = self.logical_to_image_rect(logical_rect)

        cropped = self.full_image.copy(image_rect)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"capture_{timestamp}.png"

        cropped.save(str(output_path), "PNG")

        print(f"Imagen guardada en: {output_path.resolve()}")

    def finish(self):
        self.close()

        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)


def main():
    app = QApplication(sys.argv)

    window = SnippingWidget()
    window.show()

    app.exec()


if __name__ == "__main__":
    main()