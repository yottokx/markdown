"""Real Qt formatting actions keep Unicode selection, undo and popup ownership."""

import pytest
from markdown_it import MarkdownIt
from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import QMenu

from md_editor.app import MainWindow
from md_editor.editor import SourceEditor
from md_editor.formatting_menu import add_format_menus
from md_editor.search import qt_position


@pytest.fixture
def editor(qtbot):
    widget = SourceEditor()
    qtbot.addWidget(widget)
    widget.resize(700, 450)
    widget.show()
    return widget


def select(editor, source, start, end, reverse=False):
    editor.setPlainText(source)
    cursor = editor.textCursor()
    cursor.setPosition(qt_position(source, end if reverse else start))
    cursor.setPosition(
        qt_position(source, start if reverse else end), QTextCursor.MoveMode.KeepAnchor
    )
    editor.setTextCursor(cursor)


def menu_for(qtbot, editor):
    menu = QMenu(editor)
    qtbot.addWidget(menu)
    add_format_menus(menu, editor)
    return menu


def action(menu, key):
    found = menu.findChild(QAction, "format." + key)
    assert found is not None, key
    return found


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("key", "tag"),
    [
        ("bold.apply", "strong"),
        ("italic.apply", "em"),
        ("strike.apply", "s"),
        ("inline_code.apply", "code"),
    ],
)
def test_inline_action_preserves_unicode_selection_and_one_undo(qtbot, editor, key, tag, reverse):
    source = "前😀 本文😀 後"
    start, end = source.index("本文"), source.index(" 後")
    select(editor, source, start, end, reverse)
    menu = menu_for(qtbot, editor)
    inline = menu.findChild(QMenu, "inlineFormatMenu")
    assert all(item.menu() is None for item in inline.actions())
    item = action(menu, key)
    assert item.isEnabled()
    item.trigger()
    result = editor.toPlainText()
    assert f"<{tag}>本文😀</{tag}>" in MarkdownIt().enable("strikethrough").render(result)
    assert editor.textCursor().selectedText() == "本文😀"
    assert (editor.textCursor().anchor() > editor.textCursor().position()) is reverse
    editor.undo()
    assert editor.toPlainText() == source
    editor.redo()
    assert editor.toPlainText() == result


def test_partial_bold_removal_preserves_surrounding_format_and_selection(qtbot, editor):
    source = "**abcdef**"
    select(editor, source, source.index("cd"), source.index("cd") + 2)
    item = action(menu_for(qtbot, editor), "bold.remove")
    assert item.isEnabled() and "解除" in item.text()
    item.trigger()
    assert editor.toPlainText() == "**ab**cd**ef**"
    assert editor.textCursor().selectedText() == "cd"
    editor.undo()
    assert editor.toPlainText() == source


def test_mixed_inline_state_offers_apply_and_remove(qtbot, editor):
    source = "ab **cd** ef"
    select(editor, source, 0, len(source))
    menu = menu_for(qtbot, editor)
    assert action(menu, "bold.apply").isEnabled()
    assert "統一" in action(menu, "bold.apply").text()
    assert action(menu, "bold.remove").isEnabled()
    action(menu, "bold.apply").trigger()
    assert "<strong>ab cd ef</strong>" in MarkdownIt().render(editor.toPlainText())


def test_unselected_text_disables_inline_but_allows_current_line_heading(qtbot, editor):
    select(editor, "text", 2, 2)
    menu = menu_for(qtbot, editor)
    assert not menu.findChild(QMenu, "inlineFormatMenu").isEnabled()
    assert action(menu, "heading.2").isEnabled()
    action(menu, "heading.2").trigger()
    assert editor.toPlainText() == "## text"


def test_block_action_excludes_row_at_selection_end(qtbot, editor):
    source = "😀first\nsecond\nthird"
    select(editor, source, 1, source.index("second"), reverse=True)
    item = action(menu_for(qtbot, editor), "heading.2")
    assert item.isEnabled()
    item.trigger()
    assert editor.toPlainText() == "## 😀first\nsecond\nthird"
    assert editor.textCursor().hasSelection()
    assert editor.textCursor().anchor() > editor.textCursor().position()
    editor.undo()
    assert editor.toPlainText() == source


def test_partial_code_selection_explicitly_removes_whole_block(qtbot, editor):
    source = "> ```python\n> print(1)\n> print(2)\n> ```"
    first = source.index("print")
    select(editor, source, first, first + 5)
    menu = menu_for(qtbot, editor)
    assert not menu.findChild(QMenu, "inlineFormatMenu").isEnabled()
    item = action(menu, "code_block.remove")
    assert item.isEnabled()
    assert "全体" in item.text()
    item.trigger()
    assert editor.toPlainText() == "> print(1)\n> print(2)"
    assert editor.textCursor().hasSelection()
    editor.undo()
    assert editor.toPlainText() == source


def test_stale_popup_and_readonly_actions_do_not_mutate_document(qtbot, editor):
    select(editor, "original", 0, 8)
    stale = action(menu_for(qtbot, editor), "bold.apply")
    editor.setPlainText("replacement")
    stale.trigger()
    assert editor.toPlainText() == "replacement"
    select(editor, "original", 0, 8)
    pending = action(menu_for(qtbot, editor), "bold.apply")
    editor.setReadOnly(True)
    pending.trigger()
    assert editor.toPlainText() == "original"
    disabled = menu_for(qtbot, editor)
    assert not disabled.findChild(QMenu, "inlineFormatMenu").isEnabled()
    assert not disabled.findChild(QMenu, "blockFormatMenu").isEnabled()


def test_source_context_has_formatting_menus_and_keeps_shared_actions_alive(
    qtbot, tmp_path, monkeypatch
):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent

    from md_editor import interactions

    settings = QSettings(str(tmp_path / "formatting.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window, before_close_func=lambda w: w.editor.document().setModified(False))
    window.resize(1000, 650)
    window.show()
    select(window.editor, "選択😀文字", 0, len("選択😀文字"))
    seen = []

    class InspectMenu(QMenu):
        def exec(self, position):
            seen.append(self)
            assert self.findChild(QMenu, "inlineFormatMenu")
            assert self.findChild(QMenu, "blockFormatMenu")
            assert window.editor.textCursor().selectedText() == "選択😀文字"
            item = action(self, "bold.apply")
            if len(seen) == 3:
                item.trigger()

    monkeypatch.setattr(interactions, "QMenu", InspectMenu)
    for _ in range(3):
        point = QPoint(30, 20)
        window.editor.contextMenuEvent(
            QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse, point, window.editor.mapToGlobal(point)
            )
        )
        qtbot.wait(10)
        window.refresh_edit_actions()
    assert window.editor.toPlainText() == "**選択😀文字**"
    assert window.insert_menu.title()
    assert window.paste_format_menu.title()
    assert not any("書式" in a.text() for a in window.menuBar().actions())
    window.editor.undo()
    assert window.editor.toPlainText() == "選択😀文字"


def test_inline_code_partial_selection_removes_and_selects_entire_span(qtbot, editor):
    source = "before `hello 世界😀` after"
    start = source.index("世界")
    select(editor, source, start, start + len("世界😀"))
    item = action(menu_for(qtbot, editor), "inline_code.remove")
    assert item.isEnabled()
    item.trigger()
    assert editor.toPlainText() == "before hello 世界😀 after"
    assert editor.textCursor().selectedText() == "hello 世界😀"
    editor.undo()
    assert editor.toPlainText() == source


def test_formatting_single_table_cell_and_link_label_preserves_surrounding_syntax(qtbot, editor):
    cases = [
        ("| A | B |\n| --- | --- |\n| other | 日本語😀 |", "日本語😀"),
        ("[日本語😀 label](https://example.com/a_b)", "日本語😀"),
    ]
    for source, selected in cases:
        start = source.index(selected)
        select(editor, source, start, start + len(selected))
        item = action(menu_for(qtbot, editor), "bold.apply")
        assert item.isEnabled()
        item.trigger()
        assert (
            editor.toPlainText()
            == source[:start] + "**" + selected + "**" + source[start + len(selected) :]
        )
        assert editor.textCursor().selectedText() == selected
        editor.undo()
        assert editor.toPlainText() == source


@pytest.mark.parametrize(
    ("source", "selected"),
    [
        ("first\n\nsecond", "first\n\nsecond"),
        ("[label](https://example.com/a_b)", "example"),
        ("text $a_b + c$ end", "a_b"),
        ("```python\nprint(1)\n```", "print"),
    ],
)
def test_protected_or_cross_paragraph_selection_disables_inline_menu(
    qtbot, editor, source, selected
):
    start = source.index(selected)
    select(editor, source, start, start + len(selected))
    menu = menu_for(qtbot, editor)
    assert not menu.findChild(QMenu, "inlineFormatMenu").isEnabled()


def test_code_block_creation_detects_language_and_selects_only_body(qtbot, editor):
    source = 'def greet(name):\n    return f"Hello, {name}"'
    select(editor, source, 0, len(source), reverse=True)
    item = action(menu_for(qtbot, editor), "code_block.apply")
    assert item.isEnabled()
    item.trigger()
    result = editor.toPlainText()
    tokens = MarkdownIt().parse(result)
    assert len(tokens) == 1 and tokens[0].type == "fence"
    assert tokens[0].info == "python"
    assert tokens[0].content == source + "\n"
    assert editor.textCursor().selectedText().replace("\u2029", "\n") == source
    assert editor.textCursor().anchor() > editor.textCursor().position()
    editor.undo()
    assert editor.toPlainText() == source
