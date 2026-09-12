"""TeX delimiters as Markdown tokens, without rewriting source or code spans."""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline


def _escaped(source: str, position: int) -> bool:
    count = 0
    while position > 0 and source[position - 1] == "\\":
        position -= 1
        count += 1
    return count % 2 == 1


def _closing(source: str, marker: str, start: int, end: int) -> int:
    position = source.find(marker, start, end)
    while position >= 0:
        if not _escaped(source, position) and (
            marker != "$$"
            or (
                (position == 0 or source[position - 1] != "$")
                and (position + 2 == end or source[position + 2] != "$")
            )
        ):
            return position
        position = source.find(marker, position + len(marker), end)
    return -1


def _inline_math(state: StateInline, silent: bool) -> bool:
    start, maximum = state.pos, state.posMax
    source = state.src
    if _escaped(source, start):
        return False
    if source.startswith("\\(", start):
        opening, closing, display = "\\(", "\\)", False
    elif source.startswith("\\[", start):
        opening, closing, display = "\\[", "\\]", True
    elif source[start] == "$":
        if start and source[start - 1] == "$":
            return False
        display = source.startswith("$$", start)
        opening = closing = "$$" if display else "$"
        if source.startswith("$$$", start):
            return False
    else:
        return False
    content_start = start + len(opening)
    if content_start >= maximum:
        return False
    single_dollar = opening == "$"
    # TeX permits padding inside the delimiters, including clipboard NBSPs.
    # Currency disambiguation depends on adjacent digits, not that padding.
    if single_dollar and start > 0 and source[start - 1].isdigit():
        return False
    end = _closing(source, closing, content_start, maximum)
    if end < 0 or end == content_start:
        return False
    if (
        single_dollar
        and end + 1 < maximum
        and (source[end + 1].isdigit() or source[end + 1] == "$")
    ):
        # In particular, '$5 to $10' and '$5-$10' are ordinary prices.
        return False
    content = source[content_start:end]
    if not content.strip() or "\n\n" in content:
        return False
    if not silent:
        token = state.push("math_inline_display" if display else "math_inline", "math", 0)
        token.content = content
        token.markup = opening
    state.pos = end + len(closing)
    return True


def _block_math(state: StateBlock, start_line: int, end_line: int, silent: bool) -> bool:
    if state.is_code_block(start_line):
        return False
    start = state.bMarks[start_line] + state.tShift[start_line]
    end = state.eMarks[start_line]
    first_line = state.src[start:end]
    if first_line.startswith("$$") and not first_line.startswith("$$$"):
        opening = closing = "$$"
    elif first_line.startswith("\\["):
        opening, closing = "\\[", "\\]"
    else:
        return False
    next_line = start_line
    close_position = _closing(state.src, closing, start + 2, end)
    while close_position < 0:
        next_line += 1
        if next_line >= end_line:
            return False
        line_start = state.bMarks[next_line] + state.tShift[next_line]
        end = state.eMarks[next_line]
        if line_start < end and state.sCount[next_line] < state.blkIndent:
            return False
        close_position = _closing(state.src, closing, line_start, end)
    if state.src[close_position + len(closing) : end].strip():
        # Prose around display math belongs to the ordinary inline parser.
        return False
    block = state.getLines(start_line, next_line + 1, state.sCount[start_line], False)
    content = block[len(opening) : block.rfind(closing)]
    if not content.strip():
        return False
    if silent:
        return True
    state.line = next_line + 1
    token = state.push("math_block", "math", 0)
    token.content = content
    token.markup = opening
    token.map = [start_line, state.line]
    return True


def math_plugin(parser: MarkdownIt) -> None:
    parser.inline.add_terminator_char("$")
    parser.inline.ruler.before("escape", "tex_math", _inline_math)
    parser.block.ruler.before(
        "fence",
        "tex_math_block",
        _block_math,
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )
