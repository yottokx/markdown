"""Source positions carried through Markdown parsing, never recovered by text search.

``MappedText`` follows the parser's slices, indentation removal and inline
transformations. Each output character retains its original Python source span;
only the final wire map converts both sides to UTF-16 offsets.
"""

from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any

from markdown_it.parser_block import ParserBlock
from markdown_it.rules_block.state_block import StateBlock


class MappedText(str):
    def __new__(cls, text="", spans=None):
        value = super().__new__(cls, text)
        value.spans = tuple(spans) if spans is not None else (None,) * len(value)
        return value

    @classmethod
    def original(cls, text):
        return cls(text, ((index, index + 1) for index in range(len(text))))

    @classmethod
    def concat(cls, parts):
        parts = list(parts)
        return cls(
            "".join(parts),
            (span for part in parts for span in getattr(part, "spans", (None,) * len(part))),
        )

    def __getitem__(self, key):
        text = super().__getitem__(key)
        spans = self.spans[key]
        return MappedText(text, spans if isinstance(key, slice) else (spans,))

    def __add__(self, other):
        return self.concat((self, other))

    def __radd__(self, other):
        return self.concat((other, self))

    def strip(self, chars=None):
        return self.lstrip(chars).rstrip(chars)

    def lstrip(self, chars=None):
        return self[len(self) - len(str.lstrip(self, chars)) :]

    def rstrip(self, chars=None):
        return self[: len(str.rstrip(self, chars))]

    def replace(self, old, new, count=-1):
        if not old or count == 0:
            return self
        parts = []
        cursor = 0
        while count != 0:
            index = self.find(old, cursor)
            if index < 0:
                break
            parts.append(self[cursor:index])
            replaced = self[index : index + len(old)]
            span = source_span(replaced)
            parts.append(MappedText(new, (span,) * len(new)))
            cursor = index + len(old)
            count -= 1
        parts.append(self[cursor:])
        return self.concat(parts)


def source_span(text):
    spans = [span for span in getattr(text, "spans", ()) if span is not None]
    return (spans[0][0], spans[-1][1]) if spans else None


class _MappedBlockState(StateBlock):
    def getLines(self, begin, end, indent, keepLastLF):
        # Follow StateBlock.getLines, including partially consumed tab columns.
        parts = []
        for line in range(begin, end):
            width = 0
            first = line_start = self.bMarks[line]
            last = self.eMarks[line] + int(line + 1 < end or keepLastLF)
            while first < last and width < indent:
                character = self.src[first]
                if character == "\t":
                    width += 4 - (width + self.bsCount[line]) % 4
                elif character == " " or first - line_start < self.tShift[line]:
                    width += 1
                else:
                    break
                first += 1
            if width > indent:
                parts.append(
                    MappedText(
                        " " * (width - indent), (self.src.spans[first - 1],) * (width - indent)
                    )
                )
            parts.append(self.src[first:last])
        return MappedText.concat(parts)


class _MappedBlockParser(ParserBlock):
    def parse(self, src, md, env, outTokens):
        if not src:
            return None
        state = _MappedBlockState(src, md, env, outTokens)
        self.tokenize(state, state.line, state.lineMax)
        return state.tokens


def _fragments_join(state):
    """The parser's delimiter cleanup, retaining spans when text tokens merge."""
    level = 0
    result = []
    parts = []
    for token in state.tokens:
        if token.nesting < 0:
            level -= 1
        token.level = level
        if token.nesting > 0:
            level += 1
        if token.type == "text" and result and result[-1].type == "text":
            parts.append(token.content)
        else:
            if parts:
                result[-1].content = MappedText.concat(parts)
            result.append(token)
            parts = [token.content] if token.type == "text" else []
    if parts:
        result[-1].content = MappedText.concat(parts)
    state.tokens[:] = result


def _text_join(state):
    for inline in state.tokens:
        if inline.type != "inline":
            continue
        result = []
        parts = []
        for token in inline.children or ():
            if token.type == "text_special":
                token.type = "text"
            if token.type == "text" and result and result[-1].type == "text":
                parts.append(token.content)
            else:
                if parts:
                    result[-1].content = MappedText.concat(parts)
                result.append(token)
                parts = [token.content] if token.type == "text" else []
        if parts:
            result[-1].content = MappedText.concat(parts)
        inline.children = result


def _capture_inline(rule, name):
    def capture(state, silent):
        start = state.pos
        token_start = len(state.tokens)
        success = rule(state, silent)
        if not success or silent:
            return success
        consumed = state.src[start : state.pos]
        created = state.tokens[token_start:]
        if name in {"emphasis", "strikethrough"}:
            cursor = start
            for token in created:
                # push() can first flush text pending before the marker run.
                if (
                    token.type == "text"
                    and token.content
                    and set(token.content) <= {state.src[start]}
                ):
                    token.content = state.src[cursor : cursor + len(token.content)]
                    cursor += len(token.content)
        elif name == "entity":
            for token in created:
                if token.type == "text_special" and token.info == "entity":
                    token.content = MappedText(
                        token.content, (source_span(consumed),) * len(token.content)
                    )
        elif name == "escape":
            for token in created:
                if token.type == "text_special" and token.info == "escape":
                    token.content = consumed if token.content == consumed else consumed[1:]
        elif name == "autolink":
            for token in created[-2:]:
                if token.type == "text":
                    label = consumed[1:-1]
                    token.content = (
                        label
                        if token.content == label
                        else MappedText(token.content, (source_span(label),) * len(token.content))
                    )
        elif name == "image":
            for token in created:
                if token.type == "image":
                    label = token.content
                    if not label:
                        # An empty alt still has an atomic image target: its URL.
                        end = state.md.helpers.parseLinkLabel(state, start + 1, False)
                        position = end + 2
                        while position < state.pos and state.src[position] in " \t\n":
                            position += 1
                        destination = state.md.helpers.parseLinkDestination(
                            state.src, position, state.pos
                        )
                        if destination.ok and state.src[end + 1 : end + 2] == "(":
                            last = destination.pos
                            if state.src[position : position + 1] == "<":
                                position += 1
                                last -= 1
                            label = state.src[position:last]
                        elif state.src[end + 1 : end + 2] == "[":
                            # An empty-alt reference image has no inline URL.
                            # Its explicit reference label is the source target.
                            label = state.src[end + 2 : state.pos - 1]
                    token.meta["selection_atomic"] = source_span(label)
        if name in {"newline", "escape"}:
            for token in created:
                if token.type in {"softbreak", "hardbreak"}:
                    newline = consumed.find("\n")
                    if newline >= 0:
                        token.meta["selection_text"] = consumed[newline : newline + 1]
        return success

    return capture


def selection_mapping_plugin(parser):
    """Install after syntax plugins so every inline transformation is observed."""
    block = _MappedBlockParser()
    block.ruler = parser.block.ruler
    parser.block = block

    def normalize(state):
        state.env["selection_mapping"] = SelectionMapping(state.src)
        state.src = (
            MappedText.original(state.src)
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\0", "\ufffd")
        )

    parser.core.ruler.at("normalize", normalize)
    for rule in parser.inline.ruler.__rules__:
        parser.inline.ruler.at(rule.name, _capture_inline(rule.fn, rule.name))
    parser.inline.ruler2.at("fragments_join", _fragments_join)
    parser.core.ruler.at("text_join", _text_join)

    def freeze(state):
        def visit(tokens):
            for token in tokens:
                if isinstance(token.content, MappedText):
                    token.meta["selection_text"] = token.content
                    token.content = str(token.content)
                if token.children:
                    visit(token.children)

        visit(state.tokens)

    parser.core.ruler.after("text_join", "selection_mapping_freeze", freeze)


class SelectionMapping:
    def __init__(self, source):
        self.positions = [0]
        for character in source:
            self.positions.append(self.positions[-1] + (2 if ord(character) > 0xFFFF else 1))
        self.entries: list[dict[str, Any]] = []

    def _entry(self, payload):
        identifier = f"s{len(self.entries)}"
        self.entries.append({"id": identifier, **payload})
        return identifier

    def text(self, text):
        segments = []
        offset = 0
        for character, span in zip(text, getattr(text, "spans", ())):
            end = offset + (2 if ord(character) > 0xFFFF else 1)
            if span is not None:
                source_start, source_end = self.positions[span[0]], self.positions[span[1]]
                previous = segments[-1] if segments else None
                if (
                    previous
                    and previous[1] == offset
                    and previous[2:] == [source_start, source_end]
                ):
                    previous[1] = end
                elif (
                    previous
                    and previous[1] == offset
                    and previous[3] == source_start
                    and previous[1] - previous[0] == previous[3] - previous[2]
                    and end - offset == source_end - source_start
                ):
                    previous[1], previous[3] = end, source_end
                else:
                    segments.append([offset, end, source_start, source_end])
            offset = end
        return self._entry({"segments": segments}) if segments else None

    def atomic(self, span):
        if span is None or span[0] >= span[1]:
            return None
        return self._entry(
            {"start": self.positions[span[0]], "end": self.positions[span[1]], "atomic": True}
        )


def mapped_html(fragment, mapping, protect, sanitize_image=None):
    """Protect generated text spans while raw HTML goes through the normal sanitizer."""
    offsets = [0]
    for line in fragment.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    class Reader(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.parts = []

        def source_offset(self):
            line, column = self.getpos()
            return offsets[line - 1] + column

        def text(self, value, raw_length):
            start = self.source_offset()
            raw = fragment[start : start + raw_length]
            text = raw if value == raw else MappedText(value, (source_span(raw),) * len(value))
            identifier = mapping.text(text)
            safe = escape(value)
            if identifier:
                safe = protect(f'<span data-selection-id="{identifier}">{safe}</span>')
            self.parts.append(safe)

        def handle_data(self, data):
            self.text(data, len(data))

        def handle_entityref(self, name):
            raw = "&" + name
            if (
                fragment[self.source_offset() + len(raw) : self.source_offset() + len(raw) + 1]
                == ";"
            ):
                raw += ";"
            self.text(unescape(raw), len(raw))

        def handle_charref(self, name):
            raw = "&#" + name
            if (
                fragment[self.source_offset() + len(raw) : self.source_offset() + len(raw) + 1]
                == ";"
            ):
                raw += ";"
            self.text(unescape(raw), len(raw))

        def handle_starttag(self, tag, attrs):
            raw = self.get_starttag_text()
            if tag == "img" and sanitize_image is not None:
                attributes = {}
                for match in re.finditer(r"""([^\s/>=]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", raw):
                    name = match[1].lower()
                    if name not in attributes:
                        value = match[2]
                        quoted = value.startswith(('"', "'"))
                        start = self.source_offset() + match.start(2) + int(quoted)
                        end = self.source_offset() + match.end(2) - int(quoted)
                        attributes[name] = fragment[start:end]
                target = attributes.get("alt") or attributes.get("src")
                identifier = mapping.atomic(source_span(target))
                cleaned = sanitize_image(raw)
                if identifier and cleaned:
                    cleaned = cleaned.replace("<img", f'<img data-selection-id="{identifier}"', 1)
                    self.parts.append(protect(cleaned))
                    return
            self.parts.append(raw)

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            self.parts.append(f"</{tag}>")

        def handle_comment(self, data):
            self.parts.append("<!--" + data + "-->")

        def handle_decl(self, decl):
            self.parts.append("<!" + decl + ">")

    reader = Reader()
    reader.feed(fragment)
    reader.close()
    return "".join(reader.parts)
