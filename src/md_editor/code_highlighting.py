"""Small, lossless Pygments fragments inside the preview's source-line spans."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import escape
from time import monotonic

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Generic, Keyword, Literal, Name, Operator
from pygments.util import ClassNotFound

from .code_language import detect_code_language

MAX_HIGHLIGHT_CHARS = 32_768
MAX_HIGHLIGHT_LINES = 2_000
MAX_HIGHLIGHT_TOKENS = 16_384
# This is a cooperative budget between lexer yields, not a hard regex timeout.
HIGHLIGHT_SECONDS = 0.10
_PLAIN_LANGUAGES = {"text", "plaintext", "plain", "txt", "none", "text/plain"}


@dataclass(frozen=True, slots=True)
class HighlightedCode:
    """Escaped HTML for each LF-delimited row, including the final empty row.

    ``language`` preserves an explicit fence label, or is the detected alias.
    Joining the rows' text with LF always reproduces the original input.
    """

    language: str
    lines: tuple[str, ...]


def _plain(text: str, language: str) -> HighlightedCode:
    return HighlightedCode(language, tuple(escape(line) for line in text.split("\n")))


@lru_cache(maxsize=128)
def _token_class(token_type) -> str:
    for category, name in (
        (Comment, "comment"),
        (Keyword.Type, "type"),
        (Keyword, "keyword"),
        (Literal.String, "string"),
        (Literal.Number, "number"),
        (Name.Builtin, "builtin"),
        (Name.Function, "function"),
        (Name.Class, "type"),
        (Name.Namespace, "type"),
        (Name.Exception, "type"),
        (Name.Tag, "tag"),
        (Name.Attribute, "attribute"),
        (Name.Decorator, "decorator"),
        (Operator, "operator"),
        (Generic.Inserted, "inserted"),
        (Generic.Deleted, "deleted"),
    ):
        if token_type in category:
            return "tok-" + name
    return ""


@lru_cache(maxsize=32)
def _highlight_cached(text: str, language: str) -> HighlightedCode:
    requested = language
    language = language or detect_code_language(text)
    if not language or language.casefold() in _PLAIN_LANGUAGES:
        return _plain(text, language)
    try:
        lexer = get_lexer_by_name(
            language.casefold(), stripnl=False, stripall=False, ensurenl=False, tabsize=0
        )
    except ClassNotFound:
        # An explicit, unsupported language must never be guessed as another one.
        return _plain(text, requested)
    deadline = monotonic() + HIGHLIGHT_SECONDS
    lines: list[str] = []
    row: list[str] = []
    cursor = 0
    try:
        # Avoid get_tokens(): its preprocessing normalizes CRLF, removes a BOM,
        # and can append a newline. One lexer pass also preserves multiline state.
        for count, (offset, token_type, value) in enumerate(lexer.get_tokens_unprocessed(text)):
            if (
                count >= MAX_HIGHLIGHT_TOKENS
                or monotonic() > deadline
                or offset != cursor
                or text[offset : offset + len(value)] != value
            ):
                return _plain(text, language)
            cursor += len(value)
            css_class = _token_class(token_type)
            for index, piece in enumerate(value.split("\n")):
                if index:
                    lines.append("".join(row))
                    row = []
                if piece:
                    fragment = escape(piece)
                    row.append(
                        f'<span class="{css_class}">{fragment}</span>' if css_class else fragment
                    )
    except Exception:  # noqa: BLE001 - third-party lexer boundary; preserve a usable preview.
        # Incomplete or unusual input must not turn a third-party lexer failure
        # into a failed Markdown preview. Escaped text is always a valid result.
        return _plain(text, language)
    if cursor != len(text):
        return _plain(text, language)
    lines.append("".join(row))
    return HighlightedCode(language, tuple(lines))


def highlight_code_lines(text: str, language: str = "") -> HighlightedCode:
    """Highlight a complete code block without changing any source characters.

    Explicit labels take priority; plain-text and unknown labels stay uncolored.
    Unlabelled code uses the conservative, offline language detector. Large blocks
    skip lexing and the cache entirely. The time budget is checked between tokens;
    the input cap limits work within a single regex call as well.
    """
    language = language.strip().split(maxsplit=1)[0] if language.strip() else ""
    if (
        len(text) > MAX_HIGHLIGHT_CHARS
        or text.count("\n") >= MAX_HIGHLIGHT_LINES
        or len(language) > 128
    ):
        return _plain(text, language)
    return _highlight_cached(text, language)
