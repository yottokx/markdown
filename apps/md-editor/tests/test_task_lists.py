from html.parser import HTMLParser

import pytest
from bs4 import BeautifulSoup

from md_editor.rendering import render_markdown
from md_editor.task_lists import task_marker_column


class TaskInputs(HTMLParser):
    def __init__(self, fragment):
        super().__init__()
        self.inputs = []
        self.feed(fragment)

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("- [ ] plain", ((0, 3),)),
        ("* [x] checked", ((0, 3),)),
        ("+ [X] uppercase", ((0, 3),)),
        ("1. [ ] ordered", ((0, 4),)),
        ("9) [x] ordered", ((0, 4),)),
        ("> 1. [X] quoted", ((0, 6),)),
        ("> > - [ ] nested quotes", ((0, 7),)),
        ("- > - [ ] nested containers", ((0, 7),)),
        ("- - [ ] nested lists", ((0, 5),)),
        ("- parent\n  - [ ] nested\n  - [x] nested", ((1, 5), (2, 5))),
        ("-\n  [ ] first paragraph", ((1, 3),)),
        ("-\t[x]\twith tabs", ((0, 3),)),
        ("- [ ]\n- [X]", ((0, 3), (1, 3))),
        ("- [ ]\r\n- [x] next", ((0, 3), (1, 3))),
    ],
)
def test_task_inputs_map_to_the_exact_state_character(source, expected):
    rendered = render_markdown(source)
    inputs = TaskInputs(rendered.html).inputs
    assert rendered.task_markers == expected
    assert len(inputs) == len(expected)
    for attrs, (line, column) in zip(inputs, expected, strict=True):
        assert attrs["type"] == "checkbox"
        assert attrs["class"] == "task-list-item-checkbox"
        assert "disabled" not in attrs
        assert int(attrs["data-task-line"]) == line
        assert int(attrs["data-task-column"]) == column
        source_line = source.split("\n")[line]
        assert task_marker_column(source_line) == column
        assert ("checked" in attrs) == (source_line[column].lower() == "x")
    assert "<label" not in rendered.html


@pytest.mark.parametrize(
    "source",
    [
        "[ ] outside list",
        "- [ ]no separating whitespace",
        "- [y] unsupported marker",
        r"- \[ ] escaped marker",
        "- `[ ]` code span",
        "```md\n- [ ] fenced code\n```",
        "    - [ ] indented code",
        "- parent\n\n  [ ] later paragraph",
        "$$\n- [ ] display math\n$$",
    ],
)
def test_ordinary_brackets_and_code_are_not_editable_tasks(source):
    rendered = render_markdown(source)
    assert rendered.task_markers == ()
    assert TaskInputs(rendered.html).inputs == []


def test_raw_html_cannot_impersonate_editable_task_inputs():
    fake = (
        '<input type="checkbox" class="task-list-item-checkbox"'
        ' data-task-line="0" data-task-column="3">'
    )
    source = f"- [ ] real\n\n<div>{fake}</div>\n\nInline {fake}\n"
    rendered = render_markdown(source)
    real, block, inline = TaskInputs(rendered.html).inputs
    assert rendered.task_markers == ((0, 3),)
    assert real["data-task-line"] == "0"
    assert real["data-task-column"] == "3"
    for attrs in (block, inline):
        assert "data-task-line" not in attrs
        assert "data-task-column" not in attrs


def test_task_marker_is_removed_before_reference_link_parsing():
    rendered = render_markdown("- [x] **done** $x$\n\n[x]: https://example.com")
    assert rendered.task_markers == ((0, 3),)
    assert BeautifulSoup(rendered.html, "html.parser").strong.get_text() == "done"
    assert 'data-render-kind="math-inline"' in rendered.html
    assert "<a " not in rendered.html
    assert "[x]" not in rendered.html


@pytest.mark.parametrize(
    "line",
    ["ordinary [ ] text", "- [ ]no space", "- [y] task", r"- \[ ] task", "- [ ](url)"],
)
def test_marker_column_rejects_non_task_prefixes(line):
    assert task_marker_column(line) is None


def test_marker_column_handles_long_nonmatching_indentation():
    assert task_marker_column(" " * 10000 + "ordinary text") is None
