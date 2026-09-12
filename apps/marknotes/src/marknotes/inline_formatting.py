"""Reversible inline source formatting, checked against Markdown's own parser."""

from __future__ import annotations

import itertools
import re
import string
from dataclasses import replace

from .formatting_types import FormatAction, FormatEdit
from .inline_syntax import analyze, find_region, parser

_LABELS = {
    "bold": "太字",
    "italic": "斜体",
    "strike": "取り消し線",
    "inline_code": "インラインコード",
}
_MARKERS = {"bold": "**", "italic": "*", "strike": "~~"}
_PROTECTED = {
    "code_inline",
    "math_inline",
    "math_inline_display",
    "image",
    "html_inline",
    "autolink",
}


def _units(atoms):
    result = []
    for atom in atoms:
        kind = "text" if atom.kind in {"text", "text_special"} else atom.kind
        for character in atom.text:
            result.append(
                (character, frozenset() if character.isspace() else atom.styles, kind, atom.link)
            )
        if not atom.text and kind != "text":
            result.append(("", atom.styles, kind, atom.link))
    return result


def _selected(atom, start, end):
    return atom.start < end and atom.end > start


def _safe_selection(atoms, spans, start, end):
    for span in spans:
        if span.start >= end or span.end <= start:
            continue
        if span.kind in _PROTECTED:
            return False
        if span.kind == "link" and not (
            span.content_start <= start < end <= span.content_end
            or start <= span.start
            and span.end <= end
        ):
            return False
    return all(
        start <= atom.start and atom.end <= end for atom in atoms if _selected(atom, start, end)
    )


def _merge(ranges):
    result = []
    for left, right in sorted(ranges):
        if left >= right:
            continue
        if result and left <= result[-1][1]:
            result[-1] = (result[-1][0], max(right, result[-1][1]))
        else:
            result.append((left, right))
    return result


def _direct(text, spans, start, end, kind, apply):
    removed = set()
    ranges = []
    for span in spans:
        if span.kind != kind or span.content_start >= end or span.content_end <= start:
            continue
        removed.update(range(span.start, span.content_start))
        removed.update(range(span.content_end, span.end))
        if apply:
            ranges.append((span.content_start, span.content_end))
        else:
            ranges.extend(
                (
                    (span.content_start, min(start, span.content_end)),
                    (max(end, span.content_start), span.content_end),
                )
            )
    if apply:
        ranges.append((start, end))
    mapping, base = [0], []
    for index, char in enumerate(text):
        if index not in removed:
            base.append(char)
        mapping.append(len(base))
    base = "".join(base)
    trimmed = []
    for left, right in _merge((mapping[a], mapping[b]) for a, b in ranges if a < b):
        while left < right and base[left].isspace():
            left += 1
        while right > left and base[right - 1].isspace():
            right -= 1
        if left < right:
            trimmed.append((left, right))
    for left, right in reversed(trimmed):
        marker = _MARKERS[kind]
        base = base[:left] + marker + base[left:right] + marker + base[right:]
    return base


def _serialize(atoms, spans, text, order, markers):
    """Split crossing styles into balanced runs, keeping link syntax untouched."""
    links = sorted((span for span in spans if span.kind == "link"), key=lambda span: span.start)

    def sequence(items):
        if not items:
            return ""
        result, active = [], ()
        for index, (raw, styles, whitespace) in enumerate(items):
            if whitespace:
                before = items[index - 1][1] if index else frozenset()
                after = items[index + 1][1] if index + 1 < len(items) else frozenset()
                styles = styles & before & after
            desired = tuple(kind for kind in order if kind in styles)
            common = 0
            while common < min(len(active), len(desired)) and active[common] == desired[common]:
                common += 1
            result.extend(markers[kind] for kind in reversed(active[common:]))
            result.extend(markers[kind] for kind in desired[common:])
            result.append(raw)
            active = desired
        result.extend(markers[kind] for kind in reversed(active))
        return "".join(result)

    result, index = [], 0
    for link in links:
        while index < len(atoms) and atoms[index].end <= link.start:
            atom = atoms[index]
            result.append((atom.raw, atom.styles, atom.text.isspace()))
            index += 1
        children = []
        while index < len(atoms) and atoms[index].start < link.end:
            children.append(atoms[index])
            index += 1
        visible = [atom.styles for atom in children if not atom.text.isspace()]
        shared = frozenset.intersection(*visible) if visible else frozenset()
        label = sequence(
            [(atom.raw, atom.styles - shared, atom.text.isspace()) for atom in children]
        )
        result.append(
            (
                text[link.start : link.content_start] + label + text[link.content_end : link.end],
                shared,
                False,
            )
        )
    for atom in atoms[index:]:
        result.append((atom.raw, atom.styles, atom.text.isspace()))
    return sequence(result)


def _encode(region, source, candidate):
    """Restore table escapes and continuation prefixes removed by block parsing."""
    prefixes = []
    for index, char in enumerate(region.text):
        if char == "\n" and index + 1 < len(region.positions):
            prefixes.append(source[region.positions[index][1] : region.positions[index + 1][0]])
    if candidate.count("\n") != len(prefixes):
        return None
    result, mapping, line = [], [0], 0
    length = 0
    for char in candidate:
        piece = ("\\" if region.table and char == "|" else "") + char
        if char == "\n":
            piece += prefixes[line]
            line += 1
        result.append(piece)
        length += len(piece)
        mapping.append(length)
    return "".join(result), mapping


def _edit(region, source, original, parsed, candidate, start, end):
    encoded = _encode(region, source, candidate)
    if encoded is None:
        return None
    text, mapping = encoded
    selected_units, total = [], 0
    for atom in original:
        size = max(1, len(atom.text))
        if _selected(atom, start, end):
            selected_units.extend(range(total, total + size))
        total += size
    if not selected_units:
        return None
    first, last = selected_units[0], selected_units[-1]
    cursor, begins, ends = 0, [], []
    for atom in parsed:
        size = max(1, len(atom.text))
        if cursor <= last and cursor + size > first:
            begins.append(atom.start)
            ends.append(atom.end)
        cursor += size
    if not begins:
        return None
    return FormatEdit(
        region.positions[0][0],
        region.positions[-1][1],
        text,
        mapping[min(begins)],
        mapping[max(ends)],
    )


def _format_edit(region, source, md, env, atoms, spans, start, end, kind, apply):
    modified = [
        replace(atom, styles=(atom.styles | {kind}) if apply else (atom.styles - {kind}))
        if _selected(atom, start, end)
        else atom
        for atom in atoms
    ]
    expected = _units(modified)

    def candidates():
        yield _direct(region.text, spans, start, end, kind, apply)
        # Ordinary selections only need one parse. Construct fallback runs
        # lazily so a long paragraph does not pay for 24 unused serializations.
        if "\n" in region.text or any(span.kind in {"html_inline", "autolink"} for span in spans):
            return
        for order in itertools.permutations(("bold", "italic", "strike")):
            for bold, italic in (("**", "*"), ("**", "_"), ("__", "*"), ("__", "_")):
                yield _serialize(
                    modified,
                    spans,
                    region.text,
                    order,
                    {"bold": bold, "italic": italic, "strike": "~~"},
                )

    seen = set()
    for candidate in candidates():
        if candidate in seen or candidate == region.text:
            continue
        seen.add(candidate)
        parsed, _ = analyze(candidate, md, env)
        if _units(parsed) == expected:
            return _edit(region, source, atoms, parsed, candidate, start, end)
    return None


def _remove_code(region, source, md, env, atoms, span, atom):
    # First try raw displayed text; print(x) does not need print\(x\).
    # Entity syntax and new headings/lists need escaping as well as '*'.
    left, right = region.positions[span.start][0], region.positions[span.end - 1][1]
    line_start = source.rfind("\n", 0, region.positions[0][0]) + 1
    line_end = source.find("\n", region.positions[-1][1])
    if line_end < 0:
        line_end = len(source)

    def shape(value):
        blocks = []
        md.block.parse(value, md, {}, blocks)
        return [
            (item.type, item.tag, item.hidden, tuple(sorted(item.attrs.items())))
            for item in blocks
            if item.type != "inline"
        ]

    original_shape = shape(source[line_start:line_end])
    expected = _units([replace(item, kind="text") if item is atom else item for item in atoms])

    def valid(value):
        candidate = region.text[: span.start] + value + region.text[span.end :]
        parsed, _ = analyze(candidate, md, env)
        if _units(parsed) != expected:
            return False
        physical = value.replace("|", "\\|") if region.table else value
        return shape(source[line_start:left] + physical + source[right:line_end]) == original_shape

    plain = atom.text
    if not valid(plain):
        # Escaping both ends of [] / () can create TeX delimiters.
        escaped = [1 if char in string.punctuation and char not in ")]" else 0 for char in plain]

        def escape_flags():
            return "".join(
                (f"&#{ord(char)};" if flag == 2 else ("\\" if flag else "") + char)
                for char, flag in zip(plain, escaped, strict=True)
            )

        if not valid(escape_flags()):
            escaped = [2 if char in string.punctuation else 0 for char in plain]
            if not valid(escape_flags()):
                return None
        attempts = 0
        for index in reversed(range(len(escaped))):
            flag = escaped[index]
            if not flag:
                continue
            if attempts >= 64:
                break
            attempts += 1
            escaped[index] = 0
            if not valid(escape_flags()):
                escaped[index] = flag
        plain = escape_flags()
    if region.table:
        plain = plain.replace("|", "\\|")
    return FormatEdit(left, right, plain, 0, len(plain))


def _code_action(region, source, md, env, atoms, spans, start, end):
    overlapping = [span for span in spans if span.start < end and span.end > start]
    codes = [span for span in overlapping if span.kind == "code_inline"]
    if len(codes) == 1 and codes[0].start <= start < end <= codes[0].end:
        span = codes[0]
        atom = next(
            atom for atom in atoms if atom.kind == "code_inline" and atom.start == span.start
        )
        edit = _remove_code(region, source, md, env, atoms, span, atom)
        return FormatAction(
            "inline_code.remove", "インラインコード全体を解除", edit, True, "inline"
        )
    if (
        not any(_selected(atom, start, end) for atom in atoms)
        or not _safe_selection(atoms, spans, start, end)
        or "\n" in region.text[start:end]
    ):
        return FormatAction("inline_code.apply", "インラインコードにする", group="inline")
    selected = region.text[start:end]
    longest = max((len(match[0]) for match in re.finditer(r"`+", selected)), default=0)
    marker = "`" * (longest + 1)
    padding = (
        " "
        if selected.startswith("`")
        or selected.endswith("`")
        or (selected.startswith(" ") and selected.endswith(" ") and selected.strip())
        else ""
    )
    wrapped = marker + padding + selected + padding + marker
    candidate = region.text[:start] + wrapped + region.text[end:]
    parsed, parsed_spans = analyze(candidate, md, env)
    code = next(
        (
            span
            for span in parsed_spans
            if span.kind == "code_inline"
            and span.start == start
            and span.end == start + len(wrapped)
        ),
        None,
    )
    encoded = _encode(region, source, candidate)
    if code is None or encoded is None:
        edit = None
    else:
        value = next(atom for atom in parsed if atom.kind == "code_inline" and atom.start == start)
        ancestors = frozenset(
            span.kind
            for span in spans
            if span.kind in _MARKERS and span.content_start <= start < end <= span.content_end
        )
        link = tuple(tuple(sorted(dict(attrs).items())) for attrs in value.link)
        expected = _units([atom for atom in atoms if atom.end <= start])
        expected += [
            (character, frozenset() if character.isspace() else ancestors, "code_inline", link)
            for character in selected
        ]
        expected += _units([atom for atom in atoms if atom.start >= end])
        if value.text != selected or _units(parsed) != expected:
            edit = None
        else:
            text, positions = encoded
            edit = FormatEdit(
                region.positions[0][0],
                region.positions[-1][1],
                text,
                positions[start + len(marker) + len(padding)],
                positions[start + len(marker) + len(padding) + len(selected)],
            )
    return FormatAction("inline_code.apply", "インラインコードにする", edit, group="inline")


def inline_actions(source: str, start: int, end: int) -> list[FormatAction]:
    """Offer safe inline edits for a nonempty Python-character selection."""
    unavailable = [
        FormatAction(kind + ".apply", label + "にする", group="inline")
        for kind, label in _LABELS.items()
    ]
    if not 0 <= start < end <= len(source) or "\r" in source:
        return unavailable
    md = parser()
    found = find_region(source, start, end, md)
    if found is None:
        return unavailable
    region, (start, end), env = found
    if len(region.text) > 20_000:
        return unavailable
    atoms, spans = analyze(region.text, md, env)
    selected = [atom for atom in atoms if _selected(atom, start, end) and not atom.text.isspace()]
    safe = bool(selected) and _safe_selection(atoms, spans, start, end)
    actions = []
    for kind, label in _LABELS.items():
        if kind == "inline_code":
            actions.append(_code_action(region, source, md, env, atoms, spans, start, end))
            continue
        any_set = any(kind in atom.styles for atom in selected)
        all_set = bool(selected) and all(kind in atom.styles for atom in selected)
        if not all_set:
            edit = (
                _format_edit(region, source, md, env, atoms, spans, start, end, kind, True)
                if safe
                else None
            )
            actions.append(
                FormatAction(
                    kind + ".apply",
                    label + ("に統一" if any_set else "にする"),
                    edit,
                    group="inline",
                )
            )
        if any_set:
            edit = (
                _format_edit(region, source, md, env, atoms, spans, start, end, kind, False)
                if safe
                else None
            )
            actions.append(
                FormatAction(kind + ".remove", label + "を解除", edit, all_set, "inline")
            )
    return actions
