import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QFont, QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextDocumentLayout

from md_editor.editor import SourceEditor


def make_editor(qtbot, text, width=520):
    editor = SourceEditor()
    qtbot.addWidget(editor)
    editor.resize(width, 450)
    editor.show()
    editor.replace_source(text)
    qtbot.wait(50)
    return editor


def test_scroll_to_arbitrary_source_and_last_line(qtbot):
    text = "\n".join(f"行 {i}: " + "長い折り返しテキスト " * (i % 7) for i in range(160))
    editor = make_editor(qtbot, text)
    for target in (0, 15, 72, 140, 159, 30, 0):
        editor.scroll_to_source(target)
        qtbot.wait(20)
        assert abs(editor.source_position() - target) < 0.05
        assert editor.firstVisibleBlock().blockNumber() == target
    assert editor.toPlainText() == text


def test_source_scroll_does_not_move_cursor_or_undo_history(qtbot):
    editor = make_editor(qtbot, "\n".join(str(i) for i in range(100)))
    editor.insertPlainText("編集")
    cursor_position = editor.textCursor().position()
    editor.scroll_to_source(80)
    assert editor.textCursor().position() == cursor_position
    editor.undo()
    assert editor.toPlainText().startswith("0\n1")


def test_trailing_empty_lines_reach_top_without_changing_source(qtbot):
    text = "見出し\n" + "本文\n" * 50 + "\n\n"
    editor = make_editor(qtbot, text)
    editor.show_end()
    assert editor.firstVisibleBlock().blockNumber() == len(text.split("\n")) - 1
    assert editor.toPlainText() == text


def test_wrapped_position_roundtrip_and_resize(qtbot):
    text = "\n".join("あいうえお abcdef 😀 " * 30 for _ in range(90))
    editor = make_editor(qtbot, text, width=620)
    editor.scroll_to_source(42.55)
    qtbot.wait(20)
    before = editor.source_position()
    scrollbar = editor.verticalScrollBar().value()
    editor.scroll_to_source(before)
    assert editor.verticalScrollBar().value() == scrollbar
    editor.resize(390, 500)
    qtbot.wait(50)
    assert abs(editor.source_position() - before) < 0.2
    editor.set_wrapping(False)
    qtbot.wait(30)
    assert int(editor.source_position()) == int(before)


@pytest.mark.parametrize("wrapping", [True, False])
@pytest.mark.parametrize("target", [0, 42.55, 89])
@pytest.mark.parametrize(
    "family,size", [("Cascadia Mono", 7), ("Cascadia Mono", 24), ("Arial", 16)]
)
def test_font_change_preserves_source_position_and_updates_metrics(
    qtbot, wrapping, target, family, size
):
    text = "\n".join("あいうえお abcdef 😀 " * 30 for _ in range(90))
    editor = make_editor(qtbot, text, width=470)
    editor.set_wrapping(wrapping)
    editor.scroll_to_source(target)
    qtbot.wait(20)
    before = editor.source_position()
    positions = []
    editor.source_position_changed.connect(positions.append)

    editor.set_source_font(QFont(family, size))
    qtbot.wait(30)

    block = editor.firstVisibleBlock()
    assert block.blockNumber() == int(before)
    # The scrollbar advances in visual lines, so fractions round down to one line.
    assert abs(editor.source_position() - before) <= 1 / max(1, block.layout().lineCount()) + 0.01
    assert positions == [editor.source_position()]
    assert editor.font().family() == family
    assert editor.font().pointSize() == size
    assert editor.gutter.width() == editor.gutter_width()
    assert editor.viewportMargins().left() == editor.gutter_width()
    assert editor.gutter.font() == editor.font()
    assert editor.tabStopDistance() == editor.fontMetrics().horizontalAdvance(" ") * 2
    assert editor.toPlainText() == text
    assert not editor.document().isModified()


@pytest.mark.parametrize("modified", [True, False])
def test_font_change_preserves_selection_cursor_and_undo_redo(qtbot, modified):
    text = "\n".join(f"本文 {index}: " + "折り返し行 " * 40 for index in range(60))
    editor = make_editor(qtbot, text)
    editor.insertPlainText("編集")
    edited_text = editor.toPlainText()
    if not modified:
        editor.undo()
    cursor = editor.textCursor()
    cursor.setPosition(18)
    cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.scroll_to_source(41.5)
    qtbot.wait(20)
    before = editor.source_position()
    revision = editor.document().revision()
    changes = []
    editor.textChanged.connect(lambda: changes.append(True))

    editor.set_source_font(QFont("Arial", 20))
    qtbot.wait(30)

    assert editor.textCursor().position() == cursor.position()
    assert editor.textCursor().anchor() == cursor.anchor()
    assert editor.textCursor().selectedText() == cursor.selectedText()
    assert editor.document().revision() == revision
    assert editor.document().isModified() == modified
    assert int(editor.source_position()) == int(before)
    assert not changes
    if modified:
        editor.undo()
        assert editor.toPlainText() == text
        editor.redo()
        assert editor.toPlainText() == edited_text
    else:
        editor.redo()
        assert editor.toPlainText() == edited_text
        editor.undo()
        assert editor.toPlainText() == text


def test_setting_same_font_is_noop(qtbot):
    editor = make_editor(qtbot, "\n".join("長い行 " * 50 for _ in range(60)))
    editor.scroll_to_source(35.6)
    qtbot.wait(20)
    before = editor.source_position()
    scrollbar = editor.verticalScrollBar().value()
    positions = []
    editor.source_position_changed.connect(positions.append)

    editor.set_source_font(QFont(editor.font()))
    qtbot.wait(20)

    assert editor.source_position() == before
    assert editor.verticalScrollBar().value() == scrollbar
    assert not positions


def test_custom_font_survives_theme_changes(qtbot):
    editor = make_editor(qtbot, "\n".join("長い行 " * 50 for _ in range(60)))
    font = QFont("Arial", 18)
    editor.set_source_font(font)
    editor.scroll_to_source(35.5)
    qtbot.wait(20)
    before = editor.source_position()

    for dark in (True, False):
        editor.apply_theme(dark)
        qtbot.wait(20)
        assert editor.font() == font
        assert editor.gutter.font() == font
        assert editor.source_position() == before
        assert not editor.document().isModified()


@pytest.mark.parametrize("custom_font", [False, True])
@pytest.mark.parametrize("modified", [False, True])
def test_document_switch_keeps_font_metrics_and_document_history(qtbot, custom_font, modified):
    text = "\n".join(f"行 {i} " + "長文の折り返し " * 30 for i in range(70))
    editor = make_editor(qtbot, text)
    owner = QObject(editor)
    first_document = editor.document()
    first_document.setParent(owner)
    if custom_font:
        editor.set_source_font(QFont("Arial", 19))
    font = QFont(editor.font())
    editor.scroll_to_source(48.5)

    next_document = QTextDocument(owner)
    next_document.setDocumentLayout(QPlainTextDocumentLayout(next_document))
    next_document.setPlainText(text)
    next_document.setModified(False)
    cursor = QTextCursor(next_document)
    cursor.insertText("編集")
    edited_text = next_document.toPlainText()
    if not modified:
        next_document.undo()
    revision = next_document.revision()
    undo_steps = next_document.availableUndoSteps()
    redo_steps = next_document.availableRedoSteps()
    changes = []

    def on_change():
        changes.append(True)

    next_document.contentsChanged.connect(on_change)

    editor.setDocument(next_document)
    qtbot.wait(30)

    assert editor.font() == font
    assert next_document.defaultFont() == font
    assert next_document.defaultTextOption().tabStopDistance() == (
        editor.fontMetrics().horizontalAdvance(" ") * 2
    )
    assert editor.gutter.font() == font
    assert editor.gutter.width() == editor.gutter_width()
    assert editor.viewportMargins().left() == editor.gutter_width()
    assert editor.source_position() == 0
    assert next_document.revision() == revision
    assert next_document.isModified() == modified
    assert next_document.availableUndoSteps() == undo_steps
    assert next_document.availableRedoSteps() == redo_steps
    assert not changes
    next_document.contentsChanged.disconnect(on_change)
    if modified:
        editor.undo()
        assert editor.toPlainText() == text
        editor.redo()
        assert editor.toPlainText() == edited_text
    else:
        editor.redo()
        assert editor.toPlainText() == edited_text
        editor.undo()
        assert editor.toPlainText() == text

    # A previously open document must also receive a font changed in another tab.
    changed_font = QFont("Arial", 24)
    editor.set_source_font(changed_font)
    expected_height = next_document.firstBlock().layout().boundingRect().height()
    editor.setDocument(first_document)
    qtbot.wait(30)
    assert editor.font() == changed_font
    assert first_document.defaultFont() == changed_font
    assert first_document.firstBlock().layout().boundingRect().height() == expected_height
    assert first_document.toPlainText() == text
    assert not first_document.isModified()
