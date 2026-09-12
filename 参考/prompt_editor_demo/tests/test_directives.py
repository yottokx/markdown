from prompt_editor_demo.directives import (
    DirectiveKind,
    directives_for_selection,
    line_end_completion_directive,
    parse_directives,
)


def test_parses_all_mvp_directive_kinds() -> None:
    text = "私は ?人名 です。\n? 注意点を説明する\n- ?注意点\n  - ?注意点を3つ..."

    directives = parse_directives(text)

    assert [directive.kind for directive in directives] == [
        DirectiveKind.INLINE,
        DirectiveKind.PARAGRAPH,
        DirectiveKind.LIST_SINGLE,
        DirectiveKind.LIST_MULTI,
    ]
    assert [directive.hint for directive in directives] == [
        "人名",
        "注意点を説明する",
        "注意点",
        "注意点を3つ",
    ]
    assert directives[-1].indent == "  "
    assert directives[-1].ellipsis_start is not None


def test_inline_requires_ascii_space_boundaries() -> None:
    text = "無効?人名です。\n有効 ?人名 です。\n末尾 ?会社名"

    directives = parse_directives(text)

    assert [directive.hint for directive in directives] == ["人名", "会社名"]


def test_inline_replacement_span_consumes_delimiter_spaces() -> None:
    text = "私は ?人名 です。"
    directive = parse_directives(text)[0]

    assert text[directive.start : directive.end] == " ?人名 "


def test_adjacent_inline_replacement_spans_do_not_overlap() -> None:
    text = "値は ?姓 ?名 です。"
    first, second = parse_directives(text)

    assert first.end == second.start
    assert text[first.start : first.end] == " ?姓"
    assert text[second.start : second.end] == " ?名 "


def test_ellipsis_only_expands_at_list_hint_end() -> None:
    text = "- ?注意点...\n- ?注意点…\n- ?注意点...を説明\n- ?注意点...."

    directives = parse_directives(text)

    assert [directive.kind for directive in directives] == [
        DirectiveKind.LIST_MULTI,
        DirectiveKind.LIST_SINGLE,
        DirectiveKind.LIST_SINGLE,
        DirectiveKind.LIST_SINGLE,
    ]


def test_ignores_code_and_escaped_question_marks() -> None:
    text = (
        "`?人名` と有効な ?人名 です。\n"
        "```text\n? 注意点\n- ?項目...\n```\n"
        "\\? 注意点\n"
    )

    directives = parse_directives(text)

    assert len(directives) == 1
    assert directives[0].kind == DirectiveKind.INLINE


def test_selection_uses_question_mark_position_and_caret_uses_line() -> None:
    text = "? 段落を作る\n\n- ?項目を作る"
    directives = parse_directives(text)
    paragraph, list_item = directives

    assert directives_for_selection(directives, 3, 3) == [paragraph]
    assert directives_for_selection(
        directives, paragraph.q_start, list_item.q_start + 1
    ) == directives
    assert directives_for_selection(directives, 1, list_item.q_start) == []


def test_line_end_completion_requires_logical_line_end_and_plain_trailing_character() -> None:
    text = "最初の行\n次の行"

    first = line_end_completion_directive(text, text.index("\n"))
    last = line_end_completion_directive(text, len(text))

    assert first is not None and first.kind == DirectiveKind.LINE_END
    assert last is not None and last.start == len(text)
    assert line_end_completion_directive(text, 2) is None


def test_line_end_completion_rejects_markdown_only_or_syntax_trailing_lines() -> None:
    assert line_end_completion_directive("- ", 2) is None
    assert line_end_completion_directive("##", 2) is None
    assert line_end_completion_directive("強調*", 3) is None
    assert line_end_completion_directive("`code`", 6) is None
    assert line_end_completion_directive("```\ncode\n```", 8) is None
