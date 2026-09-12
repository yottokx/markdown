from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from .directives import DirectiveKind, parse_directives


class MarkdownHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self.heading = _format("#8eddf6", bold=True)
        self.marker = _format("#78a9c2", bold=True)
        self.emphasis = _format("#d7e7ef", italic=True)
        self.strong = _format("#eaf4fa", bold=True)
        self.code = _format("#b7d98b", background="#111a22")
        self.link = _format("#72c9ea")
        self.directive_mark = _format("#64d4f4", bold=True)
        self.directive_hint = _format("#a5c4d4")
        self.directive_expand = _format("#f0bd72", bold=True)
        self.fenced_code = _format("#a9c995", background="#0e151c")

    def highlightBlock(self, text: str) -> None:
        previous = self.previousBlockState()
        fence = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", text)
        if previous == 1:
            self.setFormat(0, len(text), self.fenced_code)
            if fence:
                self.setCurrentBlockState(0)
            else:
                self.setCurrentBlockState(1)
            return
        if fence:
            self.setFormat(0, len(text), self.fenced_code)
            self.setCurrentBlockState(1)
            return
        self.setCurrentBlockState(0)

        if re.match(r"^\s{0,3}#{1,6}(?:\s|$)", text):
            self.setFormat(0, len(text), self.heading)
        for match in re.finditer(r"^\s*(?:[-+*]|\d+\.)\s+", text):
            self.setFormat(match.start(), match.end() - match.start(), self.marker)
        for match in re.finditer(r"^\s*>+\s?", text):
            self.setFormat(match.start(), match.end() - match.start(), self.marker)
        for match in re.finditer(r"\*\*(.+?)\*\*|__(.+?)__", text):
            self.setFormat(match.start(), match.end() - match.start(), self.strong)
        for match in re.finditer(r"(?<!\*)\*([^*\n]+)\*(?!\*)|_([^_\n]+)_", text):
            self.setFormat(match.start(), match.end() - match.start(), self.emphasis)
        for match in re.finditer(r"`+[^`]+`+", text):
            self.setFormat(match.start(), match.end() - match.start(), self.code)
        for match in re.finditer(r"\[[^\]]+\]\([^)]+\)", text):
            self.setFormat(match.start(), match.end() - match.start(), self.link)

        for directive in parse_directives(text):
            self.setFormat(directive.q_start, 1, self.directive_mark)
            self.setFormat(
                directive.hint_start,
                max(0, directive.hint_end - directive.hint_start),
                self.directive_hint,
            )
            if directive.kind == DirectiveKind.LIST_MULTI and directive.ellipsis_start is not None:
                self.setFormat(directive.ellipsis_start, 3, self.directive_expand)


def _format(
    foreground: str,
    *,
    background: str | None = None,
    bold: bool = False,
    italic: bool = False,
) -> QTextCharFormat:
    value = QTextCharFormat()
    value.setForeground(QColor(foreground))
    if background is not None:
        value.setBackground(QColor(background))
    if bold:
        value.setFontWeight(QFont.Weight.DemiBold)
    value.setFontItalic(italic)
    return value

