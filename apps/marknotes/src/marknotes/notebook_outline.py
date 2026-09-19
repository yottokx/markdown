"""Current-note Markdown headings and source-line navigation."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from .math_parser import math_plugin


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    title: str
    line: int


def markdown_headings(source: str) -> tuple[Heading, ...]:
    """Use CommonMark boundaries so code and math never become headings."""
    parser = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    validate_link = parser.validateLink
    parser.validateLink = lambda url: urlsplit(url).scheme.lower() == "file" or validate_link(url)
    parser.use(math_plugin)
    tokens = parser.parse(source)

    def plain_text(children):
        parts = []
        for child in children:
            if child.type in {"softbreak", "hardbreak"}:
                parts.append(" ")
            elif child.type == "image":
                parts.append(plain_text(child.children or []))
            elif child.type in {"text", "code_inline", "math_inline", "math_inline_display"}:
                parts.append(child.content)
        return "".join(parts)

    return tuple(
        Heading(
            int(token.tag[1:]),
            " ".join(plain_text(tokens[index + 1].children or []).split()) or "（空の見出し）",
            token.map[0],
        )
        for index, token in enumerate(tokens[:-1])
        if token.type == "heading_open" and token.map is not None
    )


class NotebookOutlinePanel(QWidget):
    heading_requested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.note_id = ""
        self.headings: tuple[Heading, ...] = ()
        self._source: str | None = None
        self.setObjectName("notebookOutlinePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("ノートを開くと見出しを表示します", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setIndentation(14)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setAccessibleName("現在のノートの見出しアウトライン")
        self.tree.itemClicked.connect(self._activate)
        self.tree.itemActivated.connect(self._activate)
        layout.addWidget(self.tree, 1)
        self.apply_theme(False)

    def set_source(self, note_id: str | None, source: str) -> None:
        note_id = note_id or ""
        if self.note_id == note_id and self._source == source:
            return
        headings = markdown_headings(source) if note_id else ()
        same_note = self.note_id == note_id
        self._source = source
        self.note_id = note_id
        if same_note and headings == self.headings:
            return
        expanded = {}
        selected = None
        if same_note:
            for item in self._items():
                key = item.data(0, Qt.ItemDataRole.UserRole + 1)
                expanded[key] = item.isExpanded()
                if item is self.tree.currentItem():
                    selected = key
        self.headings = headings
        with QSignalBlocker(self.tree):
            self.tree.clear()
            parents = []
            occurrences = {}
            for heading in headings:
                while parents and parents[-1][0] >= heading.level:
                    parents.pop()
                parent = parents[-1][1] if parents else self.tree
                item = QTreeWidgetItem(parent, [heading.title])
                item.setData(0, Qt.ItemDataRole.UserRole, heading.line)
                pair = (heading.level, heading.title)
                occurrences[pair] = occurrences.get(pair, 0) + 1
                key = (*pair, occurrences[pair])
                item.setData(0, Qt.ItemDataRole.UserRole + 1, key)
                item.setToolTip(0, f"H{heading.level} · {heading.line + 1}行目\n{heading.title}")
                item.setData(0, Qt.ItemDataRole.AccessibleTextRole, item.toolTip(0))
                item.setExpanded(expanded.get(key, True))
                if key == selected:
                    self.tree.setCurrentItem(item)
                parents.append((heading.level, item))
        self.status_label.setText(
            f"見出し {len(headings)} 件"
            if headings
            else ("見出しはありません" if note_id else "ノートを開くと見出しを表示します")
        )

    def _items(self):
        def descendants(parent):
            for index in range(parent.childCount()):
                item = parent.child(index)
                yield item
                yield from descendants(item)

        return descendants(self.tree.invisibleRootItem())

    def _activate(self, item, _column=0):
        if self.note_id:
            self.heading_requested.emit(self.note_id, item.data(0, Qt.ItemDataRole.UserRole))

    def apply_theme(self, dark: bool) -> None:
        text = "#e1e7ef" if dark else "#253047"
        muted = "#a0adbf" if dark else "#67758a"
        selected = "#314968" if dark else "#e1ebfa"
        hover = "#2d3542" if dark else "#e8edf4"
        self.setStyleSheet(f"""
            QWidget#notebookOutlinePanel {{ background: transparent; }}
            QWidget#notebookOutlinePanel QLabel {{ color: {muted}; background: transparent; }}
            QWidget#notebookOutlinePanel QTreeWidget {{
                background: transparent; color: {text}; border: none; outline: none;
            }}
            QWidget#notebookOutlinePanel QTreeWidget::item {{ padding: 5px 2px; }}
            QWidget#notebookOutlinePanel QTreeWidget::item:hover {{ background: {hover}; }}
            QWidget#notebookOutlinePanel QTreeWidget::item:selected {{ background: {selected}; }}
        """)
