import pytest
from markdown_it import MarkdownIt
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QInputMethodEvent, QKeyEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication

from marknotes.editor import SourceEditor
from marknotes.indentation import python_index, utf16_length


@pytest.fixture
def editor(qtbot):
    widget = SourceEditor()
    qtbot.addWidget(widget)
    widget.resize(600, 450)
    widget.show()
    return widget


def at_end(editor, text):
    editor.replace_source(text)
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)


def key(editor, value, modifiers=Qt.KeyboardModifier.NoModifier):
    editor.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, value, modifiers))


@pytest.mark.parametrize(
    ("source", "result"),
    [
        ("- 日本語😀", "- 日本語😀\n- "),
        ("* 項目", "* 項目\n* "),
        ("+ 項目", "+ 項目\n+ "),
        ("9. 項目", "9. 項目\n10. "),
        ("10) 項目", "10) 項目\n11) "),
        ("- [x] 完了😀", "- [x] 完了😀\n- [ ] "),
        ("  - ", "  - \n  - "),
        ("- ", "\n"),
        ("- [ ] ", "\n"),
        ("- 親項目\n  - 子項目", "- 親項目\n  - 子項目\n  - "),
        ("- 最初の項目\n  補足説明", "- 最初の項目\n  補足説明\n- "),
        ("10. 最初の項目\n    補足説明", "10. 最初の項目\n    補足説明\n11. "),
        ("> - 引用項目", "> - 引用項目\n> - "),
        (">   + ", ">   + \n>   + "),
        ("  普通の字下げ", "  普通の字下げ\n  "),
        ("    - コードの例", "    - コードの例\n    "),
        ("```md\n- コードの例", "```md\n- コードの例\n"),
        ("~~~\n  - コードの例", "~~~\n  - コードの例\n  "),
        ("> ```md\n> - コードの例", "> ```md\n> - コードの例\n> "),
        ("- - -", "- - -\n"),
    ],
)
def test_enter_respects_markdown_context(editor, source, result):
    at_end(editor, source)
    key(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == result
    assert editor.textCursor().position() == utf16_length(result)
    editor.undo()
    assert editor.toPlainText() == source


@pytest.mark.parametrize(
    ("source", "after_first", "after_second"),
    [
        ("- 親項目\n  - ", "- 親項目\n    ", "- 親項目\n- "),
        ("- 最初の項目\n- ", "- 最初の項目\n  ", "- 最初の項目\n"),
        ("  - 最初の項目\n  - ", "  - 最初の項目\n    ", "  - 最初の項目\n- "),
        ("10. 親項目\n    - ", "10. 親項目\n      ", "10. 親項目\n- "),
        ("> - 親\n>   - ", "> - 親\n>     ", "> - 親\n> - "),
    ],
)
def test_two_stage_backspace_preserves_body_position(editor, source, after_first, after_second):
    at_end(editor, source)
    key(editor, Qt.Key.Key_Backspace)
    assert editor.toPlainText() == after_first
    key(editor, Qt.Key.Key_Backspace)
    assert editor.toPlainText() == after_second
    editor.undo()
    assert editor.toPlainText() == after_first
    editor.undo()
    assert editor.toPlainText() == source


def test_enter_after_erased_marker_continues_previous_list(editor):
    at_end(editor, "  - 最初の項目\n  - ")
    key(editor, Qt.Key.Key_Backspace)
    key(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == "  - 最初の項目\n    \n  - "


def test_enter_mid_line_and_selection_are_normal_text_edits(editor):
    at_end(editor, "- 😀本文")
    cursor = editor.textCursor()
    cursor.setPosition(4)
    editor.setTextCursor(cursor)
    key(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == "- 😀\n本文"
    editor.undo()
    cursor = editor.textCursor()
    cursor.setPosition(2)
    cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    key(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == "- \n"


@pytest.mark.parametrize("reverse", [False, True])
def test_multiline_tab_keeps_selection_direction_and_excludes_terminal_row(editor, reverse):
    source = "😀一行目\n- 二行目\n三行目"
    at_end(editor, source)
    end = utf16_length("😀一行目\n- 二行目\n")
    cursor = editor.textCursor()
    cursor.setPosition(end if reverse else 0)
    cursor.setPosition(0 if reverse else end, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    key(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "  😀一行目\n  - 二行目\n三行目"
    assert editor.textCursor().hasSelection()
    assert editor.textCursor().selectionStart() == 0
    assert editor.textCursor().selectionEnd() == end + 4
    assert (editor.textCursor().anchor() > editor.textCursor().position()) is reverse
    key(editor, Qt.Key.Key_Backtab)
    assert editor.toPlainText() == source
    assert editor.textCursor().selectionEnd() == end
    editor.undo()
    assert editor.toPlainText() == "  😀一行目\n  - 二行目\n三行目"
    editor.undo()
    assert editor.toPlainText() == source


def test_partial_selection_indents_all_rows_and_keeps_selected_text(editor):
    at_end(editor, "😀first\nsecond\nthird")
    cursor = editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(utf16_length("😀first\nsec"), QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    key(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "  😀first\n  second\nthird"
    assert editor.textCursor().selectedText() == "first\u2029  sec"
    editor.undo()
    assert editor.toPlainText() == "😀first\nsecond\nthird"


def test_ordered_children_align_with_parent_content_and_outdent(editor):
    at_end(editor, "10. 親項目\n11. 子項目")
    key(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "10. 親項目\n    1. 子項目"
    items = [t for t in MarkdownIt().parse(editor.toPlainText()) if t.type == "list_item_open"]
    assert len(items) == 2
    assert items[1].level > items[0].level
    key(editor, Qt.Key.Key_Backtab)
    assert editor.toPlainText() == "10. 親項目\n1. 子項目"


def test_tab_on_list_body_indents_line_instead_of_inserting_at_caret(editor):
    at_end(editor, "- 😀項目")
    key(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "  - 😀項目"
    assert editor.textCursor().position() == utf16_length(editor.toPlainText())


def test_plain_tab_uses_two_column_stops_at_unicode_caret(editor):
    at_end(editor, "a😀z")
    cursor = editor.textCursor()
    cursor.setPosition(1)
    editor.setTextCursor(cursor)
    key(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "a 😀z"
    assert editor.textCursor().position() == 2


@pytest.mark.parametrize(
    ("source", "is_code"),
    [
        ("```\n", True),
        ("````md\n```\n- ", True),
        ("~~~\n```\n- ", True),
        ("```\n- example\n```", False),
        ("    - example", True),
        ("10. parent\n    - child", False),
        ("> ```\n> - example", True),
    ],
)
def test_clipboard_context_tracks_unclosed_fences_and_real_code(editor, source, is_code):
    at_end(editor, source)
    assert editor.in_code_block() is is_code


def test_ime_preedit_enter_does_not_insert_a_list_marker(editor):
    at_end(editor, "- 本文")
    QApplication.sendEvent(editor, QInputMethodEvent("変換中", []))
    assert editor._composing
    key(editor, Qt.Key.Key_Return)
    assert not editor.toPlainText().endswith("\n- ")
    QApplication.sendEvent(editor, QInputMethodEvent())
    assert not editor._composing


def format_at(editor, line, position):
    block = editor.document().findBlockByNumber(line)
    for span in block.layout().formats():
        if span.start <= position < span.start + span.length:
            return QTextCharFormat(span.format)
    raise AssertionError(f"No format at line {line}, UTF-16 column {position}")


def test_highlighter_keeps_markdown_inside_fences_literal_and_tracks_delimiter_edits(editor):
    at_end(editor, "````md\n# 😀 **literal**\n```\n- still code\n````\n# Heading")
    editor.highlighter.rehighlight()
    assert format_at(editor, 1, 5).fontWeight() != QFont.Weight.Bold
    assert format_at(editor, 1, 5).background().color().name() == "#f1f4f7"
    assert editor.document().findBlockByNumber(3).userState() > 0
    assert editor.document().findBlockByNumber(4).userState() == 0
    assert format_at(editor, 5, 3).fontWeight() == QFont.Weight.Bold
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText("plain")
    editor.highlighter.rehighlight()
    assert format_at(editor, 1, 5).fontWeight() == QFont.Weight.Bold


def test_unicode_highlight_ranges_and_theme_leave_text_and_undo_intact(editor):
    at_end(editor, "😀 **太字** [link](url) ![画像](img/a.png)")
    editor.insertPlainText("編集")
    text = editor.toPlainText()
    editor.document().setModified(False)
    editor.apply_theme(True)
    editor.highlighter.rehighlight()
    assert format_at(editor, 0, utf16_length("😀 **")).fontWeight() == QFont.Weight.Bold
    assert format_at(editor, 0, utf16_length("😀 **太字** [")).fontUnderline()
    assert not editor.document().isModified()
    assert editor.toPlainText() == text
    editor.undo()
    assert editor.toPlainText() == text[:-2]
    assert python_index("a😀b", 3) == 2


@pytest.mark.parametrize(
    "source", ["    example\n    ", "- parent\n\n      code\n      ", ">     code\n>     "]
)
def test_blank_code_lines_keep_indentation_and_plain_clipboard_context(editor, source):
    at_end(editor, source)
    assert editor.in_code_block()
    prefix = source.split("\n")[-1]
    key(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == source + "\n" + prefix
    assert editor.in_code_block()


def test_multirow_numbered_indent_restarts_child_numbering_and_preserves_one_undo(editor):
    source = "10. parent\n11. child\n12. sibling\n13. outside"
    at_end(editor, source)
    cursor = editor.textCursor()
    cursor.setPosition(len("10. parent\n"))
    cursor.setPosition(len("10. parent\n11. child\n12. sibling\n"), QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    key(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "10. parent\n    1. child\n    2. sibling\n13. outside"
    assert editor.textCursor().selectedText() == "    1. child\u2029    2. sibling\u2029"
    editor.undo()
    assert editor.toPlainText() == source


def test_key_delivery_through_qt_and_readonly_mode(editor, qtbot):
    at_end(editor, "- item")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == "- item\n- "
    editor.setReadOnly(True)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    qtbot.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.toPlainText() == "- item\n- "


def test_editing_setext_separator_rehighlights_previous_line(editor, qtbot):
    at_end(editor, "heading\n")
    editor.insertPlainText("---")
    qtbot.wait(20)
    assert format_at(editor, 0, 0).fontWeight() == QFont.Weight.Bold
