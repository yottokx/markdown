"""Selection-aware formatting controls owned by each transient context menu."""

from __future__ import annotations

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QMenu

from .block_formatting import block_actions
from .formatting_types import FormatAction, FormatEdit
from .inline_formatting import inline_actions
from .search import python_position, qt_position


def apply_format_edit(editor, source: str, revision: int, edit: FormatEdit, reverse=False) -> bool:
    """Apply one source patch and select its body, preserving the drag direction."""
    if (
        editor.isReadOnly()
        or editor.document().revision() != revision
        or editor.toPlainText() != source
        or not 0 <= edit.start <= edit.end <= len(source)
        or not 0 <= edit.selection_start <= edit.selection_end <= len(edit.text)
    ):
        return False
    if source[edit.start : edit.end] == edit.text:
        return False
    cursor = editor.textCursor()
    cursor.beginEditBlock()
    cursor.setPosition(qt_position(source, edit.start))
    cursor.setPosition(qt_position(source, edit.end), QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText(edit.text)
    cursor.endEditBlock()
    updated = editor.toPlainText()
    first = qt_position(updated, edit.start + edit.selection_start)
    last = qt_position(updated, edit.start + edit.selection_end)
    cursor.setPosition(last if reverse else first)
    cursor.setPosition(first if reverse else last, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.setFocus()
    return True


def _populate(menu, actions: list[FormatAction], editor, source, revision, reverse):
    groups: dict[str, QMenu] = {}
    by_key = {item.key: item for item in actions}
    for item in actions:
        kind, _, operation = item.key.rpartition(".")
        if item.edit is None and operation in {"apply", "remove"}:
            other = by_key.get(kind + (".remove" if operation == "apply" else ".apply"))
            if other is not None and (other.edit is not None or operation == "remove"):
                continue
        parent = menu
        if item.group == "heading":
            if item.group not in groups:
                group = menu.addMenu({"heading": "見出し"}.get(item.group, item.group))
                group.setObjectName("formatGroup." + item.group)
                groups[item.group] = group
            parent = groups[item.group]
        action = parent.addAction(item.label)
        action.setObjectName("format." + item.key)
        action.setEnabled(item.edit is not None and not editor.isReadOnly())
        if item.checked:
            action.setCheckable(True)
            action.setChecked(True)
        if item.edit is not None:
            action.triggered.connect(
                lambda checked=False, edit=item.edit: apply_format_edit(
                    editor, source, revision, edit, reverse
                )
            )
    for group in groups.values():
        group.setEnabled(any(action.isEnabled() for action in group.actions()))
    menu.setEnabled(any(action.isEnabled() for action in menu.actions()))


def add_format_menus(menu: QMenu, editor) -> tuple[QMenu, QMenu]:
    """Keep the current selection; only image/table commands use the click position."""
    source = editor.toPlainText()
    cursor = editor.textCursor()
    start = python_position(source, cursor.selectionStart())
    end = python_position(source, cursor.selectionEnd())
    revision = editor.document().revision()
    reverse = cursor.anchor() > cursor.position()
    inline = menu.addMenu("文字の書式")
    inline.setObjectName("inlineFormatMenu")
    blocks = menu.addMenu("行・ブロックの書式")
    blocks.setObjectName("blockFormatMenu")
    _populate(inline, inline_actions(source, start, end), editor, source, revision, reverse)
    _populate(blocks, block_actions(source, start, end), editor, source, revision, reverse)
    return inline, blocks
