"""Preview task edits change only the marker and preserve editor state."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QTextCursor

from md_editor.app import MainWindow
from md_editor.search import qt_position


@pytest.fixture
def task_window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "tasks.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.resize(1000, 650)
    window.show()
    return window


@pytest.mark.parametrize("backwards", [False, True])
def test_toggle_preserves_unicode_selection_and_single_undo(task_window, backwards):
    window = task_window
    source = "😀 名前を選択\n\n> 12. [ ] task with **text**\n  unchanged"
    window.set_source(source, window.base_dir)
    cursor = window.editor.textCursor()
    start, end = qt_position(source, 0), qt_position(source, len("😀 名前を選択"))
    cursor.setPosition(end if backwards else start)
    cursor.setPosition(start if backwards else end, QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    expected_cursor = (cursor.anchor(), cursor.position())
    revision = window._revision
    line, column = next(iter(window._preview_task_markers))
    assert window._toggle_preview_task(line, column, True, revision)
    assert window.editor.toPlainText() == source.replace("[ ]", "[x]")
    current = window.editor.textCursor()
    assert (current.anchor(), current.position()) == expected_cursor
    assert window.editor.document().isModified()
    assert window._revision > revision
    steps = window.editor.document().availableUndoSteps()
    assert window._toggle_preview_task(line, column, True, window._revision)
    assert window.editor.document().availableUndoSteps() == steps
    window.editor.undo()
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()
    window.editor.redo()
    assert window.editor.toPlainText() == source.replace("[ ]", "[x]")


def test_uppercase_checked_marker_can_be_unchecked(task_window):
    window = task_window
    source = "- [X] done\n- [ ] pending"
    window.set_source(source, window.base_dir)
    assert window._toggle_preview_task(0, 3, False, window._revision)
    assert window.editor.toPlainText() == "- [ ] done\n- [ ] pending"
    window.editor.undo()
    assert window.editor.toPlainText() == source


def test_readonly_forged_and_code_markers_are_rejected(task_window):
    window = task_window
    source = "- [ ] actual\n\n```\n- [ ] code\n```\n\n[ ] ordinary"
    window.set_source(source, window.base_dir)
    for line, column in [(0, 4), (3, 3), (6, 1), (-1, 3), (99, 3)]:
        assert not window._toggle_preview_task(line, column, True, window._revision)
    window.editor.setReadOnly(True)
    assert not window._toggle_preview_task(0, 3, True, window._revision)
    assert window.editor.toPlainText() == source
    assert not window.editor.document().isUndoAvailable()


def test_stale_preview_is_rejected_after_typing_or_loading_another_document(task_window):
    window = task_window
    source = "- [ ] task"
    window.set_source(source, window.base_dir)
    stale_revision = window._revision
    window.editor.moveCursor(QTextCursor.MoveOperation.End)
    window.editor.insertPlainText(" changed")
    window._render_timer.stop()
    assert not window._toggle_preview_task(0, 3, True, stale_revision)
    assert window.editor.toPlainText() == source + " changed"
    window.set_source("- [ ] different task", window.base_dir)
    assert not window._toggle_preview_task(0, 3, True, stale_revision)
    assert window.editor.toPlainText() == "- [ ] different task"


def test_preview_only_toggle_keeps_pane_and_scroll_position(task_window, qtbot):
    window = task_window
    source = "\n\n".join(f"- [ ] Task {index}" for index in range(50))
    window.set_source(source, window.base_dir)
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    window.set_display_mode("preview", persist=False)
    qtbot.waitUntil(lambda: not window._changing_display_mode, timeout=15000)
    window._scroll_preview_to(40)
    window.preview.view.setFocus()
    assert window._toggle_preview_task(40, 3, True, window._revision)
    qtbot.waitUntil(lambda: window._rendered_revision == window._revision, timeout=15000)
    assert window.display_mode == "preview"
    assert window._preview_position == pytest.approx(40)
    assert window.editor.toPlainText().splitlines()[40] == "- [x] Task 20"
    assert not window.editor.hasFocus()
