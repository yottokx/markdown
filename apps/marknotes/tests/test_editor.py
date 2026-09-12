from marknotes.editor import SourceEditor


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
