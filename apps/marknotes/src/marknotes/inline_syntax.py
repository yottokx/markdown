"""Source-positioned inline tokens, using the renderer's Markdown grammar."""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.parser_inline import ParserInline
from markdown_it.rules_inline import StateInline

from .math_parser import math_plugin


class _State(StateInline):
    @property
    def pending(self):
        return getattr(self, "_pending", "")

    @pending.setter
    def pending(self, value):
        if value and not self.pending:
            self._pending_start = self.pos
        self._pending = value

    def pushPending(self):
        start = self._pending_start
        token = super().pushPending()
        token.meta["range"] = (start, start + len(token.content))
        return token

    def push(self, *args):
        token = super().push(*args)
        token.meta["position"] = self.pos
        return token


class _Parser(ParserInline):
    def parse(self, src, md, env, tokens):
        state = _State(src, md, env, tokens)
        self.tokenize(state)
        for rule in self.ruler2.getRules(""):
            rule(state)
        return state.tokens


def _track(name, rule):
    def tracked(state, silent):
        start, count = state.pos, len(state.tokens)
        result = rule(state, silent)
        if not result or silent:
            return result
        new = [token for token in state.tokens[count:] if "range" not in token.meta]
        if name in {"emphasis", "strikethrough"}:
            offset = start
            for token in new:
                token.meta["range"] = (offset, offset + len(token.content))
                offset += len(token.content)
        elif name == "link":
            for token in new:
                position = token.meta["position"]
                token.meta["range"] = (
                    (start, position) if token.type == "link_open" else (position, state.pos)
                )
        else:
            for token in new:
                token.meta["range"] = (start, state.pos)
                if name == "autolink":
                    token.meta["autolink"] = True
        return result

    return tracked


def parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True})
    md.inline = _Parser()
    md.enable(["table", "strikethrough"])
    md.use(math_plugin)
    md.inline.ruler2.disable(["fragments_join"])
    for name, rule in zip(
        md.get_active_rules()["inline"], md.inline.ruler.getRules(""), strict=True
    ):
        md.inline.ruler.at(name, _track(name, rule))
    return md


@dataclass(frozen=True)
class InlineRegion:
    text: str
    positions: tuple[tuple[int, int], ...]
    table: bool = False

    def selection(self, start, end):
        first = next((i for i, (left, _) in enumerate(self.positions) if left == start), None)
        last = next((i + 1 for i, (_, right) in enumerate(self.positions) if right == end), None)
        return (first, last) if first is not None and last is not None and first < last else None


def _table_cells(line: str, offset: int) -> list[InlineRegion]:
    prefix = re.match(r"^(?: {0,3}>[ \t]?)*(?: *(?:[-+*]|\d+[.)])[ \t]+)?", line).end()
    line = line[prefix:]
    offset += prefix
    cells, chars, positions = [], [], []

    def finish():
        value = "".join(chars)
        left = len(value) - len(value.lstrip())
        right = len(value.rstrip())
        cells.append(InlineRegion(value[left:right], tuple(positions[left:right]), True))
        chars.clear()
        positions.clear()

    for i, char in enumerate(line):
        if char == "|" and (i == 0 or line[i - 1] != "\\"):
            finish()
        elif char == "|":
            chars.pop()
            begin, _ = positions.pop()
            chars.append(char)
            positions.append((begin, offset + i + 1))
        else:
            chars.append(char)
            positions.append((offset + i, offset + i + 1))
    finish()
    if cells and not cells[0].text:
        cells.pop(0)
    if cells and not cells[-1].text:
        cells.pop()
    return cells


def find_region(source: str, start: int, end: int, md: MarkdownIt):
    """Parse document structure once; only the selected inline is tokenized."""
    env, tokens = {}, []
    md.block.parse(source, md, env, tokens)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    columns = {}
    previous = None
    for token in tokens:
        if token.type != "inline" or token.map is None:
            previous = token.type
            continue
        first, last = token.map
        if previous in {"th_open", "td_open"}:
            column = columns.get(first, 0)
            columns[first] = column + 1
            cells = _table_cells(lines[first].rstrip("\n"), offsets[first])
            region = cells[column] if column < len(cells) else None
            if region and region.text != token.content:
                region = None
        else:
            if not token.content:
                previous = token.type
                continue
            positions = []
            pieces = token.content.split("\n")
            for index, piece in enumerate(pieces):
                line_index = first + index
                if line_index >= last or line_index >= len(lines) or not piece:
                    positions = []
                    break
                line = lines[line_index].rstrip("\n")
                at = line.find(piece)
                # Refuse ambiguous mappings rather than editing a heading/list marker.
                if at < 0 or line.find(piece, at + 1) >= 0:
                    positions = []
                    break
                positions.extend(
                    (offsets[line_index] + at + n, offsets[line_index] + at + n + 1)
                    for n in range(len(piece))
                )
                if index < len(pieces) - 1:
                    newline = offsets[line_index + 1] - 1
                    positions.append((newline, newline + 1))
            region = InlineRegion(token.content, tuple(positions)) if positions else None
        previous = token.type
        if region:
            selection = region.selection(start, end)
            if selection:
                return region, selection, env
    return None


@dataclass(frozen=True)
class Atom:
    raw: str
    text: str
    start: int
    end: int
    styles: frozenset[str]
    kind: str = "text"
    link: tuple = ()


@dataclass(frozen=True)
class Span:
    kind: str
    start: int
    content_start: int
    content_end: int
    end: int


def analyze(text: str, md: MarkdownIt, env: dict):
    tokens = []
    md.inline.parse(text, md, env, tokens)
    atoms, spans, stack, links = [], [], [], []
    styles = []
    names = {"strong": "bold", "em": "italic", "s": "strike"}
    for token in tokens:
        start, end = token.meta.get("range", (0, 0))
        family = token.type.rsplit("_", 1)[0]
        if family in names and token.nesting:
            kind = names[family]
            if token.type == "strong_open":
                start -= 1
            if token.type == "strong_close":
                end += 1
            if token.nesting == 1:
                stack.append((kind, start, end))
                styles.append(kind)
            else:
                opened, begin, content_start = stack.pop()
                if opened != kind:
                    raise ValueError("Unbalanced inline tokens")
                spans.append(Span(kind, begin, content_start, start, end))
                styles.remove(kind)
        elif token.type == "link_open":
            links.append(
                (tuple(sorted(token.attrs.items())), start, end, token.meta.get("autolink"))
            )
        elif token.type == "link_close":
            _attrs, begin, label_start, automatic = links.pop()
            spans.append(Span("autolink" if automatic else "link", begin, label_start, start, end))
        elif token.content or token.type in {"softbreak", "hardbreak", "image", "code_inline"}:
            kind = token.type
            value = "\n" if kind in {"softbreak", "hardbreak"} else token.content
            raw = text[start:end]
            link = tuple(item[0] for item in links)
            if kind == "text" and value == raw:
                atoms.extend(
                    Atom(char, char, start + i, start + i + 1, frozenset(styles), link=link)
                    for i, char in enumerate(value)
                )
            else:
                atoms.append(Atom(raw, value, start, end, frozenset(styles), kind, link))
            if kind in {
                "code_inline",
                "math_inline",
                "math_inline_display",
                "image",
                "html_inline",
            }:
                width = len(token.markup) if kind == "code_inline" else 0
                spans.append(Span(kind, start, start + width, end - width, end))
    return atoms, spans
