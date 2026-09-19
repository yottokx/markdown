"""Rendered selection spans refer to original syntax positions, including repeats."""

from html.parser import HTMLParser

import pytest

from marknotes.rendering import render_markdown
from marknotes.search import qt_position


class SelectionElements(HTMLParser):
    def __init__(self, fragment):
        super().__init__(convert_charrefs=True)
        self.elements = {}
        self.stack = []
        self.feed(fragment)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        identifier = attrs.get("data-selection-id")
        if identifier:
            assert identifier not in self.elements
            self.elements[identifier] = {"tag": tag, "text": "", "attrs": attrs}
        if tag not in {"img", "br", "hr", "input", "source"}:
            self.stack.append((tag, identifier))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        for _tag, identifier in self.stack:
            if identifier:
                self.elements[identifier]["text"] += data


def source_slice(source, start, end):
    return source.encode("utf-16-le")[start * 2 : end * 2].decode("utf-16-le")


def entries(source):
    rendered = render_markdown(source)
    elements = SelectionElements(rendered.html).elements
    assert set(elements) == {entry["id"] for entry in rendered.selection_map}
    return rendered, [(elements[entry["id"]], entry) for entry in rendered.selection_map]


def text_sources(source, values):
    result = []
    for element, entry in values:
        if entry.get("atomic"):
            continue
        for dom_start, dom_end, start, end in entry["segments"]:
            result.append(
                (
                    source_slice(element["text"], dom_start, dom_end),
                    source_slice(source, start, end),
                )
            )
    return result


def test_repeated_words_use_distinct_original_offsets_and_utf16():
    source = "😀 same **same** [same](https://same.test) *same*"
    _rendered, values = entries(source)
    word_entries = [(element, entry) for element, entry in values if element["text"] == "same"]
    assert [entry["segments"] for _element, entry in word_entries] == [
        [[0, 4, qt_position(source, start), qt_position(source, start + 4)]]
        for start in (
            source.index("**same") + 2,
            source.index("[same") + 1,
            source.index("*same*", source.index("https")) + 1,
        )
    ]
    assert all(display == original for display, original in text_sources(source, values))
    assert not any("https" in original for _display, original in text_sources(source, values))


def test_escape_and_entities_map_transformed_characters_as_exact_source_spans():
    source = "😀 \\* &amp; &NotEqualTilde; done"
    _rendered, values = entries(source)
    chunks = text_sources(source, values)
    assert ("&", "&amp;") in chunks
    assert ("≂̸", "&NotEqualTilde;") in chunks
    assert "".join(display for display, _original in chunks) == "😀 * & ≂̸ done"
    assert not any("\\" in original for _display, original in chunks)


@pytest.mark.parametrize(
    "source",
    [
        "> - [x] same **same**\n>   continued",
        "- parent\n  - same same\n\n    continued",
        "| same | same\\|cell |\n| --- | --- |\n| cell | [same](target) |",
        "## same **same**\n\nsame\n====",
    ],
)
def test_container_and_table_parsing_retains_actual_source_characters(source):
    _rendered, values = entries(source)
    chunks = text_sources(source, values)
    assert chunks
    assert all(display == original for display, original in chunks)
    joined = "".join(display for display, _original in chunks)
    assert "same" in joined
    assert "[x]" not in joined
    assert "target" not in joined
    assert "---" not in joined


def test_inline_code_maps_trimmed_padding_and_newline_without_delimiters():
    source = "before ` a\nb ` after"
    _rendered, values = entries(source)
    code = next((element, entry) for element, entry in values if element["tag"] == "code")
    assert code[0]["text"] == "a b"
    assert code[1]["segments"] == [[0, 3, source.index("a\nb"), source.index("a\nb") + 3]]


def test_fenced_and_indented_code_rows_preserve_source_positions():
    source = '> ```python\n> value = "😀"\n> \n> same\n> ```\n\n    same'
    _rendered, values = entries(source)
    code = [
        (element, entry)
        for element, entry in values
        if "code-line" in element["attrs"].get("class", "")
    ]
    assert [element["text"] for element, _entry in code] == ['value = "😀"', "same", "same"]
    for element, entry in code:
        assert len(entry["segments"]) == 1
        _lo, _hi, start, end = entry["segments"][0]
        assert source_slice(source, start, end) == element["text"]
    assert code[1][1]["segments"][0][2] != code[2][1]["segments"][0][2]


def test_html_maps_visible_text_and_entities_but_rejects_forged_ids_and_removed_content():
    source = '<div data-selection-id="s999">same &amp; <b>same</b><script>hidden</script><img alt="photo" src="asset.png"></div>'
    rendered, values = entries(source)
    assert "s999" not in rendered.html
    assert "hidden" not in rendered.html
    chunks = text_sources(source, values)
    assert ("&", "&amp;") in chunks
    assert "same & same" == "".join(display for display, _original in chunks)
    images = [(element, entry) for element, entry in values if element["tag"] == "img"]
    assert len(images) == 1
    assert images[0][1]["atomic"]
    assert source_slice(source, images[0][1]["start"], images[0][1]["end"]) == "photo"


def test_math_mermaid_and_images_are_atomic_without_outer_syntax():
    source = "$x^2$\n\n$$\ny + 1\n$$\n\n```mermaid\ngraph LR\nA-->B\n```\n\n![photo](asset.png) ![](empty.png)"
    _rendered, values = entries(source)
    originals = [
        source_slice(source, entry["start"], entry["end"])
        for _element, entry in values
        if entry.get("atomic")
    ]
    assert originals == ["x^2", "\ny + 1\n", "graph LR\nA-->B\n", "photo", "empty.png"]


def test_autolink_does_not_consume_preceding_pending_text():
    source = "before <https://example.com> after"
    _rendered, values = entries(source)
    assert text_sources(source, values) == [
        ("before ", "before "),
        ("https://example.com", "https://example.com"),
        (" after", " after"),
    ]


@pytest.mark.parametrize(
    "source",
    ["***unclosed and `literal", "same ~~same~~ same", "same [**same** and same](url) same"],
)
def test_unmatched_delimiters_and_nested_labels_keep_individual_positions(source):
    _rendered, values = entries(source)
    chunks = text_sources(source, values)
    assert chunks
    assert all(display == original for display, original in chunks)
    starts = [segment[2] for _element, entry in values for segment in entry.get("segments", [])]
    assert starts == sorted(starts)


def test_reference_links_exclude_definitions_and_empty_alt_images_map_the_reference():
    source = "same [same][ref] ![][pic]\n\n[ref]: https://example.com\n[pic]: asset.png"
    _rendered, values = entries(source)
    chunks = text_sources(source, values)
    assert all(display == original for display, original in chunks)
    assert not any("https" in original or "asset" in original for _display, original in chunks)
    image = next(entry for element, entry in values if element["tag"] == "img")
    assert source_slice(source, image["start"], image["end"]) == "pic"


def test_raw_html_entity_without_semicolon_uses_only_its_source_characters():
    source = "<div>&copy next &#169 next</div>"
    _rendered, values = entries(source)
    assert text_sources(source, values) == [
        ("©", "&copy"),
        (" next ", " next "),
        ("©", "&#169"),
        (" next", " next"),
    ]


def test_source_normalization_preserves_utf16_offsets_after_crlf_and_null():
    source = "first\r\n\r\n😀\0next"
    _rendered, values = entries(source)
    final = next(entry for element, entry in values if element["text"] == "😀�next")
    assert final["segments"] == [[0, 7, 9, 16]]
