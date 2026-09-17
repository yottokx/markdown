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
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .application_profile import DEVELOPMENT_PROFILE
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
        self.setFixedHeight(40)
        self._center_widget: QWidget | None = None
        self.file_buttons: list[QToolButton] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 0, 0)
        row.setSpacing(0)
        self.left_group = QWidget(self)
        self.left_group.setObjectName("titleBarActions")
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
        self.drag_region = QWidget(self)
        self.drag_region.setObjectName("titleBarDragRegion")
        self.drag_region.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drag_region.setAccessibleName("ウィンドウを移動")
        self.drag_region.hide()
        self.controls = QWidget(self)
        self.controls.setObjectName("titleBarControls")
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        self.dev_badge = QLabel("dev", self.controls)
        self.dev_badge.setObjectName("developmentBadge")
        self.dev_badge.setFixedSize(48, 22)
        self.dev_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dev_badge.setAccessibleName("開発版")
        self.dev_badge.setToolTip("MarkNotes 開発版")
        self.dev_badge.setVisible(
            QApplication.applicationName() == DEVELOPMENT_PROFILE.application_name
        )
        controls.addWidget(self.dev_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.minimize_button = self._control("最小化", window.showMinimized)
        self.maximize_button = self._control("最大化", self.toggle_maximized)
        self.close_button = self._control("閉じる", window.close)
        self.close_button.setObjectName("windowCloseButton")
        self.close_button.installEventFilter(self)
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
        button.setObjectName("windowControlButton")
        button.setFixedSize(44, 40)
        button.setIconSize(QSize(18, 18))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(callback)
        return button

    def set_center_widget(self, widget: QWidget, drag_width: int = 112) -> None:
        """Embed tabs while always retaining a usable native window drag target."""
        row = self.layout()
        if self._center_widget is None:
            row.takeAt(1)  # Replace the filename's flexible space.
        elif self._center_widget is not widget:
            row.removeWidget(self._center_widget)
            self._center_widget.setParent(None)
        row.removeWidget(self.drag_region)
        self._center_widget = widget
        widget.setParent(self)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.insertWidget(1, widget, 1)
        self.drag_region.setFixedWidth(max(0, drag_width))
        row.insertWidget(2, self.drag_region)
        self.drag_region.show()
        self.title_label.hide()
        widget.show()

    def set_file_actions(self, actions: list[QAction]) -> None:
        for button in self.file_buttons:
            self.left_layout.removeWidget(button)
            button.deleteLater()
        self.file_buttons.clear()
        for action in actions:
            button = QToolButton(self.left_group)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setFixedSize(32, 30)
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
        disabled = "#7e8998" if dark else "#9299a3"
        badge_text = "#f0cb7c" if dark else "#805b13"
        badge_background = "#493a20" if dark else "#fff1cc"
        self.setStyleSheet(
            f"#applicationTitleBar {{ background: {background}; color: {foreground}; }}"
            f"#applicationTitleBar QLabel {{ color: {foreground}; background: transparent;"
            " font-size: 10pt; font-weight: 300; }"
            "#applicationTitleBar QLabel#developmentBadge {"
            f" color: {badge_text}; background: {badge_background};"
            " font-size: 9pt; font-weight: 600; border-radius: 4px; margin-right: 8px; }"
            f"#applicationTitleBar QToolButton {{ color: {foreground}; border: 0;"
            " background: transparent; padding: 0; border-radius: 5px; }"
            "#applicationTitleBar QToolButton#windowControlButton,"
            "#applicationTitleBar QToolButton#windowCloseButton { border-radius: 0; }"
            f"#applicationTitleBar QToolButton:hover {{ background: {hover}; }}"
            f"#applicationTitleBar QToolButton:pressed {{ background: {pressed}; }}"
            f"#applicationTitleBar QToolButton:disabled {{ color: {disabled};"
            " background: transparent; }"
            "#applicationTitleBar QToolButton#windowCloseButton:hover { background: #c42b1c; }"
            "#applicationTitleBar QToolButton#windowCloseButton:pressed { background: #a82217; }"
        )
        self.minimize_button.setIcon(outline_icon("minimize", foreground))
        self.close_button.setIcon(outline_icon("close", foreground))
        self.update_window_state()

    def eventFilter(self, watched, event):
        if watched is self.close_button and event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            color = (
                "#ffffff"
                if event.type() == QEvent.Type.Enter
                else ("#e1e7ef" if self._dark else "#253047")
            )
            self.close_button.setIcon(outline_icon("close", color))
        return super().eventFilter(watched, event)

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
        self.chrome_header.setObjectName("applicationChromeHeader")
        header_layout = QVBoxLayout(self.chrome_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        self.title_bar = TitleBar(self)
        self.menu_row = QWidget(self.chrome_header)
        self.menu_row.setObjectName("applicationMenuRow")
        self._menu_row_layout = QHBoxLayout(self.menu_row)
        self._menu_row_layout.setContentsMargins(6, 2, 6, 3)
        self._menu_row_layout.setSpacing(6)
        self._menu_leading_widget: QWidget | None = None
        self._chrome_menu = QMenuBar(self.menu_row)
        self._chrome_menu.setObjectName("applicationMenuBar")
        self._menu_row_layout.addWidget(self._chrome_menu, 1)
        header_layout.addWidget(self.title_bar)
        header_layout.addWidget(self.menu_row)
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

    def set_menu_leading_widget(self, widget: QWidget) -> None:
        """Keep a sidebar toggle separate from native menu action geometry."""
        if self._menu_leading_widget is not None and self._menu_leading_widget is not widget:
            self._menu_row_layout.removeWidget(self._menu_leading_widget)
            self._menu_leading_widget.setParent(None)
        self._menu_leading_widget = widget
        widget.setParent(self.menu_row)
        self._menu_row_layout.insertWidget(0, widget)
        widget.show()

    def apply_chrome_theme(self, dark: bool) -> None:
        self.title_bar.apply_theme(dark)
        foreground = "#e1e7ef" if dark else "#253047"
        background = "#20252d" if dark else "#f5f7fa"
        border = "#353e4b" if dark else "#dce2ea"
        hover = "#303947" if dark else "#e5eaf1"
        pressed = "#345480" if dark else "#c6dcff"
        highlighted = "#ffffff" if dark else "#162b49"
        disabled = "#7e8998" if dark else "#9299a3"
        accent = "#88b9ff" if dark else "#346bc6"
        self.chrome_header.setStyleSheet(
            f"#applicationChromeHeader {{ background: {background}; }}"
            f"#applicationMenuRow {{ background: {background}; color: {foreground};"
            f" border: none; border-bottom: 1px solid {border}; }}"
            f"#applicationMenuRow QToolButton {{ background: transparent; color: {foreground};"
            " border: none; border-radius: 5px; padding: 0; }"
            f"#applicationMenuRow QToolButton:hover {{ background: {hover}; }}"
            f"#applicationMenuRow QToolButton:pressed,"
            f"#applicationMenuRow QToolButton:checked {{ background: {pressed};"
            f" color: {highlighted}; }}"
            f"#applicationMenuRow QToolButton:disabled {{ color: {disabled};"
            " background: transparent; }"
            "#applicationMenuRow QToolButton#notebookSidebarToggle:focus {"
            f" border: 1px solid {accent}; }}"
        )
        # Explicit colors avoid a stale palette when switching a visible window.
        # Native menu item rules are removed; the menu row owns its bottom rule.
        self._chrome_menu.setStyleSheet(
            f"QMenuBar {{ background: {background}; color: {foreground};"
            " border: none; padding: 0; font-size: 10pt; font-weight: 400; }"
            "QMenuBar::item { background: transparent; border: none; padding: 6px 9px; }"
            f"QMenuBar::item:selected {{ background: {hover};"
            " border-radius: 4px; }"
            f"QMenuBar::item:pressed {{ background: {pressed};"
            f" color: {highlighted}; border-radius: 4px; }}"
            f"QMenuBar::item:disabled {{ color: {disabled}; }}"
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
