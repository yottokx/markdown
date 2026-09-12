"""Markdown rendering with source-line anchors for content-based scrolling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import nh3
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from mdit_py_plugins.tasklists import tasklists_plugin

from .code_highlighting import highlight_code_lines
from .image_sources import parse_srcset
from .math_parser import math_plugin


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """A safe HTML fragment and the number of QPlainTextEdit source blocks."""

    html: str
    line_count: int


_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "caption",
    "code",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "input",
    "kbd",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "picture",
    "source",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
    "var",
}
_ATTRIBUTES = {
    "*": {"class", "title"},
    "a": {"href"},
    "img": {"src", "srcset", "sizes", "alt", "width", "height"},
    "source": {"srcset", "sizes", "media", "type"},
    "input": {"type", "checked", "disabled"},
    "ol": {"start", "reversed"},
    "li": {"value"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align"},
    "details": {"open"},
}
_SOURCE_ATTRIBUTES = {"data-source-line", "data-source-end"}


def _safe_image_url(value: str) -> bool:
    compact = re.sub(r"[\x00-\x20]", "", value)
    try:
        scheme = urlsplit(compact).scheme.lower()
    except ValueError:
        return False
    if scheme == "data":
        return bool(
            re.match(
                r"data:image/(?:png|jpeg|gif|webp|avif|bmp|tiff|svg\+xml|x-icon)[;,]",
                compact,
                re.IGNORECASE,
            )
        )
    return scheme in {"", "file", "http", "https"}


def _image_attribute(tag: str, attribute: str, value: str) -> str | None:
    if attribute == "srcset":
        return (
            ", ".join(
                url + (" " + descriptor if descriptor else "")
                for url, descriptor in parse_srcset(value)
                if _safe_image_url(url)
            )
            or None
        )
    if attribute == "src" and tag == "img":
        return value if _safe_image_url(value) else None
    if attribute == "href" and re.sub(r"[\x00-\x20]", "", value).lower().startswith("data:"):
        return None
    return value


def _sanitize(fragment: str, *, source_attributes: bool = False) -> str:
    # Only generated markup may retain private scroll/render attributes. Raw
    # HTML is cleaned first and cannot impersonate anchors or render nodes.
    attributes = {tag: set(names) for tag, names in _ATTRIBUTES.items()}
    if source_attributes:
        attributes["*"].update(_SOURCE_ATTRIBUTES | {"data-render-kind"})
        attributes["code"] = {"data-code-source"}
    return nh3.clean(
        fragment,
        tags=_TAGS,
        attributes=attributes,
        clean_content_tags={"script", "style", "iframe", "object", "template"},
        url_schemes={"http", "https", "mailto", "file", "data"},
        attribute_filter=_image_attribute,
        strip_comments=True,
        link_rel="noopener noreferrer",
    )


def _anchor(start: int, end: int) -> str:
    return f'data-source-line="{start}" data-source-end="{end}"'


class _SourceRenderer(RendererHTML):
    def __init__(self, parser: Any = None) -> None:
        self._protected_inline: list[dict[str, str]] = []
        super().__init__(parser)

    def renderInline(self, tokens: list[Token], options: Any, env: dict[str, Any]) -> str:
        # Clean paired inline HTML together. Generated math travels through the
        # sanitizer as unpredictable plain-text markers, so raw HTML can never
        # acquire its private rendering attributes or impersonate a source map.
        protected: dict[str, str] = {}
        self._protected_inline.append(protected)
        try:
            cleaned = _sanitize(super().renderInline(tokens, options, env))
        finally:
            self._protected_inline.pop()
        for marker, fragment in protected.items():
            cleaned = cleaned.replace(marker, fragment)
        return cleaned

    def renderInlineAsText(self, tokens, options, env) -> str:
        # Math in an image alt attribute is useful plain TeX, never a render node.
        parts = []
        for token in tokens or []:
            if token.type in {"math_inline", "math_inline_display"}:
                closing = {"\\(": "\\)", "\\[": "\\]"}.get(token.markup, token.markup)
                parts.append(token.markup + token.content + closing)
            else:
                parts.append(super().renderInlineAsText([token], options, env))
        return "".join(parts)

    def _inline_math(self, token: Token, *, display: bool) -> str:
        kind = "math-block" if display else "math-inline"
        fragment = f'<span class="{kind}" data-render-kind="{kind}">{escape(token.content)}</span>'
        marker = "\ue000md-editor-math-" + uuid4().hex + "\ue001"
        self._protected_inline[-1][marker] = fragment
        return marker

    def math_inline(self, tokens, idx, options, env) -> str:
        return self._inline_math(tokens[idx], display=False)

    def math_inline_display(self, tokens, idx, options, env) -> str:
        return self._inline_math(tokens[idx], display=True)

    def math_block(self, tokens, idx, options, env) -> str:
        token = tokens[idx]
        start, end = token.map or (0, 1)
        return (
            f'<div class="math-block" data-render-kind="math-block" {_anchor(start, end)}>'
            + escape(token.content)
            + "</div>\n"
        )

    def paragraph_open(
        self, tokens: list[Token], idx: int, options: Any, env: dict[str, Any]
    ) -> str:
        token = tokens[idx]
        if token.hidden:
            return '<span class="source-paragraph"' + self.renderAttrs(token) + ">"
        return self.renderToken(tokens, idx, options, env)

    def paragraph_close(
        self, tokens: list[Token], idx: int, options: Any, env: dict[str, Any]
    ) -> str:
        if tokens[idx].hidden:
            return "</span>"
        return self.renderToken(tokens, idx, options, env)

    def html_block(self, tokens: list[Token], idx: int, options: Any, env: dict[str, Any]) -> str:
        token = tokens[idx]
        cleaned = _sanitize(token.content)
        if not cleaned.strip():
            return ""
        start, end = token.map or (0, 1)
        return f'<div class="source-html" {_anchor(start, end)}>{cleaned}</div>\n'

    def fence(self, tokens: list[Token], idx: int, options: Any, env: dict[str, Any]) -> str:
        token = tokens[idx]
        language = token.info.strip().split(maxsplit=1)[0] if token.info.strip() else ""
        if language.casefold() == "mermaid":
            start, end = token.map or (0, 1)
            return (
                f'<div class="mermaid-block" data-render-kind="mermaid" {_anchor(start, end)}>'
                + escape(token.content)
                + "</div>\n"
            )
        return self._code(token, fenced=True)

    def code_block(self, tokens: list[Token], idx: int, options: Any, env: dict[str, Any]) -> str:
        return self._code(tokens[idx], fenced=False)

    @staticmethod
    def _code(token: Token, *, fenced: bool) -> str:
        start, end = token.map or (0, 1)
        highlighted = highlight_code_lines(token.content, token.info)
        lines = list(highlighted.lines)
        if lines and lines[-1] == "":
            lines.pop()
        language = highlighted.language
        language_class = f' class="language-{escape(language, quote=True)}"' if language else ""
        source_attribute = f' data-code-source="{escape(token.content, quote=True)}"'
        parts = [f"<pre><code{language_class}{source_attribute}>"]
        if fenced:
            parts.append(f'<span class="code-boundary" {_anchor(start, start + 1)}></span>')
        content_start = start + int(fenced)
        for offset, line in enumerate(lines):
            line_number = content_start + offset
            # A BR gives empty code rows real height without inserting spaces
            # or zero-width characters into copied code.
            content = line if line else "<br>"
            parts.append(
                f'<span class="code-line" {_anchor(line_number, line_number + 1)}>{content}</span>'
            )
        content_end = content_start + len(lines)
        if fenced and content_end < end:
            parts.append(f'<span class="code-boundary" {_anchor(end - 1, end)}></span>')
        parts.append("</code></pre>\n")
        return "".join(parts)


def render_markdown(source: str) -> RenderedDocument:
    """Render CommonMark, tables, tasks, and safe TeX/Mermaid placeholders.

    Anchor ranges are zero-based, with an exclusive end. Containers such as
    lists, quotes and tables intentionally have no duplicate parent anchors.
    Ordinary fences and code rows have separate anchors; math and Mermaid
    blocks each span their complete source range. Non-rendering lines are
    interpolated by the preview using surrounding anchors and EOF.
    """
    parser = MarkdownIt("commonmark", {"html": True}, renderer_cls=_SourceRenderer)
    validate_link = parser.validateLink
    # Managed images restored from another Windows drive temporarily require
    # absolute file URIs. Page navigation remains restricted by PreviewPage.
    parser.validateLink = lambda url: urlsplit(url).scheme.lower() == "file" or validate_link(url)
    parser.enable(["table", "strikethrough"])
    parser.use(tasklists_plugin, enabled=False)
    parser.use(math_plugin)
    env: dict[str, Any] = {}
    tokens = parser.parse(source, env)
    for token in tokens:
        if (
            token.type in {"paragraph_open", "heading_open", "tr_open", "hr"}
            and token.map is not None
        ):
            token.attrSet("data-source-line", str(token.map[0]))
            token.attrSet("data-source-end", str(token.map[1]))
        # Markdown-it expresses column alignment as inline style. Keep only
        # this narrowly defined presentation hint through the HTML sanitizer.
        if token.type in {"th_open", "td_open"}:
            style = token.attrGet("style") or ""
            if style in {"text-align:left", "text-align:center", "text-align:right"}:
                token.attrSet("align", style.removeprefix("text-align:"))
    html = parser.renderer.render(tokens, parser.options, env)
    return RenderedDocument(
        html=_sanitize(html, source_attributes=True),
        line_count=len(source.split("\n")),
    )
