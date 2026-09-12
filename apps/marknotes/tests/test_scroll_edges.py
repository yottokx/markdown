from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from test_integration import javascript, metrics

from marknotes.app import RESOURCE_DIR, MainWindow


def test_viewport_tops_align_and_single_long_line_syncs(qtbot):
    window = MainWindow()
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.resize(1200, 700)
    window.show()
    text = "単一行の長い段落 😀 折り返しの途中も連動します。 " * 400
    window.set_source(text, RESOURCE_DIR)
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    left_top = window.editor.viewport().mapToGlobal(QPoint(0, 0)).y()
    right_top = window.preview.view.mapToGlobal(QPoint(0, 0)).y()
    assert abs(left_top - right_top) <= 5
    window.editor.scroll_to_source(0.5)
    qtbot.wait(100)
    result = metrics(qtbot, window)
    assert result["maxSource"] > 0.9
    assert 0.45 <= result["measuredSource"] <= 0.55
    javascript(qtbot, window, f"window.scrollTo(0, {result['maxScroll'] * 0.75})")
    qtbot.wait(100)
    assert window.editor.source_position() > 0.65
    assert abs(window.editor.source_position() - metrics(qtbot, window)["source"]) < 0.025
    assert window.editor.toPlainText() == text


def test_wheel_and_scrollbar_input_on_source(qtbot):
    window = MainWindow()
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.show()
    window.open_path(RESOURCE_DIR / "scroll-check.md")
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    viewport = window.editor.viewport()
    local = viewport.rect().center()
    event = QWheelEvent(
        local,
        viewport.mapToGlobal(local),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(viewport, event)
    qtbot.wait(100)
    assert window.editor.source_position() > 0
    assert abs(metrics(qtbot, window)["source"] - window.editor.source_position()) < 0.1
    bar = window.editor.verticalScrollBar()
    qtbot.mouseClick(
        bar, Qt.MouseButton.LeftButton, pos=QPoint(bar.width() // 2, bar.height() // 2)
    )
    qtbot.wait(100)
    assert abs(metrics(qtbot, window)["source"] - window.editor.source_position()) < 0.1
