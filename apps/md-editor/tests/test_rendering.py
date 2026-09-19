from html.parser import HTMLParser

import pytest
from bs4 import BeautifulSoup

from md_editor.rendering import render_markdown


class Elements(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    @property
    def anchors(self):
        return [
            (tag, int(attrs["data-source-line"]), int(attrs["data-source-end"]))
            for tag, attrs in self.tags
            if "data-source-line" in attrs
        ]


@pytest.mark.parametrize("source", ["", "text", "text\n", "\n\n", "a\nb\n\n"])
def test_line_count_includes_last_empty_editor_block(source):
    assert render_markdown(source).line_count == len(source.split("\n"))


def test_leaf_anchors_cover_paragraphs_and_nested_lists_without_parent_duplicates():
    rendered = render_markdown("# Heading\n\nFirst\nsecond\n\n- item\n  - nested\n\n> quote\n")
    elements = Elements(rendered.html)
    assert elements.anchors == [
        ("h1", 0, 1),
        ("p", 2, 4),
        ("span", 5, 6),
        ("span", 6, 7),
        ("p", 8, 9),
    ]
    assert not any(tag in {"ul", "li", "blockquote"} for tag, _, _ in elements.anchors)


def test_tables_have_row_anchors_and_preserve_column_alignment():
    rendered = render_markdown("| A | B |\n| :--- | ---: |\n| one | two |\n| three | four |")
    elements = Elements(rendered.html)
    assert elements.anchors == [("tr", 0, 1), ("tr", 2, 3), ("tr", 3, 4)]
    assert ("th", {"align": "left"}) in elements.tags
    assert ("th", {"align": "right"}) in elements.tags


def test_fenced_code_maps_blank_lines_and_both_fences():
    rendered = render_markdown('before\n\n```python\nx = "<tag>"\n\nprint(x)\n```\nafter')
    elements = Elements(rendered.html)
    assert elements.anchors == [
        ("p", 0, 1),
        ("span", 2, 3),
        ("span", 3, 4),
        ("span", 4, 5),
        ("span", 5, 6),
        ("span", 6, 7),
        ("p", 7, 8),
    ]
    code_rows = [attrs for tag, attrs in elements.tags if attrs.get("class") == "code-line"]
    assert len(code_rows) == 3
    assert "&lt;tag&gt;" in rendered.html
    assert "<br>" in rendered.html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("```\nx", [("span", 0, 1), ("span", 1, 2)]),
        ("```\n", [("span", 0, 1)]),
        ("```\n```", [("span", 0, 1), ("span", 1, 2)]),
        ("    one\n    two\n", [("span", 0, 1), ("span", 1, 2)]),
    ],
)
def test_code_has_no_phantom_source_rows(source, expected):
    assert Elements(render_markdown(source).html).anchors == expected


def test_images_and_relative_links_remain_usable():
    elements = Elements(render_markdown("![画像](img/sample.png)\n\n[other](other.md)").html)
    assert any(
        tag == "img" and attrs.get("src") == "img/sample.png" for tag, attrs in elements.tags
    )
    assert any(tag == "a" and attrs.get("href") == "other.md" for tag, attrs in elements.tags)


def test_raw_html_is_sanitized_and_cannot_inject_scroll_anchors():
    source = (
        '<div data-source-line="999" onclick="alert(1)">\n'
        '<script>alert(1)</script><img src="img/a.png" onerror="alert(1)">\n'
        '<a href="javascript:alert(1)">bad</a></div>\n\n'
        'A <em data-source-line="999" onmouseover="alert(1)">safe</em> paragraph'
    )
    rendered = render_markdown(source)
    elements = Elements(rendered.html)
    assert elements.anchors == [("div", 0, 3), ("p", 4, 5)]
    assert "alert(1)" not in rendered.html
    assert any(tag == "em" for tag, _ in elements.tags)
    for tag, attrs in elements.tags:
        assert tag not in {"script", "iframe", "object", "style"}
        assert not any(name.startswith("on") for name in attrs)
        assert not (attrs.get("href") or "").startswith("javascript:")


def test_tasklists_and_strikethrough_are_rendered():
    rendered = render_markdown("- [x] done\n- [ ] ~~pending~~")
    elements = Elements(rendered.html)
    inputs = [attrs for tag, attrs in elements.tags if tag == "input"]
    assert len(inputs) == 2
    assert all("disabled" not in attrs for attrs in inputs)
    assert "checked" in inputs[0]
    assert "checked" not in inputs[1]
    assert BeautifulSoup(rendered.html, "html.parser").s.get_text() == "pending"


def test_render_is_fragment_and_never_rewrites_source_text():
    source = "# 日本語 😀\n\n---\n\n[end]: other.md\n\n"
    rendered = render_markdown(source)
    assert rendered.line_count == 7
    assert "<html" not in rendered.html
    assert "<body" not in rendered.html
    assert Elements(rendered.html).anchors == [("h1", 0, 1), ("hr", 2, 3)]


def test_cross_drive_image_uri_survives_rendering_without_allowing_script_schemes():
    result = render_markdown(
        "![local](file:///C:/image%20folder/photo.png)\n\n"
        '<img src="file:///D:/image.png" onerror="alert(1)">\n'
        '<img src="javascript:alert(1)">'
    )
    assert 'src="file:///C:/image%20folder/photo.png"' in result.html
    assert 'src="file:///D:/image.png"' in result.html
    assert "onerror" not in result.html
    assert "javascript:" not in result.html
