"""Markdown block context and position helpers shared by source editing and coloring."""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QInputMethodEvent, QKeyEvent, QTextCursor, QTextDocument

from .math_parser import math_plugin
from .theme import palette, scrollbar_stylesheet


def utf16_length(text: str) -> int:
    """Return a Qt text position, whose unit is UTF-16 rather than Python characters."""
    return len(text.encode("utf-16-le")) // 2


def python_index(text: str, position: int) -> int:
    units = 0
    for index, character in enumerate(text):
        if units >= position:
            return index
        units += 2 if ord(character) > 0xFFFF else 1
    return len(text)


_LIST = re.compile(
    r"^(?P<quote>(?: {0,3}>[ \t]?)*)(?P<indent> *)"
    r"(?P<marker>[-+*]|[0-9]{1,9}[.)])(?P<gap>[ \t]+)"
    r"(?:(?P<task>\[[ xX]\])[ \t]+)?(?P<body>.*)$"
)
_PREFIX = re.compile(r"^(?P<quote>(?: {0,3}>[ \t]?)*)(?P<indent>[ \t]*)")


@dataclass(frozen=True, slots=True)
class ListPrefix:
    quote: str
    indent: str
    marker: str
    gap: str
    task: str
    body: str

    @property
    def content_indent(self) -> int:
        # Task checkboxes are inline content, not the list container's indentation.
        return len((self.indent + self.marker + self.gap).expandtabs(4))

    @property
    def body_indent(self) -> int:
        return self.content_indent + (4 if self.task else 0)

    def prefix(self, *, next_item: bool = False, indent: int | None = None) -> str:
        marker = self.marker
        if next_item and marker[0].isdigit():
            marker = f"{int(marker[:-1]) + 1}{marker[-1]}"
        spacing = self.indent if indent is None else " " * indent
        return self.quote + spacing + marker + " " + ("[ ] " if self.task else "")


def list_prefix(text: str) -> ListPrefix | None:
    match = _LIST.match(text)
    return (
        ListPrefix(**{name: value or "" for name, value in match.groupdict().items()})
        if match
        else None
    )


def line_prefix(text: str) -> tuple[str, str]:
    match = _PREFIX.match(text)
    assert match is not None
    return match.group("quote"), match.group("indent")


class MarkdownContext:
    """Cache parser-derived block ranges once per document revision.

    A regular expression alone mistakes code examples and thematic breaks for list
    items. The same CommonMark parser as the preview establishes those boundaries.
    """

    def __init__(self, document: QTextDocument) -> None:
        self.document = document
        self.parser = MarkdownIt("commonmark").enable("table").enable("strikethrough")
        self.parser.use(math_plugin)
        self.revision = -2
        self.lines: list[str] = []
        self.code_lines: set[int] = set()
        self.code_highlight_lines: set[int] = set()
        self.list_lines: set[int] = set()
        self.heading_lines: set[int] = set()
        self.table_lines: set[int] = set()
        self.fence_states: dict[int, int] = {}
        self.indented_thresholds: dict[int, tuple[str, int]] = {}
        document.contentsChange.connect(self.invalidate)

    def invalidate(self, *_args) -> None:
        self.revision = -2

    def refresh(self) -> None:
        revision = self.document.revision()
        if revision == self.revision:
            return
        self.revision = revision
        text = self.document.toPlainText()
        self.lines = text.split("\n")
        self.code_lines.clear()
        self.code_highlight_lines.clear()
        self.list_lines.clear()
        self.heading_lines.clear()
        self.table_lines.clear()
        self.fence_states.clear()
        self.indented_thresholds.clear()
        for token in self.parser.parse(text):
            if token.map is None:
                continue
            start, end = token.map
            if token.type in {"fence", "code_block"}:
                self.code_highlight_lines.update(range(start, end))
                if token.type == "code_block":
                    self.code_lines.update(range(start, end))
                    quote, raw_indent = line_prefix(self.lines[start])
                    _, content_indent = line_prefix(token.content.split("\n")[0])
                    threshold = len(raw_indent.expandtabs(4)) - len(content_indent.expandtabs(4))
                    for line in range(start, end):
                        self.indented_thresholds[line] = (quote, threshold)
                    continue
                marker = token.markup
                last = self.lines[end - 1] if end > start + 1 else ""
                # Nested quote/list fences retain their container indentation in
                # the raw source. A closing fence consists solely of its markers.
                closing = (
                    re.fullmatch(
                        r"[ \t]*(?:>[ \t]*)*"
                        + re.escape(marker[0])
                        + "{"
                        + str(len(marker))
                        + r",}[ \t]*",
                        last,
                    )
                    is not None
                )
                logical_end = end - 1 if closing else end
                if not closing and end == len(text.splitlines()):
                    logical_end = len(self.lines)
                self.code_lines.update(range(start, logical_end))
                self.code_highlight_lines.update(range(start, logical_end))
                state = len(marker) * 2 + (1 if marker[0] == "~" else 0)
                for line in range(start, logical_end):
                    self.fence_states[line] = state
            elif token.type == "list_item_open":
                self.list_lines.add(start)
            elif token.type == "heading_open":
                self.heading_lines.update(range(start, end))
            elif token.type == "table_open":
                self.table_lines.update(range(start, end))

    def is_code(self, line: int) -> bool:
        self.refresh()
        if line in self.code_lines:
            return True
        if not 0 <= line < len(self.lines):
            return False
        quote, indent = line_prefix(self.lines[line])
        if self.lines[line] != quote + indent or not indent:
            return False
        # Parsers omit trailing blank lines of indented code. Keep editing in
        # that code block while the caret retains the required indentation.
        previous = line - 1
        while previous >= 0:
            previous_quote, previous_indent = line_prefix(self.lines[previous])
            if self.lines[previous] != previous_quote + previous_indent:
                break
            previous -= 1
        threshold = self.indented_thresholds.get(previous)
        return bool(
            threshold and quote == threshold[0] and len(indent.expandtabs(4)) >= threshold[1]
        )

    def list_at(self, line: int) -> ListPrefix | None:
        self.refresh()
        if not 0 <= line < len(self.lines) or line in self.code_lines:
            return None
        candidate = list_prefix(self.lines[line])
        if line in self.list_lines:
            return candidate
        # An unfinished nested '- ' can temporarily parse as a setext underline.
        # Preserve the reference editor's empty-item behavior only in a list.
        if candidate and not candidate.body.strip() and candidate.indent:
            for previous_line in range(line - 1, -1, -1):
                previous = list_prefix(self.lines[previous_line])
                if previous_line in self.list_lines and previous:
                    if previous.quote == candidate.quote and len(previous.indent) < len(
                        candidate.indent
                    ):
                        return candidate
                    break
                if self.lines[previous_line].strip():
                    break
        return None


class MarkdownEditingMixin:
    """SourceEditor key behavior, independent of application actions."""

    def apply_theme(self, dark: bool) -> None:
        self._gutter_background = QColor("#20252d" if dark else "#f5f7fa")
        self._gutter_foreground = QColor("#8b9bb0" if dark else "#8190a2")
        background, foreground = ("#171c24", "#e1e7ef") if dark else ("#ffffff", "#243244")
        selected, selection_text = ("#345579", "#ffffff") if dark else ("#d5e7ff", "#152b48")
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: {background}; color: {foreground}; "
            f"selection-background-color: {selected}; selection-color: {selection_text}; border: 0; }}"
            + scrollbar_stylesheet(dark)
        )
        colors = palette(dark)
        self.setPalette(colors)
        self.verticalScrollBar().setPalette(colors)
        self.horizontalScrollBar().setPalette(colors)
        self.highlighter.set_dark_mode(dark)
        self.gutter.update()

    def in_code_block(self, cursor: QTextCursor | None = None) -> bool:
        return self.markdown_context.is_code((cursor or self.textCursor()).blockNumber())

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._composing or self.isReadOnly():
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and not modifiers & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            outdent = event.key() == Qt.Key.Key_Backtab or bool(
                modifiers & Qt.KeyboardModifier.ShiftModifier
            )
            self._change_indent(outdent=outdent)
            event.accept()
            return
        if not modifiers:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._insert_newline():
                event.accept()
                return
            if event.key() == Qt.Key.Key_Backspace and self._structural_backspace():
                event.accept()
                return
        self._removed_list = None
        super().keyPressEvent(event)

    def _previous_list(self, line: int, quote: str, maximum_indent: int) -> ListPrefix | None:
        """Find a preceding sibling/ancestor without crossing an unrelated paragraph."""
        self.markdown_context.refresh()
        for number in range(line - 1, -1, -1):
            text = self.markdown_context.lines[number]
            if not text.strip():
                continue
            prefix = self.markdown_context.list_at(number)
            if prefix:
                if prefix.quote != quote:
                    return None
                if len(prefix.indent) <= maximum_indent:
                    return prefix
            else:
                previous_quote, previous_indent = line_prefix(text)
                if previous_quote != quote or len(previous_indent) <= maximum_indent:
                    return None
        return None

    def _indent_amount(self, line: int, prefix: ListPrefix | None) -> int:
        if prefix:
            previous = self._previous_list(line, prefix.quote, len(prefix.indent))
            if previous and previous.content_indent > len(prefix.indent):
                return max(2, previous.content_indent - len(prefix.indent))
        return 2

    def _outdent_amount(self, line: int, prefix: ListPrefix | None, indent: str) -> int:
        if not indent:
            return 0
        if indent.startswith("\t"):
            return 1
        if prefix:
            parent = self._previous_list(line, prefix.quote, len(indent) - 1)
            if parent and parent.content_indent == len(indent):
                return len(indent) - len(parent.indent)
        return min(2, len(indent))

    def _change_indent(self, *, outdent: bool) -> None:
        cursor = self.textCursor()
        prefix = self.markdown_context.list_at(cursor.blockNumber())
        if not outdent and not cursor.hasSelection() and prefix is None:
            text = cursor.block().text()
            before = text[: python_index(text, cursor.positionInBlock())]
            cursor.insertText(" " * (2 - len(before.expandtabs(2)) % 2))
            self.setTextCursor(cursor)
            return
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        first = self.document().findBlock(start)
        last = self.document().findBlock(max(start, end - 1))
        lines: list[tuple[int, int, str]] = []
        block = first
        initial_prefix = self.markdown_context.list_at(first.blockNumber())
        amount = self._indent_amount(first.blockNumber(), initial_prefix)
        _, initial_indent = line_prefix(first.text())
        removal = self._outdent_amount(first.blockNumber(), initial_prefix, initial_indent)
        if cursor.hasSelection():
            removal = max(2, removal)
        parent = (
            self._previous_list(
                first.blockNumber(), initial_prefix.quote, len(initial_prefix.indent)
            )
            if initial_prefix
            else None
        )
        restart_numbering = bool(
            not outdent
            and initial_prefix
            and initial_prefix.marker[0].isdigit()
            and parent
            and parent.content_indent == len(initial_prefix.indent) + amount
        )
        next_number = 1
        while block.isValid():
            quote, indent = line_prefix(block.text())
            position = block.position() + utf16_length(quote)
            prefix = self.markdown_context.list_at(block.blockNumber())
            if outdent:
                remove = min(removal, len(indent))
                if indent.startswith("\t"):
                    remove = 1
                if remove:
                    lines.append((position, position + remove, ""))
            else:
                lines.append((position, position, " " * amount))
                if (
                    restart_numbering
                    and prefix
                    and prefix.marker[0].isdigit()
                    and prefix.indent == initial_prefix.indent
                    and prefix.quote == initial_prefix.quote
                ):
                    marker_start = position + len(prefix.indent)
                    lines.append(
                        (
                            marker_start,
                            marker_start + len(prefix.marker),
                            f"{next_number}{prefix.marker[-1]}",
                        )
                    )
                    next_number += 1
            if block == last:
                break
            block = block.next()
        if not lines:
            return

        def moved(position: int, *, right: bool) -> int:
            offset = 0
            for edit_start, edit_end, replacement in lines:
                added = utf16_length(replacement)
                if position < edit_start:
                    break
                if position == edit_start:
                    return edit_start + offset + (added if right else 0)
                if position < edit_end:
                    return edit_start + offset + min(position - edit_start, added)
                offset += added - (edit_end - edit_start)
            return position + offset

        had_selection = cursor.hasSelection()
        reverse = cursor.anchor() > cursor.position()
        new_start = moved(start, right=not had_selection)
        new_end = moved(end, right=True)
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for edit_start, edit_end, replacement in reversed(lines):
            edit.setPosition(edit_start)
            edit.setPosition(edit_end, QTextCursor.MoveMode.KeepAnchor)
            edit.insertText(replacement)
        edit.endEditBlock()
        cursor.setPosition(new_end if reverse else new_start)
        if had_selection:
            cursor.setPosition(new_start if reverse else new_end, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self._removed_list = None

    def _continuation_list(self, line: int, text: str) -> ListPrefix | None:
        quote, indent = line_prefix(text)
        if not indent or line == 0:
            return None
        previous = self._previous_list(line, quote, len(indent) - 1)
        if previous and len(indent.expandtabs(2)) >= previous.content_indent:
            return previous
        return None

    def _insert_newline(self) -> bool:
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        if cursor.hasSelection() or cursor.positionInBlock() != utf16_length(text):
            return False
        quote, indent = line_prefix(text)
        prefix = (
            None
            if self.in_code_block(cursor)
            else self.markdown_context.list_at(block.blockNumber())
        )
        continuation = None
        if prefix is None and not self.in_code_block(cursor):
            continuation = self._continuation_list(block.blockNumber(), text)
        cursor.beginEditBlock()
        if prefix:
            if not prefix.body.strip() and not prefix.indent:
                cursor.setPosition(block.position())
                cursor.setPosition(
                    block.position() + utf16_length(text), QTextCursor.MoveMode.KeepAnchor
                )
                cursor.insertText(prefix.quote + "\n" + prefix.quote)
            else:
                cursor.insertText("\n" + prefix.prefix(next_item=True))
        elif continuation:
            cursor.insertText("\n" + continuation.prefix(next_item=True))
        elif quote or indent:
            cursor.insertText("\n" + quote + indent)
        else:
            cursor.endEditBlock()
            return False
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._removed_list = None
        return True

    def _replace_current_line(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _structural_backspace(self) -> bool:
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        if cursor.hasSelection() or cursor.positionInBlock() != utf16_length(text):
            return False
        prefix = self.markdown_context.list_at(block.blockNumber())
        if prefix and not prefix.body.strip():
            replacement = prefix.quote + " " * (len(text) - len(prefix.quote))
            self._replace_current_line(replacement)
            self._removed_list = (block.blockNumber(), prefix, replacement)
            return True
        quote, indent = line_prefix(text)
        if not indent or text != quote + indent:
            return False
        previous = self.markdown_context.list_at(block.blockNumber() - 1)
        removed = self._removed_list
        if removed and removed[0] == block.blockNumber() and removed[2] == text and previous:
            original = removed[1]
            amount = self._outdent_amount(block.blockNumber(), original, original.indent)
            replacement = (
                original.prefix(indent=len(original.indent) - amount) if original.indent else quote
            )
        elif not self.in_code_block(cursor) and previous and len(indent) >= previous.content_indent:
            shallow = len(indent) - (previous.body_indent - len(previous.indent)) - 2
            replacement = previous.prefix(indent=shallow) if shallow >= 0 else quote
        else:
            remove = 1 if indent.endswith("\t") else min(2, len(indent))
            replacement = text[:-remove]
        self._replace_current_line(replacement)
        self._removed_list = None
        return True
