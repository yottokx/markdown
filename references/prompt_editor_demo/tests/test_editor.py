from pathlib import Path
from time import monotonic

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

from desktop_llm.storage import SettingsStore
from prompt_editor_demo.editor import PromptEditorWindow, PromptTextEdit


class _Keys:
    def get(self, _endpoint_id: str) -> str:
        return ""


def _wait_for_completion(window: PromptEditorWindow, app) -> None:
    deadline = monotonic() + 2.0
    while window._worker is not None and monotonic() < deadline:
        app.processEvents()


def test_tab_completes_caret_directive_and_undo_restores_it(
    tmp_path: Path, qapplication
) -> None:
    store = SettingsStore(tmp_path / "settings.sqlite3")
    window = PromptEditorWindow(store, _Keys())  # type: ignore[arg-type]
    window.editor.setPlainText("私は ?人名 です。")
    cursor = window.editor.textCursor()
    cursor.setPosition(window.editor.toPlainText().index("?人名") + 1)
    window.editor.setTextCursor(cursor)

    window._tab_requested()
    _wait_for_completion(window, qapplication)

    assert window.editor.toPlainText() == "私は山田太郎です。"
    window.editor.undo()
    assert window.editor.toPlainText() == "私は ?人名 です。"
    window.close()


def test_selection_completes_multiple_directives_in_one_edit_block(
    tmp_path: Path, qapplication
) -> None:
    store = SettingsStore(tmp_path / "settings.sqlite3")
    window = PromptEditorWindow(store, _Keys())  # type: ignore[arg-type]
    source = "? 注意点を説明する\n- ?項目を3つ..."
    window.editor.setPlainText(source)
    cursor = window.editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len(source), QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)

    window._tab_requested()
    _wait_for_completion(window, qapplication)

    assert "?" not in window.editor.toPlainText()
    assert window.editor.toPlainText().count("\n- ") == 3
    window.editor.undo()
    assert window.editor.toPlainText() == source
    window.close()


def test_ai_off_keeps_directive_unchanged(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.sqlite3")
    window = PromptEditorWindow(store, _Keys())  # type: ignore[arg-type]
    window.editor.setPlainText("? 段落")
    window.ai_toggle.setChecked(False)

    window._tab_requested()

    assert window.editor.toPlainText() == "? 段落"
    assert window.activity_label.text() == "AIがOFFです"
    window.close()


def test_tab_completes_plain_text_at_line_end(tmp_path: Path, qapplication) -> None:
    store = SettingsStore(tmp_path / "settings.sqlite3")
    window = PromptEditorWindow(store, _Keys())  # type: ignore[arg-type]
    window.editor.setPlainText("この文章")
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)

    window._tab_requested()
    _wait_for_completion(window, qapplication)

    assert window.editor.toPlainText() == "この文章を自然に補完します。"
    window.close()


def test_tab_at_list_body_line_end_completes_instead_of_indenting(
    tmp_path: Path, qapplication
) -> None:
    store = SettingsStore(tmp_path / "settings.sqlite3")
    window = PromptEditorWindow(store, _Keys())  # type: ignore[arg-type]
    window.editor.setPlainText("- この項目")
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)

    window._tab_requested()
    _wait_for_completion(window, qapplication)

    assert window.editor.toPlainText() == "- この項目を自然に補完します。"
    window.close()


def test_tab_at_empty_list_prefix_indents_without_ai(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.sqlite3")
    window = PromptEditorWindow(store, _Keys())  # type: ignore[arg-type]
    window.editor.setPlainText("- ")
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)

    window._tab_requested()

    assert window.editor.toPlainText() == "  - "
    assert window._worker is None
    window.close()


def test_backspace_removes_list_marker_then_outdents_with_marker() -> None:
    editor = PromptTextEdit()
    editor.setPlainText("- 親項目\n  - ")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    backspace = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Backspace,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.keyPressEvent(backspace)
    assert editor.toPlainText() == "- 親項目\n    "
    assert editor.textCursor().positionInBlock() == 4

    editor.keyPressEvent(backspace)
    assert editor.toPlainText() == "- 親項目\n- "
    assert editor.textCursor().positionInBlock() == 2


def test_backspace_from_removed_top_level_marker_exits_list() -> None:
    editor = PromptTextEdit()
    editor.setPlainText("- 最初の項目\n- ")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    backspace = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Backspace,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.keyPressEvent(backspace)
    assert editor.toPlainText() == "- 最初の項目\n  "
    editor.keyPressEvent(backspace)
    assert editor.toPlainText() == "- 最初の項目\n"


def test_enter_after_removed_marker_resumes_list_at_previous_level() -> None:
    editor = PromptTextEdit()
    editor.setPlainText("  - 最初の項目\n  - ")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    backspace = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Backspace,
        Qt.KeyboardModifier.NoModifier,
    )
    enter = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.keyPressEvent(backspace)
    editor.keyPressEvent(enter)

    assert editor.toPlainText() == "  - 最初の項目\n    \n  - "


def test_enter_after_outdent_continues_at_shallower_list_level() -> None:
    editor = PromptTextEdit()
    editor.setPlainText("  - 最初の項目\n  - ")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    backspace = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Backspace,
        Qt.KeyboardModifier.NoModifier,
    )
    enter = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.keyPressEvent(backspace)
    editor.keyPressEvent(backspace)
    editor.insertPlainText("浅い階層の項目")
    editor.keyPressEvent(enter)

    assert editor.toPlainText() == "  - 最初の項目\n- 浅い階層の項目\n- "


def test_enter_after_continuation_text_resumes_list() -> None:
    editor = PromptTextEdit()
    editor.setPlainText("- 最初の項目\n  補足説明")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    enter = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.keyPressEvent(enter)

    assert editor.toPlainText() == "- 最初の項目\n  補足説明\n- "


def test_enter_keeps_nested_list_indent_even_when_item_is_empty() -> None:
    editor = PromptTextEdit()
    editor.setPlainText("  - ")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    enter = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.keyPressEvent(enter)

    assert editor.toPlainText() == "  - \n  - "


def test_enter_keeps_nested_list_indent_with_item_text() -> None:
    editor = PromptTextEdit()
    editor.setPlainText("- 親項目\n  - ネストした項目")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    enter = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.keyPressEvent(enter)

    assert editor.toPlainText() == "- 親項目\n  - ネストした項目\n  - "


def test_ime_cursor_rectangle_is_shifted_below_the_editing_line() -> None:
    editor = PromptTextEdit()
    editor.setPlainText("入力")
    base = QPlainTextEdit.inputMethodQuery(
        editor, Qt.InputMethodQuery.ImCursorRectangle
    )
    shifted = editor.inputMethodQuery(Qt.InputMethodQuery.ImCursorRectangle)

    assert isinstance(base, QRectF)
    assert isinstance(shifted, QRectF)
    assert shifted.x() == base.x()
    assert shifted.y() == base.y() + editor._ime_candidate_vertical_offset()
