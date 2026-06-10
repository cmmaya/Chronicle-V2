from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QToolButton,
    QHBoxLayout,
    QSizePolicy,
    QStyleOptionButton,
    QStyle,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QBrush,
    QFont,
)


# =========================
# Palette
# =========================

NAVY = QColor("#061946")
NAVY_INNER = QColor("#071D52")

BORDER_BLUE = QColor("#254D9C")
BORDER_BLUE_LIGHT = QColor("#3A67C7")

BUTTON_BLUE = QColor("#274F9B")
BUTTON_BLUE_HOVER = QColor("#315DB1")
BUTTON_BLUE_PRESSED = QColor("#1E3F82")

CREAM = QColor("#F6E0A6")
CREAM_BORDER = QColor("#FFEFC1")

BUBBLE_BLUE = QColor("#294F9D")
BUBBLE_BLUE_BORDER = QColor("#3765BD")

TEXT_LIGHT = QColor("#FFF0BF")
TEXT_DARK = QColor("#071846")


# =========================
# Pixel geometry helpers
# =========================

def pixel_round_rect_path(x: int, y: int, w: int, h: int, cut: int = 10) -> QPainterPath:
    """
    Rectángulo con esquinas pixeladas.
    No usa curvas; todo son segmentos rectos.
    """
    path = QPainterPath()
    path.moveTo(x + cut, y)
    path.lineTo(x + w - cut, y)
    path.lineTo(x + w, y + cut)
    path.lineTo(x + w, y + h - cut)
    path.lineTo(x + w - cut, y + h)
    path.lineTo(x + cut, y + h)
    path.lineTo(x, y + h - cut)
    path.lineTo(x, y + cut)
    path.closeSubpath()
    return path


# =========================
# Panels
# =========================

class PixelPanel(QWidget):
    """
    Panel plano con borde pixelado, sin sombra ni bisel.
    Úsalo para sidebar, workspace, panel de transcripciones y cajas internas.
    """

    def __init__(self, parent=None, inner: bool = False):
        super().__init__(parent)
        self.inner = inner
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        rect = self.rect().adjusted(2, 2, -3, -3)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        # Fase 1: borde único, sin doble contorno interno.
        # El panel interno conserva la misma geometría, pero usa un trazo más ligero
        # para que se parezca al mockup de referencia y no genere efecto de bisel.
        cut = 9 if not self.inner else 7
        fill = NAVY_INNER if self.inner else NAVY
        border = BORDER_BLUE_LIGHT if self.inner else BORDER_BLUE
        pen_width = 2 if not self.inner else 2

        path = pixel_round_rect_path(rect.x(), rect.y(), rect.width(), rect.height(), cut)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, pen_width))
        painter.drawPath(path)

        super().paintEvent(event)


# =========================
# Titles
# =========================

class PixelSectionTitle(QLabel):
    def __init__(self, text: str, parent=None, center: bool = False):
        super().__init__(text, parent)
        self.setObjectName("PixelSectionTitle")
        self.setAlignment(Qt.AlignCenter if center else Qt.AlignLeft)

        font = QFont("Courier New")
        font.setPointSize(11)
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        self.setFont(font)

        self.setStyleSheet(
            """
            QLabel#PixelSectionTitle {
                color: #FFE9A8;
                background: transparent;
                border: none;
            }
            """
        )


# =========================
# Buttons
# =========================

class PixelButton(QPushButton):
    """
    Botón pixelado plano.
    Acepta sidebar=True para compatibilidad con MainWindow.
    """

    def __init__(self, text: str = "", parent=None, sidebar: bool = False):
        super().__init__(text, parent)
        self.sidebar = sidebar
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("PixelButton")

        if sidebar:
            self.setMinimumHeight(52)
        else:
            self.setMinimumHeight(42)

        font = QFont("Courier New")
        font.setPointSize(12 if sidebar else 10)
        font.setBold(True)
        self.setFont(font)

        self.setStyleSheet(
            """
            QPushButton#PixelButton {
                color: #FFF0BF;
                background: #274F9B;
                border: 2px solid #3A67C7;
                border-radius: 7px;
                padding: 7px 12px;
                text-align: left;
            }

            QPushButton#PixelButton:hover {
                background: #315DB1;
            }

            QPushButton#PixelButton:pressed {
                background: #1E3F82;
                padding-top: 10px;
                padding-left: 16px;
            }

            QPushButton#PixelButton[iconOnly="true"] {
                text-align: center;
                padding: 6px;
            }

            QPushButton#PixelButton[iconOnly="true"]:pressed {
                padding: 7px 5px 5px 7px;
            }

            QPushButton#PixelButton:disabled {
                color: #8090B8;
                background: #18336F;
                border: 2px solid #284B94;
            }
            """
        )


class PixelToolButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(QSize(52, 52))
        self.setIconSize(QSize(28, 28))
        self.setObjectName("PixelToolButton")

        font = QFont("Courier New")
        font.setPointSize(11)
        font.setBold(True)
        self.setFont(font)

        self.setStyleSheet(
            """
            QToolButton#PixelToolButton {
                color: #FFF0BF;
                background: #274F9B;
                border: 2px solid #3A67C7;
                border-radius: 7px;
                padding: 6px;
            }

            QToolButton#PixelToolButton:hover {
                background: #315DB1;
            }

            QToolButton#PixelToolButton:pressed {
                background: #1E3F82;
            }

            QToolButton#PixelToolButton:disabled {
                color: #8090B8;
                background: #18336F;
                border: 2px solid #284B94;
            }
            """
        )


# =========================
# Chat bubbles
# =========================

class PixelBubble(QWidget):
    """
    Burbuja plana tipo mockup 2:
    sin bisel, sin sombra, con esquinas pixeladas y cola pixelada.
    """

    def __init__(
        self,
        text: str,
        variant: str = "blue",
        tail: str = "left",
        max_width: int = 520,
        parent=None,
    ):
        super().__init__(parent)
        self.variant = variant
        self.tail = tail
        self.cut = 7
        self.tail_size = 13

        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setMaximumWidth(max_width)

        font = QFont("Courier New")
        font.setPointSize(12)
        font.setBold(True)
        self.label.setFont(font)
        self.label.setAlignment(Qt.AlignCenter)

        if variant == "cream":
            self.label.setStyleSheet(
                """
                QLabel {
                    color: #071846;
                    background: transparent;
                    border: none;
                }
                """
            )
        else:
            self.label.setStyleSheet(
                """
                QLabel {
                    color: #FFF0BF;
                    background: transparent;
                    border: none;
                }
                """
            )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 14, 22, 16)
        layout.setSpacing(0)
        layout.addWidget(self.label)

    def _bubble_path(self) -> QPainterPath:
        rect = self.rect().adjusted(1, 1, -3, -3)

        if self.tail == "left":
            body = rect.adjusted(self.tail_size, 0, 0, 0)
        else:
            body = rect.adjusted(0, 0, -self.tail_size, 0)

        path = pixel_round_rect_path(body.x(), body.y(), body.width(), body.height(), self.cut)
        tail_y = body.bottom() - 14

        if self.tail == "left":
            path.moveTo(body.left(), tail_y)
            path.lineTo(body.left() - self.tail_size, tail_y + 7)
            path.lineTo(body.left(), tail_y + 11)
            path.closeSubpath()
        else:
            path.moveTo(body.right(), tail_y)
            path.lineTo(body.right() + self.tail_size, tail_y + 7)
            path.lineTo(body.right(), tail_y + 11)
            path.closeSubpath()

        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        if self.variant == "cream":
            fill = CREAM
            border = CREAM_BORDER
        else:
            fill = BUBBLE_BLUE
            border = BUBBLE_BLUE_BORDER

        path = self._bubble_path()
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, 2))
        painter.drawPath(path)

        super().paintEvent(event)


def aligned_bubble(text: str, variant: str = "blue", align: str = "left", max_width: int = 520) -> QWidget:
    """
    Retorna una fila con burbuja alineada.
    MainWindow ya usa esta función para chat y transcripts.
    """
    row = QWidget()
    row.setAttribute(Qt.WA_StyledBackground, False)
    row.setAutoFillBackground(False)

    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    tail = "right" if align == "right" else "left"
    bubble = PixelBubble(text=text, variant=variant, tail=tail, max_width=max_width)

    if align == "right":
        layout.addStretch(1)
        layout.addWidget(bubble, 0, Qt.AlignRight)
    else:
        layout.addWidget(bubble, 0, Qt.AlignLeft)
        layout.addStretch(1)

    return row
