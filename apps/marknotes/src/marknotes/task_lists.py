"""Source-aware task list tokens shared by rendering and preview edits."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.token import Token

_TASK_START = re.compile(r"\[[ xX]\](?=[ \t\n\v\f\r]|$)")
_TASK_LINE = re.compile(
    r"^[ \t]*(?:>[ \t]*|(?:[-+*]|[0-9]{1,9}[.)])[ \t]+)*"
    r"\[(?P<state>[ xX])\](?=[ \t\v\f\r]|$)"
)


def task_marker_column(line: str) -> int | None:
    """Return the state-character column of a possible task marker.

    Container prefixes may contain indentation, quotes, or nested list markers.
    This checks a single line only: the renderer's parsed task positions must
    also be checked before editing, because an identical line can occur in code.
    """
    match = _TASK_LINE.match(line)
    return match.start("state") if match is not None else None


def tasklists_plugin(parser: MarkdownIt) -> None:
    """Generate checkbox tokens only for parsed list-item task markers."""

    def prepare(state: StateCore) -> None:
        source_lines = state.src.split("\n")
        markers: list[tuple[int, int]] = []
        state.env["task_markers"] = markers
        tokens = state.tokens
        lists: list[Token] = []
        for index, token in enumerate(tokens):
            if token.type in {"bullet_list_open", "ordered_list_open"}:
                lists.append(token)
            elif token.type in {"bullet_list_close", "ordered_list_close"}:
                lists.pop()
            if not (
                index >= 2
                and token.type == "inline"
                and token.map is not None
                and tokens[index - 1].type == "paragraph_open"
                and tokens[index - 2].type == "list_item_open"
                and _TASK_START.match(token.content)
            ):
                continue
            line = token.map[0]
            column = task_marker_column(source_lines[line])
            if column is None:
                continue
            token.meta["task_checkbox"] = {
                "line": line,
                "column": column,
                "checked": token.content[1].lower() == "x",
            }
            markers.append((line, column))
            # Remove the marker before inline parsing, so a [x] link reference
            # cannot consume the marker or leave fragments of its label behind.
            token.content = token.content[3:]
            tokens[index - 2].attrSet("class", "task-list-item enabled")
            lists[-1].attrSet("class", "contains-task-list")

    def add_checkboxes(state: StateCore) -> None:
        for token in state.tokens:
            if "task_checkbox" not in token.meta:
                continue
            checkbox = Token("task_checkbox", "input", 0)
            checkbox.meta = token.meta["task_checkbox"]
            assert token.children is not None
            token.children.insert(0, checkbox)

    parser.core.ruler.before("inline", "source_tasks_prepare", prepare)
    parser.core.ruler.after("inline", "source_tasks_checkboxes", add_checkboxes)
