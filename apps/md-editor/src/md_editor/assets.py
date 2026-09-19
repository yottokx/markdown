"""Local image assets and syntax-aware Markdown destination rewriting."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from html import escape, unescape
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from markdown_it import MarkdownIt
from markdown_it.common.utils import normalizeReference
from markdown_it.rules_inline.html_inline import html_inline as html_rule
from markdown_it.rules_inline.image import image as image_rule
from markdown_it.rules_inline.link import link as link_rule
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader

from .image_sources import parse_srcset
from .math_parser import math_plugin
from .selection_mapping import MappedText


@dataclass(frozen=True, slots=True)
class Destination:
    """Character offsets cover the URL only, excluding optional angle brackets."""

    start: int
    end: int
    url: str
    is_image: bool
    image_spans: tuple[tuple[int, int], ...] = ()
    is_html: bool = False


def _html_destinations(fragment: str, positions: list[int]) -> list[Destination]:
    """Attribute positions from actual HTML tags; comments/scripts stay opaque."""
    results = []
    offsets = [0]
    for line in fragment.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    class Reader(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.opaque = 0

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style", "textarea", "template", "pre", "code"}:
                self.opaque += 1
            if self.opaque or tag not in {"img", "source", "a"}:
                return
            attributes = (
                {"src", "srcset"} if tag == "img" else {"srcset"} if tag == "source" else {"href"}
            )
            raw = self.get_starttag_text()
            tag_end = re.match(r"<[^\s/>]+", raw).end()
            # Consume complete attributes so src inside alt="..." or data-src
            # never gets mistaken for the actual image destination.
            pattern = r"([^\s/>=]+)(?:\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+))?"
            line, column = self.getpos()
            start = offsets[line - 1] + column
            span = ((positions[start], positions[min(start + len(raw), len(positions)) - 1] + 1),)
            seen = set()
            for match in re.finditer(pattern, raw[tag_end:]):
                attribute = match[1].lower()
                if attribute not in attributes or attribute in seen or match[2] is None:
                    continue
                seen.add(attribute)
                value = match[2]
                quoted = value.startswith(('"', "'"))
                lo = start + tag_end + match.start(2) + int(quoted)
                hi = start + tag_end + match.end(2) - int(quoted)
                if lo >= hi or hi > len(positions):
                    continue
                value = value[1:-1] if quoted else value
                candidates = [(lo, hi, unescape(value))]
                if attribute == "srcset":
                    decoded, mapping, cursor = "", [], 0
                    for entity in re.finditer(
                        r"&(?:#[xX][\da-fA-F]+;?|#\d+;?|[a-zA-Z][a-zA-Z\d]+;)", value
                    ):
                        decoded += value[cursor : entity.start()]
                        mapping.extend((i, i + 1) for i in range(cursor, entity.start()))
                        replacement = unescape(entity[0])
                        decoded += replacement
                        mapping.extend((entity.start(), entity.end()) for _ in replacement)
                        cursor = entity.end()
                    decoded += value[cursor:]
                    mapping.extend((i, i + 1) for i in range(cursor, len(value)))
                    candidates, cursor = [], 0
                    for url, _descriptor in parse_srcset(decoded):
                        index = decoded.find(url, cursor)
                        if index >= 0 and url:
                            candidates.append(
                                (lo + mapping[index][0], lo + mapping[index + len(url) - 1][1], url)
                            )
                            cursor = index + len(url)
                for first, last, url in candidates:
                    results.append(
                        Destination(
                            positions[first],
                            positions[last - 1] + 1,
                            url,
                            tag in {"img", "source"},
                            span if tag in {"img", "source"} else (),
                            True,
                        )
                    )

        def handle_endtag(self, tag):
            if tag in {"script", "style", "textarea", "template", "pre", "code"} and self.opaque:
                self.opaque -= 1

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    Reader().feed(fragment)
    return results


def markdown_destinations(source: str) -> list[Destination]:
    """Find parsed destinations, including used reference definitions.

    The Markdown parser decides which constructs are actual links: code fences,
    inline code, TeX bodies, escaped examples, and unused definitions are never rewritten.
    Source positions are mapped from its inline tokens, preserving all other text.
    """
    parser = MarkdownIt("commonmark", {"html": True, "store_labels": True})
    validate_link = parser.validateLink
    parser.validateLink = lambda url: url.lower().startswith("file:") or validate_link(url)
    parser.enable(["table", "strikethrough"])
    parser.use(math_plugin)
    lines = source.splitlines(keepends=True) or [""]
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    found: list[Destination] = []
    used_labels: dict[str, bool] = {}
    image_labels: dict[str, list[tuple[int, int]]] = {}
    current_positions: list[int] = []
    current_tokens: list = []

    def wrap(rule, is_image):
        def capture(state, silent):
            start = state.pos
            maximum = state.posMax
            wanted = "!" if is_image else "["
            if state.src[start : start + 1] != wanted:
                return False
            label_end = state.md.helpers.parseLinkLabel(state, start + int(is_image), not is_image)
            token_start = len(state.tokens)
            success = rule(state, silent)
            if not success or silent or state.tokens is not current_tokens:
                return success
            token_type = "image" if is_image else "link_open"
            candidates = [t for t in state.tokens[token_start:] if t.type == token_type]
            if not candidates:
                return success
            token = candidates[-1] if is_image else candidates[0]
            span = ()
            if is_image and start < len(current_positions) and state.pos <= len(current_positions):
                span = ((current_positions[start], current_positions[state.pos - 1] + 1),)
            label = token.meta.get("label")
            if label:
                used_labels[label] = used_labels.get(label, False) or is_image
                if span:
                    image_labels.setdefault(label, []).extend(span)
                return success
            pos = label_end + 1
            if label_end < 0 or pos >= maximum or state.src[pos] != "(":
                return success
            pos += 1
            while pos < maximum and state.src[pos] in " \t\n":
                pos += 1
            destination = state.md.helpers.parseLinkDestination(state.src, pos, maximum)
            if not destination.ok:
                return success
            end = destination.pos
            if state.src[pos : pos + 1] == "<":
                pos += 1
                end -= 1
            if end <= pos or end > len(current_positions):
                return success
            indices = current_positions[pos:end]
            if any(b != a + 1 for a, b in pairwise(indices)):
                return success
            found.append(
                Destination(
                    indices[0],
                    indices[-1] + 1,
                    token.attrGet("src" if is_image else "href") or "",
                    is_image,
                    span,
                )
            )
            return success

        return capture

    def capture_html(state, silent):
        start = state.pos
        success = html_rule(state, silent)
        if success and not silent and state.tokens is current_tokens:
            positions = current_positions[start : state.pos]
            if positions and len(positions) == state.pos - start:
                found.extend(_html_destinations(state.src[start : state.pos], positions))
        return success

    parser.inline.ruler.at("html_inline", capture_html)
    parser.inline.ruler.at("image", wrap(image_rule, True))
    parser.inline.ruler.at("link", wrap(link_rule, False))

    def parse_inline(state):
        nonlocal current_positions, current_tokens
        active_map = [0, len(lines)]
        consumed: dict[tuple[int, int], int] = {}
        for token in state.tokens:
            if token.map:
                active_map = token.map
            if token.type == "html_block":
                lo, hi = offsets[token.map[0]], offsets[min(token.map[1], len(offsets) - 1)]
                raw = source[lo:hi]
                found.extend(_html_destinations(raw, list(range(lo, hi))))
            if token.type != "inline":
                continue
            start_line, end_line = token.map or active_map
            lo = offsets[min(start_line, len(offsets) - 1)]
            hi = offsets[min(end_line, len(offsets) - 1)]
            span = (lo, hi)
            cursor = consumed.get(span, lo)
            mapping: list[int] = []
            # Blockquotes and list items remove their prefixes from content;
            # locate each content line within its corresponding source range.
            for part in token.content.splitlines(keepends=True):
                body = part.removesuffix("\n")
                index = source.find(body, cursor, hi) if body else cursor
                if index < 0 and "|" in body:
                    # The table block rule unescapes pipes before inline parsing.
                    # Map those cells against the original escaped characters.
                    pattern = re.escape(body).replace(r"\|", r"\\?\|")
                    match = re.search(pattern, source[cursor:hi])
                    if match:
                        index = cursor + match.start()
                        raw_cursor = index
                        for character in body:
                            if character == "|" and source[raw_cursor] == "\\":
                                raw_cursor += 1
                            mapping.append(raw_cursor)
                            raw_cursor += 1
                        cursor = raw_cursor
                        continue
                if index < 0:
                    mapping = []
                    break
                mapping.extend(range(index, index + len(body)))
                cursor = index + len(body)
                if part.endswith("\n"):
                    newline_pos = source.find("\n", cursor, hi)
                    if newline_pos < 0:
                        mapping = []
                        break
                    mapping.append(newline_pos)
                    cursor = newline_pos + 1
            consumed[span] = cursor
            current_positions = mapping
            current_tokens = []
            token.children = current_tokens
            state.md.inline.parse(token.content, state.md, state.env, current_tokens)

    parser.core.ruler.at("inline", parse_inline)
    env: dict = {}
    parser.parse(source, env)
    for label, is_image in used_labels.items():
        definition = env.get("references", {}).get(label)
        if not definition:
            continue
        first, last = definition["map"]
        lo, hi = offsets[first], offsets[min(last, len(offsets) - 1)]
        for match in re.finditer(r"\[((?:\\.|[^\]])+)\]:[ \t\n]*", source[lo:hi]):
            if normalizeReference(match[1]) != label:
                continue
            pos = lo + match.end()
            # A reference destination on the next line can retain blockquote
            # prefixes in the source even though the block parser removed them.
            if "\n" in match[0]:
                continuation = re.match(r"(?:>[ \t]*)+", source[pos:hi])
                if continuation:
                    pos += continuation.end()
            result = parser.helpers.parseLinkDestination(source, pos, hi)
            if not result.ok or parser.normalizeLink(result.str) != definition["href"]:
                continue
            end = result.pos
            if source[pos : pos + 1] == "<":
                pos += 1
                end -= 1
            spans = tuple(image_labels.get(label, []))
            if is_image:
                spans += ((lo + match.start(), end),)
            found.append(Destination(pos, end, definition["href"], is_image, spans))
            break
    return sorted({(d.start, d.end): d for d in found}.values(), key=lambda d: d.start)


def rewrite_destinations(source: str, replacement: Callable[[Destination], str | None]) -> str:
    """Replace only parsed URL spans, preserving labels, titles and code samples."""
    changes = [(d, replacement(d)) for d in markdown_destinations(source)]
    for destination, value in reversed(changes):
        if value is not None and value != destination.url:
            if destination.is_html:
                value = escape(value, quote=True)
            if isinstance(source, MappedText):
                source = source.replace_slice(destination.start, destination.end, value)
            else:
                source = source[: destination.start] + value + source[destination.end :]
    return source


def local_path(url: str, base_dir: Path) -> Path | None:
    """Resolve a relative URL without treating remote, absolute or fragment links as files."""
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    decoded = unquote(parsed.path).replace("\\", "/")
    if decoded.startswith("/") or re.match(r"^[a-zA-Z]:", decoded):
        return None
    return (base_dir / decoded).resolve()


def managed_image_path(url: str, base_dir: Path) -> Path | None:
    """Only existing files genuinely inside this document's img directory qualify."""
    decoded = unquote(urlsplit(url).path).replace("\\", "/")
    if not decoded.startswith("img/"):
        return None
    base = base_dir.resolve()
    image_dir = base / "img"
    # A linked img directory may expose unrelated files; never manage it.
    if image_dir.is_symlink() or image_dir.resolve() != image_dir:
        return None
    candidate = local_path(url, base)
    if candidate is None or not candidate.is_relative_to(image_dir):
        return None
    original = base / decoded
    if original.is_symlink() or candidate != original.absolute():
        # Reject escaping/indirect paths as well as symbolic-link components.
        return None
    return candidate if candidate.is_file() else None


def rebase_url(url: str, old_base: Path, new_base: Path) -> str | None:
    target = local_path(url, old_base)
    if target is None:
        return None
    parsed = urlsplit(url)
    try:
        relative = os.path.relpath(target, new_base).replace("\\", "/")
    except ValueError:
        raise ValueError(f"別ドライブへの相対リンクを維持できません: {url}") from None
    return urlunsplit(("", "", quote(relative, safe="/-._~"), parsed.query, parsed.fragment))


class AssetManager:
    """Collision-safe image creation. Existing files are never overwritten or deleted."""

    def __init__(self, base_dir: Path, stem: str = "untitled"):
        self.base_dir = Path(base_dir).resolve()
        self.stem = stem

    @property
    def image_dir(self) -> Path:
        directory = self.base_dir / "img"
        if directory.is_symlink() or directory.resolve() != directory:
            raise ValueError("img が別の場所へのリンクです。画像は保存できません。")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _create(self, data: bytes, extension: str) -> Path:
        directory = self.image_dir
        # The same sequence is shared by PNG, JPEG, GIF, etc.
        pattern = re.compile(rf"^{re.escape(self.stem)}_(\d+)\.[^.]+$", re.IGNORECASE)
        maximum = max(
            (int(m[1]) for p in directory.iterdir() if (m := pattern.match(p.name))), default=0
        )
        while True:
            maximum += 1
            destination = directory / f"{self.stem}_{maximum:05d}.{extension.lower()}"
            opened = False
            try:
                with destination.open("xb") as stream:
                    opened = True
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                continue
            except OSError:
                if opened:
                    destination.unlink(missing_ok=True)
                raise
            return destination

    def _markdown(self, path: Path) -> str:
        relative = "img/" + path.name
        return f"![画像]({quote(relative, safe='/-._~')})"

    def add_image(self, image: QImage) -> str:
        if image.isNull():
            raise ValueError("クリップボードの画像を読み取れません。")
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise OSError("画像をPNGに変換できません。")
        return self._markdown(self._create(bytes(buffer.data()), "png"))

    def import_image_file(self, path: Path) -> str:
        path = Path(path)
        reader = QImageReader(str(path))
        reader.setDecideFormatFromContent(True)
        if not reader.canRead():
            raise ValueError(f"対応する画像ファイルではありません: {path.name}")
        image_format = bytes(reader.format()).decode("ascii").lower()
        extension = {"jpeg": "jpg", "tiff": "tif"}.get(image_format, image_format)
        if not re.fullmatch(r"[a-z0-9]+", extension):
            raise ValueError("画像形式を判定できません。")
        return self._markdown(self._create(path.read_bytes(), extension))

    def copy_managed(self, source: Path) -> Path:
        """Copy bytes unchanged, choosing a fresh filename under this manager's stem."""
        return self._create(source.read_bytes(), source.suffix.lstrip("."))
