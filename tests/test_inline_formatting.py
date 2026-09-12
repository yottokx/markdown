"""Formatting changes rendered styles without losing source text or link targets."""

import pytest
from bs4 import BeautifulSoup, NavigableString
from markdown_it import MarkdownIt

from md_editor.inline_formatting import inline_actions
from md_editor.math_parser import math_plugin


def action(source, selected, key, *, occurrence=0):
    start = -1
    for _ in range(occurrence + 1):
        start = source.index(selected, start + 1)
    choices = {item.key: item for item in inline_actions(source, start, start + len(selected))}
    assert key in choices, choices
    return choices[key]


def changed(source, selected, key):
    edit = action(source, selected, key).edit
    assert edit is not None, (source, selected, key)
    assert 0 <= edit.selection_start < edit.selection_end <= len(edit.text)
    return source[: edit.start] + edit.text + source[edit.end :]


def characters(source):
    md = (
        MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"]).use(math_plugin)
    )
    soup = BeautifulSoup(md.render(source), "html.parser")
    result = []
    for node in soup.descendants:
        if not isinstance(node, NavigableString):
            continue
        parents = [parent.name for parent in node.parents]
        if node == "\n" and not any(name in parents for name in ("p", "code", "td", "th")):
            continue
        styles = frozenset(name for name in parents if name in {"strong", "em", "s", "code"})
        hrefs = tuple(parent.get("href") for parent in node.parents if parent.name == "a")
        result.extend((char, styles if not char.isspace() else frozenset(), hrefs) for char in node)
    return result


@pytest.mark.parametrize(
    "kind,marker,tag", [("bold", "**", "strong"), ("italic", "*", "em"), ("strike", "~~", "s")]
)
def test_basic_apply_remove_and_selected_markers(kind, marker, tag):
    source = "before 日本語😀 after"
    result = changed(source, "日本語😀", kind + ".apply")
    assert result == f"before {marker}日本語😀{marker} after"
    edit = action(result, "日本語😀", kind + ".remove").edit
    assert edit and edit.text[edit.selection_start : edit.selection_end] == "日本語😀"
    assert changed(result, "日本語😀", kind + ".remove") == source
    assert changed(result, marker + "日本語😀" + marker, kind + ".remove") == source


@pytest.mark.parametrize(
    "source,kind,expected",
    [
        ("**abcdef**", "bold", "**ab**cd**ef**"),
        ("__abcdef__", "bold", "**ab**cd**ef**"),
        ("*abcdef*", "italic", "*ab*cd*ef*"),
        ("_abcdef_", "italic", "*ab*cd*ef*"),
        ("~~abcdef~~", "strike", "~~ab~~cd~~ef~~"),
    ],
)
def test_partial_removal_keeps_unselected_characters_formatted(source, kind, expected):
    assert changed(source, "cd", kind + ".remove") == expected


@pytest.mark.parametrize(
    "source,kind,selected",
    [
        ("***abcdef***", "bold", "cd"),
        ("***abcdef***", "italic", "cd"),
        ("**ab*cd*ef**", "bold", "d"),
        ("**a *b* c**", "bold", "b"),
        ("~~**abcdef**~~", "bold", "cd"),
        ("***abcdef***", "bold", "abcdef"),
    ],
)
def test_nested_removal_preserves_every_other_style(source, kind, selected):
    tag = {"bold": "strong", "italic": "em"}[kind]
    original = characters(source)
    result = changed(source, selected, kind + ".remove")
    rendered = "".join(char for char, _, _ in original)
    start = rendered.index(selected)
    expected = [
        (char, styles - {tag} if start <= i < start + len(selected) else styles, links)
        for i, (char, styles, links) in enumerate(original)
    ]
    assert characters(result) == expected


@pytest.mark.parametrize("kind,marker", [("bold", "**"), ("italic", "*"), ("strike", "~~")])
def test_mixed_selection_offers_unify_and_remove(kind, marker):
    source = f"plain {marker}styled{marker} tail"
    choices = {item.key: item for item in inline_actions(source, 0, len(source))}
    assert choices[kind + ".apply"].label.endswith("に統一")
    assert not choices[kind + ".apply"].checked
    assert choices[kind + ".remove"].edit is not None
    assert changed(source, source, kind + ".apply") == marker + "plain styled tail" + marker
    assert changed(source, source, kind + ".remove") == "plain styled tail"


@pytest.mark.parametrize(
    "source",
    [
        "# selected",
        "- selected",
        "> selected",
        "> - selected",
        "| selected | other |\n|---|---|",
        "A | B\n---|---\nselected | other",
    ],
)
def test_single_heading_list_quote_or_table_cell_preserves_container(source):
    assert changed(source, "selected", "bold.apply") == source.replace("selected", "**selected**")


def test_same_text_in_other_table_cells_does_not_confuse_offsets():
    source = "| same | same |\n|---|---|\n| same | same |"
    edit = action(source, "same", "bold.apply", occurrence=3).edit
    assert edit
    assert (
        source[: edit.start] + edit.text + source[edit.end :]
        == source[: source.rfind("same")] + "**same** |"
    )


def test_table_escaped_pipe_survives_block_and_inline_unescaping():
    source = "| before a\\|b after | other |\n|---|---|"
    result = changed(source, "a\\|b", "bold.apply")
    assert result == source.replace("a\\|b", "**a\\|b**")
    assert "".join(char for char, _, _ in characters(result)) == "".join(
        char for char, _, _ in characters(source)
    )


def test_link_labels_remain_links_with_exact_destination_and_title():
    source = '[a **b**](dest_(x) "title *notem*")'
    result = changed(source, "a **b**", "bold.apply")
    assert result == '[**a b**](dest_(x) "title *notem*")'
    assert action(source, "dest", "bold.apply").edit is None
    assert action(source, "title", "bold.apply").edit is None
    assert action(source, "b**](dest", "bold.remove").edit is None


@pytest.mark.parametrize(
    "source,selected",
    [
        ("before `**code**` after", "code"),
        ("before `code\n**not bold**` after", "not bold"),
        ("before $x_i$ after", "x_i"),
        (r"before \(x_i\) after", "x_i"),
        ("![alt](image.png)", "alt"),
        ("![alt](image.png)", "image"),
        ("<https://example.test>", "example"),
        ("first\n\nsecond", "first\n\nsecond"),
        ("|a|b|\n|-|-|", "a|b"),
        ("```python\ncode\n```", "code"),
        ("    code", "code"),
    ],
)
def test_protected_or_multiple_block_ranges_are_disabled(source, selected):
    start = source.index(selected)
    assert all(
        item.edit is None
        for item in inline_actions(source, start, start + len(selected))
        if item.key != "inline_code.remove"
    )


@pytest.mark.parametrize("source", [r"\*literal\*", "a &amp; b", "word 😀 日本語"])
def test_apply_preserves_escapes_entities_and_unicode(source):
    result = changed(source, source, "bold.apply")
    original = characters(source)
    assert characters(result) == [
        (char, frozenset({"strong"}) if not char.isspace() else frozenset(), links)
        for char, _, links in original
    ]


@pytest.mark.parametrize("text", ["value", "a`b", "`value`", " value ", "  value", "value  "])
def test_inline_code_uses_safe_delimiters_and_padding(text):
    # Existing parsed spans offer removal instead; unmatched literal ticks are safe inputs.
    if text == "`value`":
        assert changed(text, "alu", "inline_code.remove") == "value"
        return
    source = "before " + text + " after"
    result = changed(source, text, "inline_code.apply")
    soup = BeautifulSoup(MarkdownIt().render(result), "html.parser")
    assert soup.code.get_text() == text


def test_inline_code_removal_is_whole_span_and_escapes_literal_markdown():
    source = "before `` **literal** `backtick` `` after"
    result = changed(source, "literal", "inline_code.remove")
    assert "<code>" not in MarkdownIt().render(result)
    assert "<strong>" not in MarkdownIt().render(result)
    assert (
        "**literal** `backtick`"
        in BeautifulSoup(MarkdownIt().render(result), "html.parser").get_text()
    )


def test_delimiter_only_or_partial_entity_selection_cannot_damage_surroundings():
    for source, selected in [("**word**", "**"), ("a &amp; b", "amp"), (r"\*word", "*")]:
        start = source.index(selected)
        assert all(
            item.edit is None for item in inline_actions(source, start, start + len(selected))
        )


def test_multiline_quote_continuation_keeps_prefixes():
    source = "> first line\n> second line"
    result = changed(source, "first line\n> second", "bold.apply")
    assert result == "> **first line\n> second** line"


def test_cancelled_or_empty_selection_has_no_edit():
    assert all(item.edit is None for item in inline_actions("text", 2, 2))
    assert all(item.edit is None for item in inline_actions("text", -1, 2))


@pytest.mark.parametrize(
    "body",
    [
        "print(x)",
        "name.value",
        "&amp;",
        "&copy;",
        "# heading",
        "1. item",
        "---",
        "[label](url)",
        "$x_i$",
    ],
)
def test_code_removal_preserves_literal_text_and_avoids_unnecessary_escapes(body):
    source = "`" + body + "`"
    result = changed(source, body, "inline_code.remove")
    expected = [(char, frozenset(), ()) for char in body]
    assert characters(result) == expected
    assert not any(
        token.type in {"heading_open", "bullet_list_open", "ordered_list_open", "hr"}
        for token in MarkdownIt().parse(result)
    )
    if body in {"print(x)", "name.value"}:
        assert result == body


@pytest.mark.parametrize(
    "source,chosen,expected",
    [
        ("[`&amp;`](dest)", "amp", "[\\&amp;](dest)"),
        ("| `a\\|b` |\n|---|", "a\\|b", "| a\\|b |\n|---|"),
        ("| `&copy;` |\n|---|", "copy", "| \\&copy; |\n|---|"),
    ],
)
def test_code_removal_inside_link_or_table_retains_characters_and_structure(
    source, chosen, expected
):
    assert changed(source, chosen, "inline_code.remove") == expected


def test_nonempty_cell_after_empty_cells_is_available():
    source = "|  | target |\n|---|---|\n| | value |"
    assert changed(source, "target", "bold.apply") == source.replace("target", "**target**")
    assert changed(source, "value", "bold.apply") == source.replace("value", "**value**")


def test_unselected_marker_spelling_is_unchanged_in_ordinary_edit():
    source = "selected and __untouched__"
    assert changed(source, "selected", "bold.apply") == "**selected** and __untouched__"


def test_inline_code_can_represent_only_spaces_inside_a_paragraph():
    source = "before    after"
    result = changed(source, "    ", "inline_code.apply")
    tokens = MarkdownIt().parseInline(result)[0].children
    assert next(token.content for token in tokens if token.type == "code_inline") == "    "
