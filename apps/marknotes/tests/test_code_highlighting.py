from html.parser import HTMLParser
from pathlib import Path

import pytest
import tinycss2
from pygments.token import Keyword

from marknotes import code_highlighting
from marknotes.code_highlighting import highlight_code_lines
from marknotes.rendering import render_markdown


class Fragment(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.tags = []
        self.feed(html)

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _text(html):
    return "".join(Fragment(html).text)


def _roundtrip(result):
    return "\n".join(_text(row) for row in result.lines)


@pytest.fixture(autouse=True)
def clear_highlight_cache():
    code_highlighting._highlight_cached.cache_clear()


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("python", ""),
        ("python", "\n\n"),
        ("python", '\tprint("日本語 😀 <&>\\\\")\n\n'),
        ("python", '\ufeff\tprint("<x>")\r\n\r\n'),
        ("python", 'value = """first\n\tsecond <tag>\nthird"""\n'),
        ("javascript", '/* first\nsecond */\nconst x = "<&>";\n'),
        ("typescript", "interface Person { name: string; }\n"),
        ("json", '{"items": [1, true, null], "name": "<&>"}'),
        ("html", '<script>if (a < b) { alert("x"); }</script>\n'),
        ("xml", '<node name="value">text &amp;</node>\n'),
        ("css", "/* first\nsecond */\na { color: red; }\n"),
        ("bash", '#!/bin/bash\nprintf "%s\\n" "$HOME"\n'),
        ("powershell", 'Write-Host "Hello $name"\n'),
        ("sql", "SELECT name FROM users WHERE id = 1;\n"),
        ("cpp", "#include <iostream>\nint main() { return 0; }\n"),
        ("rust", 'fn main() { println!("hello"); }\n'),
        ("go", 'package main\nfunc main() { println("hello") }\n'),
        ("java", "public class Main { public static void main(String[] args) {} }\n"),
        ("csharp", "using System;\nConsole.WriteLine(42);\n"),
    ],
)
def test_lexing_retains_every_character_and_row(language, source):
    result = highlight_code_lines(source, language)
    assert _roundtrip(result) == source
    assert len(result.lines) == len(source.split("\n"))
    for row in result.lines:
        for tag, attrs in Fragment(row).tags:
            assert tag == "span"
            assert set(attrs) == {"class"}
            assert attrs["class"].startswith("tok-")


def test_multiline_string_and_comment_retain_lexer_state_between_rows():
    python = highlight_code_lines('value = """first\n\tsecond\nthird"""\nreturn value', "python")
    assert python.lines[1] == '<span class="tok-string">\tsecond</span>'
    assert 'class="tok-string"' in python.lines[2]
    assert 'class="tok-keyword">return</span>' in python.lines[3]
    javascript = highlight_code_lines("/* first\nsecond */\nconst x = 1;", "javascript")
    assert javascript.lines[1] == '<span class="tok-comment">second */</span>'
    assert 'class="tok-keyword">const</span>' in javascript.lines[2]


def test_explicit_language_wins_and_unlabelled_code_is_conservatively_detected(monkeypatch):
    source = "def greet(name):\n    return name\n"
    guessed = highlight_code_lines(source)
    assert guessed.language == "python"
    assert "tok-keyword" in guessed.lines[0]
    prose = highlight_code_lines("Please select a book from the shelf.")
    assert prose.language == ""
    assert prose.lines == ("Please select a book from the shelf.",)

    def unexpected_detection(_text):
        pytest.fail("Explicit labels must not run automatic detection")

    monkeypatch.setattr(code_highlighting, "detect_code_language", unexpected_detection)
    explicit = highlight_code_lines(source, "javascript")
    assert explicit.language == "javascript"
    assert _roundtrip(explicit) == source


@pytest.mark.parametrize("language", ["text", "plaintext", "plain", "txt", "Text", "unknown-lang"])
def test_plain_or_unknown_labels_never_guess_a_different_language(language, monkeypatch):
    def unexpected_detection(_text):
        pytest.fail("Explicit labels must not run automatic detection")

    monkeypatch.setattr(code_highlighting, "detect_code_language", unexpected_detection)
    result = highlight_code_lines('<script>alert("hi")</script>\n', language)
    assert result.language == language
    assert all("<span" not in line for line in result.lines)
    assert _roundtrip(result) == '<script>alert("hi")</script>\n'
    assert not Fragment(result.lines[0]).tags


@pytest.mark.parametrize(
    "source",
    ["x" * (code_highlighting.MAX_HIGHLIGHT_CHARS + 1), "x\n" * 2_000],
    ids=["character-limit", "line-limit"],
)
def test_large_blocks_skip_detection_lexing_and_cache(source, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Oversized blocks must skip detection and lexing")

    monkeypatch.setattr(code_highlighting, "detect_code_language", unexpected)
    monkeypatch.setattr(code_highlighting, "get_lexer_by_name", unexpected)
    result = highlight_code_lines(source)
    assert _roundtrip(result) == source
    assert code_highlighting._highlight_cached.cache_info().currsize == 0


def test_repeated_blocks_reuse_a_bounded_cache():
    source = "def test():\n    return 1\n"
    first = highlight_code_lines(source, "python")
    assert highlight_code_lines(source, "python") is first
    assert highlight_code_lines(source, "text") is not first
    for index in range(40):
        highlight_code_lines(f"word {index}", "text")
    assert code_highlighting._highlight_cached.cache_info().maxsize == 32
    assert code_highlighting._highlight_cached.cache_info().currsize == 32


@pytest.mark.parametrize(
    "tokens",
    [
        [(1, Keyword, "ab")],  # Gap.
        [(0, Keyword, "ac")],  # Replaced source characters.
        [(0, Keyword, "a")],  # Incomplete source coverage.
        [(0, Keyword, "a"), (0, Keyword, "b")],  # Reordered offsets.
    ],
)
def test_lexer_cannot_change_the_source(tokens, monkeypatch):
    class BrokenLexer:
        def get_tokens_unprocessed(self, _text):
            return iter(tokens)

    monkeypatch.setattr(code_highlighting, "get_lexer_by_name", lambda *a, **kw: BrokenLexer())
    assert highlight_code_lines("ab", "python").lines == ("ab",)


def test_lexer_failure_and_cooperative_budget_fall_back_to_safe_plain_text(monkeypatch):
    class BrokenLexer:
        def get_tokens_unprocessed(self, _text):
            raise ValueError("incomplete input")

    monkeypatch.setattr(code_highlighting, "get_lexer_by_name", lambda *a, **kw: BrokenLexer())
    assert highlight_code_lines("<&>", "python").lines == ("&lt;&amp;&gt;",)
    code_highlighting._highlight_cached.cache_clear()

    class SlowLexer:
        def get_tokens_unprocessed(self, text):
            yield 0, Keyword, text

    clock = iter([0.0, 1.0])
    monkeypatch.setattr(code_highlighting, "get_lexer_by_name", lambda *a, **kw: SlowLexer())
    monkeypatch.setattr(code_highlighting, "monotonic", lambda: next(clock))
    assert highlight_code_lines("<&>", "python").lines == ("&lt;&amp;&gt;",)


def test_renderer_retains_source_anchors_and_exact_copy_payload():
    source = 'text = """first\n\tsecond <tag>\nthird"""\n\nprint(text)\n'
    rendered = render_markdown("before\n\n```python\n" + source + "```\n\nafter")
    fragment = Fragment(rendered.html)
    code = next(attrs for tag, attrs in fragment.tags if tag == "code")
    assert code["class"] == "language-python"
    assert code["data-code-source"] == source
    anchors = [
        (attrs.get("class"), int(attrs["data-source-line"]), int(attrs["data-source-end"]))
        for tag, attrs in fragment.tags
        if tag == "span" and "data-source-line" in attrs
    ]
    assert anchors == [
        ("code-boundary", 2, 3),
        ("code-line", 3, 4),
        ("code-line", 4, 5),
        ("code-line", 5, 6),
        ("code-line", 6, 7),
        ("code-line", 7, 8),
        ("code-boundary", 8, 9),
    ]
    assert 'class="tok-string"' in rendered.html
    assert 'data-source-line="6" data-source-end="7"><br>' in rendered.html
    assert not any(tag == "tag" for tag, _ in fragment.tags)


def test_copy_metadata_is_generated_only_and_mermaid_keeps_its_renderer():
    raw = render_markdown('<pre><code data-code-source="forged">hello</code></pre>')
    assert all("data-code-source" not in attrs for _, attrs in Fragment(raw.html).tags)
    mermaid = render_markdown("```mermaid\ngraph TD; A-->B;\n```")
    assert 'data-render-kind="mermaid"' in mermaid.html
    assert "tok-" not in mermaid.html
    assert "data-code-source" not in mermaid.html


def test_highlight_styles_only_change_colors_in_light_and_dark():
    css = Path(code_highlighting.__file__).parent / "resources" / "code-highlight.css"
    rules = tinycss2.parse_stylesheet(css.read_text(encoding="utf-8"), skip_comments=True)
    selectors = []
    for rule in rules:
        if rule.type == "whitespace":
            continue
        assert rule.type == "qualified-rule"
        selectors.append(tinycss2.serialize(rule.prelude))
        declarations = tinycss2.parse_declaration_list(rule.content, skip_whitespace=True)
        assert all(item.type == "declaration" and item.name == "color" for item in declarations)
    assert any('data-theme="dark"' in selector for selector in selectors)
    assert any(selector.strip() == "pre code .tok-keyword" for selector in selectors)
