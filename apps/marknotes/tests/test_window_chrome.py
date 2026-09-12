"""Shared actions, native-operation dispatch and ordinary window state behavior."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QMouseEvent
from PySide6.QtWidgets import QApplication, QPlainTextEdit

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
    assert menu.parent() is window.chrome_header
    assert menu.y() >= window.title_bar.height()
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
