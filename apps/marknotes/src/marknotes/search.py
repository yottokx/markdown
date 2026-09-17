"""Search and replacement without mixing Python and QTextCursor offsets."""

from __future__ import annotations

import re

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def qt_position(text: str, position: int) -> int:
    return len(text[:position].encode("utf-16-le")) // 2


def python_position(text: str, position: int) -> int:
    return len(text.encode("utf-16-le")[: position * 2].decode("utf-16-le", errors="ignore"))


class SearchBar(QWidget):
    navigated = Signal()
    closed = Signal()
    refreshed = Signal()

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.query = QLineEdit()
        self.query.setPlaceholderText("検索する文字列")
        self.query.setClearButtonEnabled(True)
        self.replacement = QLineEdit()
        self.replacement.setPlaceholderText("置換後の文字列（正規表現では \\1 等を使用可）")
        self.case_sensitive = QCheckBox("大小文字を区別")
        self.regex = QCheckBox("正規表現")
        self.status = QLabel()
        self.status.setMinimumWidth(100)
        self._matches: list[re.Match] = []
        self._match_state = None
        self._last_span: tuple[int, int] | None = None
        self._dark = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.refresh)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        row = QHBoxLayout()
        outer.addLayout(row)
        row.addWidget(self.query, 1)
        for title, callback in (("前へ", self.previous), ("次へ", self.next)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addWidget(self.case_sensitive)
        row.addWidget(self.regex)
        row.addWidget(self.status)
        close = QPushButton("閉じる")
        close.clicked.connect(self.close_bar)
        row.addWidget(close)
        self.replace_row = QWidget()
        replacement_layout = QHBoxLayout(self.replace_row)
        replacement_layout.setContentsMargins(0, 0, 0, 0)
        replacement_layout.addWidget(self.replacement, 1)
        for title, callback in (("置換", self.replace_one), ("すべて置換", self.replace_all)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            replacement_layout.addWidget(button)
        outer.addWidget(self.replace_row)
        self.query.textChanged.connect(self._query_changed)
        self.case_sensitive.toggled.connect(self._query_changed)
        self.regex.toggled.connect(self._query_changed)
        self.query.returnPressed.connect(self.next)
        self.editor.textChanged.connect(self._timer.start)
        self.hide()

    def show_bar(self, replace=False):
        self.replace_row.setVisible(replace)
        self.show()
        selected = self.editor.textCursor().selectedText()
        if selected and "\u2029" not in selected:
            self.query.setText(selected)
        self.query.setFocus()
        self.query.selectAll()
        self.refresh()

    def close_bar(self):
        self.hide()
        self.editor.setExtraSelections([])
        self.editor.setFocus()
        self.closed.emit()

    def _query_changed(self, *_args):
        self._last_span = None
        self.refresh()

    def pattern(self):
        value = self.query.text()
        if not value:
            return None
        return re.compile(
            value if self.regex.isChecked() else re.escape(value),
            0 if self.case_sensitive.isChecked() else re.IGNORECASE,
        )

    def _current_match_state(self):
        document = self.editor.document()
        return (
            document,
            document.revision(),
            self.query.text(),
            self.regex.isChecked(),
            self.case_sensitive.isChecked(),
        )

    def _ensure_matches(self):
        # Navigation can precede the delayed refresh after an edit or Undo.
        # Reuse unchanged matches without clearing/repainting either pane.
        if self._timer.isActive() or self._match_state != self._current_match_state():
            self.refresh()

    def refresh(self):
        self._timer.stop()
        self._match_state = self._current_match_state()
        try:
            pattern = self.pattern()
        except re.error as exc:
            self._matches = []
            self.status.setText(f"正規表現エラー: {exc}")
            self.editor.setExtraSelections([])
            self.refreshed.emit()
            return
        source = self.editor.toPlainText()
        self._matches = list(pattern.finditer(source)) if pattern else []
        self.status.setText(f"{len(self._matches)} 件")
        highlights = []
        if self.isVisible():
            for match in self._matches[:1000]:
                if match.start() == match.end():
                    continue
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(self.editor.document())
                selection.cursor.setPosition(qt_position(source, match.start()))
                selection.cursor.setPosition(
                    qt_position(source, match.end()), QTextCursor.MoveMode.KeepAnchor
                )
                selection.format = QTextCharFormat()
                selection.format.setBackground(QColor("#665523" if self._dark else "#fff0a8"))
                highlights.append(selection)
        self.editor.setExtraSelections(highlights)
        self.refreshed.emit()

    def _find(self, backwards=False):
        self._ensure_matches()
        if not self._matches:
            return
        source = self._matches[0].string
        cursor = self.editor.textCursor()
        start = python_position(source, cursor.selectionStart())
        end = python_position(source, cursor.selectionEnd())
        available = [
            m
            for m in self._matches
            if (m.end() <= start if backwards else m.start() >= end)
            and not (start == end and m.start() == m.end() == start and m.span() == self._last_span)
        ]
        match = (
            (available[-1] if backwards else available[0])
            if available
            else (self._matches[-1] if backwards else self._matches[0])
        )
        cursor.setPosition(qt_position(source, match.start()))
        cursor.setPosition(qt_position(source, match.end()), QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self._last_span = match.span()
        self.status.setText(f"{self._matches.index(match) + 1} / {len(self._matches)} 件")
        self.navigated.emit()

    def next(self):
        self._find(False)

    def previous(self):
        self._find(True)

    def _replacement_for(self, match):
        value = self.replacement.text()
        return match.expand(value) if self.regex.isChecked() else value

    def replace_one(self):
        self.refresh()
        source = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        span = (
            python_position(source, cursor.selectionStart()),
            python_position(source, cursor.selectionEnd()),
        )
        match = next((m for m in self._matches if m.span() == span), None)
        if match is None:
            self.next()
            return
        try:
            replacement = self._replacement_for(match)
        except (re.error, IndexError) as exc:
            self.status.setText(f"置換エラー: {exc}")
            return
        cursor.beginEditBlock()
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)
        if match.start() == match.end():
            # The matched boundary moves after any inserted replacement. Skip
            # it once so an empty match advances over the original character.
            position = python_position(self.editor.toPlainText(), cursor.position())
            self._last_span = (position, position)
        else:
            self._last_span = None
        self.next()

    def replace_all(self):
        self.refresh()
        source = self.editor.toPlainText()
        try:
            edits = [(m.start(), m.end(), self._replacement_for(m)) for m in self._matches]
        except (re.error, IndexError) as exc:
            self.status.setText(f"置換エラー: {exc}")
            return
        if not edits:
            return
        cursor = QTextCursor(self.editor.document())
        cursor.beginEditBlock()
        for start, end, replacement in reversed(edits):
            cursor.setPosition(qt_position(source, start))
            cursor.setPosition(qt_position(source, end), QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(replacement)
        cursor.endEditBlock()
        self._last_span = None
        self.refresh()
        self.status.setText(f"{len(edits)} 件を置換")

    def apply_theme(self, dark):
        self._dark = dark
        self.refresh()
