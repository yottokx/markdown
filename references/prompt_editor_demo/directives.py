from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class DirectiveKind(str, Enum):
    INLINE = "inline"
    PARAGRAPH = "paragraph"
    LIST_SINGLE = "list_single"
    LIST_MULTI = "list_multi"
    LINE_END = "line_end"


@dataclass(frozen=True, slots=True)
class Directive:
    id: str
    kind: DirectiveKind
    start: int
    end: int
    line_start: int
    line_end: int
    q_start: int
    hint_start: int
    hint_end: int
    hint: str
    indent: str = ""
    marker: str = ""
    ellipsis_start: int | None = None

    def contains_caret(self, position: int) -> bool:
        if self.kind == DirectiveKind.INLINE:
            return self.q_start <= position <= self.hint_end
        return self.line_start <= position <= self.line_end


_INLINE_DIRECTIVE = re.compile(r"(?<= )\?(?P<hint>[^\s]+)(?= |$)")
_FENCE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")


def parse_directives(text: str) -> list[Directive]:
    """Parse valid prompt directives while ignoring Markdown code."""
    directives: list[Directive] = []
    offset = 0
    fence_character = ""
    fence_length = 0

    for raw_line in text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        line_start = offset
        line_end = line_start + len(content)
        offset += len(raw_line)

        fence_match = _FENCE.match(content)
        if fence_character:
            if fence_match:
                fence = fence_match.group("fence")
                if fence[0] == fence_character and len(fence) >= fence_length:
                    fence_character = ""
                    fence_length = 0
            continue
        if fence_match:
            fence = fence_match.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
            continue

        excluded = _inline_code_ranges(content)
        line_directive = _parse_list_line(content, line_start, line_end, excluded)
        if line_directive is None:
            line_directive = _parse_paragraph_line(content, line_start, line_end, excluded)
        if line_directive is not None:
            directives.append(line_directive)
            continue

        inline_matches = list(_INLINE_DIRECTIVE.finditer(content))
        for index, match in enumerate(inline_matches):
            q_column = match.start()
            if _is_escaped(content, q_column) or _overlaps(q_column, match.end(), excluded):
                continue
            q_start = line_start + q_column
            hint_start = line_start + match.start("hint")
            hint_end = line_start + match.end("hint")
            replacement_start = q_start - 1
            replacement_end = line_start + match.end()
            if match.end() < len(content) and content[match.end()] == " ":
                next_match = inline_matches[index + 1] if index + 1 < len(inline_matches) else None
                shared_with_next = (
                    next_match is not None and next_match.start() == match.end() + 1
                )
                if not shared_with_next:
                    replacement_end += 1
            directives.append(
                Directive(
                    id=f"inline:{q_start}",
                    kind=DirectiveKind.INLINE,
                    start=replacement_start,
                    end=replacement_end,
                    line_start=line_start,
                    line_end=line_end,
                    q_start=q_start,
                    hint_start=hint_start,
                    hint_end=hint_end,
                    hint=match.group("hint"),
                )
            )

    return directives


def line_end_completion_directive(text: str, position: int) -> Directive | None:
    """Return an insertion target when the caret is at an eligible logical line end."""
    if not 0 < position <= len(text):
        return None
    line_start = text.rfind("\n", 0, position) + 1
    newline = text.find("\n", position)
    line_end = len(text) if newline < 0 else newline
    if position != line_end:
        return None
    line = text[line_start:line_end]
    if not line.strip() or _position_is_in_fenced_code(text, position):
        return None
    if _position_is_in_inline_code(line, position - line_start):
        return None
    if re.fullmatch(r"\s*(?:#{1,6}|>|[-+*]|\d+\.)\s*", line):
        return None
    if re.fullmatch(r"\s*(?:`{3,}|~{3,}|[-*_]{3,})\s*", line):
        return None
    previous = text[position - 1]
    if previous in "\\`*_{}[]<>()#+-!|>~?":
        return None
    return Directive(
        id=f"line_end:{position}",
        kind=DirectiveKind.LINE_END,
        start=position,
        end=position,
        line_start=line_start,
        line_end=line_end,
        q_start=position,
        hint_start=line_start,
        hint_end=position,
        hint=line,
    )


def directives_for_selection(
    directives: list[Directive], selection_start: int, selection_end: int
) -> list[Directive]:
    low, high = sorted((selection_start, selection_end))
    if low == high:
        return [directive for directive in directives if directive.contains_caret(low)]
    return [directive for directive in directives if low <= directive.q_start < high]


def _parse_list_line(
    content: str,
    line_start: int,
    line_end: int,
    excluded: list[tuple[int, int]],
) -> Directive | None:
    match = re.match(r"^(?P<indent> *)- (?P<body>\?.*)$", content)
    if match is None:
        return None
    q_column = match.start("body")
    if _is_escaped(content, q_column) or _overlaps(q_column, len(content), excluded):
        return None
    raw_hint = content[q_column + 1 :].rstrip(" \t")
    if not raw_hint or raw_hint[0].isspace():
        return None

    ellipsis_start: int | None = None
    kind = DirectiveKind.LIST_SINGLE
    hint = raw_hint
    if raw_hint.endswith("...") and not raw_hint.endswith("...."):
        candidate = raw_hint[:-3].rstrip()
        if candidate:
            hint = candidate
            kind = DirectiveKind.LIST_MULTI
            ellipsis_start = line_start + q_column + 1 + len(raw_hint) - 3

    hint_start = line_start + q_column + 1
    hint_end = hint_start + len(hint)
    q_start = line_start + q_column
    return Directive(
        id=f"{kind.value}:{q_start}",
        kind=kind,
        start=line_start,
        end=line_end,
        line_start=line_start,
        line_end=line_end,
        q_start=q_start,
        hint_start=hint_start,
        hint_end=hint_end,
        hint=hint,
        indent=match.group("indent"),
        marker="-",
        ellipsis_start=ellipsis_start,
    )


def _parse_paragraph_line(
    content: str,
    line_start: int,
    line_end: int,
    excluded: list[tuple[int, int]],
) -> Directive | None:
    indent_match = re.match(r"^(?P<indent> *)", content)
    indent = indent_match.group("indent") if indent_match is not None else ""
    q_column = len(indent)
    if not content[q_column:].startswith("? "):
        return None
    raw_hint = content[q_column + 2 :].rstrip(" \t")
    if not raw_hint or raw_hint[0].isspace():
        return None
    if _is_escaped(content, q_column) or _overlaps(q_column, len(content), excluded):
        return None
    q_start = line_start + q_column
    hint_start = q_start + 2
    return Directive(
        id=f"paragraph:{q_start}",
        kind=DirectiveKind.PARAGRAPH,
        start=line_start,
        end=line_end,
        line_start=line_start,
        line_end=line_end,
        q_start=q_start,
        hint_start=hint_start,
        hint_end=hint_start + len(raw_hint),
        hint=raw_hint,
        indent=indent,
    )


def _inline_code_ranges(content: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(content):
        opening = re.search(r"`+", content[cursor:])
        if opening is None:
            break
        start = cursor + opening.start()
        ticks = opening.group(0)
        closing = content.find(ticks, start + len(ticks))
        if closing < 0:
            break
        end = closing + len(ticks)
        ranges.append((start, end))
        cursor = end
    return ranges


def _position_is_in_inline_code(line: str, position: int) -> bool:
    if any(start <= position <= end for start, end in _inline_code_ranges(line)):
        return True
    prefix = line[:position]
    return len(re.findall(r"(?<!\\)`", prefix)) % 2 == 1


def _position_is_in_fenced_code(text: str, position: int) -> bool:
    fence_character = ""
    fence_length = 0
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        line_end = offset + len(content)
        fence_match = _FENCE.match(content)
        was_inside = bool(fence_character)
        if fence_character:
            if fence_match:
                fence = fence_match.group("fence")
                if fence[0] == fence_character and len(fence) >= fence_length:
                    fence_character = ""
                    fence_length = 0
        elif fence_match:
            fence = fence_match.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
        if offset <= position <= line_end:
            return was_inside or fence_match is not None
        offset += len(raw_line)
    return bool(fence_character)


def _is_escaped(text: str, position: int) -> bool:
    backslashes = 0
    cursor = position - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)
