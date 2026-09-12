from html import escape
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage

from marknotes.clipboard import as_code_block, as_quote, choose_paste, html_to_markdown


def payload(plain=None, html=None, image=False):
    mime = QMimeData()
    if plain is not None:
        mime.setText(plain)
    if html is not None:
        mime.setHtml(html)
    if image:
        mime.setImageData(QImage(2, 2, QImage.Format.Format_ARGB32))
    return mime


@pytest.mark.parametrize(
    "plain",
    [
        "# 見出し\n\n**bold** _italic_",
        "def some_func(value):\n    return value * 2",
        "const variable_name = '*literal*';",
        "```python\nsome_name = 3\n```",
        r"![画像](img/some_name.png)",
        '{\n  "name_with_underscore": "value"\n}',
        "foo_bar = r'C:\\Users\\test'",
    ],
)
def test_source_plain_is_preserved_even_with_rich_html(plain):
    decision = choose_paste(payload(plain, "<div><strong>formatted fallback</strong></div>"))
    assert decision.kind == "markdown"
    assert decision.text == plain


def test_code_copy_wrapper_does_not_escape_source():
    plain = "some_name * other_name"
    decision = choose_paste(
        payload(plain, "<div><pre><code>some_name * other_name</code></pre></div>")
    )
    assert decision.kind == "text"
    assert decision.text == plain


def test_code_context_always_prefers_plain():
    decision = choose_paste(payload("literal_name", "<b>literal_name</b>"), in_code=True)
    assert decision.kind == "text"
    assert decision.text == "literal_name"


def test_article_with_code_and_bitmap_is_converted_as_article():
    mime = payload("Introduction\ncall()", "<h2>Introduction</h2><pre>call()</pre>", image=True)
    decision = choose_paste(mime)
    assert decision.kind == "html"
    assert "## Introduction" in decision.text


def test_rich_article_becomes_markdown():
    decision = choose_paste(
        payload("Heading\nHello world", "<h1>Heading</h1><p>Hello <strong>world</strong></p>")
    )
    assert decision.kind == "html"
    assert decision.text == "# Heading\n\nHello **world**"


def test_plain_decorative_span_is_unchanged():
    decision = choose_paste(
        payload("some_name * literal", '<span style="color:red">some_name * literal</span>')
    )
    assert decision.kind == "text"
    assert decision.text == "some_name * literal"


def test_image_only_payload_uses_bitmap():
    assert (
        choose_paste(payload(html='<img src="https://example.test/a.png">', image=True)).kind
        == "image"
    )
    assert choose_paste(payload(image=True)).kind == "image"


def test_image_file_url_payload_and_non_image_payload(tmp_path):
    mime = QMimeData()
    image = tmp_path / "日本語 image.png"
    mime.setUrls([QUrl.fromLocalFile(str(image))])
    decision = choose_paste(mime)
    assert decision.kind == "files"
    assert decision.files == [Path(image)]
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "file.md"))])
    assert choose_paste(mime).kind != "files"


def test_explicit_html_conversion_removes_scripts_and_cf_html_metadata():
    html = "Version:0.9\nSourceURL:https://example.test\n<!--StartFragment--><p><b>内容</b></p><script>bad()</script><!--EndFragment-->"
    assert html_to_markdown(html) == "**内容**"


def test_html_only_rich_text_and_empty_payload():
    assert choose_paste(payload(html="<p>hello</p>")).text == "hello"
    assert choose_paste(payload()).text == ""


@pytest.mark.parametrize(
    "plain",
    [
        "式 $x_i$ です。",
        "式 $ x_i $ です。",
        r"式 \(x_i + \frac{a}{b}\) です。",
        "前の本文\n\n$$\nx_i^2 + y_i^2 = 1\n$$\n\n後の本文",
        "前の本文\n\n\\[\nx_i^2 + y_i^2 = 1\n\\]\n\n後の本文",
        "式 $$x_i$$ の説明です。",
    ],
)
def test_math_source_is_preserved_when_clipboard_also_contains_rich_html(plain):
    decision = choose_paste(payload(plain, f"<p><strong>{escape(plain)}</strong></p>"))
    assert decision.kind == "markdown"
    assert decision.text == plain


@pytest.mark.parametrize(
    "plain",
    [
        "Prices range from $5 to $10.",
        "価格は$5-$10です。",
        "Budget is $5 today.",
        "式 $x_i はまだ閉じていません。",
        r"Literal \$x_i\$ is escaped.",
    ],
)
def test_currency_and_non_math_delimiters_do_not_disable_html_conversion(plain):
    decision = choose_paste(payload(plain, f"<p><strong>{escape(plain)}</strong></p>"))
    assert decision.kind == "html"
    assert decision.text.startswith("**") and decision.text.endswith("**")


@pytest.mark.parametrize(
    "text",
    [
        "def example(value_name):\n    return value_name * 2",
        "\tleading tab\r\n\r\n  spaces and trailing blanks  \r\n\r\n",
        "```python\nprint('nested fence')\n```",
        "````\n```\n````\n",
        "😀 日本語と <script>literal()</script> & `_name_`",
        "line one\rline two",
    ],
)
def test_code_block_round_trips_literal_clipboard_contents(text):
    snippet = as_code_block(text, "python")
    tokens = MarkdownIt("commonmark").parse(snippet)
    assert len(tokens) == 1 and tokens[0].type == "fence"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    assert tokens[0].content == normalized + ("" if normalized.endswith("\n") else "\n")
    assert tokens[0].info == "python"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("First\n\nSecond", "> First\n>\n> Second"),
        ("  indent\r\n> nested\rnext", ">   indent\n> > nested\n> next"),
        ("**bold** _name_\n", "> **bold** _name_\n>"),
    ],
)
def test_quote_keeps_blank_lines_indentation_and_existing_markdown(text, expected):
    snippet = as_quote(text)
    assert snippet == expected
    tokens = MarkdownIt("commonmark").parse(snippet)
    assert tokens[0].type == "blockquote_open" and tokens[-1].type == "blockquote_close"
