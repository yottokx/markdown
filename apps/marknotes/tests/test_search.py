import pytest

from marknotes.editor import SourceEditor
from marknotes.search import SearchBar, python_position, qt_position


@pytest.fixture
def search(qtbot):
    editor = SourceEditor()
    qtbot.addWidget(editor)
    editor.show()
    bar = SearchBar(editor)
    qtbot.addWidget(bar)
    bar.show_bar(replace=True)
    return editor, bar


def prepare(search, source, query, *, regex=False, sensitive=False, replacement=""):
    editor, bar = search
    editor.replace_source(source)
    bar.regex.setChecked(regex)
    bar.case_sensitive.setChecked(sensitive)
    bar.query.setText(query)
    bar.replacement.setText(replacement)
    bar.refresh()
    return editor, bar


def cursor_at(editor, position):
    cursor = editor.textCursor()
    cursor.setPosition(position)
    editor.setTextCursor(cursor)


def span(editor):
    return editor.textCursor().selectionStart(), editor.textCursor().selectionEnd()


def test_emoji_navigation_uses_utf16_positions_and_wraps_in_both_directions(search, qtbot):
    editor, bar = prepare(search, "😀cat 猫😀cat CAT", "cat")
    expected = [(2, 5), (9, 12), (13, 16)]
    with qtbot.waitSignal(bar.navigated, timeout=500):
        bar.next()
    assert span(editor) == expected[0]
    bar.next()
    assert span(editor) == expected[1]
    bar.next()
    assert span(editor) == expected[2]
    bar.next()
    assert span(editor) == expected[0]
    bar.previous()
    assert span(editor) == expected[2]
    bar.previous()
    assert span(editor) == expected[1]
    assert bar.status.text() == "2 / 3 件"
    assert len(editor.extraSelections()) == 3


def test_unicode_position_helpers_roundtrip_only_character_boundaries():
    source = "😀a𠮷b"
    assert [qt_position(source, i) for i in range(len(source) + 1)] == [0, 2, 3, 5, 6]
    assert [python_position(source, i) for i in (0, 2, 3, 5, 6)] == [0, 1, 2, 3, 4]


def test_literal_metacharacters_regex_and_case_options(search):
    editor, bar = prepare(search, "A.b aXb a.b A.B", "a.b")
    assert [m.span() for m in bar._matches] == [(0, 3), (8, 11), (12, 15)]
    bar.case_sensitive.setChecked(True)
    assert [m.span() for m in bar._matches] == [(8, 11)]
    bar.regex.setChecked(True)
    assert [m.span() for m in bar._matches] == [(4, 7), (8, 11)]
    bar.case_sensitive.setChecked(False)
    assert len(bar._matches) == 4
    bar.query.setText("")
    assert bar._matches == []
    assert editor.extraSelections() == []


def test_manual_cursor_move_can_find_previously_selected_match(search):
    editor, bar = prepare(search, "first cat last", "cat")
    bar.next()
    assert span(editor) == (6, 9)
    cursor_at(editor, 0)
    bar.query.setText("cat|last")
    bar.regex.setChecked(True)
    bar.next()
    assert span(editor) == (6, 9)
    cursor_at(editor, 0)
    bar.next()
    assert span(editor) == (6, 9)


def test_replace_one_expands_named_and_numbered_captures_after_astral_text(search):
    editor, bar = prepare(
        search,
        "😀item-12 item-34",
        r"(?P<label>item)-(\d+)",
        regex=True,
        replacement=r"\2:\g<label>😀",
    )
    bar.next()
    bar.replace_one()
    assert editor.toPlainText() == "😀12:item😀 item-34"
    assert editor.textCursor().selectedText() == "item-34"
    editor.undo()
    assert editor.toPlainText() == "😀item-12 item-34"
    editor.redo()
    assert editor.toPlainText() == "😀12:item😀 item-34"


def test_literal_replacement_does_not_interpret_backslashes(search):
    editor, bar = prepare(search, "cat cat", "cat", replacement=r"\1\folder")
    bar.replace_all()
    assert editor.toPlainText() == r"\1\folder \1\folder"


@pytest.mark.parametrize("replacement", [r"\2", r"\g<missing>", r"\k"])
@pytest.mark.parametrize("method", ["replace_one", "replace_all"])
def test_invalid_replacement_preserves_entire_document(search, replacement, method):
    original = "😀a b a"
    editor, bar = prepare(search, original, "(a)", regex=True, replacement=replacement)
    bar.next()
    before = span(editor)
    getattr(bar, method)()
    assert editor.toPlainText() == original
    assert span(editor) == before
    assert not editor.document().isModified()
    assert not editor.document().isUndoAvailable()
    assert "置換エラー" in bar.status.text()


def test_invalid_query_and_empty_query_never_replace_or_highlight(search):
    editor, bar = prepare(search, "unchanged 😀", "(", regex=True, replacement="changed")
    bar.next()
    bar.previous()
    bar.replace_one()
    bar.replace_all()
    assert editor.toPlainText() == "unchanged 😀"
    assert "正規表現エラー" in bar.status.text()
    assert not editor.document().isModified()
    assert editor.extraSelections() == []
    bar.query.setText("")
    bar.replace_all()
    assert editor.toPlainText() == "unchanged 😀"


def test_replace_all_is_one_undo_redo_with_adjacent_unicode_and_multiline_matches(search):
    source = "😀a😀a\na😀a"
    editor, bar = prepare(search, source, "a", replacement="猫😀")
    bar.replace_all()
    expected = "😀猫😀😀猫😀\n猫😀😀猫😀"
    assert editor.toPlainText() == expected
    assert bar.status.text() == "4 件を置換"
    editor.undo()
    assert editor.toPlainText() == source
    assert not editor.document().isUndoAvailable()
    editor.redo()
    assert editor.toPlainText() == expected


def test_regex_multiline_capture_replacement_keeps_newlines_and_backreferences(search):
    editor, bar = prepare(
        search, "one\ntwo\nthree", r"(one)\n(two)", regex=True, replacement=r"\2\n\1"
    )
    bar.replace_all()
    assert editor.toPlainText() == "two\none\nthree"
    editor.undo()
    assert editor.toPlainText() == "one\ntwo\nthree"


def test_zero_width_navigation_advances_one_unicode_character_and_wraps(search):
    editor, bar = prepare(search, "😀a", r"(?=.)|$", regex=True)
    positions = []
    for _ in range(4):
        bar.next()
        positions.append(span(editor))
    assert positions == [(0, 0), (2, 2), (3, 3), (0, 0)]
    bar.previous()
    assert span(editor) == (3, 3)
    bar.previous()
    assert span(editor) == (2, 2)


@pytest.mark.parametrize("replacement", ["#", ""])
def test_zero_width_replace_one_moves_past_original_boundary(search, replacement):
    editor, bar = prepare(search, "😀😀", r"(?=😀)", regex=True, replacement=replacement)
    bar.next()
    bar.replace_one()
    assert editor.toPlainText() == replacement + "😀😀"
    assert span(editor) == (len(replacement) + 2, len(replacement) + 2)


def test_replace_all_zero_width_boundaries_inserts_once_each_and_undo_restores_source(search):
    editor, bar = prepare(search, "😀a", r"(?=.)|$", regex=True, replacement="|")
    bar.replace_all()
    assert editor.toPlainText() == "|😀|a|"
    editor.undo()
    assert editor.toPlainText() == "😀a"
    editor.redo()
    assert editor.toPlainText() == "|😀|a|"


def test_close_clears_highlights_and_theme_does_not_change_source(search):
    editor, bar = prepare(search, "cat cat", "cat")
    assert len(editor.extraSelections()) == 2
    bar.apply_theme(True)
    assert len(editor.extraSelections()) == 2
    assert not editor.document().isModified()
    bar.close_bar()
    assert editor.extraSelections() == []
    bar.refresh()
    assert editor.extraSelections() == []


def test_navigation_reuses_matches_without_refreshing_highlights(search, monkeypatch):
    editor, bar = prepare(search, "😀cat cat CAT", "cat")
    matches = bar._matches
    refreshed = []
    bar.refreshed.connect(lambda: refreshed.append(True))

    def unexpected_refresh():
        pytest.fail("Unchanged search navigation must not rebuild highlights")

    monkeypatch.setattr(bar, "refresh", unexpected_refresh)
    for move, expected in (
        (bar.next, (2, 5)),
        (bar.next, (6, 9)),
        (bar.next, (10, 13)),
        (bar.next, (2, 5)),
        (bar.previous, (10, 13)),
    ):
        move()
        assert span(editor) == expected
    assert bar._matches is matches
    assert refreshed == []
    assert len(editor.extraSelections()) == 3
    assert bar.status.text() == "3 / 3 件"


def test_navigation_refreshes_pending_edits_and_undo_redo_before_timer(search):
    editor, bar = prepare(search, "cat cat", "cat")
    refreshed = []
    bar.refreshed.connect(lambda: refreshed.append(True))
    editor.insertPlainText("😀 ")
    assert bar._timer.isActive()
    bar.next()
    assert span(editor) == (3, 6)
    assert len(refreshed) == 1
    assert not bar._timer.isActive()
    assert [m.span() for m in bar._matches] == [(2, 5), (6, 9)]

    editor.undo()
    cursor_at(editor, 0)
    bar.next()
    assert span(editor) == (0, 3)
    assert len(refreshed) == 2
    editor.redo()
    cursor_at(editor, 0)
    bar.next()
    assert span(editor) == (3, 6)
    assert len(refreshed) == 3
    bar.next()
    assert span(editor) == (7, 10)
    assert len(refreshed) == 3
