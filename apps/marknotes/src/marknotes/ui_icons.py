"""Small, theme-colored SVG outline icons for toolbars and window controls."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Original 24-unit drawings share their stroke, cap and join treatment. Keeping
# the SVG in code allows palette colors without temporary files or raster assets.
_SHAPES = {
    "new": (
        '<path d="M14 3.5H6a1.5 1.5 0 0 0-1.5 1.5v14A1.5 1.5 0 0 0 6 20.5h12'
        'a1.5 1.5 0 0 0 1.5-1.5V9L14 3.5Z"/>'
        '<path d="M14 3.5V9h5.5M12 12v5M9.5 14.5h5"/>'
    ),
    "open": (
        '<path d="M3.5 10V6a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8'
        'a1.5 1.5 0 0 1 1.5 1.5v2"/>'
        '<path d="M3.5 10h18l-2.2 8.4a1.5 1.5 0 0 1-1.5 1.1H5'
        'A1.5 1.5 0 0 1 3.5 18V10Z"/>'
    ),
    "save": (
        '<path d="M15.8 3.5H5A1.5 1.5 0 0 0 3.5 5v14A1.5 1.5 0 0 0 5 20.5h14'
        'a1.5 1.5 0 0 0 1.5-1.5V8.2L15.8 3.5Z"/>'
        '<path d="M7.5 3.5V9h8V3.5M7.5 20.5V14h9v6.5"/>'
    ),
    "undo": '<path d="m9 5-5 5 5 5M4 10h9a7 7 0 0 1 7 7v2"/>',
    "redo": '<path d="m15 5 5 5-5 5M20 10h-9a7 7 0 0 0-7 7v2"/>',
    "source": '<path d="m8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16"/>',
    "preview": (
        '<path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5'
        '-9.5-6.5-9.5-6.5Z"/><circle cx="12" cy="12" r="2.7"/>'
    ),
    "split": '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M12 4.5v15"/>',
    "minimize": '<path d="M6 12h12"/>',
    "maximize": '<rect x="5.5" y="5.5" width="13" height="13" rx="1.2"/>',
    "restore": (
        '<path d="M9 5.5h8.5a1 1 0 0 1 1 1V15"/>'
        '<rect x="5.5" y="9" width="9.5" height="9.5" rx="1"/>'
    ),
    "close": '<path d="m6.5 6.5 11 11m0-11-11 11"/>',
}


class _OutlineEngine(QIconEngine):
    def __init__(self, name: str, color: QColor):
        super().__init__()
        self._name = name
        self._color = QColor(color)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
            f'<g fill="none" stroke="{color.name()}" stroke-opacity="{color.alphaF():.6f}" '
            'stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round">'
            f"{_SHAPES[name]}</g></svg>"
        )
        self._renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))

    def clone(self) -> QIconEngine:
        return _OutlineEngine(self._name, self._color)

    def key(self) -> str:
        return "marknotes-outline"

    def iconName(self) -> str:
        return self._name

    def isNull(self) -> bool:
        return not self._renderer.isValid()

    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:
        side = min(size.width(), size.height())
        return QSize(side, side)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(
        self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float
    ) -> QPixmap:
        logical = self.actualSize(size, mode, state)
        if logical.isEmpty() or scale <= 0:
            return QPixmap()
        pixmap = QPixmap(logical * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if mode == QIcon.Mode.Disabled:
            # Opacity stays distinct on both light and dark document windows.
            painter.setOpacity(0.4)
        self._renderer.render(painter, QRectF(0, 0, logical.width(), logical.height()))
        painter.end()
        return pixmap

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        device = painter.device()
        scale = device.devicePixelRatioF() if device is not None else 1.0
        size = self.actualSize(rect.size(), mode, state)
        target = QRect(
            rect.x() + (rect.width() - size.width()) // 2,
            rect.y() + (rect.height() - size.height()) // 2,
            size.width(),
            size.height(),
        )
        painter.drawPixmap(target, self.scaledPixmap(size, mode, state, scale))


def outline_icon(name: str, color: QColor | str) -> QIcon:
    """Return a scalable 24-unit icon in the requested foreground color.

    Names: new, open, save, undo, redo, source, preview, split, minimize, maximize, restore,
    close. Recreate icons with the new foreground color after a theme change.
    """
    if name not in _SHAPES:
        raise ValueError(f"Unknown outline icon: {name}")
    foreground = QColor(color)
    if not foreground.isValid():
        raise ValueError(f"Invalid icon color: {color}")
    return QIcon(_OutlineEngine(name, foreground))
