import pytest
from markdown_it import MarkdownIt

from md_editor.block_formatting import block_actions
from md_editor.math_parser import math_plugin


def _actions(source, start=0, end=None):
    return {
        action.key: action for action in block_actions(source, start, start if end is None else end)
    }


def _apply(source, key, start=0, end=None):
    action = _actions(source, start, end)[key]
    assert action.edit is not None, action
    edit = action.edit
    assert 0 <= edit.start <= edit.end <= len(source)
    assert 0 <= edit.selection_start <= edit.selection_end <= len(edit.text)
    return source[: edit.start] + edit.text + source[edit.end :], edit


def _parse(source):
    return (
        MarkdownIt("commonmark").enable(["table", "strikethrough"]).use(math_plugin).parse(source)
    )


def _nodes(source, kind):
    return [token for token in _parse(source) if token.type == kind]


@pytest.mark.parametrize("source", ["", "text", "日本語 😀\n", "a\nb\n"])
def test_actions_have_stable_keys_and_valid_python_offsets(source):
    actions = block_actions(source, len(source), len(source))
    assert {action.key for action in actions} == {f"heading.{level}" for level in range(7)} | {
        f"{kind}.{mode}"
        for kind in ("quote", "bullet", "ordered", "code_block")
        for mode in ("apply", "remove")
    }
    for action in actions:
        if action.edit:
            edit = action.edit
            assert 0 <= edit.start <= edit.end <= len(source)
            assert 0 <= edit.selection_start <= edit.selection_end <= len(edit.text)
    assert all(action.group == "heading" for action in actions[:7])


def test_partial_line_selection_expands_rows_but_excludes_next_line_start():
    source = "alpha\nbeta\ngamma"
    result, edit = _apply(source, "heading.2", 2, source.index("gamma"))
    assert result == "## alpha\n## beta\ngamma"
    assert edit.end == source.index("\ngamma")
    assert [token.tag for token in _nodes(result, "heading_open")] == ["h2", "h2"]


@pytest.mark.parametrize("prefix", ["", "> ", "- ", "> - ", "- > ", "> > "])
def test_heading_changes_preserve_outer_quote_and_list_structure(prefix):
    source = prefix + "### title ###"
    result, edit = _apply(source, "heading.1", source.index("title"), len(source))
    assert result == prefix + "# title"
    before, after = _parse(source), _parse(result)
    containers = {"blockquote_open", "list_item_open", "bullet_list_open"}
    assert [(t.type, t.level) for t in before if t.type in containers] == [
        (t.type, t.level) for t in after if t.type in containers
    ]
    assert edit.text[edit.selection_start : edit.selection_end] == "title"


def test_mixed_heading_and_paragraph_selection_has_no_checked_heading():
    source = "# Heading\n\nparagraph"
    assert not any(
        action.checked
        for action in block_actions(source, 0, len(source))
        if action.group == "heading"
    )
    assert _actions(source, 3)["heading.1"].checked


def test_standard_paragraph_only_removes_heading_and_keeps_block_separation():
    source = "before\n# title\nafter"
    result, _ = _apply(source, "heading.0", source.index("title"))
    assert result == "before\n\ntitle\n\nafter"
    assert len(_nodes(result, "paragraph_open")) == 3
    assert not _nodes(result, "heading_open")
    assert _actions("> - ordinary", 6)["heading.0"].edit is None


@pytest.mark.parametrize("underline", ["---", "==="])
def test_multiline_setext_paragraph_keeps_hard_break_and_underlining_is_atomic(underline):
    source = "first  \nsecond\n" + underline
    result, _ = _apply(source, "heading.0", len(source) - 1)
    assert result == "first  \nsecond"
    assert any(
        child.type == "hardbreak" for token in _nodes(result, "inline") for child in token.children
    )
    assert all(_actions(source, 2)[f"heading.{level}"].edit is None for level in range(1, 7))
    quote, _ = _apply(source, "quote.apply", len(source) - 1)
    assert len(_nodes(quote, "heading_open")) == 1
    assert _nodes(quote, "heading_open")[0].level == 1


def test_single_line_setext_heading_preserves_literal_trailing_hash():
    source = "title #\n---"
    result, _ = _apply(source, "heading.3", 1)
    assert _nodes(result, "inline")[0].children[-1].content == "title #"


@pytest.mark.parametrize("key", ["heading.2", "quote.apply", "code_block.apply"])
def test_editing_one_line_canonicalizes_unselected_lazy_continuations(key):
    source = "> - first\nlazy continuation"
    result, _ = _apply(source, key, source.index("first"))
    nodes = _parse(result)
    assert len([t for t in nodes if t.type == "blockquote_open"]) >= 1
    assert len([t for t in nodes if t.type == "list_item_open"]) == 1
    lazy = next(t for t in nodes if t.type == "inline" and "lazy continuation" in t.content)
    assert lazy.level >= 3


@pytest.mark.parametrize("key", ["quote.apply", "bullet.apply", "ordered.apply"])
def test_new_block_does_not_capture_unselected_neighbor_as_lazy_text(key):
    result, _ = _apply("first\nsecond", key, 2)
    second = next(t for t in _nodes(result, "inline") if t.content == "second")
    assert second.level == 1


def test_quote_removal_strips_only_innermost_quote_and_expands_lazy_structure():
    source = "> > first\nlazy continuation\n> > last"
    action = _actions(source, source.index("lazy"))["quote.remove"]
    assert action.label == "引用ブロック全体を解除"
    result, _ = _apply(source, "quote.remove", source.index("lazy"))
    assert result == "> first\n> lazy continuation\n> last"
    assert len(_nodes(result, "blockquote_open")) == 1


def test_quote_around_parent_item_also_contains_its_child_list():
    source = "- parent\n  - child"
    result, _ = _apply(source, "quote.apply", 0, len(source))
    assert result == "- > parent\n  > - child"
    assert [
        (t.type, t.level)
        for t in _parse(result)
        if t.type in {"blockquote_open", "bullet_list_open"}
    ] == [("bullet_list_open", 0), ("blockquote_open", 2), ("bullet_list_open", 3)]


def test_list_kind_change_resizes_every_continuation_including_child_list():
    source = "- parent\n  - child\n- sibling"
    result, _ = _apply(source, "ordered.apply", source.index("parent"))
    assert result == "1. parent\n   - child\n- sibling"
    assert [
        (t.type, t.level)
        for t in _parse(result)
        if t.type in {"ordered_list_open", "bullet_list_open"}
    ] == [("ordered_list_open", 0), ("bullet_list_open", 2), ("bullet_list_open", 0)]


def test_ordered_double_digit_markers_preserve_nested_fence_and_task_state():
    items = [f"- [x] item {index}\n  continuation {index}" for index in range(1, 13)]
    items[9] += "\n  ```python\n  print(10)\n  ```"
    source = "\n".join(items)
    result, _ = _apply(source, "ordered.apply", 0, len(source))
    assert "10. [x] item 10\n    continuation 10\n    ```python\n    print(10)\n    ```" in result
    assert len(_nodes(result, "list_item_open")) == 12
    fence = _nodes(result, "fence")[0]
    assert fence.level == 2
    assert fence.content == "print(10)\n"
    back, _ = _apply(result, "bullet.apply", 0, len(result))
    assert back == source


def test_removing_nested_list_layer_keeps_quote_parent_and_child_structure():
    source = "> - parent\n>   - child\n>     continuation\n> - sibling"
    action = _actions(source, source.index("continuation"))["bullet.remove"]
    assert action.label == "リスト項目全体を解除"
    result, _ = _apply(source, "bullet.remove", source.index("continuation"))
    assert len(_nodes(result, "blockquote_open")) == 1
    assert len(_nodes(result, "list_item_open")) == 2
    text = next(t for t in _nodes(result, "inline") if "child" in t.content)
    assert text.level == 4
    assert "continuation" in text.content


def test_list_removal_separates_middle_item_from_siblings():
    source = "1. before\n2. middle\n3. after"
    result, _ = _apply(source, "ordered.remove", source.index("middle"))
    assert result == "1. before\n\nmiddle\n\n3. after"
    middle = next(t for t in _nodes(result, "inline") if t.content == "middle")
    assert middle.level == 1


@pytest.mark.parametrize(
    "prefix,continuation",
    [("", ""), ("> ", "> "), ("- ", "  "), ("> - ", ">   "), ("- > ", "  > ")],
)
def test_fenced_code_removal_preserves_container_membership(prefix, continuation):
    source = prefix + "```python\n" + continuation + "print(1)\n" + continuation + "```"
    result, _ = _apply(source, "code_block.remove", source.index("print"))
    assert result == prefix + "print(1)"
    assert not _nodes(result, "fence")
    assert [
        (t.type, t.level) for t in _parse(source) if t.type in {"blockquote_open", "list_item_open"}
    ] == [
        (t.type, t.level) for t in _parse(result) if t.type in {"blockquote_open", "list_item_open"}
    ]


@pytest.mark.parametrize(
    "source,expected",
    [
        ("    x\n    y", "x\ny"),
        (">     x\n>     y", "> x\n> y"),
        ("- parent\n\n      x\n      y", "- parent\n\n  x\n  y"),
    ],
)
def test_indented_code_removal_keeps_literal_content_and_parents(source, expected):
    result, _ = _apply(source, "code_block.remove", source.index("x"))
    assert result == expected
    assert not _nodes(result, "code_block")


def test_unclosed_fence_removal_and_empty_code_are_supported():
    result, _ = _apply("```python\nprint(1)", "code_block.remove", 14)
    assert result == "print(1)"
    empty, _ = _apply("```\n```", "code_block.remove", 0)
    assert empty == ""


def test_code_creation_uses_full_lines_safe_fence_detection_and_body_selection():
    source = "def greet(name):\n    return name"
    result, edit = _apply(source, "code_block.apply", 2, len(source) - 1)
    assert result == "```python\n" + source + "\n```"
    assert edit.text[edit.selection_start : edit.selection_end] == source
    assert _nodes(result, "fence")[0].content == source + "\n"
    literal = "text `inside```ticks`\n\t<&>\n"
    wrapped, _ = _apply(literal, "code_block.apply", 0, len(literal))
    assert wrapped.startswith("````\n")
    assert _nodes(wrapped, "fence")[0].content == literal


@pytest.mark.parametrize(
    "source",
    [
        "```python\n# fake\n- fake\n```",
        "~~~\n> fake\n~~~",
        "$$\n# fake\n- fake\n$$",
        "\\[\n# fake\n\\]",
        "| a | b |\n|---|---|\n| c | d |",
        "<div>\n# fake\n</div>",
    ],
)
def test_partial_protected_blocks_cannot_be_reformatted(source):
    position = source.index("\n") + 1
    actions = _actions(source, position)
    for key, action in actions.items():
        if key == "code_block.remove" and _nodes(source, "fence"):
            assert action.edit is not None
        else:
            assert action.edit is None, (key, action)


@pytest.mark.parametrize(
    "source",
    [
        "before `x\n**not bold**` after",
        "before $x\n+y$ after",
        "**first\nsecond**",
        "~~first\nsecond~~",
        "[first\nsecond](https://example.test)",
    ],
)
def test_multiline_inline_syntax_partial_transform_is_disabled_but_whole_quote_is_safe(source):
    position = source.index("\n") + 1
    assert all(action.edit is None for action in block_actions(source, position, len(source)))
    result, _ = _apply(source, "quote.apply", 0, len(source))
    before = [(child.type, child.content) for t in _nodes(source, "inline") for child in t.children]
    after = [(child.type, child.content) for t in _nodes(result, "inline") for child in t.children]
    assert after == before


def test_code_cannot_cross_partial_list_items_and_source_line_endings_are_retained():
    assert _actions("- first\n- second", 3, 12)["code_block.apply"].edit is None
    result, edit = _apply("first\r\nsecond\r\n", "heading.2", 1, 7)
    assert result == "## first\r\nsecond\r\n"
    assert edit.end == 5


def test_reversed_unicode_selection_is_normalized_without_splitting_characters():
    source = "前😀後\n次の行"
    result, _ = _apply(source, "heading.2", source.index("次"), 1)
    assert result == "## 前😀後\n次の行"


def test_closing_fence_is_protected_from_other_block_formatting():
    source = "```python\nprint(1)\n```\nafter"
    actions = _actions(source, source.rindex("```"))
    assert actions["code_block.remove"].edit is not None
    assert all(action.edit is None for key, action in actions.items() if key != "code_block.remove")


def test_complete_table_can_be_quoted_without_changing_its_cells():
    source = "| a | b |\n|---|---|\n| c | d |"
    result, _ = _apply(source, "quote.apply", 0, len(source))
    assert len(_nodes(result, "table_open")) == 1
    assert _nodes(result, "table_open")[0].level == 1
    assert [t.content for t in _nodes(result, "inline")] == [
        t.content for t in _nodes(source, "inline")
    ]
