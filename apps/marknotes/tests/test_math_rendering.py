from html.parser import HTMLParser

import pytest
from bs4 import BeautifulSoup

from marknotes.rendering import render_markdown


class RenderNodes(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.tags = []
        self._active = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags.append((tag, values))
        if "data-render-kind" in values:
            node = {"tag": tag, "attrs": values, "text": ""}
            self.nodes.append(node)
            self._active.append((tag, node))

    def handle_endtag(self, tag):
        if self._active and self._active[-1][0] == tag:
            self._active.pop()

    def handle_data(self, data):
        for _, node in self._active:
            node["text"] += data


@pytest.mark.parametrize(
    ("source", "content", "kind", "tag"),
    [
        ("before $x^2 + y_1$ after", "x^2 + y_1", "math-inline", "span"),
        (r"before \(\frac{a}{b}\) after", r"\frac{a}{b}", "math-inline", "span"),
        ("before $$x+y$$ after", "x+y", "math-block", "span"),
        (r"before \[x+y\] after", "x+y", "math-block", "span"),
        ("$$x+y$$", "x+y", "math-block", "div"),
        (r"\[x+y\]", "x+y", "math-block", "div"),
        ("$$\nx &= y \\\\\n  z &= 2\n$$", "\nx &= y \\\\\n  z &= 2\n", "math-block", "div"),
        ("\\[\nx^2\n\\]", "\nx^2\n", "math-block", "div"),
        ("$$a+b\n=c$$", "a+b\n=c", "math-block", "div"),
        ("$2$", "2", "math-inline", "span"),
        ("本文 $ E = mc^2 $。", " E = mc^2 ", "math-inline", "span"),
        ("$ x$", " x", "math-inline", "span"),
        ("$x $", "x ", "math-inline", "span"),
        ("$\tx_i\t$", "\tx_i\t", "math-inline", "span"),
        ("$\u00a0x_i\u00a0$", "\u00a0x_i\u00a0", "math-inline", "span"),
    ],
)
def test_tex_delimiters_create_safe_typed_nodes_without_changing_source_line_count(
    source, content, kind, tag
):
    rendered = render_markdown(source)
    parsed = RenderNodes(rendered.html)
    assert len(parsed.nodes) == 1
    node = parsed.nodes[0]
    assert node["tag"] == tag
    assert node["attrs"]["data-render-kind"] == kind
    assert node["attrs"]["class"] == kind
    assert node["text"] == content
    assert rendered.line_count == len(source.split("\n"))
    assert "\ue000marknotes-math" not in rendered.html


@pytest.mark.parametrize(
    "source",
    [
        "$5 to $10",
        "$5-$10",
        "$5, $10 and $20.",
        "$1,000 – $2,000",
        "It costs $5.00 today and $7.00 tomorrow.",
        r"\$x\$",
        r"\\(x\\)",
        r"\\[x\\]",
        "$ $",
        "$\t\u00a0$",
        "2$x$",
        "$x$2",
        "$$$x$$$",
        "$x$$",
        "$unclosed",
        "$$\nunclosed",
        r"\(unclosed",
        "\\[\nunclosed",
    ],
)
def test_currency_escapes_ambiguous_boundaries_and_incomplete_math_stay_plain(source):
    assert RenderNodes(render_markdown(source).html).nodes == []


def test_escaped_dollar_within_tex_is_preserved_and_even_backslashes_allow_delimiter():
    result = RenderNodes(render_markdown(r"$\text{price: \$5}$ and \\$x$").html)
    assert [node["text"] for node in result.nodes] == [r"\text{price: \$5}", "x"]


@pytest.mark.parametrize(
    "source",
    [
        r"`$x$ \(y\) $$z$$ \[w\]`",
        "```\n$x$\n\\(y\\)\n$$z$$\n```",
        "```math\n$$\nx\n$$\n```",
        "```tex\n\\[x\\]\n```",
        "    $$\n    x\n    $$",
    ],
)
def test_code_examples_do_not_become_math(source):
    rendered = render_markdown(source)
    assert RenderNodes(rendered.html).nodes == []
    assert "<code" in rendered.html


def test_display_math_in_quotes_and_lists_has_exact_whole_block_anchors():
    source = "> $$\n> x^2\n> $$\n\n- $$\n  y\n  $$\n\nafter"
    rendered = render_markdown(source)
    parsed = RenderNodes(rendered.html)
    assert [node["text"] for node in parsed.nodes] == ["\nx^2\n", "\ny\n"]
    assert [
        (node["attrs"]["data-source-line"], node["attrs"]["data-source-end"])
        for node in parsed.nodes
    ] == [("0", "3"), ("4", "7")]
    assert "<blockquote>" in rendered.html
    assert "<li>" in rendered.html
    assert rendered.line_count == 9


def test_display_math_interrupts_paragraphs_and_preserves_following_source_anchors():
    source = "before\n$$\nx\n$$\nafter\n\n"
    rendered = render_markdown(source)
    parsed = RenderNodes(rendered.html)
    anchors = [
        (tag, attrs["data-source-line"], attrs["data-source-end"])
        for tag, attrs in parsed.tags
        if "data-source-line" in attrs
    ]
    assert anchors == [("p", "0", "1"), ("div", "1", "4"), ("p", "4", "5")]
    assert rendered.line_count == 7


def test_inline_tex_inside_links_tables_and_image_alt_is_not_html_markup():
    rendered = render_markdown(
        "[equation $x$](https://example.com) ![$y$](img/a.png)\n\n"
        "| A | B |\n| --- | --- |\n| $z$ | \\(w\\) |"
    )
    parsed = RenderNodes(rendered.html)
    assert [node["text"] for node in parsed.nodes] == ["x", "z", "w"]
    images = [attrs for tag, attrs in parsed.tags if tag == "img"]
    assert images[0]["alt"] == "$y$"


@pytest.mark.parametrize("fence", ["```mermaid", "~~~mermaid", "```Mermaid"])
def test_mermaid_fence_is_one_escaped_node_and_one_whole_block_anchor(fence):
    content = 'flowchart LR\n A["<img src=x onerror=alert(1)>"] --> B\n'
    source = "before\n\n" + fence + "\n" + content + fence[:3] + "\n\nafter"
    rendered = render_markdown(source)
    parsed = RenderNodes(rendered.html)
    assert len(parsed.nodes) == 1
    node = parsed.nodes[0]
    assert node["tag"] == "div"
    assert node["attrs"] == {
        "class": "mermaid-block",
        "data-render-kind": "mermaid",
        "data-source-line": "2",
        "data-source-end": "6",
        "data-selection-id": node["attrs"]["data-selection-id"],
    }
    assert any(
        entry["id"] == node["attrs"]["data-selection-id"] for entry in rendered.selection_map
    )
    assert node["text"] == content
    assert not any(tag == "img" for tag, _ in parsed.tags)
    assert "&lt;img" in rendered.html
    assert rendered.line_count == len(source.split("\n"))


def test_unclosed_mermaid_keeps_its_actual_eof_and_plain_fences_remain_literal():
    source = "```mermaid\nflowchart LR\n A --> B"
    node = RenderNodes(render_markdown(source).html).nodes[0]
    assert node["attrs"]["data-source-end"] == "3"
    assert node["text"] == "flowchart LR\n A --> B"
    rendered = render_markdown("```text\n```mermaid\n$$x$$\n````")
    assert RenderNodes(rendered.html).nodes == []
    assert "language-text" in rendered.html


def test_tex_html_is_escaped_as_text_and_dangerous_commands_are_not_interpreted_in_python():
    formula = r'\href{javascript:alert(1)}{<img src=x onerror="bad"> & x}'
    source = "$" + formula + "$\n\n$$\n" + formula + "\n$$"
    parsed = RenderNodes(render_markdown(source).html)
    assert [node["text"] for node in parsed.nodes] == [formula, "\n" + formula + "\n"]
    assert not any(tag in {"img", "script", "iframe"} for tag, _ in parsed.tags)
    assert not any(name.startswith("on") for _, attrs in parsed.tags for name in attrs)


def test_raw_html_cannot_impersonate_math_mermaid_or_scroll_nodes():
    source = (
        '<div class="mermaid-block" data-render-kind="mermaid" data-source-line="999">'
        "unsafe raw graph</div>\n\n"
        'A <span class="math-inline" DATA-RENDER-KIND="math-inline" data-source-end="999">'
        "unsafe raw math</span> and $actual$\n\n"
        '<span data-render-kind="math-block" onclick="bad()">raw</span>'
    )
    rendered = render_markdown(source)
    parsed = RenderNodes(rendered.html)
    assert [node["text"] for node in parsed.nodes] == ["actual"]
    assert "999" not in rendered.html
    assert "onclick" not in rendered.html
    assert "unsafe raw graph" in rendered.html
    assert "unsafe raw math" in rendered.html


def test_sanitizing_paired_inline_html_keeps_nesting_around_generated_math():
    rendered = render_markdown('Text <em data-render-kind="mermaid">safe $x$</em> end')
    emphasis = BeautifulSoup(rendered.html, "html.parser").em
    assert emphasis.get_text() == "safe x"
    assert "data-render-kind" not in emphasis.attrs
    assert emphasis.select_one('.math-inline[data-render-kind="math-inline"]').get_text() == "x"
    assert len(RenderNodes(rendered.html).nodes) == 1
