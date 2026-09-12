"""Line-based Markdown block edits, with parser-aware container boundaries."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, replace

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .code_language import detect_code_language
from .formatting_types import FormatAction, FormatEdit
from .inline_syntax import analyze
from .inline_syntax import parser as inline_parser
from .math_parser import math_plugin

_CONTAINERS = {"blockquote_open", "bullet_list_open", "ordered_list_open", "list_item_open"}
_LEAVES = {
    "paragraph_open",
    "heading_open",
    "fence",
    "code_block",
    "math_block",
    "table_open",
    "html_block",
    "hr",
}
_PROTECTED = {"fence", "code_block", "math_block", "table_open", "html_block", "inline_crossing"}
_ITEM = re.compile(r"^( {0,3})([-+*]|\d{1,9}[.)])([ \t]+|$)")
_QUOTE = re.compile(r"^ {0,3}>[ \t]?")
_ATX = re.compile(r"^( {0,3})#{1,6}(?:[ \t]+|$)(.*)$")


@dataclass(eq=False, slots=True)
class _Node:
    token: Token
    first: int
    stop: int
    parents: tuple[_Node, ...]

    @property
    def kind(self):
        return self.token.type


@dataclass(frozen=True, slots=True)
class _View:
    prefix: str
    continuation: str
    body: str


class _Document:
    def __init__(self, source: str):
        self.source = source
        raw = source.split("\n")
        self.lines = [line.removesuffix("\r") for line in raw]
        self.offsets = [0]
        for line in raw[:-1]:
            self.offsets.append(self.offsets[-1] + len(line) + 1)
        self.newline = "\r\n" if "\r\n" in source else "\n"
        self.nodes: list[_Node] = []
        self.leaves: list[_Node] = []
        self._padding: dict[int, tuple[str, int]] = {}
        self._views: dict[tuple[int, tuple[_Node, ...]], _View] = {}
        stack: list[_Node] = []
        # One parse is shared by every menu action, including all heading levels.
        parser = (
            MarkdownIt("commonmark", {"html": True})
            .enable(["table", "strikethrough"])
            .use(math_plugin)
        )
        env: dict = {}
        span_parser = None
        for token in parser.parse(source, env):
            if token.type.removesuffix("_close") + "_open" in _CONTAINERS and token.nesting < 0:
                stack.pop()
            elif token.map and token.type in _CONTAINERS | _LEAVES:
                node = _Node(token, *token.map, tuple(stack))
                self.nodes.append(node)
                if token.type in _CONTAINERS:
                    stack.append(node)
                else:
                    self.leaves.append(node)
            elif (
                token.type == "inline"
                and token.map
                and "\n" in token.content
                and any(
                    child.type not in {"text", "softbreak", "hardbreak"}
                    for child in token.children or []
                )
            ):
                span_parser = span_parser or inline_parser()
                _, spans = analyze(token.content, span_parser, env)
                for span in spans:
                    if "\n" not in token.content[span.start : span.end]:
                        continue
                    first = token.map[0] + token.content[: span.start].count("\n")
                    stop = (
                        token.map[0]
                        + token.content[: max(span.start, span.end - 1)].count("\n")
                        + 1
                    )
                    self.leaves.append(
                        _Node(Token("inline_crossing", "", 0), first, stop, tuple(stack))
                    )
        self._parents = [() for _ in self.lines]
        for node in self.nodes:
            parents = node.parents + (node,) if node.kind in _CONTAINERS else node.parents
            for row in range(node.first, min(node.stop, len(self.lines))):
                if len(parents) >= len(self._parents[row]):
                    self._parents[row] = parents

    def row(self, offset: int) -> int:
        return bisect_right(self.offsets, offset) - 1

    def extent(self, first: int, last: int) -> tuple[int, int]:
        return self.offsets[first], self.offsets[last] + len(self.lines[last])

    def parents(self, row: int) -> tuple[_Node, ...]:
        return self._parents[row]

    def item_padding(self, item: _Node) -> tuple[str, int]:
        cached = self._padding.get(id(item))
        if cached is not None:
            return cached
        view = self.view(item.first, item.parents)
        match = _ITEM.match(view.body)
        if match is None:
            raise ValueError("Unsupported list marker indentation")
        # CommonMark: more than four padding spaces contributes only one space
        # to the list marker; the remainder belongs to the item content.
        leading, marker, spacing = match.groups()
        padding_width = len((leading + marker + spacing).expandtabs(4)) - len(
            (leading + marker).expandtabs(4)
        )
        if padding_width > 4 and "\t" in spacing:
            raise ValueError("Unsupported partial-tab list padding")
        spacing = spacing if padding_width <= 4 else spacing[:1]
        prefix = leading + marker + spacing
        width = len(prefix.expandtabs(4))
        result = prefix, width
        self._padding[id(item)] = result
        return result

    def view(self, row: int, parents: tuple[_Node, ...] | None = None) -> _View:
        """Separate actual/canonical container prefixes from literal line content.

        Lazy continuations acquire explicit prefixes when edited, so inserting a
        heading or quote cannot accidentally eject the line from its list item.
        """
        parents = self.parents(row) if parents is None else parents
        key = row, parents
        if key in self._views:
            return self._views[key]
        body = self.lines[row]
        prefix = continuation = ""
        for node in parents:
            if node.kind == "blockquote_open":
                match = _QUOTE.match(body)
                part = match[0] if match else "> "
                body = body[len(match[0]) :] if match else body
                prefix += part
                continuation += part
            elif node.kind == "list_item_open":
                marker, width = self.item_padding(node)
                if row == node.first:
                    if not body.startswith(marker):
                        raise ValueError("Unsupported same-line container boundary")
                    body = body[len(marker) :]
                    prefix += marker
                else:
                    body, _ = _dedent(body, width)
                    prefix += " " * width
                continuation += " " * width
        result = _View(prefix, continuation, body)
        self._views[key] = result
        return result


def _dedent(line: str, width: int) -> tuple[str, int]:
    """Remove up to width leading columns, retaining the rest of a partial tab."""
    consumed = column = 0
    while consumed < len(line) and line[consumed] in " \t" and column < width:
        column += 4 - column % 4 if line[consumed] == "\t" else 1
        consumed += 1
    return " " * max(0, column - width) + line[consumed:], min(column, width)


def _map_column(old: str, new: str, column: int) -> int:
    common = 0
    while common < min(len(old), len(new)) and old[-common - 1] == new[-common - 1]:
        common += 1
    if column >= len(old) - common:
        return max(0, len(new) - (len(old) - column))
    return min(column, len(new) - common)


def _edit(
    doc: _Document, first: int, last: int, rows: list[str], start: int, end: int, *, shift: int = 0
) -> FormatEdit:
    begin, finish = doc.extent(first, last)
    text = doc.newline.join(rows)
    if start != end:
        a, b = 0, len(text)
    else:
        old_row = doc.row(start)
        row = max(0, min(len(rows) - 1, old_row - first + shift))
        column = _map_column(doc.lines[old_row], rows[row], start - doc.offsets[old_row])
        a = b = sum(len(value) + len(doc.newline) for value in rows[:row]) + column
    return FormatEdit(begin, finish, text, a, b)


def _overlaps(node: _Node, first: int, last: int) -> bool:
    return node.first <= last and first < node.stop


def _common_parent(doc: _Document, first: int, last: int, kind: str) -> _Node | None:
    candidates = [
        node for node in doc.nodes if node.kind == kind and node.first <= first and last < node.stop
    ]
    return max(candidates, key=lambda node: len(node.parents)) if candidates else None


def _separate(
    doc: _Document, first: int, last: int, rows: list[str], parents: tuple[_Node, ...]
) -> tuple[list[str], int]:
    """Separate an unwrapped block from sibling lists/quotes and lazy paragraphs."""
    blank = doc.view(first, parents).continuation.rstrip()
    shift = 0
    if first and doc.lines[first - 1].strip(" >\t"):
        rows.insert(0, blank)
        shift = 1
    if last + 1 < len(doc.lines) and doc.lines[last + 1].strip(" >\t"):
        rows.append(blank)
    return rows, shift


def _context_edit(
    doc: _Document,
    first: int,
    last: int,
    rows: list[str],
    start: int,
    end: int,
    *,
    separate: bool = False,
    shift: int = 0,
    selection: tuple[int, int] | None = None,
    parents: tuple[_Node, ...] | None = None,
) -> FormatEdit:
    """Keep unselected lazy continuations inside their original containers."""
    selection = selection or (0, len(doc.newline.join(rows)))
    added_before = 0
    if separate:
        rows, extra = _separate(
            doc, first, last, rows, doc.parents(first) if parents is None else parents
        )
        if extra:
            added_before += len(rows[0]) + len(doc.newline)
        shift += extra
    expanded_first, expanded_last = first, last
    for node in doc.leaves:
        if node.kind == "paragraph_open" and node.parents and _overlaps(node, first, last):
            expanded_first = min(expanded_first, node.first)
            expanded_last = max(expanded_last, node.stop - 1)
    before = []
    after = []
    for row in range(expanded_first, first):
        view = doc.view(row)
        before.append(view.prefix + view.body)
    for row in range(last + 1, expanded_last + 1):
        view = doc.view(row)
        after.append(view.prefix + view.body)
    added_before += sum(len(row) + len(doc.newline) for row in before)
    result = _edit(
        doc, expanded_first, expanded_last, before + rows + after, start, end, shift=shift
    )
    if start != end:
        result = replace(
            result,
            selection_start=added_before + selection[0],
            selection_end=added_before + selection[1],
        )
    return result


def _heading_edit(
    doc: _Document, first: int, last: int, level: int, start: int, end: int
) -> FormatEdit | None:
    headings = [
        node for node in doc.leaves if node.kind == "heading_open" and _overlaps(node, first, last)
    ]
    for node in headings:
        if node.token.markup in {"=", "-"}:
            first, last = min(first, node.first), max(last, node.stop - 1)
            if node.stop - node.first > 2 and level > 0:
                # Multiline setext headings may contain hard breaks. ATX cannot
                # represent them without changing the inline source semantics.
                return None
    rows: list[str] = []
    by_first = {node.first: node for node in headings}
    line = first
    while line <= last:
        view = doc.view(line)
        heading = by_first.get(line)
        if heading and heading.token.markup in {"=", "-"}:
            values = [doc.view(index, heading.parents) for index in range(line, heading.stop - 1)]
            if level == 0:
                rows.extend(value.prefix + value.body for value in values)
                line = heading.stop
                continue
            body = values[0].body
            line = heading.stop
        else:
            match = _ATX.match(view.body) if heading else None
            body = re.sub(r"[ \t]+#+[ \t]*$", "", match[2]) if match else view.body
            line += 1
        if not body.strip() and start != end:
            rows.append(view.prefix + body)
        else:
            # A formerly literal trailing hash must not turn into an ATX closer.
            if level and (not heading or heading.token.markup in {"=", "-"}):
                body = re.sub(r"([ \t])(#(?:#+)?)([ \t]*)$", r"\1\\\2\3", body)
            rows.append(view.prefix + ("#" * level + " " if level else "") + body)
    if level == 0 and not headings:
        return None
    selection = None
    if len(rows) == 1 and level:
        selection = (len(doc.view(first).prefix) + level + 1, len(rows[0]))
    return _context_edit(
        doc, first, last, rows, start, end, separate=level == 0, selection=selection
    )


def _quote_remove(doc: _Document, quote: _Node, start: int, end: int) -> FormatEdit:
    rows = []
    for row in range(quote.first, quote.stop):
        view = doc.view(row, quote.parents)
        match = _QUOTE.match(view.body)
        rows.append(view.prefix + (view.body[len(match[0]) :] if match else view.body))
    rows, shift = _separate(doc, quote.first, quote.stop - 1, rows, quote.parents)
    return _edit(doc, quote.first, quote.stop - 1, rows, start, end, shift=shift)


def _items(doc: _Document, first: int, last: int) -> list[_Node]:
    first_items = [node for node in doc.parents(first) if node.kind == "list_item_open"]
    last_items = [node for node in doc.parents(last) if node.kind == "list_item_open"]
    if not first_items or not last_items:
        return []
    depth = min(len(first_items), len(last_items)) - 1
    left, right = first_items[depth], last_items[depth]
    if left.parents[-1] is not right.parents[-1]:
        return []
    parent = left.parents[-1]
    return [
        node
        for node in doc.nodes
        if node.kind == "list_item_open"
        and node.parents[-1] is parent
        and _overlaps(node, first, last)
    ]


def _list_edit(doc: _Document, items: list[_Node], kind: str, start: int, end: int) -> FormatEdit:
    rows = []
    for index, item in enumerate(items, 1):
        old_marker, width = doc.item_padding(item)
        marker = "- " if kind == "bullet" else f"{index}. " if kind == "ordered" else ""
        for row in range(item.first, item.stop):
            view = doc.view(row, item.parents)
            if row == item.first:
                body = view.body[len(old_marker) :]
                rows.append(view.prefix + marker + body)
            else:
                body, _ = _dedent(view.body, width)
                rows.append(view.prefix + " " * len(marker) + body)
        if not kind and index < len(items) and rows[-1].strip(" >\t"):
            rows.append(doc.view(item.first, item.parents).continuation.rstrip())
    first, last = items[0].first, items[-1].stop - 1
    shift = 0
    if not kind:
        rows, shift = _separate(doc, first, last, rows, items[0].parents)
    return _edit(doc, first, last, rows, start, end, shift=shift)


def _code_remove(doc: _Document, node: _Node, start: int, end: int) -> FormatEdit:
    view = doc.view(node.first, node.parents)
    content = node.token.content.removesuffix("\n").split("\n")
    rows = [view.prefix + content[0]]
    rows.extend(view.continuation + line for line in content[1:])
    rows, shift = _separate(doc, node.first, node.stop - 1, rows, node.parents)
    return _edit(
        doc, node.first, node.stop - 1, rows, start, end, shift=shift - (node.kind == "fence")
    )


def _code_apply(doc: _Document, first: int, last: int, start: int, end: int) -> FormatEdit | None:
    parents = doc.parents(first)
    # A fence cannot cross list items or quote boundaries; reject partial mixed
    # containers instead of swallowing markers belonging to adjacent blocks.
    if any(doc.parents(row) != parents for row in range(first, last + 1) if doc.lines[row].strip()):
        return None
    first_view = doc.view(first, parents)
    content = "\n".join(doc.view(row, parents).body for row in range(first, last + 1))
    longest = max((len(match[0]) for match in re.finditer(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    language = detect_code_language(content)
    rows = [first_view.prefix + fence + language]
    rows.extend(first_view.continuation + line for line in content.split("\n"))
    rows.append(first_view.continuation + fence)
    body_start = len(rows[0]) + len(doc.newline) + len(first_view.continuation)
    body_end = len(doc.newline.join(rows[:-1]))
    return _context_edit(
        doc, first, last, rows, start, end, shift=1, selection=(body_start, body_end)
    )


def block_actions(source: str, start: int, end: int) -> list[FormatAction]:
    """Return parser-aware edits for headings, quotes, lists, and code blocks.

    Offsets are Python string offsets. A non-empty selection touches whole lines,
    except that its end at the next line's start excludes that next line. Removing
    a containing quote/list/code expands to that complete structure and says so
    in the menu label. Unsupported partial structures yield unavailable actions.
    """
    start, end = sorted((max(0, min(len(source), start)), max(0, min(len(source), end))))
    doc = _Document(source)
    first, last = doc.row(start), doc.row(end - 1 if end > start else end)
    for node in doc.leaves:
        if (
            node.kind == "heading_open"
            and node.token.markup in {"=", "-"}
            and _overlaps(node, first, last)
        ):
            first, last = min(first, node.first), max(last, node.stop - 1)
    protected = [
        node for node in doc.leaves if node.kind in _PROTECTED and _overlaps(node, first, last)
    ]
    partial = any(first > node.first or last + 1 < node.stop for node in protected)
    codes = [node for node in protected if node.kind in {"fence", "code_block"}]
    containing_code = next(
        (node for node in codes if node.first <= first and last < node.stop), None
    )
    headings = [
        node for node in doc.leaves if node.kind == "heading_open" and _overlaps(node, first, last)
    ]
    quote = _common_parent(doc, first, last, "blockquote_open")
    actions: list[FormatAction] = []
    try:
        items = _items(doc, first, last)
        list_kind = items[0].parents[-1].kind if items else ""
        heading_levels = {
            row: int(node.token.tag[1:])
            for node in headings
            for row in range(node.first, node.stop)
        }
        selected_levels = {
            heading_levels.get(row, 0)
            for row in range(first, last + 1)
            if doc.view(row).body.strip()
        }
        for level in range(7):
            checked = (selected_levels or {0}) == {level} and not protected
            edit = None if protected else _heading_edit(doc, first, last, level, start, end)
            actions.append(
                FormatAction(
                    f"heading.{level}",
                    "標準の段落" if level == 0 else f"見出し {level}",
                    edit,
                    checked,
                    "heading",
                )
            )
        quote_edit = None
        if not partial:
            quote_rows = []
            parents = tuple(node for node in doc.parents(first) if node in doc.parents(last))
            for row in range(first, last + 1):
                view = doc.view(row, parents)
                quote_rows.append(view.prefix + "> " + view.body)
            quote_edit = _context_edit(
                doc, first, last, quote_rows, start, end, separate=True, parents=parents
            )
        actions.append(FormatAction("quote.apply", "引用を追加", quote_edit))
        removal = _quote_remove(doc, quote, start, end) if quote and not partial else None
        expanded = quote and (quote.first < first or quote.stop > last + 1)
        actions.append(
            FormatAction(
                "quote.remove",
                "引用ブロック全体を解除" if expanded else "引用を解除",
                removal,
                bool(quote),
            )
        )
        for kind, token_kind, title in [
            ("bullet", "bullet_list_open", "箇条書き"),
            ("ordered", "ordered_list_open", "番号付きリスト"),
        ]:
            edit = None
            list_safe = not protected or (
                items
                and not partial
                and all(any(item in node.parents for item in items) for node in protected)
            )
            if list_safe:
                if items and list_kind != token_kind:
                    edit = _list_edit(doc, items, kind, start, end)
                elif (
                    not items
                    and not any(node.token.markup in {"=", "-"} for node in headings)
                    and not any(
                        node.kind == "list_item_open" and _overlaps(node, first, last)
                        for node in doc.nodes
                    )
                ):
                    rows = []
                    number = 1
                    for row in range(first, last + 1):
                        view = doc.view(row)
                        marker = "- " if kind == "bullet" else f"{number}. "
                        rows.append(
                            view.prefix
                            + (marker if view.body.strip() or start == end else "")
                            + view.body
                        )
                        number += bool(view.body.strip())
                    edit = _context_edit(doc, first, last, rows, start, end, separate=True)
            actions.append(
                FormatAction(f"{kind}.apply", f"{title}にする", edit, list_kind == token_kind)
            )
            remove = (
                _list_edit(doc, items, "", start, end)
                if items and list_kind == token_kind and list_safe
                else None
            )
            expanded = items and (items[0].first < first or items[-1].stop > last + 1)
            actions.append(
                FormatAction(
                    f"{kind}.remove",
                    "リスト項目全体を解除" if expanded else f"{title}を解除",
                    remove,
                    list_kind == token_kind,
                )
            )
        code_edit = _code_apply(doc, first, last, start, end) if not partial and not codes else None
        actions.append(FormatAction("code_block.apply", "コードブロックにする", code_edit))
        remove = _code_remove(doc, containing_code, start, end) if containing_code else None
        actions.append(
            FormatAction(
                "code_block.remove", "コードブロック全体を解除", remove, bool(containing_code)
            )
        )
    except (ValueError, IndexError):
        # Tabs or unusual same-line containers that cannot be mapped safely must
        # not offer a partially computed edit. Keep menu keys stable for the UI.
        return [
            FormatAction(
                f"heading.{level}",
                "標準の段落" if level == 0 else f"見出し {level}",
                group="heading",
            )
            for level in range(7)
        ] + [
            FormatAction(f"{kind}.{mode}", label)
            for kind, label in [
                ("quote", "引用"),
                ("bullet", "箇条書き"),
                ("ordered", "番号付きリスト"),
                ("code_block", "コードブロック"),
            ]
            for mode in ("apply", "remove")
        ]
    return actions
