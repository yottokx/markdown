"""Ephemeral preview search highlights using the source search's Python pattern."""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections.abc import Iterable
from itertools import islice

from PySide6.QtCore import QObject
from shiboken6 import isValid


class SearchHighlights(QObject):
    """Apply visible-text ranges after rendering without touching preview DOM."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.generation = 0
        window.search.refreshed.connect(self.refresh)
        window.search.closed.connect(self.refresh)
        window.preview.ready.connect(self._preview_ready)

    def _state(self):
        window = self.window
        return (
            window._revision,
            window.editor.document(),
            window.editor.document().revision(),
            window.search.query.text(),
            window.search.regex.isChecked(),
            window.search.case_sensitive.isChecked(),
        )

    def _preview_ready(self, revision):
        if revision == self.window._revision:
            self.refresh()

    def refresh(self):
        window = self.window
        if not isValid(window) or getattr(window, "_closing", False):
            return
        self.generation += 1
        generation = self.generation
        state = self._state()
        revision = window._revision
        if window._rendered_revision != revision:
            return
        try:
            pattern = window.search.pattern() if window.search.isVisible() else None
        except re.error:
            pattern = None
        if pattern is None:
            window.preview.page().runJavaScript(
                preview_search_ranges_script([], revision, generation=generation)
            )
            return

        def collected(raw):
            if (
                not isValid(window)
                or getattr(window, "_closing", False)
                or generation != self.generation
                or state != self._state()
                or revision != window._rendered_revision
                or not window.search.isVisible()
            ):
                return
            try:
                result = json.loads(raw) if isinstance(raw, str) else None
            except (TypeError, ValueError):
                return
            if not isinstance(result, dict) or not result.get("applied"):
                return
            runs = result.get("runs")
            if not isinstance(runs, list) or any(not isinstance(run, str) for run in runs):
                return
            ranges = preview_search_match_ranges(pattern, runs)
            window.preview.page().runJavaScript(
                preview_search_ranges_script(ranges, revision, generation=generation)
            )

        script = (
            preview_search_text_script(revision, generation=generation).strip().removesuffix(";")
        )
        window.preview.page().runJavaScript(f"JSON.stringify({script})", collected)


def utf16_ranges(text: str, ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Translate Python offsets to QTextCursor positions, including astral emoji."""
    positions = [0]
    for character in text:
        positions.append(positions[-1] + (2 if ord(character) > 0xFFFF else 1))
    return tuple(
        (positions[start], positions[end]) for start, end in ranges if 0 <= start < end <= len(text)
    )


def preview_search_match_ranges(
    pattern: re.Pattern[str], runs: Iterable[str], limit: int = 1000
) -> tuple[tuple[int, int, int], ...]:
    """Match the preview as one document and map results back to its text runs.

    The collector removes layout-only whitespace runs while preserving code
    whitespace. Joining visible blocks with newlines gives anchors and
    lookarounds document scope. The match limit includes zero-width matches,
    following source search; only ranges containing real text are highlighted.
    """
    values = tuple(runs)
    if not values or limit <= 0:
        return ()
    starts: list[int] = []
    position = 0
    for value in values:
        starts.append(position)
        position += len(value) + 1
    by_run: dict[int, list[tuple[int, int]]] = {}
    for match in islice(pattern.finditer("\n".join(values)), limit):
        start, end = match.span()
        if start == end:
            continue
        index = max(0, bisect_right(starts, start) - 1)
        while index < len(values) and starts[index] < end:
            local_start = max(0, start - starts[index])
            local_end = min(len(values[index]), end - starts[index])
            if local_start < local_end:
                by_run.setdefault(index, []).append((local_start, local_end))
            index += 1
    return tuple(
        (index, start, end)
        for index, spans in by_run.items()
        for start, end in utf16_ranges(values[index], spans)
    )


def preview_search_text_script(revision: int, *, generation: int = 0) -> str:
    """Snapshot visible text for matching with the source search's Python pattern.

    The result contains ``runs``, a list of strings. Inline markup is joined
    within each block; explicit line breaks remain newlines. DOM positions stay
    in JavaScript until ``preview_search_ranges_script`` applies UTF-16 ranges
    bearing the same revision and generation.
    """
    payload = json.dumps(
        {"revision": int(revision), "generation": int(generation)},
        ensure_ascii=True,
    )
    return _NOTE_SEARCH_TEXT_SCRIPT.replace("__MD_EDITOR_PAYLOAD__", payload).replace(
        "__MD_EDITOR_TEXT_RUNS__", _PREVIEW_TEXT_RUNS
    )


def preview_search_ranges_script(
    ranges: Iterable[tuple[int, int, int]],
    revision: int,
    *,
    generation: int = 0,
) -> str:
    """Highlight ``(run_index, utf16_start, utf16_end)`` snapshot ranges.

    An empty iterable clears highlights and invalidates the snapshot, even if
    text has not been collected. Older revisions/generations cannot clear newer
    highlights. Each request is bounded to 5,000 text-run ranges.
    """
    limited = list(islice(ranges, 5001))
    payload = json.dumps(
        {
            "ranges": limited[:5000],
            "truncated": len(limited) > 5000,
            "revision": int(revision),
            "generation": int(generation),
        },
        ensure_ascii=True,
    )
    return _NOTE_SEARCH_RANGES_SCRIPT.replace("__MD_EDITOR_PAYLOAD__", payload).replace(
        "__MD_EDITOR_TEXT_RUNS__", _PREVIEW_TEXT_RUNS
    )


_PREVIEW_TEXT_RUNS = r"""
  const collectTextRuns = () => {
    const walker = document.createTreeWalker(
      content, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
        acceptNode(node) {
          const isText = node.nodeType === Node.TEXT_NODE;
          if (!isText && node.nodeName !== "BR") return NodeFilter.FILTER_SKIP;
          const parent = isText ? node.parentElement : node;
          if (!parent || (isText && !node.data) ||
              parent.closest("script,style,textarea,button,[hidden],.katex-mathml") ||
              !parent.getClientRects().length ||
              getComputedStyle(parent).visibility === "hidden") {
            return NodeFilter.FILTER_REJECT;
          }
          return NodeFilter.FILTER_ACCEPT;
        }
      }
    );
    const runs = [];
    let node;
    let run = null;
    while ((node = walker.nextNode())) {
      const block = node.parentElement.closest(
        "p,h1,h2,h3,h4,h5,h6,pre,li,td,th,dt,dd,figcaption,div"
      ) || content;
      if (!run || run.block !== block) {
        run = {block, text: "", nodes: []};
        runs.push(run);
      }
      const codeLine = node.parentElement.closest("pre > code[data-code-source] > .code-line");
      if (codeLine && run.codeLine !== codeLine) {
        // Generated rows are block spans without separating text nodes. Their
        // boundaries supply newlines; an empty row's BR only supplies height.
        if (run.codeLine) run.text += "\n";
        run.codeLine = codeLine;
      }
      if (node.nodeType === Node.TEXT_NODE) {
        run.nodes.push({node, text: node.data,
          start: run.text.length, end: run.text.length + node.data.length});
        run.text += node.data;
      } else if (!codeLine) {
        // Markdown's <br> already has a following newline text node. Raw HTML
        // may not; only synthesize the separator when the DOM omits it.
        const next = node.nextSibling;
        if (!next || next.nodeType !== Node.TEXT_NODE || !next.data.startsWith("\n")) {
          run.text += "\n";
        }
      }
    }
    // Whitespace between block elements is HTML formatting, not document text.
    // Preserve whitespace-only code blocks, where spaces and newlines matter.
    return runs.filter(run => run.text.trim() || run.block.closest("pre,code"));
  };
  const textRunRange = (current, start, end) => {
    if (!current || !Number.isInteger(start) || !Number.isInteger(end) ||
        start < 0 || start >= end || end > current.text.length) return null;
    const first = current.nodes.find(part => part.end > start && part.start < end);
    const last = current.nodes.findLast(part => part.start < end && part.end > start);
    if (!first || !last || !first.node.isConnected || !last.node.isConnected ||
        first.node.data !== first.text || last.node.data !== last.text) return null;
    const range = new Range();
    range.setStart(first.node, Math.max(0, start - first.start));
    range.setEnd(last.node, Math.min(last.node.length, end - last.start));
    return range;
  };
"""


_NOTE_SEARCH_TEXT_SCRIPT = r"""
(() => {
  "use strict";
  const request = __MD_EDITOR_PAYLOAD__;
  const content = document.getElementById("content");
  if (!content || !window.previewApi ||
      window.previewApi.metrics().revision !== request.revision) {
    return {applied: false, reason: "revision"};
  }
  const previous = window.__mdEditorSearch;
  if (previous && previous.revision === request.revision) {
    if (previous.generation > request.generation) {
      return {applied: false, reason: "generation"};
    }
  }
  if (!window.CSS || !CSS.highlights || !window.Highlight) {
    return {applied: false, reason: "unsupported"};
  }
  __MD_EDITOR_TEXT_RUNS__
  const runs = collectTextRuns();
  window.__mdEditorSearch = {...request, runs};
  CSS.highlights.delete("md-editor-search");
  return {...request, applied: true, runs: runs.map(run => run.text)};
})();
"""


_NOTE_SEARCH_RANGES_SCRIPT = r"""
(() => {
  "use strict";
  const request = __MD_EDITOR_PAYLOAD__;
  const content = document.getElementById("content");
  if (!content || !window.previewApi ||
      window.previewApi.metrics().revision !== request.revision) {
    return {applied: false, reason: "revision"};
  }
  const snapshot = window.__mdEditorSearch;
  if (snapshot && snapshot.revision === request.revision) {
    if (snapshot.generation > request.generation) {
      return {applied: false, reason: "generation"};
    }
  }
  if (!window.CSS || !CSS.highlights || !window.Highlight) {
    return {applied: false, reason: "unsupported"};
  }
  const name = "md-editor-search";
  if (!request.ranges.length) {
    CSS.highlights.delete(name);
    window.__mdEditorSearch = {...request, runs: []};
    return {applied: true, count: 0, truncated: false};
  }
  if (!snapshot || snapshot.revision !== request.revision ||
      snapshot.generation !== request.generation) {
    return {applied: false, reason: "snapshot"};
  }
  __MD_EDITOR_TEXT_RUNS__
  const ranges = [];
  for (const [index, start, end] of request.ranges) {
    if (!Number.isInteger(index) || index < 0) continue;
    const range = textRunRange(snapshot.runs[index], start, end);
    if (range) ranges.push(range);
  }
  if (!document.getElementById("md-editor-search-highlight-style")) {
    const style = document.createElement("style");
    style.id = "md-editor-search-highlight-style";
    style.textContent = "::highlight(md-editor-search) {" +
      "background-color:#fff0a8;text-shadow:none;}" +
      ":root[data-theme=dark] ::highlight(md-editor-search) {" +
      "background-color:#665523;}";
    document.head.appendChild(style);
  }
  CSS.highlights.set(name, new Highlight(...ranges));
  return {applied: true, count: ranges.length, truncated: request.truncated};
})();
"""
