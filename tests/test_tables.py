import pytest
from markdown_it import MarkdownIt
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QDialog, QMenu, QTableWidgetItem

from md_editor.tables import TableDialog, find_table, parse_delimited, serialize_table


def test_csv_quotes_zeros_empty_cells_and_embedded_newlines():
    text = 'code,text,empty\r\n001,"comma, value",\r\n002,"line1\r\nline2",x\r\n003,"a ""quote""",0\r\n'
    assert parse_delimited(text) == [
        ["code", "text", "empty"],
        ["001", "comma, value", ""],
        ["002", "line1\nline2", "x"],
        ["003", 'a "quote"', "0"],
    ]


def test_tsv_priority_and_empty_trailing_cells():
    assert parse_delimited("001\tcomma,value\t\n002\t\t0") == [
        ["001", "comma,value", ""],
        ["002", "", "0"],
    ]


def test_delimited_invalid_and_explicit_one_column():
    assert parse_delimited("ordinary prose") is None
    assert parse_delimited('a,"unterminated\n') is None
    assert parse_delimited("a\nb", ",") == [["a"], ["b"]]
    with pytest.raises(ValueError):
        parse_delimited("a;b", ";")


def test_delimited_ragged_rows_preserve_cells():
    assert parse_delimited("a,b,c\n0,x\n\n3,y,z") == [
        ["a", "b", "c"],
        ["0", "x", ""],
        ["", "", ""],
        ["3", "y", "z"],
    ]


@pytest.mark.parametrize(
    "source",
    [
        "ordinary | inline | pipes",
        "a | b\nnot a divider",
        "```\n|a|b|\n|-|-|\n|c|d|\n```",
        "    |a|b|\n    |-|-|\n    |c|d|",
        "|a|b|\n|---|\n|c|d|",
        "<table><tr><td>a</td></tr></table>",
    ],
)
def test_no_false_table_context(source):
    for position in range(len(source)):
        assert find_table(source, position) is None


def test_source_range_and_alignment_include_only_table():
    source = "😀 prefix\n\n|a|b|c|\n|:---|:---:|---:|\n|1|2|3|\n\nafter"
    region = find_table(source, source.index("|1|"))
    assert region is not None
    assert source[region.start : region.end] == "|a|b|c|\n|:---|:---:|---:|\n|1|2|3|\n"
    assert region.rows == [["a", "b", "c"], ["1", "2", "3"]]
    assert region.alignments == ["left", "center", "right"]
    assert find_table(source, source.index("after")) is None


@pytest.mark.parametrize(
    "prefix,first",
    [
        ("> ", "> "),
        ("  ", "- "),
        (">   ", "> - "),
        ("   ", "1. "),
        ("  > ", "- > "),
    ],
)
def test_nested_table_preserves_container_and_adjacent_text(prefix, first):
    source = "before\n\n" + first + "|a|b|\n" + prefix + "|-|-|\n" + prefix + "|c|d|\n\nafter"
    region = find_table(source, source.index("|c|"))
    assert region is not None
    assert region.first_prefix == first
    assert region.prefix == prefix
    updated = (
        source[: region.start] + region.wrap(serialize_table(region.rows)) + source[region.end :]
    )
    assert updated.startswith("before\n\n" + first)
    assert updated.endswith("\n\nafter")
    again = find_table(updated, updated.index("c |"))
    assert again is not None
    assert again.rows == region.rows


def test_pipe_backslash_code_break_roundtrip_preserves_rendered_cells():
    source = r"|a|b|" + "\n|---|---|\n" + r"|`a\|b`|text\\\|value<br>next **bold**|"
    region = find_table(source, source.index("text"))
    assert region is not None
    assert region.rows[1][0] == "`a|b`"
    assert "\n" in region.rows[1][1]
    parser = MarkdownIt("commonmark", {"html": True}).enable("table")
    assert parser.render(serialize_table(region.rows)) == parser.render(source)


def test_br_inside_code_is_not_decoded_as_cell_linebreak():
    source = "|a|b|\n|-|-|\n|`<br>`|a<br />b|"
    region = find_table(source, source.index("`<br>`"))
    assert region.rows[1] == ["`<br>`", "a\nb"]


def test_dialog_add_header_and_edit_without_mutating_input(qtbot):
    original = [["001", "a"], ["002", "b"]]
    dialog = TableDialog(rows=original, paste_mode=True)
    qtbot.addWidget(dialog)
    dialog.header_mode.setCurrentIndex(1)
    assert dialog.rows() == [["", ""], ["001", "a"], ["002", "b"]]
    dialog.grid.setItem(0, 0, QTableWidgetItem("番号"))
    dialog.grid.setItem(0, 1, QTableWidgetItem("値"))
    assert dialog.markdown().startswith("| 番号 | 値 |\n")
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert original == [["001", "a"], ["002", "b"]]


def header_menu(dialog, monkeypatch, axis, index=None, action=None, position=None):
    """Use the same viewport position delivered by a header right click."""
    from md_editor import tables

    menus = []

    class Menu(QMenu):
        def exec(self, _position):
            actions = {item.text(): item for item in self.actions() if not item.isSeparator()}
            menus.append(
                {text: (item.isEnabled(), item.isChecked()) for text, item in actions.items()}
            )
            if action is not None:
                actions[action].trigger()

    header = dialog.grid.verticalHeader() if axis == "row" else dialog.grid.horizontalHeader()
    if position is None:
        center = header.sectionViewportPosition(index) + header.sectionSize(index) // 2
        position = (
            QPoint(header.width() // 2, center)
            if axis == "row"
            else QPoint(center, header.height() // 2)
        )
    with monkeypatch.context() as patch:
        patch.setattr(tables, "QMenu", Menu)
        header.customContextMenuRequested.emit(position)
    return menus


def test_dialog_row_column_operations_preserve_alignment(qtbot, monkeypatch):
    dialog = TableDialog(
        rows=[["A", "B"], ["1", "2"], ["3", "4"]], alignments=["left", "right"], paste_mode=False
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.grid.setCurrentCell(0, 0)
    menu = header_menu(dialog, monkeypatch, "row", 1, "行を下へ移動")[0]
    assert not menu["行を上へ移動"][0]
    assert dialog.rows()[2] == ["1", "2"]
    dialog.grid.setCurrentCell(0, 0)
    menu = header_menu(dialog, monkeypatch, "column", 1, "列を左へ移動")[0]
    assert not menu["列を右へ移動"][0]
    assert menu["右寄せ"][1]
    assert dialog.rows()[0] == ["B", "A"]
    assert dialog.alignments() == ["right", "left"]
    # Column alignment applies to the clicked column, not another selected cell.
    dialog.grid.setCurrentCell(0, 0)
    header_menu(dialog, monkeypatch, "column", 1, "中央揃え")
    assert dialog.alignments() == ["right", "center"]
    header_menu(dialog, monkeypatch, "column", 1, "列を削除")
    assert dialog.rows() == [["B"], ["4"], ["2"]]
    assert dialog.alignments() == ["right"]
    menu = header_menu(dialog, monkeypatch, "column", 0)[0]
    assert not menu["列を削除"][0]
    assert not menu["列を左へ移動"][0]
    assert not menu["列を右へ移動"][0]
    # The header stays intact even if a disabled operation is triggered.
    menu = header_menu(dialog, monkeypatch, "row", 0, "行を削除")[0]
    assert len(dialog.rows()) == 3
    for title in ("上に行を挿入", "行を削除", "行を上へ移動", "行を下へ移動"):
        assert not menu[title][0]
    dialog.grid.setCurrentCell(0, 0)
    header_menu(dialog, monkeypatch, "row", 2, "行を削除")
    assert dialog.rows() == [["B"], ["4"]]
    # Empty header space is outside the data; no context actions are offered.
    vertical = dialog.grid.verticalHeader()
    horizontal = dialog.grid.horizontalHeader()
    assert not header_menu(dialog, monkeypatch, "row", position=QPoint(5, vertical.length() + 10))
    assert not header_menu(
        dialog, monkeypatch, "column", position=QPoint(horizontal.length() + 10, 5)
    )


@pytest.mark.parametrize(
    "action,expected",
    [
        ("上に行を挿入", [["H"], [""], ["first"], ["last"]]),
        ("下に行を挿入", [["H"], ["first"], [""], ["last"]]),
    ],
)
def test_header_context_inserts_row_above_or_below_clicked_row(
    qtbot, monkeypatch, action, expected
):
    dialog = TableDialog(rows=[["H"], ["first"], ["last"]], paste_mode=False)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.grid.setCurrentCell(2, 0)
    header_menu(dialog, monkeypatch, "row", 1, action)
    assert dialog.rows() == expected
    assert dialog.rows()[0] == ["H"]
    # Even a header-only table can gain its first data row from the header menu.
    dialog.set_rows([["H"]])
    header_menu(dialog, monkeypatch, "row", 0, "下に行を挿入")
    assert dialog.rows() == [["H"], [""]]


@pytest.mark.parametrize(
    "action,expected,alignment",
    [
        ("左に列を挿入", [["A", "", "B"], ["1", "", "2"]], ["left", "", "right"]),
        ("右に列を挿入", [["A", "B", ""], ["1", "2", ""]], ["left", "right", ""]),
    ],
)
def test_header_context_inserts_column_on_clicked_side(
    qtbot, monkeypatch, action, expected, alignment
):
    dialog = TableDialog(
        rows=[["A", "B"], ["1", "2"]], alignments=["left", "right"], paste_mode=False
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.grid.setCurrentCell(1, 0)
    header_menu(dialog, monkeypatch, "column", 1, action)
    assert dialog.rows() == expected
    assert dialog.alignments() == alignment
    # The final column has a real independent size instead of absorbing spare width.
    last = dialog.grid.columnCount() - 1
    dialog.grid.setColumnWidth(last, 95)
    dialog.resize(dialog.width() + 120, dialog.height())
    qtbot.wait(10)
    assert dialog.grid.columnWidth(last) == 95


def test_dialog_delimiter_override_and_cell_copy_paste(qtbot):
    from PySide6.QtWidgets import QApplication

    dialog = TableDialog(raw_text="a,b\nx,y", delimiter=",")
    qtbot.addWidget(dialog)
    assert dialog.rows() == [["a", "b"], ["x", "y"]]
    dialog.delimiter_combo.setCurrentIndex(dialog.delimiter_combo.findData("\t"))
    assert dialog.rows() == [["a,b"], ["x,y"]]
    QApplication.clipboard().setText('001\t"comma,value"\n002\t"line1\nline2"')
    dialog.grid.setCurrentCell(1, 0)
    dialog.grid.paste_cells()
    assert dialog.rows()[1:] == [["001", "comma,value"], ["002", "line1\nline2"]]
    assert "line1<br>line2" in dialog.markdown()


def test_dialog_cell_multiline_edit_commits_from_keyboard(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPlainTextEdit

    dialog = TableDialog(rows=[["A", "B"], ["old", "value"]])
    qtbot.addWidget(dialog)
    dialog.show()
    item = dialog.grid.item(1, 0)
    dialog.grid.setCurrentItem(item)
    dialog.grid.editItem(item)
    editor = dialog.grid.findChild(QPlainTextEdit)
    assert editor is not None
    editor.setPlainText("line1")
    cursor = editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    qtbot.keyClick(editor, Qt.Key.Key_Return, modifier=Qt.KeyboardModifier.ShiftModifier)
    qtbot.keyClicks(editor, "line2")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert dialog.rows()[1][0] == "line1\nline2"
    assert "line1<br>line2" in dialog.markdown()


def test_dialog_inherits_parent_palette_and_shows_literal_br(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QLabel, QWidget

    from md_editor.theme import palette

    parent = QWidget()
    parent.setPalette(palette(True))
    qtbot.addWidget(parent)
    dialog = TableDialog(parent, rows=[["A"], ["B"]])
    qtbot.addWidget(dialog)
    assert dialog.palette().color(QPalette.ColorRole.Window) == parent.palette().color(
        QPalette.ColorRole.Window
    )
    assert dialog.grid.palette().color(QPalette.ColorRole.Base) == parent.palette().color(
        QPalette.ColorRole.Base
    )
    help_label = next(label for label in dialog.findChildren(QLabel) if "<br>" in label.text())
    assert help_label.textFormat() == Qt.TextFormat.PlainText


@pytest.mark.parametrize("opening,closing", [("$$", "$$"), (r"\[", r"\]")])
def test_math_body_is_not_an_editable_table_but_adjacent_table_is(opening, closing):
    source = (
        f"{opening}\n|fake|math|\n|---|---|\n|x|y|\n{closing}\n\n|real|table|\n|---|---|\n|one|two|"
    )
    for label in ("fake", "|---|", "|x|y|"):
        assert find_table(source, source.index(label)) is None
    actual = find_table(source, source.index("real"))
    assert actual is not None
    assert actual.rows == [["real", "table"], ["one", "two"]]
    assert source[actual.start : actual.end] == "|real|table|\n|---|---|\n|one|two|"
