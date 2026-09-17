"""Native preview interactions stay within generated task checkbox bounds."""

import json

import pytest
from PySide6.QtCore import QPoint, Qt

from md_editor.preview import PreviewPane
from md_editor.rendering import render_markdown


@pytest.fixture
def pane(qtbot):
    preview = PreviewPane()
    qtbot.addWidget(preview)
    preview.resize(640, 380)
    preview.show()
    qtbot.waitUntil(lambda: preview._shell_ready, timeout=15000)
    return preview


def evaluate(qtbot, pane, expression):
    values = []
    pane.page().runJavaScript(f"JSON.stringify({expression})", values.append)
    qtbot.waitUntil(lambda: bool(values), timeout=5000)
    return json.loads(values[0])


def load(qtbot, pane, tmp_path, source, revision=1):
    document = render_markdown(source)
    with qtbot.waitSignal(pane.ready, timeout=10000):
        pane.set_document(document.html, document.line_count, tmp_path, revision)


def click(qtbot, pane, selector):
    position = evaluate(
        qtbot,
        pane,
        "(() => {const r=document.querySelector("
        + json.dumps(selector)
        + ").getBoundingClientRect();return "
        "[Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)];})()",
    )
    target = pane.view.focusProxy()
    assert target is not None
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=QPoint(*position))


@pytest.mark.parametrize("dark", [False, True])
def test_task_checkbox_is_enabled_and_only_its_box_toggles(qtbot, pane, tmp_path, dark):
    toggles = []
    pane.set_task_toggle_handler(lambda *args: toggles.append(args) or True)
    pane.apply_theme(dark)
    load(qtbot, pane, tmp_path, "- [ ] **Task label**\n- [x] Completed")
    assert evaluate(qtbot, pane, "document.querySelector('input').disabled") is False
    assert evaluate(qtbot, pane, "getComputedStyle(document.querySelector('input')).cursor") == (
        "pointer"
    )
    assert evaluate(qtbot, pane, "getComputedStyle(document.querySelector('strong')).cursor") != (
        "pointer"
    )
    click(qtbot, pane, "strong")
    qtbot.wait(100)
    assert toggles == []
    assert evaluate(qtbot, pane, "document.querySelector('input').checked") is False
    click(qtbot, pane, "input")
    qtbot.waitUntil(lambda: len(toggles) == 1, timeout=5000)
    assert toggles == [(0, 3, True, 1)]
    qtbot.waitUntil(
        lambda: evaluate(qtbot, pane, "document.querySelector('input').checked") is True,
        timeout=5000,
    )
    click(qtbot, pane, "input")
    qtbot.waitUntil(lambda: len(toggles) == 2, timeout=5000)
    assert toggles[-1] == (0, 3, False, 1)
    qtbot.waitUntil(
        lambda: evaluate(qtbot, pane, "document.querySelector('input').checked") is False,
        timeout=5000,
    )
    click(qtbot, pane, "li:nth-child(2) input")
    qtbot.waitUntil(lambda: len(toggles) == 3, timeout=5000)
    assert toggles[-1] == (1, 3, False, 1)


def test_rejected_and_stale_task_edits_restore_or_leave_checkbox_unchanged(qtbot, pane, tmp_path):
    toggles = []
    pane.set_task_toggle_handler(lambda *args: toggles.append(args) or False)
    load(qtbot, pane, tmp_path, "- [ ] Rejected")
    click(qtbot, pane, "input")
    qtbot.waitUntil(lambda: len(toggles) == 1, timeout=5000)
    qtbot.waitUntil(
        lambda: evaluate(qtbot, pane, "document.querySelector('input').checked") is False,
        timeout=5000,
    )
    evaluate(qtbot, pane, "(window.oldTask = document.querySelector('input'), true)")
    load(qtbot, pane, tmp_path, "- [x] New document", revision=2)
    assert not pane._bridge.toggleTask(0, 3, False, 1)
    assert not pane._bridge.toggleTask(-1, 3, False, 2)
    assert not pane._bridge.toggleTask(1, 3, False, 2)
    assert not pane._bridge.toggleTask(0, -1, False, 2)
    evaluate(qtbot, pane, "(window.oldTask.click(), true)")
    qtbot.wait(100)
    assert toggles == [(0, 3, True, 1)]
    assert evaluate(qtbot, pane, "document.querySelector('input').checked") is True


def test_no_source_handler_disables_edits_and_removing_handler_updates_current_document(
    qtbot, pane, tmp_path
):
    load(qtbot, pane, tmp_path, "- [ ] Task")
    assert evaluate(qtbot, pane, "document.querySelector('input').disabled") is True
    assert not pane._bridge.toggleTask(0, 3, True, 1)
    toggles = []
    pane.set_task_toggle_handler(lambda *args: toggles.append(args) or True)
    qtbot.waitUntil(
        lambda: evaluate(qtbot, pane, "document.querySelector('input').disabled") is False,
        timeout=5000,
    )
    pane.set_task_toggle_handler(None)
    qtbot.waitUntil(
        lambda: evaluate(qtbot, pane, "document.querySelector('input').disabled") is True,
        timeout=5000,
    )
    click(qtbot, pane, "input")
    qtbot.wait(100)
    assert toggles == []
    assert evaluate(qtbot, pane, "document.querySelector('input').checked") is False
