"""Conservative clipboard classification and explicit HTML conversion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from markdownify import markdownify
from PySide6.QtCore import QMimeData

from md_editor.math_parser import math_plugin

IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".ico"}
)


@dataclass(frozen=True, slots=True)
class PasteDecision:
    kind: str
    text: str = ""
    files: list[Path] = field(default_factory=list)
    reason: str = ""


def _fragment(html: str) -> BeautifulSoup:
    """Strip Windows CF_HTML framing and non-content elements without executing HTML."""
    start = html.find("<!--StartFragment-->")
    end = html.find("<!--EndFragment-->")
    if start >= 0 and end > start:
        html = html[start + len("<!--StartFragment-->") : end]
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "head", "iframe", "object", "template"]):
        element.decompose()
    return soup


def html_to_markdown(html: str) -> str:
    """Convert an explicitly chosen HTML fragment using Markdownify."""
    return markdownify(
        str(_fragment(html)), heading_style="ATX", bullets="-", newline_style="BACKSLASH"
    ).strip("\r\n")


def _contains_math_source(text: str) -> bool:
    """Use the renderer's delimiter rules, including its currency exclusions."""
    if "$" not in text and r"\(" not in text and r"\[" not in text:
        return False
    parser = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    tokens = parser.use(math_plugin).parse(text)
    while tokens:
        token = tokens.pop()
        if token.type in {"math_inline", "math_inline_display", "math_block"}:
            return True
        if token.children:
            tokens.extend(token.children)
    return False


def looks_like_markdown_or_code(text: str) -> bool:
    """Prefer preserving source bytes when the plain payload has source syntax.

    This is intentionally a preference, not language identification. Ambiguous
    unformatted text also remains plain; the UI offers explicit HTML conversion.
    """
    if re.search(r"(?m)^ {0,3}(?:#{1,6}\s|`{3,}|~{3,}|>\s|[-+*]\s|\d+[.)]\s)", text):
        return True
    if re.search(r"!?\[[^\]\n]+\]\([^\n]*\)|`[^`\n]+`|\*\*[^\n]+\*\*", text):
        return True
    if re.search(r"(?m)^\s*\|?\s*:?-{3,}:?\s*\|", text):
        return True
    # TeX source must reach the preview intact: Markdownify otherwise escapes
    # underscores/backslashes in rich HTML representations of copied formulas.
    if _contains_math_source(text):
        return True
    # Common Python/JS/shell/SQL/C-family source and JSON/config payloads.
    code_pattern = re.compile(
        r"^\s*(?:async\s+def\b|def\s+\w+\s*\(|class\s+\w+|"
        r"from\s+\S+\s+import\b|import\s+[\w.{*]|(?:const|let|var)\s+\w+|"
        r"function\b|(?:if|for|while|with|try|except)\b[^\n]*[:{]\s*$|"
        r"(?:SELECT|INSERT|UPDATE|CREATE)\s|(?:[\w.]+\s*\([^\n]*\)\s*;?$)|"
        r"(?:[\w.$]+\s*(?:=|:=|\+=|-=)\s*\S)|[{}\[\]]\s*$|"
        r"(?:#include|#!)|(?:\$\s|PS>\s)|(?:uv|pip|npm|git|python)\s)"
    )
    lines = [line for line in text.splitlines() if line.strip()]
    code_lines = [bool(code_pattern.search(line)) for line in lines]
    if code_lines and (code_lines[0] or sum(code_lines) > len(code_lines) / 2):
        return True
    return bool(re.search(r"(?m)^ {4,}\S.*\n {4,}\S", text))


def choose_paste(mime: QMimeData, in_code: bool = False) -> PasteDecision:
    """Choose a paste representation without mutating the clipboard or filesystem."""
    plain = mime.text() if mime.hasText() else ""
    if in_code and mime.hasText():
        return PasteDecision("text", plain, reason="コード領域ではプレーンテキストを使用")
    if mime.hasUrls():
        urls = mime.urls()
        files = [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]
        if (
            files
            and len(files) == len(urls)
            and all(path.suffix.lower() in IMAGE_SUFFIXES for path in files)
        ):
            return PasteDecision("files", files=files, reason="画像ファイル")
    if plain and looks_like_markdown_or_code(plain):
        return PasteDecision("markdown", plain, reason="Markdownまたはソースコードを保持")
    soup = _fragment(mime.html()) if mime.hasHtml() else None
    if soup is not None:
        content_text = soup.get_text("", strip=True)
        code_elements = soup.find_all(["pre", "code"])
        # Chat copy buttons often wrap the entire payload in PRE or CODE.
        # A code example embedded in an article does not make the article code.
        if (
            plain
            and code_elements
            and any(element.get_text("", strip=True) == content_text for element in code_elements)
        ):
            return PasteDecision("text", plain, reason="コードコピーのHTMLラッパーを無視")
        if mime.hasImage() and not content_text:
            return PasteDecision("image", reason="画像のみのクリップボード")
        semantic = soup.find(
            [
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "strong",
                "b",
                "em",
                "i",
                "a",
                "ul",
                "ol",
                "blockquote",
                "table",
                "img",
                "del",
                "s",
                "pre",
            ]
        )
        if semantic is not None or len(soup.find_all("p")) > 1 or not mime.hasText():
            return PasteDecision(
                "html", html_to_markdown(mime.html()), reason="HTMLをMarkdownに変換"
            )
    if mime.hasText():
        return PasteDecision("text", plain, reason="判定が曖昧な場合はプレーンテキストを保持")
    if mime.hasImage():
        return PasteDecision("image", reason="クリップボード画像")
    return PasteDecision("text", reason="対応する内容がありません")


def as_code_block(text: str, language: str = "") -> str:
    """Fence literal clipboard text without letting its own backticks close it."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    longest = max((len(match[0]) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    ending = "" if text.endswith("\n") else "\n"
    return f"{fence}{language}\n{text}{ending}{fence}"


def as_quote(text: str) -> str:
    """Quote every line, including blanks, while preserving existing Markdown."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join("> " + line if line else ">" for line in text.split("\n"))


def existing_fence_paste(
    source: str, start: int, end: int, text: str
) -> tuple[int, int, str, int] | None:
    """Replace inside a top-level fence, preserving its language and delimiters.

    Positions use Python character offsets. Return the replacement range,
    replacement text and caret offset within it. Selecting the outer fence or
    an indented-code container instead calls for a newly fenced block.
    """
    lines = source.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    parser = MarkdownIt("commonmark").use(math_plugin)
    for token in parser.parse(source):
        if token.type != "fence" or token.level != 0 or token.map is None:
            continue
        first, stop = token.map
        opening = re.fullmatch(r"( {0,3})(`{3,}|~{3,})(.*)", lines[first])
        if opening is None or first + 1 >= len(lines):
            continue
        marker = token.markup[0]
        closing = (
            re.fullmatch(
                r"( {0,3})(" + re.escape(marker) + "{" + str(len(token.markup)) + r",})([ \t]*)",
                lines[stop - 1],
            )
            if stop > first + 1
            else None
        )
        body_start = offsets[first + 1]
        body_end = offsets[stop - 1] if closing else min(len(source), offsets[stop])
        if not body_start <= start <= end <= body_end:
            continue
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        body = source[body_start:start] + text + source[end:body_end]
        if closing and body and not body.endswith("\n"):
            body += "\n"
        longest = max((len(m[0]) for m in re.finditer(re.escape(marker) + "+", body)), default=0)
        length = max(len(token.markup), longest + 1)
        header = opening[1] + marker * length + opening[3] + "\n"
        caret = len(header) + start - body_start + len(text)
        if closing:
            footer = closing[1] + marker * max(length, len(closing[2])) + closing[3]
            finish = offsets[stop - 1] + len(lines[stop - 1])
        else:
            footer, finish = "", body_end
        return offsets[first], finish, header + body + footer, caret
    return None
