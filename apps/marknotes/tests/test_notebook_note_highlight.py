"""Note-search preview highlights use Python matching and leave rendered HTML intact."""

import json
import re

import pytest

from marknotes.notebook_highlight import (
    preview_highlight_script,
    preview_search_match_ranges,
    preview_search_ranges_script,
    preview_search_text_script,
)
from marknotes.preview import PreviewPane
from marknotes.rendering import render_markdown


@pytest.fixture
def preview(qtbot):
    pane = PreviewPane()
    pane.resize(720, 440)
    qtbot.addWidget(pane)
    pane.show()
    qtbot.waitUntil(lambda: pane._shell_ready, timeout=15000)
    return pane


def evaluate(qtbot, pane, expression):
    results = []
    expression = expression.strip().removesuffix(";")
    pane.page().runJavaScript(f"JSON.stringify({expression})", results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    return json.loads(results[0])


def load_document(qtbot, pane, source, base_dir, revision=1):
    rendered = render_markdown(source)
    with qtbot.waitSignal(pane.ready, timeout=10000):
        pane.set_document(rendered.html, rendered.line_count, base_dir, revision)


def note_matches(qtbot, pane):
    return evaluate(
        qtbot,
        pane,
        'Array.from(CSS.highlights.get("marknotes-note-search") || [], range => range.toString())',
    )


def match_runs(snapshot, expression, flags=0):
    return preview_search_match_ranges(re.compile(expression, flags), snapshot["runs"])


def test_note_search_matches_python_regex_across_inline_markup_without_dom_changes(
    qtbot, preview, tmp_path
):
    source = "# メモ\n\n😀メ**モ** メモ\n\n```python\nprint('メモ メモ')\n```\n"
    load_document(qtbot, preview, source, tmp_path, revision=3)
    original = evaluate(qtbot, preview, 'document.getElementById("content").innerHTML')
    geometry = evaluate(qtbot, preview, "window.previewApi.metrics()")
    snapshot = evaluate(
        qtbot, preview, preview_search_text_script(3, generation=4, note_id="note-a")
    )
    assert snapshot["applied"]
    assert snapshot["revision"] == 3
    assert snapshot["generation"] == 4
    assert snapshot["noteId"] == "note-a"
    ranges = match_runs(snapshot, r"(?P<word>メモ)\s+(?P=word)")
    result = evaluate(
        qtbot,
        preview,
        preview_search_ranges_script(ranges, 3, generation=4, note_id="note-a"),
    )
    assert result == {"applied": True, "count": 2, "truncated": False}
    assert note_matches(qtbot, preview) == ["メモ メモ", "メモ メモ"]
    assert evaluate(qtbot, preview, 'document.getElementById("content").innerHTML') == original
    after = evaluate(qtbot, preview, "window.previewApi.metrics()")
    assert after["anchors"] == geometry["anchors"]
    assert after["contentHeight"] == geometry["contentHeight"]
    assert after["scrollY"] == geometry["scrollY"]


@pytest.mark.parametrize(
    ("expression", "flags", "expected"),
    [
        ("alpha", 0, ["alpha"]),
        ("alpha", re.IGNORECASE, ["Alpha", "alpha"]),
        (r"😀メモ", 0, ["😀メモ"]),
        (r"(?<=😀)メモ", 0, ["メモ"]),
        (r"(?=メモ)", 0, []),
    ],
)
def test_note_ranges_preserve_python_case_regex_and_emoji_semantics(
    qtbot, preview, tmp_path, expression, flags, expected
):
    load_document(qtbot, preview, "Alpha alpha 😀メ**モ**", tmp_path)
    snapshot = evaluate(qtbot, preview, preview_search_text_script(1))
    result = evaluate(
        qtbot,
        preview,
        preview_search_ranges_script(match_runs(snapshot, expression, flags), 1),
    )
    assert result["applied"]
    assert result["count"] == len(expected)
    assert note_matches(qtbot, preview) == expected


def test_note_snapshot_excludes_hidden_math_accessibility_and_controls(qtbot, preview, tmp_path):
    load_document(qtbot, preview, "$x$\n\n```python\nx = 1\n```", tmp_path)
    evaluate(
        qtbot,
        preview,
        r"""(() => {
          const p = document.createElement("p");
          p.innerHTML = '<span>visible</span><button>control-only</button>' +
            '<span hidden>hidden-attribute</span>' +
            '<span style="display:none">display-hidden</span>' +
            '<span style="visibility:hidden">visibility-hidden</span>';
          document.getElementById("content").appendChild(p);
          return true;
        })()""",
    )
    snapshot = evaluate(qtbot, preview, preview_search_text_script(1))
    text = "".join(snapshot["runs"])
    assert "visible" in text
    assert "control-only" not in text
    assert "hidden-attribute" not in text
    assert "display-hidden" not in text
    assert "visibility-hidden" not in text
    result = evaluate(qtbot, preview, preview_search_ranges_script(match_runs(snapshot, "x"), 1))
    assert result["count"] == 2
    assert note_matches(qtbot, preview) == ["x", "x"]


def test_note_snapshot_keeps_blocks_separate_and_explicit_line_breaks(qtbot, preview, tmp_path):
    load_document(qtbot, preview, "one**two**  \nthree\n\nfour", tmp_path)
    snapshot = evaluate(qtbot, preview, preview_search_text_script(1))
    assert any(re.search(r"onetwo\nthree", text) for text in snapshot["runs"])
    assert not match_runs(snapshot, "threefour")
    ranges = match_runs(snapshot, r"onetwo\nthree")
    assert len(ranges) == 1
    result = evaluate(qtbot, preview, preview_search_ranges_script(ranges, 1))
    assert result["count"] == 1
    assert "onetwo" in note_matches(qtbot, preview)[0]
    assert "three" in note_matches(qtbot, preview)[0]


def test_note_search_rejects_old_revision_generation_and_other_note(qtbot, preview, tmp_path):
    load_document(qtbot, preview, "Alpha", tmp_path, revision=10)
    assert evaluate(qtbot, preview, preview_search_text_script(9)) == {
        "applied": False,
        "reason": "revision",
    }
    snapshot = evaluate(qtbot, preview, preview_search_text_script(10, generation=3, note_id="a"))
    ranges = match_runs(snapshot, "Alpha")
    evaluate(qtbot, preview, preview_search_ranges_script(ranges, 10, generation=3, note_id="a"))
    for script in (
        preview_search_text_script(10, generation=2, note_id="a"),
        preview_search_ranges_script(ranges, 10, generation=2, note_id="a"),
        preview_search_ranges_script([], 10, generation=2, note_id="a"),
    ):
        assert evaluate(qtbot, preview, script) == {"applied": False, "reason": "generation"}
    for script in (
        preview_search_text_script(10, generation=3, note_id="b"),
        preview_search_ranges_script(ranges, 10, generation=3, note_id="b"),
        preview_search_ranges_script([], 10, generation=3, note_id="b"),
    ):
        assert evaluate(qtbot, preview, script) == {"applied": False, "reason": "note"}
    assert note_matches(qtbot, preview) == ["Alpha"]
    load_document(qtbot, preview, "Beta", tmp_path, revision=11)
    assert evaluate(
        qtbot, preview, preview_search_ranges_script(ranges, 10, generation=3, note_id="a")
    ) == {"applied": False, "reason": "revision"}


def test_clear_before_capture_and_after_search_does_not_clear_library_search(
    qtbot, preview, tmp_path
):
    load_document(qtbot, preview, "Alpha", tmp_path)
    evaluate(qtbot, preview, preview_highlight_script("Alpha", 1))
    result = evaluate(
        qtbot, preview, preview_search_ranges_script([], 1, generation=1, note_id="a")
    )
    assert result == {"applied": True, "count": 0, "truncated": False}
    snapshot = evaluate(qtbot, preview, preview_search_text_script(1, generation=1, note_id="a"))
    ranges = match_runs(snapshot, "Alpha")
    evaluate(qtbot, preview, preview_search_ranges_script(ranges, 1, generation=1, note_id="a"))
    assert note_matches(qtbot, preview) == ["Alpha"]
    evaluate(qtbot, preview, preview_search_ranges_script([], 1, generation=2, note_id="a"))
    assert note_matches(qtbot, preview) == []
    assert evaluate(qtbot, preview, 'CSS.highlights.has("marknotes-note-search")') is False
    assert evaluate(qtbot, preview, 'CSS.highlights.has("marknotes-library-search")') is True
    assert evaluate(
        qtbot, preview, preview_search_ranges_script(ranges, 1, generation=1, note_id="a")
    ) == {"applied": False, "reason": "generation"}


def test_note_ranges_require_a_matching_snapshot_and_ignore_invalid_offsets(
    qtbot, preview, tmp_path
):
    load_document(qtbot, preview, "Alpha", tmp_path)
    assert evaluate(qtbot, preview, preview_search_ranges_script([(0, 0, 5)], 1)) == {
        "applied": False,
        "reason": "snapshot",
    }
    snapshot = evaluate(qtbot, preview, preview_search_text_script(1))
    valid = match_runs(snapshot, "Alpha")
    index = valid[0][0]
    invalid = [(-1, 0, 5), (9999, 0, 5), (index, -1, 5), (index, 0, 9999), (index, 0, 0)]
    result = evaluate(qtbot, preview, preview_search_ranges_script([*invalid, *valid], 1))
    assert result["count"] == 1
    assert note_matches(qtbot, preview) == ["Alpha"]


def test_note_search_bounds_highlight_count(qtbot, preview, tmp_path):
    load_document(qtbot, preview, "Alpha", tmp_path)
    snapshot = evaluate(qtbot, preview, preview_search_text_script(1))
    match = match_runs(snapshot, "Alpha")[0]
    result = evaluate(qtbot, preview, preview_search_ranges_script((match for _ in range(6000)), 1))
    assert result == {"applied": True, "count": 5000, "truncated": True}
    assert len(note_matches(qtbot, preview)) == 5000


def test_note_identity_is_json_escaped_and_never_executed(qtbot, preview, tmp_path):
    load_document(qtbot, preview, "Alpha", tmp_path)
    note_id = '");window.badSearch=true;//'
    snapshot = evaluate(qtbot, preview, preview_search_text_script(1, note_id=note_id))
    assert snapshot["noteId"] == note_id
    result = evaluate(
        qtbot,
        preview,
        preview_search_ranges_script(match_runs(snapshot, "Alpha"), 1, note_id=note_id),
    )
    assert result["count"] == 1
    assert evaluate(qtbot, preview, "Boolean(window.badSearch)") is False


@pytest.mark.parametrize(
    ("expression", "flags", "expected"),
    [
        (r"^needle", 0, ((0, 0, 6),)),
        (r"needle$", 0, ((1, 0, 6),)),
        (r"\Aneedle", 0, ((0, 0, 6),)),
        (r"needle\Z", 0, ((1, 0, 6),)),
        (r"(?<=\n)needle", 0, ((1, 0, 6),)),
        (r"^needle", re.MULTILINE, ((0, 0, 6), (1, 0, 6))),
        (r"needle\nneedle", 0, ((0, 0, 6), (1, 0, 6))),
    ],
)
def test_preview_match_anchors_and_lookarounds_use_document_scope(expression, flags, expected):
    assert preview_search_match_ranges(re.compile(expression, flags), ["needle", "needle"]) == (
        expected
    )


def test_preview_match_mapping_preserves_emoji_code_whitespace_and_zero_width_limit():
    runs = ["😀メモ", "  ", "メモ😀"]
    assert preview_search_match_ranges(re.compile(r"メモ\n  \nメモ"), runs) == (
        (0, 2, 4),
        (1, 0, 2),
        (2, 0, 2),
    )
    assert preview_search_match_ranges(re.compile(r"^|メモ"), runs, limit=1) == ()
    assert preview_search_match_ranges(re.compile(r"^|メモ"), runs, limit=2) == ((0, 2, 4),)
    assert preview_search_match_ranges(re.compile("メモ"), runs, limit=0) == ()


def test_note_snapshot_omits_layout_whitespace_but_keeps_code_whitespace(qtbot, preview, tmp_path):
    load_document(qtbot, preview, "needle\n\n```text\n  \n```\n\nneedle", tmp_path)
    snapshot = evaluate(qtbot, preview, preview_search_text_script(1))
    assert len(snapshot["runs"]) == 3
    assert snapshot["runs"][0] == "needle"
    assert snapshot["runs"][1].startswith("  ")
    assert snapshot["runs"][2] == "needle"
    ranges = match_runs(snapshot, r"\Aneedle|needle\Z")
    result = evaluate(qtbot, preview, preview_search_ranges_script(ranges, 1))
    assert result["count"] == 2
