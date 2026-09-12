"""Markdown source highlighting with parser-backed multiline code boundaries."""

from __future__ import annotations

import re

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from .indentation import MarkdownContext


class MarkdownHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument, context: MarkdownContext | None = None) -> None:
        super().__init__(document)
        self.context = context or MarkdownContext(document)
        self.dark = False
        self.formats: dict[str, QTextCharFormat] = {}
        self._offsets: list[int] = []
        self.set_dark_mode(False)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.rehighlight)
        document.contentsChange.connect(self._schedule_refresh)

    def _schedule_refresh(self, _position: int, removed: int, added: int) -> None:
        if removed or added:
            # Setext headings and table separators can change earlier blocks.
            self._refresh_timer.start(0)

    def set_dark_mode(self, dark: bool) -> None:
        self.dark = bool(dark)
        palette = {
            "heading": "#8ab4ff" if dark else "#155cb0",
            "marker": "#b1a4f8" if dark else "#7751a8",
            "link": "#75c9f1" if dark else "#006c9e",
            "image": "#83d4bb" if dark else "#14795c",
            "code": "#e4bd85" if dark else "#8a4c0d",
            "quote": "#92a4b8" if dark else "#63778d",
            "table": "#92a4b8" if dark else "#63778d",
        }
        self.formats = {}
        for name, color in palette.items():
            style = QTextCharFormat()
            style.setForeground(QColor(color))
            self.formats[name] = style
        self.formats["heading"].setFontWeight(QFont.Weight.Bold)
        self.formats["link"].setFontUnderline(True)
        self.formats["code"].setBackground(QColor("#293342" if dark else "#f1f4f7"))
        bold = QTextCharFormat()
        bold.setFontWeight(QFont.Weight.Bold)
        self.formats["bold"] = bold
        italic = QTextCharFormat()
        italic.setFontItalic(True)
        self.formats["italic"] = italic
        strike = QTextCharFormat()
        strike.setFontStrikeOut(True)
        self.formats["strike"] = strike
        self.rehighlight()

    def _paint(self, start: int, end: int, name: str, *, merge: bool = False) -> None:
        qt_start, qt_end = self._offsets[start], self._offsets[end]
        style = self.formats[name]
        if not merge:
            self.setFormat(qt_start, qt_end - qt_start, style)
            return
        for position in range(qt_start, qt_end):
            combined = self.format(position)
            combined.merge(style)
            self.setFormat(position, 1, combined)

    def highlightBlock(self, text: str) -> None:
        self.context.refresh()
        line = self.currentBlock().blockNumber()
        self._offsets = [0]
        for character in text:
            self._offsets.append(self._offsets[-1] + (2 if ord(character) > 0xFFFF else 1))
        self.setCurrentBlockState(self.context.fence_states.get(line, 0))
        if line in self.context.code_highlight_lines:
            self._paint(0, len(text), "code")
            return
        if line in self.context.heading_lines:
            self._paint(0, len(text), "heading")
        if line in self.context.table_lines:
            for match in re.finditer(r"(?<!\\)\||(?<=\|)[ :\-]+(?=\|)", text):
                self._paint(match.start(), match.end(), "table")
        quote = re.match(r"^ {0,3}(?:>[ \t]?)+", text)
        if quote:
            self._paint(0, quote.end(), "quote")
        prefix = self.context.list_at(line)
        if prefix:
            end = len(text) - len(prefix.body)
            self._paint(len(prefix.quote) + len(prefix.indent), end, "marker")
        for match in re.finditer(
            r"(?<!\\)\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<![\w\\])__(?=\S)(.+?)(?<=\S)__(?!\w)", text
        ):
            self._paint(match.start(), match.end(), "bold", merge=True)
        for match in re.finditer(
            r"(?<![\\*])\*(?!\*)(?=\S)(.+?)(?<=\S)\*(?!\*)|(?<![\w\\])_(?!_)(?=\S)(.+?)(?<=\S)_(?![\w_])",
            text,
        ):
            self._paint(match.start(), match.end(), "italic", merge=True)
        for match in re.finditer(r"(?<!\\)~~(?=\S).+?(?<=\S)~~", text):
            self._paint(match.start(), match.end(), "strike", merge=True)
        for match in re.finditer(
            r"(?<!\\)(!?)\[(?:\\.|[^\]\\])*\](?:\((?:\\.|[^)\\])*\)|\[[^\]]*\])", text
        ):
            self._paint(
                match.start(), match.end(), "image" if match.group(1) else "link", merge=True
            )
        definition = re.match(r"^ {0,3}\[[^\]]+\]:\s+.+", text)
        if definition:
            self._paint(definition.start(), definition.end(), "link")
        # Inline code wins over any apparent emphasis or links inside it.
        for match in re.finditer(r"(?<![\\`])(`+)(?!`)(.+?)(?<!`)\1(?!`)", text):
            self._paint(match.start(), match.end(), "code")
