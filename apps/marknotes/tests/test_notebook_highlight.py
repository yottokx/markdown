"""Library-search highlights never mutate source text or preview layout."""

import json

import pytest

from marknotes.notebook_highlight import (
    highlight_html,
    preview_highlight_script,
    ranges_html,
    snippet_html,
    utf16_ranges,
)
from marknotes.notebook_store import SearchSnippet, find_match_ranges
from marknotes.preview import PreviewPane
from marknotes.rendering import render_markdown


def test_snippet_content_and_match_are_escaped_before_highlighting():
    text = '<img src=x onerror="run()"> & content'
    result = highlight_html(text, "<img")
    assert "<img" not in result
    assert "&lt;img</span>" in result
    assert "&quot;run()&quot;" in result
    assert "&amp;" in result
    assert result.count("<span ") == 1
    assert snippet_html(SearchSnippet(text, 100, ((0, 4),))) == result


@pytest.mark.parametrize(
    ("text", "query", "matched"),
    [
        ("x ＡＢＣ y", "abc", "ＡＢＣ"),
        ("x Straße y", "STRASSE", "Straße"),
        ("x ｶﾞ y", "ガ", "ｶﾞ"),
        ("x cafe\u0301 y", "café", "cafe\u0301"),
        ("😀メモ😀", "メモ", "メモ"),
    ],
)
def test_normalized_search_maps_to_original_highlight(text, query, matched):
    result = highlight_html(text, query)
    assert matched + "</span>" in result
    assert result.count("<span ") == 1


def test_qt_ranges_account_for_utf16_and_combining_sequences():
    text = "😀 ｶﾞ cafe\u0301 😀"
    assert utf16_ranges(text, find_match_ranges(text, "ガ")) == ((3, 5),)
    assert utf16_ranges(text, find_match_ranges(text, "café")) == ((6, 11),)
    assert utf16_ranges(text, [(0, 1), (-1, 4), (8, 100)]) == ((0, 2),)


def test_empty_query_and_invalid_ranges_do_not_create_markup():
    assert highlight_html("<hello>\n", "") == "&lt;hello&gt;<br>"
    assert ranges_html("abc", [(2, 1), (9, 10)]) == "abc"
    assert ranges_html("abc", [(0, 2), (1, 3)]).count("<span ") == 2


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


def test_preview_matches_across_inline_markup_and_clear_keeps_dom_and_layout(
    qtbot, preview, tmp_path
):
    source = "# メモ\n\nメ**モ**とメモ [資料](https://example.com/メモ)\n\n" + "long line\n\n" * 30
    load_document(qtbot, preview, source, tmp_path)
    original = evaluate(qtbot, preview, 'document.getElementById("content").innerHTML')
    geometry = evaluate(qtbot, preview, "window.previewApi.metrics()")

    result = evaluate(
        qtbot, preview, preview_highlight_script("メモ", 1, generation=1, note_id="a")
    )

    assert result == {"applied": True, "count": 3, "truncated": False}
    assert evaluate(qtbot, preview, 'document.getElementById("content").innerHTML') == original
    after = evaluate(qtbot, preview, "window.previewApi.metrics()")
    assert after["anchors"] == geometry["anchors"]
    assert after["contentHeight"] == geometry["contentHeight"]
    assert after["scrollY"] == geometry["scrollY"]
    assert evaluate(
        qtbot,
        preview,
        'Array.from(CSS.highlights.get("marknotes-library-search"), range => range.toString())',
    ) == ["メモ", "メモ", "メモ"]

    cleared = evaluate(qtbot, preview, preview_highlight_script("", 1, generation=2, note_id="a"))
    assert cleared == {"applied": True, "count": 0}
    assert evaluate(qtbot, preview, 'CSS.highlights.has("marknotes-library-search")') is False
    assert evaluate(qtbot, preview, 'document.getElementById("content").innerHTML') == original


@pytest.mark.parametrize(
    ("source", "query", "match"),
    [
        ("# ＡＢＣ", "abc", "ＡＢＣ"),
        ("# Straße", "STRASSE", "Straße"),
        ("# ｶﾞ", "ガ", "ｶﾞ"),
        ("# cafe\u0301", "café", "cafe\u0301"),
        ("# 😀メモ", "メモ", "メモ"),
        ("# ΟΣ ος", "οσ", "ΟΣ"),
    ],
)
def test_preview_uses_full_unicode_folding(qtbot, preview, tmp_path, source, query, match):
    load_document(qtbot, preview, source, tmp_path)
    result = evaluate(qtbot, preview, preview_highlight_script(query, 1))
    assert result["applied"]
    assert result["count"] >= 1
    assert (
        evaluate(
            qtbot,
            preview,
            'Array.from(CSS.highlights.get("marknotes-library-search"))[0].toString()',
        )
        == match
    )


def test_preview_rejects_old_revision_and_query_generation(qtbot, preview, tmp_path):
    load_document(qtbot, preview, "# メモ", tmp_path, revision=10)
    assert evaluate(qtbot, preview, preview_highlight_script("メモ", 9)) == {
        "applied": False,
        "reason": "revision",
    }
    evaluate(qtbot, preview, preview_highlight_script("メモ", 10, generation=3))
    assert evaluate(qtbot, preview, preview_highlight_script("", 10, generation=2)) == {
        "applied": False,
        "reason": "generation",
    }
    assert evaluate(qtbot, preview, 'CSS.highlights.has("marknotes-library-search")') is True


def test_preview_query_is_literal_and_never_executed(qtbot, preview, tmp_path):
    query = '");window.badSearch=true;//'
    load_document(qtbot, preview, "# safe\n\n" + query, tmp_path)
    result = evaluate(qtbot, preview, preview_highlight_script(query, 1, note_id=query))
    assert result["count"] == 1
    assert evaluate(qtbot, preview, "Boolean(window.badSearch)") is False


def test_preview_highlights_visible_math_without_duplicate_accessibility_text(
    qtbot, preview, tmp_path
):
    load_document(qtbot, preview, "$x$", tmp_path)
    result = evaluate(qtbot, preview, preview_highlight_script("x", 1))
    assert result["applied"]
    assert result["count"] == 1
