"""Conservative, bounded language hints for a newly inserted Markdown fence.

Only a short prefix is inspected; no code, subprocess or network is executed.
Ambiguous fragments intentionally keep an unlabelled fence. Pygments validates
our small candidate set rather than guessing among hundreds of obscure lexers.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from pygments.lexers import get_lexer_by_name
from pygments.token import Error, Keyword, Name
from pygments.util import ClassNotFound, shebang_matches

MAX_ANALYSIS_CHARS = 16_384
MAX_LEX_CHARS = 4_096

# Signals are declarations or distinctive language constructs, not a grammar.
# A shared C/Java/JS-style statement or lone assignment is insufficient.
_HINTS = {
    "python": (
        (7, r"^\s*(?:async\s+)?def\s+\w+\s*\([^\n]*\)\s*(?:->[^\n:]+)?\s*:"),
        (5, r"^\s*class\s+\w+\s*(?:\([^\n]*\))?\s*:"),
        (
            5,
            r"^\s*(?:from\s+[.\w]+\s+import\s+(?:\*|\w+(?:\s+as\s+\w+)?(?:\s*,\s*\w+(?:\s+as\s+\w+)?)*)|import\s+[\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)\s*(?:#.*)?$",
        ),
        (
            5,
            r"^\s*(?:for\s+\w+\s+in\s+[^\n]+|(?:if|elif|while|with|except)\s+[^\n]+)\s*:\s*(?:#.*)?$",
        ),
        (4, r"^\s*(?:print|len|range)\s*\([^\n]*\)\s*(?:#.*)?$"),
    ),
    "javascript": (
        (7, r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\w*\s*\([^\n]*\)\s*\{"),
        (4, r"^\s*(?:export\s+)?const\s+[$\w]+\s*=\s*\S"),
        (6, r"(?:^|[=(:,])\s*(?:async\s+)?(?:\([^\n()]*\)|[$\w]+)\s*=>"),
        (
            6,
            r"\b(?:console\.(?:log|warn|error)|document\.(?:querySelector|getElementById)|JSON\.(?:parse|stringify))\s*\(",
        ),
        (5, r"^\s*(?:import\s+[^\n]+\s+from\s+['\"]|(?:module\.)?exports\s*[.=])"),
    ),
    "typescript": (
        (9, r"^\s*(?:export\s+)?(?:interface|enum)\s+\w+[^\n{]*\{"),
        (9, r"^\s*(?:export\s+)?type\s+\w+(?:<[^\n>]+>)?\s*="),
        (9, r"^\s*(?:export\s+)?(?:const|let|var)\s+[$\w]+\s*:\s*[^\n=]+="),
        (8, r"^\s*(?:export\s+)?(?:async\s+)?function\s+\w+\s*\([^\n]*:\s*\w+[^\n]*\)"),
    ),
    "html": (
        (9, r"(?i)^\s*<!doctype\s+html\b"),
        (
            7,
            r"(?i)^\s*<(?:html|head|body|div|span|p|a|table|ul|ol|li|h[1-6]|script|style|section|button|form|img|input|br)\b[^>]*>",
        ),
    ),
    "xml": (
        (10, r"^\s*<\?xml\s"),
        (4, r"\A\s*<[A-Za-z_][\w:.-]*(?:\s+[^<>]*)?\s*/>\s*\Z"),
        (4, r"\A\s*<([A-Za-z_][\w:.-]*)\b[^>]*>[\s\S]*</\1\s*>\s*\Z"),
    ),
    "css": (
        (
            6,
            r"^\s*(?:[#.][\w-]+|:root|(?:html|body|div|p|a|span|h[1-6]|button|input|section)\b)[^{}\n]{0,120}\{\s*[\w-]+\s*:",
        ),
        (7, r"^\s*@(?:media|supports|font-face|keyframes|layer)\b[^\n;]*\{"),
    ),
    "bash": (
        (6, r"^\s*(?:for\s+\w+\s+in\s+[^\n]+;\s*do|if\s+\[.+\]\s*;\s*then)\b"),
        (5, r"^\s*export\s+[A-Za-z_]\w*="),
        (4, r"^\s*(?:echo|printf)\s+[^\n]*\$(?:[A-Za-z_]|\{|\()"),
        (4, r"^\s*(?:set\s+-[a-zA-Z]*[eu]|(?:sudo\s+)?(?:grep|sed|awk|curl|chmod|find)\s+-\S+)"),
    ),
    "powershell": (
        (
            7,
            r"(?i)(?:^|\|)\s*(?:Get|Set|New|Remove|Write|Read|Test|Select|Where|ForEach|Invoke|Import|Export|ConvertTo|ConvertFrom|Start|Stop|Join|Split)-[A-Za-z]+\b",
        ),
        (7, r"(?i)^\s*param\s*\(\s*\[\w+\]\s*\$\w+"),
        (5, r"(?i)\$(?:PSVersionTable|PSScriptRoot|PSCommandPath|ErrorActionPreference)\b"),
    ),
    "sql": (
        (7, r"(?is)^\s*SELECT\b.{1,800}?\bFROM\s+[\w.\[\]\"`]+"),
        (
            7,
            r"(?i)^\s*(?:INSERT\s+INTO|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)\s+[\w.\[\]\"`]+",
        ),
        (7, r"(?is)^\s*UPDATE\s+[\w.\[\]\"`]+\s+SET\s+.{1,500}?="),
    ),
    "c": (
        (4, r"^\s*#\s*include\s*[<\"](?:stdio|stdlib|string|stdint|stdbool|stddef|math)\.h[>\"]"),
    ),
    "cpp": (
        (9, r"^\s*#\s*include\s*<(?:iostream|vector|string|memory|algorithm|map|set|utility)>"),
        (8, r"\bstd::\w+"),
        (8, r"^\s*(?:template\s*<|using\s+namespace\s+\w+\s*;)"),
    ),
    "csharp": (
        (8, r"^\s*(?:global\s+)?using\s+System(?:\.[\w.]+)?\s*;"),
        (7, r"\bConsole\.(?:WriteLine|ReadLine|Write|ReadKey)\s*\("),
        (7, r"^\s*namespace\s+[\w.]+\s*;"),
        (6, r"\b(?:public|private|protected)\s+(?:static\s+)?(?:string|bool|Task<[^>]+>)\s+\w+"),
    ),
    "java": (
        (8, r"^\s*import\s+(?:java|javax|jakarta)\.[\w.*]+\s*;"),
        (8, r"\bSystem\.(?:out|err)\.print(?:ln)?\s*\("),
        (7, r"\b(?:public\s+)?static\s+void\s+main\s*\(\s*String\s*(?:\[\]|\.\.\.)"),
        (6, r"^\s*package\s+[\w.]+\s*;"),
    ),
    "rust": (
        (8, r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+\w+\s*\("),
        (6, r"\b(?:println|eprintln|format|vec)!\s*[(\[]"),
        (6, r"^\s*let\s+mut\s+\w+\s*(?:[:=])"),
        (6, r"^\s*(?:use\s+(?:std|crate|super)::|impl(?:<[^>]*>)?\s+\w+)"),
    ),
    "go": (
        (7, r"^\s*package\s+[A-Za-z_]\w*\s*$"),
        (3, r"^\s*func\s+(?:\([^\n]*\)\s*)?\w+\s*\("),
        (4, r"\bfmt\.(?:Println|Printf|Sprintf|Print)\s*\("),
        (3, r"^\s*import\s+(?:\(|\"[^\n\"]+\")"),
        (2, r"\b\w+\s*:=\s*\S"),
    ),
}

# Inline flags belong before MULTILINE. Compile once rather than per paste.
_SIGNALS = {
    alias: tuple((weight, re.compile(pattern, re.MULTILINE)) for weight, pattern in patterns)
    for alias, patterns in _HINTS.items()
}
_SHEBANGS = (
    ("python", r"(?:python|pypy)(?:\d+(?:\.\d+)*)?"),
    ("javascript", r"(?:node|nodejs)"),
    ("typescript", r"(?:ts-node|tsx|deno)"),
    ("bash", r"(?:bash|sh|zsh|ksh|dash)"),
    ("powershell", r"(?:pwsh|powershell)"),
)
_SEMANTIC_TOKENS = (Keyword, Name.Builtin, Name.Function, Name.Class, Name.Tag, Name.Namespace)
_KNOWN_API_NAMES = {
    "javascript": {"console", "document", "JSON", "module", "exports"},
    "csharp": {"Console"},
    "java": {"System"},
    "go": {"fmt"},
}


@lru_cache(maxsize=24)
def _lexer(alias: str):
    return get_lexer_by_name(alias)


def _reject_json_constant(value):
    raise ValueError(value)


def _plausible_tokens(alias: str, text: str) -> bool:
    """Reject a guessed family if its lexer only sees prose or broken tokens."""
    lexer = _lexer(alias)
    semantic = False
    errors = 0
    occupied = 0
    for token, value in lexer.get_tokens(text[:MAX_LEX_CHARS]):
        length = len(value.strip())
        occupied += length
        if token in Error:
            errors += length
        if any(token in category for category in _SEMANTIC_TOKENS) or (
            token in Name and value in _KNOWN_API_NAMES.get(alias, ())
        ):
            semantic = semantic or bool(length)
    return (semantic or lexer.analyse_text(text[:MAX_LEX_CHARS]) >= 0.4) and errors <= max(
        2, occupied // 20
    )


def detect_code_language(text: str) -> str:
    """Return a canonical Pygments fence alias, or ``''`` when evidence is weak.

    Analysis is limited to the first 16,384 characters, and lexing to 4,096.
    JSON validation uses the complete input only when it fits that limit.
    Code shared by several languages (e.g. ``x = 1`` or a bare class) stays
    unlabelled; common C-compatible sources may use ``c`` and JS-compatible
    sources use ``javascript`` unless more specific features are present.
    """
    sample = text[:MAX_ANALYSIS_CHARS].lstrip("\ufeff").strip()
    if not sample or "\x00" in sample or re.match(r"(?:`{3,}|~{3,})", sample):
        return ""
    for alias, pattern in _SHEBANGS:
        if shebang_matches(sample, pattern):
            return alias
    if len(text) <= MAX_ANALYSIS_CHARS and sample.startswith(("{", "[")):
        try:
            value = json.loads(sample, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError):
            pass
        else:
            if isinstance(value, (dict, list)):
                return "json"
    # A markup document keeps its outer language even when script/style text
    # contributes stronger-looking signals for an embedded language.
    for alias in ("html", "xml"):
        if any(pattern.match(sample) for _, pattern in _SIGNALS[alias]):
            return alias if _plausible_tokens(alias, sample) else ""
    scores = {
        alias: sum(weight for weight, pattern in patterns if pattern.search(sample))
        for alias, patterns in _SIGNALS.items()
    }
    # Lower-case imperatives such as "select a book from the shelf" are prose,
    # even though a permissive SQL lexer recognizes their words as keywords.
    if scores["sql"] and not (
        re.search(r"\b(?:SELECT|FROM|INSERT|UPDATE|CREATE|DELETE|ALTER|DROP)\b", sample)
        or re.search(r"[;=*]|\b\w\s*,\s*\w", sample)
    ):
        scores["sql"] = 0
    ranked = sorted(((score, alias) for alias, score in scores.items() if score >= 4), reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 2):
        return ""
    _, alias = ranked[0]
    try:
        return _lexer(alias).aliases[0] if _plausible_tokens(alias, sample) else ""
    except ClassNotFound:
        return ""
