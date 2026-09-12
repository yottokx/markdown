"""A compact title row with Qt-managed native move, resize and window states."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QAction, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .ui_icons import outline_icon


class _ResizeGrip(QWidget):
    def __init__(self, window: ChromeMainWindow, edges: Qt.Edges, cursor: Qt.CursorShape):
        super().__init__(window)
        self.owner = window
        self.edges = edges
        self.setCursor(cursor)
        self.setAccessibleName("ウィンドウのサイズ変更")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.owner.windowHandle()
            if handle is not None and handle.startSystemResize(self.edges):
                event.accept()
                return
        super().mousePressEvent(event)


class TitleBar(QWidget):
    def __init__(self, window: ChromeMainWindow):
        super().__init__(window)
        self.owner = window
        self._dark = False
        self._title = ""
        self._drag_start: QPoint | None = None
        self.setObjectName("applicationTitleBar")
        self.setFixedHeight(36)
        self.file_buttons: list[QToolButton] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 0, 0)
        row.setSpacing(0)
        self.left_group = QWidget(self)
        self.left_layout = QHBoxLayout(self.left_group)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(3)
        self.app_icon = QLabel(self.left_group)
        self.app_icon.setFixedSize(24, 28)
        self.app_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.app_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.left_layout.addWidget(self.app_icon)
        row.addWidget(self.left_group)
        row.addStretch(1)
        self.controls = QWidget(self)
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        self.minimize_button = self._control("最小化", window.showMinimized)
        self.maximize_button = self._control("最大化", self.toggle_maximized)
        self.close_button = self._control("閉じる", window.close)
        self.close_button.setObjectName("windowCloseButton")
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            controls.addWidget(button)
        row.addWidget(self.controls)
        self.title_label = QLabel(self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        window.windowTitleChanged.connect(self.set_title)
        window.windowIconChanged.connect(lambda _icon: self.refresh_app_icon())
        self.apply_theme(False)
        self.refresh_app_icon()

    def _control(self, label, callback):
        button = QToolButton(self.controls)
        button.setFixedSize(44, 36)
        button.setIconSize(QSize(18, 18))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(callback)
        return button

    def set_file_actions(self, actions: list[QAction]) -> None:
        for button in self.file_buttons:
            self.left_layout.removeWidget(button)
            button.deleteLater()
        self.file_buttons.clear()
        for action in actions:
            button = QToolButton(self.left_group)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setFixedSize(32, 28)
            button.setIconSize(QSize(18, 18))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.left_layout.addWidget(button)
            self.file_buttons.append(button)
        self.layout().activate()
        self._position_title()

    def set_title(self, title: str) -> None:
        self._title = title
        self.title_label.setToolTip(title)
        self._position_title()

    def _position_title(self):
        # Equal left/right exclusion keeps the filename at the window's center.
        inset = max(self.left_group.sizeHint().width() + 16, self.controls.sizeHint().width() + 8)
        width = max(0, self.width() - 2 * inset)
        self.title_label.setGeometry(inset, 0, width, self.height())
        self.title_label.setText(
            self.title_label.fontMetrics().elidedText(
                self._title, Qt.TextElideMode.ElideMiddle, width
            )
        )

    def refresh_app_icon(self):
        icon = self.owner.windowIcon()
        if icon.isNull():
            icon = QApplication.windowIcon()
        self.app_icon.setPixmap(icon.pixmap(QSize(16, 16), self.devicePixelRatioF()))

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        foreground = "#e1e7ef" if dark else "#253047"
        background = "#20252d" if dark else "#f5f7fa"
        hover = "#384454" if dark else "#e0e7f0"
        pressed = "#46556a" if dark else "#cbd7e7"
        self.setStyleSheet(
            f"#applicationTitleBar {{ background: {background}; color: {foreground}; }}"
            f"#applicationTitleBar QLabel {{ color: {foreground}; background: transparent;"
            " font-size: 10pt; font-weight: 300; }"
            "#applicationTitleBar QToolButton { border: 0; background: transparent; padding: 0; }"
            f"#applicationTitleBar QToolButton:hover {{ background: {hover}; }}"
            f"#applicationTitleBar QToolButton:pressed {{ background: {pressed}; }}"
            "#applicationTitleBar QToolButton#windowCloseButton:hover { background: #c42b1c; }"
            "#applicationTitleBar QToolButton#windowCloseButton:pressed { background: #a82217; }"
        )
        self.minimize_button.setIcon(outline_icon("minimize", foreground))
        self.close_button.setIcon(outline_icon("close", foreground))
        self.update_window_state()

    def update_window_state(self):
        maximized = self.owner.isMaximized()
        name = "restore" if maximized else "maximize"
        label = "元に戻す" if maximized else "最大化"
        self.maximize_button.setIcon(outline_icon(name, "#e1e7ef" if self._dark else "#253047"))
        self.maximize_button.setToolTip(label)
        self.maximize_button.setAccessibleName(label)

    def toggle_maximized(self):
        if self.owner.isMaximized():
            self.owner.showNormal()
        else:
            self.owner.showMaximized()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.position().toPoint() - self._drag_start).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._drag_start = None
                handle = self.owner.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_title()


class ChromeMainWindow(QMainWindow):
    """Keep menuBar() stable while placing a title row above the original menu.

    Qt 6.11's Windows backend constrains frameless maximize to the monitor work
    area. Dragging and resizing are delegated to the platform's system operations.
    """

    RESIZE_BORDER = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.chrome_header = QWidget(self)
        header_layout = QVBoxLayout(self.chrome_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        self.title_bar = TitleBar(self)
        self._chrome_menu = QMenuBar(self.chrome_header)
        header_layout.addWidget(self.title_bar)
        header_layout.addWidget(self._chrome_menu)
        self.setMenuWidget(self.chrome_header)
        edge = Qt.Edge
        self.resize_grips = [
            _ResizeGrip(self, edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            _ResizeGrip(self, edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            _ResizeGrip(self, edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            _ResizeGrip(self, edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            _ResizeGrip(self, edge.LeftEdge | edge.TopEdge, Qt.CursorShape.SizeFDiagCursor),
            _ResizeGrip(self, edge.RightEdge | edge.TopEdge, Qt.CursorShape.SizeBDiagCursor),
            _ResizeGrip(self, edge.LeftEdge | edge.BottomEdge, Qt.CursorShape.SizeBDiagCursor),
            _ResizeGrip(self, edge.RightEdge | edge.BottomEdge, Qt.CursorShape.SizeFDiagCursor),
        ]
        self._update_chrome_state()

    def menuBar(self) -> QMenuBar:
        return self._chrome_menu

    def apply_chrome_theme(self, dark: bool) -> None:
        self.title_bar.apply_theme(dark)
        colors = self.palette()
        # Fusion draws a bottom rule under every menu item and empty area.
        # Style both explicitly, preserving mnemonic and keyboard behavior.
        # Resolve colors on every theme change; QSS palette() can retain the
        # previous palette when a window changes theme after being shown.
        self._chrome_menu.setStyleSheet(
            f"QMenuBar {{ background: {colors.window().color().name()};"
            f" color: {colors.windowText().color().name()};"
            " border: none; padding: 0; font-size: 10pt; font-weight: 300; }"
            "QMenuBar::item { background: transparent; border: none; padding: 8px 8px; }"
            f"QMenuBar::item:selected {{ background: {colors.button().color().name()};"
            " border-radius: 4px; }"
            f"QMenuBar::item:pressed {{ background: {colors.highlight().color().name()};"
            f" color: {colors.highlightedText().color().name()}; border-radius: 4px; }}"
        )

    def _update_chrome_state(self):
        border = 0 if self.isMaximized() or self.isFullScreen() else self.RESIZE_BORDER
        self.setContentsMargins(border, border, border, border)
        self.title_bar.update_window_state()
        self._position_grips()

    def _position_grips(self):
        active = not self.isMaximized() and not self.isFullScreen()
        width, height, border = self.width(), self.height(), self.RESIZE_BORDER
        corner = 2 * border
        rectangles = [
            (0, corner, border, max(0, height - 2 * corner)),
            (width - border, corner, border, max(0, height - 2 * corner)),
            (corner, 0, max(0, width - 2 * corner), border),
            (corner, height - border, max(0, width - 2 * corner), border),
            (0, 0, corner, corner),
            (width - corner, 0, corner, corner),
            (0, height - corner, corner, corner),
            (width - corner, height - corner, corner, corner),
        ]
        for grip, rectangle in zip(self.resize_grips, rectangles, strict=True):
            grip.setGeometry(*rectangle)
            grip.setVisible(active)
            grip.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "resize_grips"):
            self._position_grips()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "resize_grips"):
            self._update_chrome_state()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, "title_bar"):
            self.title_bar.refresh_app_icon()
        return result
