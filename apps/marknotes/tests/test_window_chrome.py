"""Shared actions, native-operation dispatch and ordinary window state behavior."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QMouseEvent, QPalette
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QToolButton, QWidget

from marknotes.ui_icons import outline_icon
from marknotes.window_chrome import ChromeMainWindow


@pytest.fixture
def window(qtbot):
    window = ChromeMainWindow()
    qtbot.addWidget(window)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.setCentralWidget(QPlainTextEdit())
    window.resize(800, 500)
    window.move(100, 100)
    window.show()
    return window


def test_header_preserves_menu_and_shared_actions_and_centers_filename(window, qtbot):
    menu = window.menuBar()
    file_menu = menu.addMenu("ファイル")
    actions = []
    triggered = []
    for name in ("new", "open", "save"):
        action = QAction(outline_icon(name, "#253047"), name, window)
        action.triggered.connect(lambda _checked=False, name=name: triggered.append(name))
        file_menu.addAction(action)
        actions.append(action)
    window.title_bar.set_file_actions(actions)
    window.setWindowTitle("確認😀.md*")
    qtbot.wait(10)
    assert window.menuBar() is menu
    assert menu.parent() is window.menu_row
    assert menu.mapTo(window.chrome_header, QPoint()).y() >= window.title_bar.height()
    assert window.title_bar.title_label.text() == "確認😀.md*"
    assert (
        window.title_bar.title_label.geometry().center().x() == window.title_bar.rect().center().x()
    )
    for index, button in enumerate(window.title_bar.file_buttons):
        assert button.defaultAction() is actions[index]
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert triggered == ["new", "open", "save"]
    actions[2].setEnabled(False)
    assert not window.title_bar.file_buttons[2].isEnabled()
    window.setWindowTitle("長いファイル名" * 40 + ".md*")
    assert "…" in window.title_bar.title_label.text()
    assert window.title_bar.title_label.toolTip() == window.windowTitle()


def test_maximize_restore_double_click_and_minimize(window, qtbot):
    normal = window.geometry()
    bar = window.title_bar
    qtbot.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(bar.width() // 2, 18))
    qtbot.waitUntil(window.isMaximized)
    assert window.contentsMargins().left() == 0
    assert all(grip.isHidden() for grip in window.resize_grips)
    assert bar.maximize_button.toolTip() == "元に戻す"
    # Work-area bounds are Qt logical coordinates even under display scaling.
    qtbot.waitUntil(lambda: window.geometry() == window.screen().availableGeometry())
    qtbot.mouseClick(bar.maximize_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.isMaximized())
    qtbot.waitUntil(lambda: window.geometry() == normal)
    assert window.contentsMargins().left() == window.RESIZE_BORDER
    assert bar.maximize_button.toolTip() == "最大化"
    qtbot.mouseClick(bar.minimize_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(window.isMinimized)
    window.showNormal()


def test_drag_starts_native_move_only_after_a_drag(window, qtbot, monkeypatch):
    bar = window.title_bar
    calls = []
    monkeypatch.setattr(
        window.windowHandle(), "startSystemMove", lambda: calls.append("move") or True
    )
    start = QPoint(bar.width() // 2, 18)
    qtbot.mousePress(bar, Qt.MouseButton.LeftButton, pos=start)
    assert not calls
    end = start + QPoint(QApplication.startDragDistance() + 5, 0)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(end),
        QPointF(bar.mapToGlobal(end)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(bar, event)
    qtbot.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=end)
    assert calls == ["move"]


def test_all_edges_dispatch_correct_native_resize_direction(window, qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        window.windowHandle(), "startSystemResize", lambda edges: calls.append(edges) or True
    )
    for grip in window.resize_grips:
        assert not grip.isHidden()
        qtbot.mouseClick(grip, Qt.MouseButton.LeftButton)
    edge = Qt.Edge
    assert calls == [
        edge.LeftEdge,
        edge.RightEdge,
        edge.TopEdge,
        edge.BottomEdge,
        edge.LeftEdge | edge.TopEdge,
        edge.RightEdge | edge.TopEdge,
        edge.LeftEdge | edge.BottomEdge,
        edge.RightEdge | edge.BottomEdge,
    ]


def test_close_button_respects_cancelled_close_and_theme_keeps_actions(qtbot):
    class GuardedWindow(ChromeMainWindow):
        allow_close = False

        def closeEvent(self, event):
            event.accept() if self.allow_close else event.ignore()

    window = GuardedWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(window)
    window.show()
    action = QAction("save", window)
    window.title_bar.set_file_actions([action])
    window.title_bar.apply_theme(True)
    assert window.title_bar.file_buttons[0].defaultAction() is action
    assert not window.title_bar.maximize_button.icon().isNull()
    qtbot.mouseClick(window.title_bar.close_button, Qt.MouseButton.LeftButton)
    assert window.isVisible()
    window.allow_close = True
    qtbot.mouseClick(window.title_bar.close_button, Qt.MouseButton.LeftButton)
    assert not window.isVisible()


def test_menu_leading_button_does_not_overlap_first_action_or_right_corner(qtbot):
    # Match application startup: corner widgets are assembled before show().
    # Reparenting a new, parentless corner widget into an already visible menu
    # leaves it hidden, so its initial x=0 does not describe the shown layout.
    window = ChromeMainWindow()
    qtbot.addWidget(window)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.setCentralWidget(QPlainTextEdit())
    window.resize(800, 500)
    menu = window.menuBar()
    first = menu.addMenu("ノート(&N)").menuAction()
    menu.addMenu("編集(&E)")
    sidebar = QToolButton()
    sidebar.setText("≡")
    sidebar.setFixedSize(32, 30)
    modes = QToolButton()
    modes.setText("表示")
    modes.setFixedSize(80, 30)
    menu.setCornerWidget(modes, Qt.Corner.TopRightCorner)
    window.set_menu_leading_widget(sidebar)
    window.apply_chrome_theme(False)
    window.show()
    qtbot.waitExposed(window)
    qtbot.waitUntil(modes.isVisible)
    for width in (800, 540):
        window.resize(width, 500)
        qtbot.waitUntil(lambda: menu.width() > 0 and sidebar.isVisible())
        window.chrome_header.layout().activate()
        window.menu_row.layout().activate()
        sidebar_right = sidebar.mapTo(window.menu_row, sidebar.rect().topRight()).x()
        first_left = menu.mapTo(window.menu_row, menu.actionGeometry(first).topLeft()).x()
        assert first_left - sidebar_right >= 6
        assert menu.cornerWidget(Qt.Corner.TopRightCorner) is modes
        assert menu.actionGeometry(first).right() < modes.x()


def test_center_widget_reserves_native_drag_space_at_narrow_widths(window, qtbot, monkeypatch):
    bar = window.title_bar
    tabs = QWidget()
    bar.set_center_widget(tabs)
    calls = []
    monkeypatch.setattr(
        window.windowHandle(), "startSystemMove", lambda: calls.append("move") or True
    )
    for width in (800, 540):
        window.resize(width, 500)
        bar.layout().activate()
        assert bar.drag_region.width() == 112
        assert tabs.geometry().right() < bar.drag_region.x()
        assert bar.drag_region.geometry().right() < bar.controls.x()
        assert bar.title_label.isHidden()
        start = bar.drag_region.mapTo(bar, bar.drag_region.rect().center())
        # A hit on the reserved region reaches TitleBar, not a tab or button.
        assert bar.childAt(start) is None
        qtbot.mousePress(bar, Qt.MouseButton.LeftButton, pos=start)
        end = start + QPoint(QApplication.startDragDistance() + 5, 0)
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(end),
            QPointF(bar.mapToGlobal(end)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(bar, event)
        qtbot.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=end)
    assert calls == ["move", "move"]


def test_chrome_text_buttons_follow_theme_without_a_global_palette_change(window, qtbot):
    center = QWidget()
    overflow = QToolButton(center)
    overflow.setText("…")
    window.title_bar.set_center_widget(center)
    sidebar = QToolButton()
    sidebar.setText("≡")
    window.set_menu_leading_widget(sidebar)
    for dark, foreground in ((True, "#e1e7ef"), (False, "#253047"), (True, "#e1e7ef")):
        window.apply_chrome_theme(dark)
        for button in (overflow, sidebar):
            button.ensurePolished()
            assert button.palette().color(QPalette.ColorRole.ButtonText).name() == foreground
        assert window.menuBar().palette().color(QPalette.ColorRole.WindowText).name() == foreground
