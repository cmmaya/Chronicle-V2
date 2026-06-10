"""Reusable pixel-styled widgets for Chronicle."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap, QTransform
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .pixel_theme import COLORS, asset_path


def _hard_shadow(widget: QWidget, *, dx: int = 5, dy: int = 5, color: str | None = None) -> None:
    """Attach a hard, non-blurred pixel-art drop shadow."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(0)
    effect.setOffset(dx, dy)
    effect.setColor(QColor(color or COLORS["blue_bubble_shadow"]))
    widget.setGraphicsEffect(effect)


def _asset_exists(filename: str) -> bool:
    return Path(asset_path(filename)).exists()


def _asset_pixmap(filename: str) -> QPixmap:
    return QPixmap(asset_path(filename))


def _set_rotated_pixmap(label: QLabel, base: QPixmap, degrees: int) -> None:
    if degrees:
        label.setPixmap(base.transformed(QTransform().rotate(degrees), Qt.SmoothTransformation))
    else:
        label.setPixmap(base)


class PixelPanel(QFrame):
    """A bordered panel used for the main Chronicle columns.

    If panel_corner_deco.svg exists, the panel automatically places the same
    corner marker in all four corners. The labels are decorative only and do
    not affect child layouts.
    """

    def __init__(self, parent=None, *, inner: bool = False, decorated: bool | None = None):
        super().__init__(parent)
        self._inner = inner
        self._corner_labels: list[QLabel] = []
        self.setObjectName("PixelPanelInner" if inner else "PixelPanel")
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)
        if not inner:
            _hard_shadow(self, dx=4, dy=4, color="#031033")

        should_decorate = (not inner) if decorated is None else decorated
        if should_decorate and _asset_exists("panel_corner_deco.svg"):
            base = _asset_pixmap("panel_corner_deco.svg")
            rotations = (0, 90, 270, 180)
            for degrees in rotations:
                label = QLabel(self)
                label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                label.setFixedSize(18, 18)
                label.setScaledContents(True)
                _set_rotated_pixmap(label, base, degrees)
                label.raise_()
                self._corner_labels.append(label)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if len(self._corner_labels) != 4:
            return
        pad = 6
        w = self.width()
        h = self.height()
        size = 18
        positions = (
            (pad, pad),
            (w - size - pad, pad),
            (pad, h - size - pad),
            (w - size - pad, h - size - pad),
        )
        for label, (x, y) in zip(self._corner_labels, positions):
            label.move(max(0, x), max(0, y))


class PixelSectionTitle(QLabel):
    """Uppercase section title matching the mockup."""

    def __init__(self, text: str, parent=None, *, center: bool = False):
        super().__init__(text.upper(), parent)
        self.setObjectName("SectionTitle")
        self.setAlignment(Qt.AlignCenter if center else Qt.AlignLeft | Qt.AlignVCenter)


class PixelButton(QPushButton):
    """Standard pixel button."""

    def __init__(self, text: str = "", parent=None, *, sidebar: bool = False, icon_only: bool = False):
        super().__init__(text, parent)
        if sidebar:
            self.setObjectName("SidebarButton")
        elif icon_only:
            self.setObjectName("IconButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)


class PixelToolButton(QToolButton):
    """Icon-style pixel tool button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)


class PixelBubble(QFrame):
    """Pixel chat bubble with blocky body, hard shadow, and square tail.

    variant: 'cream' or 'blue'.
    tail: 'left' or 'right'.
    """

    def __init__(self, text: str, parent=None, *, variant: str = "blue", tail: str = "left", max_width: int = 520):
        super().__init__(parent)
        self.variant = variant
        self.tail = tail
        self.setMaximumWidth(max_width)
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.body = QFrame(self)
        self.body.setFrameShape(QFrame.NoFrame)
        self.body.setAttribute(Qt.WA_StyledBackground, True)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(20, 14, 20, 14)
        body_layout.setSpacing(0)

        self.label = QLabel(text, self.body)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body_layout.addWidget(self.label)
        outer.addWidget(self.body)

        tail_row = QHBoxLayout()
        tail_row.setContentsMargins(0, 0, 0, 0)
        tail_row.setSpacing(0)

        tail_asset_name = f"bubble_tail_{variant}_{tail}.svg"
        self.tail_uses_asset = _asset_exists(tail_asset_name)
        self.tail_asset_label: QLabel | None = None

        if self.tail_uses_asset:
            self.tail_block = QLabel(self)
            self.tail_block.setFixedSize(18, 18)
            self.tail_block.setScaledContents(True)
            self.tail_block.setPixmap(_asset_pixmap(tail_asset_name))
            self.tail_asset_label = self.tail_block
        else:
            self.tail_block = QFrame(self)
            self.tail_block.setFixedSize(18, 10)
            self.tail_block.setAttribute(Qt.WA_StyledBackground, True)

        self.tail_shadow = QFrame(self)
        self.tail_shadow.setFixedSize(10, 10)
        self.tail_shadow.setAttribute(Qt.WA_StyledBackground, True)

        if tail == "right":
            tail_row.addStretch()
            tail_row.addWidget(self.tail_block)
            tail_row.addWidget(self.tail_shadow)
            tail_row.addSpacing(10)
        else:
            tail_row.addSpacing(10)
            tail_row.addWidget(self.tail_shadow)
            tail_row.addWidget(self.tail_block)
            tail_row.addStretch()
        outer.addLayout(tail_row)

        self._apply_style()

    def _apply_style(self):
        c = COLORS
        if self.variant == "cream":
            bg = c["cream"]
            shadow = c["cream_shadow"]
            fg = c["text_dark"]
            border = c["cream_light"]
        else:
            bg = c["blue_bubble"]
            shadow = c["blue_bubble_shadow"]
            fg = c["text_light"]
            border = c["blue_bubble_light"]

        self.body.setStyleSheet(
            "QFrame {"
            f" background-color: {bg};"
            f" border-top: 3px solid {border};"
            f" border-left: 3px solid {border};"
            f" border-right: 6px solid {shadow};"
            f" border-bottom: 6px solid {shadow};"
            "}"
        )
        if not self.tail_uses_asset:
            self.tail_block.setStyleSheet(f"QFrame {{ background-color: {bg}; border: 0; }}")
        self.tail_shadow.setStyleSheet(f"QFrame {{ background-color: {shadow}; border: 0; }}")
        self.label.setStyleSheet(
            f"QLabel {{ color: {fg}; background: transparent; font-size: 15px; line-height: 135%; font-weight: 800; }}"
        )


def aligned_bubble(text: str, *, variant: str, align: str, max_width: int = 520) -> QWidget:
    """Return a row widget containing an aligned PixelBubble."""
    row = QWidget()
    row.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    bubble = PixelBubble(text, variant=variant, tail="right" if align == "right" else "left", max_width=max_width)
    row.setProperty("bubble", bubble)
    if align == "right":
        layout.addStretch()
        layout.addWidget(bubble)
    else:
        layout.addWidget(bubble)
        layout.addStretch()
    return row
