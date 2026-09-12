"""Delimited text import and GFM table editing with exact source boundaries."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeySequence, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from md_editor.math_parser import math_plugin


def parse_delimited(text: str, delimiter: str | None = None) -> list[list[str]] | None:
    """Read CSV/TSV losslessly as strings; return None for non-tabular text.

    Automatic detection is for the explicit table-paste action only. A single
    column is accepted when the user explicitly selects the delimiter.
    """
    if not text or not text.strip():
        return None
    delimiters = [delimiter] if delimiter is not None else ["\t", ","]
    candidates: list[list[list[str]]] = []
    for separator in delimiters:
        if separator not in {",", "\t"}:
            raise ValueError("Delimiter must be a comma or tab")
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=separator, strict=True))
        except csv.Error:
            continue
        if not rows:
            continue
        width = max(map(len, rows))
        if width == 0 or (delimiter is None and width < 2):
            continue
        # Ragged input is displayed with empty cells instead of discarding it.
        rows = [[cell.replace("\r\n", "\n").replace("\r", "\n") for cell in row] for row in rows]
        candidates.append([row + [""] * (width - len(row)) for row in rows])
    if not candidates:
        return None
    # TSV is the normal spreadsheet payload and takes priority over commas
    # occurring inside spreadsheet cells.
    return candidates[0]


def _cell_newlines(cell: str) -> str:
    """Expose BR as an editable newline, preserving BR inside inline code."""
    parts = re.split(r"(`+)", cell)
    fence = ""
    for index, part in enumerate(parts):
        if index % 2:
            if not fence:
                fence = part
            elif fence == part:
                fence = ""
        elif not fence:
            parts[index] = re.sub(r"<br\s*/?>", "\n", part, flags=re.IGNORECASE)
    return "".join(parts)


def _serialize_cell(cell: str) -> str:
    # Table parsing removes one backslash immediately before EVERY pipe, even
    # in a code span. Insert that one backslash back; retain all existing ones.
    return cell.replace("\r\n", "\n").replace("\r", "\n").replace("|", "\\|").replace("\n", "<br>")


def serialize_table(rows: list[list[str]], alignments: list[str] | None = None) -> str:
    """Serialize cell Markdown to a GFM table; row zero is always the header."""
    if not rows or not any(rows):
        return ""
    width = max(map(len, rows))
    padded = [row + [""] * (width - len(row)) for row in rows]
    alignments = (list(alignments or []) + [""] * width)[:width]
    separators = {"": "---", "left": ":---", "center": ":---:", "right": "---:"}

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(_serialize_cell(cell) for cell in cells) + " |"

    result = [line(padded[0]), line([separators.get(value, "---") for value in alignments])]
    result.extend(line(row) for row in padded[1:])
    return "\n".join(result)


@dataclass(frozen=True, slots=True)
class TableRegion:
    start: int
    end: int
    rows: list[list[str]]
    alignments: list[str]
    prefix: str = ""
    first_prefix: str = ""
    trailing_newline: str = ""

    def wrap(self, markdown: str) -> str:
        """Restore the surrounding block quote/list without touching its neighbors."""
        lines = markdown.split("\n")
        result = [self.first_prefix + lines[0]]
        result.extend(self.prefix + line for line in lines[1:])
        return "\n".join(result) + self.trailing_newline


_PREFIX = re.compile(r"^[ \t]*(?:(?:>[ \t]*)|(?:(?:[-+*]|\d+[.)])[ \t]+))*")


def find_table(source: str, position: int) -> TableRegion | None:
    """Find a parser-confirmed GFM table at a Python codepoint position."""
    if position < 0 or position > len(source):
        return None
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    parser = MarkdownIt("commonmark", {"html": True}).enable("table").use(math_plugin)
    tokens = parser.parse(source)
    for index, token in enumerate(tokens):
        if token.type != "table_open" or token.map is None:
            continue
        start_line, end_line = token.map
        start, end = offsets[start_line], offsets[end_line]
        # The caret at EOF belongs to the final row only without a final newline.
        at_eof = position == end == len(source) and not source.endswith(("\n", "\r"))
        if not (start <= position < end or at_eof):
            continue
        rows: list[list[str]] = []
        alignments: list[str] = []
        for child in tokens[index + 1 :]:
            if child.type == "table_close":
                break
            if child.type == "tr_open":
                rows.append([])
            elif child.type == "th_open":
                alignments.append((child.attrGet("style") or "").removeprefix("text-align:"))
            elif child.type == "inline" and rows:
                rows[-1].append(_cell_newlines(child.content))
        header = lines[start_line]
        divider = lines[start_line + 1]
        first_prefix = _PREFIX.match(header).group(0)
        prefix = re.match(r"^[ \t>]*", divider).group(0)
        last = lines[end_line - 1]
        newline = "\r\n" if last.endswith("\r\n") else "\n" if last.endswith("\n") else ""
        return TableRegion(start, end, rows, alignments, prefix, first_prefix, newline)
    return None


class _CellDelegate(QStyledItemDelegate):
    """Shift+Enter inserts a real cell newline; Enter commits the edited cell."""

    def createEditor(self, parent, option, index):
        editor = QPlainTextEdit(parent)
        editor.setTabChangesFocus(True)
        editor.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.ItemDataRole.EditRole) or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect
        rect.setHeight(max(rect.height(), editor.fontMetrics().lineSpacing() * 3 + 8))
        editor.setGeometry(rect)

    def eventFilter(self, editor, event):
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(editor, QPlainTextEdit)
            and event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
        ):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                editor.insertPlainText("\n")
            else:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
            return True
        return super().eventFilter(editor, event)


class _TableGrid(QTableWidget):
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_cells()
            event.accept()
        elif event.matches(QKeySequence.StandardKey.Paste):
            self.paste_cells()
            event.accept()
        else:
            super().keyPressEvent(event)

    def copy_cells(self) -> None:
        selection = self.selectedRanges()
        if not selection:
            return
        area = selection[0]
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        for row in range(area.topRow(), area.bottomRow() + 1):
            writer.writerow(
                [
                    self.item(row, col).text() if self.item(row, col) else ""
                    for col in range(area.leftColumn(), area.rightColumn() + 1)
                ]
            )
        QApplication.clipboard().setText(stream.getvalue())

    def paste_cells(self) -> None:
        rows = parse_delimited(QApplication.clipboard().text(), delimiter="\t")
        if not rows:
            return
        top, left = max(0, self.currentRow()), max(0, self.currentColumn())
        self.setRowCount(max(self.rowCount(), top + len(rows)))
        self.setColumnCount(max(self.columnCount(), left + len(rows[0])))
        for row, cells in enumerate(rows, top):
            for col, text in enumerate(cells, left):
                self.setItem(row, col, QTableWidgetItem(text))


class _TableComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        # A native Windows arrow can ignore dark palettes; draw a small vector
        # chevron in the actual control text color instead.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Text), 1.5))
        center = QPointF(self.width() - 11, self.height() / 2)
        painter.drawPolyline(
            QPolygonF(
                [center + QPointF(-3, -1.5), center + QPointF(0, 1.5), center + QPointF(3, -1.5)]
            )
        )


class TableDialog(QDialog):
    """Modal, reversible table editor shared by import and source editing."""

    def __init__(
        self,
        parent=None,
        *,
        rows: list[list[str]] | None = None,
        alignments: list[str] | None = None,
        paste_mode: bool = True,
        delimiter: str | None = None,
        raw_text: str | None = None,
        create_mode: bool = False,
    ):
        super().__init__(parent)
        # QDialog is a separate window: QWidget palettes do not automatically
        # propagate from its parent unless explicitly requested.
        self.setAttribute(Qt.WidgetAttribute.WA_WindowPropagation, True)
        if parent is not None:
            self.setPalette(parent.palette())
        self._style_controls()
        self.setWindowTitle(
            "表を作成" if create_mode else "表として貼り付け" if paste_mode else "表を編集"
        )
        self.resize(860, 570)
        self.setModal(True)
        self._alignments = list(alignments or [])
        self._raw_text = raw_text
        self._added_header = False
        self.grid = _TableGrid(self)
        self.grid.setItemDelegate(_CellDelegate(self.grid))
        self.grid.itemChanged.connect(lambda _: self.grid.resizeRowsToContents())
        self.table = self.grid
        self.grid.setAlternatingRowColors(True)
        columns = self.grid.horizontalHeader()
        columns.setStretchLastSection(False)
        columns.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for header, callback in (
            (self.grid.verticalHeader(), self._row_context_menu),
            (columns, self._column_context_menu),
        ):
            header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            header.customContextMenuRequested.connect(callback)
            header.setToolTip("右クリックで行・列の挿入、削除、移動を操作できます")
        layout = QVBoxLayout(self)
        options = QHBoxLayout()
        self.header_mode = _TableComboBox()
        self.header_mode.addItems(["1行目をヘッダーとして使用", "ヘッダー行を追加して編集"])
        self.header_mode.setVisible(paste_mode and not create_mode)
        options.addWidget(self.header_mode)
        self.delimiter_combo = _TableComboBox()
        self.delimiter_combo.addItem("TSV（タブ区切り）", "\t")
        self.delimiter_combo.addItem("CSV（カンマ区切り）", ",")
        chosen = delimiter or ("\t" if raw_text and "\t" in raw_text else ",")
        self.delimiter_combo.setCurrentIndex(self.delimiter_combo.findData(chosen))
        self.delimiter_combo.setVisible(raw_text is not None and not create_mode)
        options.addWidget(self.delimiter_combo)
        options.addStretch()
        layout.addLayout(options)
        help_label = QLabel(
            "セルはMarkdown形式です。Shift+Enterでセル内改行、Enterで確定。改行は <br> に変換します。"
        )
        help_label.setTextFormat(Qt.TextFormat.PlainText)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        layout.addWidget(self.grid)
        footer = QHBoxLayout()
        self.dimensions = QLabel()
        footer.addWidget(self.dimensions)
        footer.addStretch()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "OK" if create_mode else "挿入" if paste_mode else "適用"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        initial = rows if rows is not None else parse_delimited(raw_text or "", chosen)
        self.set_rows(initial or [["", ""] for _ in range(3 if create_mode else 2)])
        if create_mode:
            for column in range(self.grid.columnCount()):
                self.grid.setColumnWidth(column, 140)
        self.header_mode.currentIndexChanged.connect(self._change_header)
        self.delimiter_combo.currentIndexChanged.connect(self._change_delimiter)
        self.grid.model().rowsInserted.connect(self._update_labels)
        self.grid.model().rowsRemoved.connect(self._update_labels)
        self.grid.model().columnsInserted.connect(self._update_labels)
        self.grid.model().columnsRemoved.connect(self._update_labels)

    def _style_controls(self):
        # Native Windows controls and an inherited stylesheet can ignore a
        # window-only palette. Set the dialog's colors explicitly for all its
        # controls, without changing the application or another window's theme.
        colors = self.palette()

        def color(role):
            return colors.color(role).name()

        window = color(QPalette.ColorRole.Window)
        text = color(QPalette.ColorRole.WindowText)
        base = color(QPalette.ColorRole.Base)
        alternate = color(QPalette.ColorRole.AlternateBase)
        button = color(QPalette.ColorRole.Button)
        highlight = color(QPalette.ColorRole.Highlight)
        selected = color(QPalette.ColorRole.HighlightedText)
        dark = colors.color(QPalette.ColorRole.Window).lightness() < 128
        border = "#465264" if dark else "#cdd5df"
        hover = "#3e4b5e" if dark else "#dde8f7"
        self.setStyleSheet(f"""
            QDialog, QLabel {{ background-color: {window}; color: {text}; }}
            QTableWidget, QPlainTextEdit, QComboBox QAbstractItemView {{
                background-color: {base}; color: {text};
                alternate-background-color: {alternate};
                selection-background-color: {highlight}; selection-color: {selected};
                gridline-color: {border}; border: 1px solid {border};
            }}
            QHeaderView {{ background-color: {base}; color: {text}; }}
            QHeaderView::section, QTableCornerButton::section {{
                background-color: {button}; color: {text}; padding: 5px;
                border: none; border-right: 1px solid {border}; border-bottom: 1px solid {border};
            }}
            QPushButton, QComboBox {{
                background-color: {button}; color: {text}; border: 1px solid {border};
                border-radius: 3px; padding: 4px 9px;
            }}
            QPushButton:hover, QComboBox:hover {{ background-color: {hover}; }}
            QPushButton:default {{ border-color: {highlight}; }}
            QPushButton:disabled {{ color: #8491a3; }}
            QComboBox {{ padding-right: 22px; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; }}
        """)

    def set_rows(self, rows: list[list[str]]) -> None:
        width = max((len(row) for row in rows), default=1)
        self.grid.clearContents()
        self.grid.setRowCount(max(1, len(rows)))
        self.grid.setColumnCount(max(1, width))
        for row, cells in enumerate(rows):
            for col, text in enumerate(cells):
                self.grid.setItem(row, col, QTableWidgetItem(str(text)))
        self._update_labels()
        self.grid.resizeColumnsToContents()
        self.grid.setCurrentCell(0, 0)

    def rows(self) -> list[list[str]]:
        return [
            [
                self.grid.item(row, col).text() if self.grid.item(row, col) else ""
                for col in range(self.grid.columnCount())
            ]
            for row in range(self.grid.rowCount())
        ]

    def alignments(self) -> list[str]:
        self._ensure_alignments()
        return self._alignments.copy()

    def markdown(self) -> str:
        # Finish a cell delegate's pending edit before extracting the value.
        self.grid.clearFocus()
        return serialize_table(self.rows(), self.alignments())

    def _ensure_alignments(self):
        width = self.grid.columnCount()
        self._alignments = (self._alignments + [""] * width)[:width]

    def _update_labels(self, *_):
        self._ensure_alignments()
        self.grid.setVerticalHeaderLabels(
            ["ヘッダー"] + [str(i) for i in range(1, self.grid.rowCount())]
        )
        self.grid.setHorizontalHeaderLabels([str(i + 1) for i in range(self.grid.columnCount())])
        self.dimensions.setText(
            f"{max(0, self.grid.rowCount() - 1)} データ行 × {self.grid.columnCount()} 列"
        )

    def _change_header(self, index: int):
        if index == 1 and not self._added_header:
            self.grid.insertRow(0)
            for col in range(self.grid.columnCount()):
                self.grid.setItem(0, col, QTableWidgetItem(""))
            self._added_header = True
            self.grid.setCurrentCell(0, 0)
        elif index == 0 and self._added_header:
            self.grid.removeRow(0)
            self._added_header = False
        self._update_labels()

    def _change_delimiter(self, _):
        if self._raw_text is None:
            return
        rows = parse_delimited(self._raw_text, self.delimiter_combo.currentData())
        if rows is None:
            self.dimensions.setText("区切り文字または引用符が不正です")
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        self._added_header = False
        self._alignments = []
        self.set_rows(rows)
        self._change_header(self.header_mode.currentIndex())

    @staticmethod
    def _menu_action(menu, title, callback, *, enabled=True):
        action = menu.addAction(title)
        action.setEnabled(enabled)
        action.triggered.connect(callback)
        return action

    def _row_context_menu(self, position):
        header = self.grid.verticalHeader()
        row = header.logicalIndexAt(position)
        if row < 0:
            return
        menu = QMenu(self)
        menu.setPalette(self.palette())
        self._menu_action(
            menu, "上に行を挿入", lambda: self.add_row(row=row, above=True), enabled=row > 0
        )
        self._menu_action(menu, "下に行を挿入", lambda: self.add_row(row=row))
        menu.addSeparator()
        self._menu_action(menu, "行を削除", lambda: self.delete_row(row), enabled=row > 0)
        self._menu_action(menu, "行を上へ移動", lambda: self.move_row(-1, row), enabled=row > 1)
        self._menu_action(
            menu,
            "行を下へ移動",
            lambda: self.move_row(1, row),
            enabled=0 < row < self.grid.rowCount() - 1,
        )
        try:
            menu.exec(header.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    def _column_context_menu(self, position):
        header = self.grid.horizontalHeader()
        column = header.logicalIndexAt(position)
        if column < 0:
            return
        menu = QMenu(self)
        menu.setPalette(self.palette())
        self._menu_action(menu, "左に列を挿入", lambda: self.add_column(column=column, left=True))
        self._menu_action(menu, "右に列を挿入", lambda: self.add_column(column=column))
        menu.addSeparator()
        self._menu_action(
            menu,
            "列を削除",
            lambda: self.delete_column(column),
            enabled=self.grid.columnCount() > 1,
        )
        self._menu_action(
            menu, "列を左へ移動", lambda: self.move_column(-1, column), enabled=column > 0
        )
        self._menu_action(
            menu,
            "列を右へ移動",
            lambda: self.move_column(1, column),
            enabled=column < self.grid.columnCount() - 1,
        )
        menu.addSeparator()
        self._ensure_alignments()
        for title, alignment in [
            ("標準の配置", ""),
            ("左寄せ", "left"),
            ("中央揃え", "center"),
            ("右寄せ", "right"),
        ]:
            action = self._menu_action(
                menu,
                title,
                lambda _checked=False, value=alignment: self._set_alignment(column, value),
            )
            action.setCheckable(True)
            action.setChecked(self._alignments[column] == alignment)
        try:
            menu.exec(header.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    def _set_alignment(self, column: int, alignment: str):
        self._ensure_alignments()
        if 0 <= column < self.grid.columnCount():
            self._alignments[column] = alignment

    def add_row(self, *, row: int | None = None, above: bool = False):
        row = self.grid.currentRow() if row is None else row
        insertion = row if above else row + 1
        if row < 0:
            insertion = self.grid.rowCount()
        # The first row is always the table header and cannot be displaced.
        if not 1 <= insertion <= self.grid.rowCount():
            return
        self.grid.insertRow(insertion)
        self.grid.setCurrentCell(insertion, max(0, self.grid.currentColumn()))

    def delete_row(self, row: int | None = None):
        row = self.grid.currentRow() if row is None else row
        if 0 < row < self.grid.rowCount():
            self.grid.removeRow(row)

    def move_row(self, direction: int, row: int | None = None):
        row = self.grid.currentRow() if row is None else row
        target = row + direction
        if not 0 < row < self.grid.rowCount() or not 0 < target < self.grid.rowCount():
            return
        for column in range(self.grid.columnCount()):
            first, second = self.grid.takeItem(row, column), self.grid.takeItem(target, column)
            self.grid.setItem(row, column, second or QTableWidgetItem(""))
            self.grid.setItem(target, column, first or QTableWidgetItem(""))
        self.grid.setCurrentCell(target, max(0, self.grid.currentColumn()))

    def add_column(self, *, column: int | None = None, left: bool = False):
        column = self.grid.currentColumn() if column is None else column
        insertion = column if left else column + 1
        if column < 0:
            insertion = self.grid.columnCount()
        if not 0 <= insertion <= self.grid.columnCount():
            return
        self._ensure_alignments()
        alignments = self._alignments.copy()
        alignments.insert(insertion, "")
        self.grid.insertColumn(insertion)
        self._alignments = alignments
        self.grid.setCurrentCell(max(0, self.grid.currentRow()), insertion)

    def delete_column(self, column: int | None = None):
        column = self.grid.currentColumn() if column is None else column
        if not 0 <= column < self.grid.columnCount() or self.grid.columnCount() <= 1:
            return
        self._ensure_alignments()
        alignments = self._alignments.copy()
        del alignments[column]
        self.grid.removeColumn(column)
        self._alignments = alignments

    def move_column(self, direction: int, column: int | None = None):
        column = self.grid.currentColumn() if column is None else column
        target = column + direction
        if not 0 <= column < self.grid.columnCount() or not 0 <= target < self.grid.columnCount():
            return
        for row in range(self.grid.rowCount()):
            first, second = self.grid.takeItem(row, column), self.grid.takeItem(row, target)
            self.grid.setItem(row, column, second or QTableWidgetItem(""))
            self.grid.setItem(row, target, first or QTableWidgetItem(""))
        self._ensure_alignments()
        self._alignments[column], self._alignments[target] = (
            self._alignments[target],
            self._alignments[column],
        )
        self.grid.setCurrentCell(max(0, self.grid.currentRow()), target)
