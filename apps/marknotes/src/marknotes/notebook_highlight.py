"""Ephemeral library-search highlights for snippets, Qt source and preview text."""

from __future__ import annotations

import json
from collections.abc import Iterable
from html import escape

from .notebook_store import SearchSnippet, find_match_ranges, normalize_text


def ranges_html(text: str, ranges: Iterable[tuple[int, int]]) -> str:
    """Escape user content before applying safe, fixed-format highlight markup."""
    pieces: list[str] = []
    position = 0
    for start, end in sorted(ranges):
        start = max(position, min(len(text), start))
        end = max(start, min(len(text), end))
        if start == end:
            continue
        pieces.append(escape(text[position:start]))
        pieces.append(
            '<span style="background-color:#f4d35e;color:#202020;">'
            + escape(text[start:end])
            + "</span>"
        )
        position = end
    pieces.append(escape(text[position:]))
    return "".join(pieces).replace("\n", "<br>")


def highlight_html(text: str, query: str) -> str:
    return ranges_html(text, find_match_ranges(text, query))


def snippet_html(snippet: SearchSnippet) -> str:
    return ranges_html(snippet.text, snippet.ranges)


def utf16_ranges(text: str, ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Translate Python offsets to QTextCursor positions, including astral emoji."""
    positions = [0]
    for character in text:
        positions.append(positions[-1] + (2 if ord(character) > 0xFFFF else 1))
    return tuple(
        (positions[start], positions[end]) for start, end in ranges if 0 <= start < end <= len(text)
    )


# Full case folding has exceptions beyond JavaScript's per-character lower case.
# This table is populated from Python's Unicode casefold data during development.
_CASEFOLD_EXCEPTIONS: dict[str, str] = {
    "\u00b5": "\u03bc",
    "\u00df": "ss",
    "\u0149": "\u02bcn",
    "\u017f": "s",
    "\u01f0": "j\u030c",
    "\u0345": "\u03b9",
    "\u0390": "\u03b9\u0308\u0301",
    "\u03b0": "\u03c5\u0308\u0301",
    "\u03c2": "\u03c3",
    "\u03d0": "\u03b2",
    "\u03d1": "\u03b8",
    "\u03d5": "\u03c6",
    "\u03d6": "\u03c0",
    "\u03f0": "\u03ba",
    "\u03f1": "\u03c1",
    "\u03f5": "\u03b5",
    "\u0587": "\u0565\u0582",
    "\u13a0": "\u13a0",
    "\u13a1": "\u13a1",
    "\u13a2": "\u13a2",
    "\u13a3": "\u13a3",
    "\u13a4": "\u13a4",
    "\u13a5": "\u13a5",
    "\u13a6": "\u13a6",
    "\u13a7": "\u13a7",
    "\u13a8": "\u13a8",
    "\u13a9": "\u13a9",
    "\u13aa": "\u13aa",
    "\u13ab": "\u13ab",
    "\u13ac": "\u13ac",
    "\u13ad": "\u13ad",
    "\u13ae": "\u13ae",
    "\u13af": "\u13af",
    "\u13b0": "\u13b0",
    "\u13b1": "\u13b1",
    "\u13b2": "\u13b2",
    "\u13b3": "\u13b3",
    "\u13b4": "\u13b4",
    "\u13b5": "\u13b5",
    "\u13b6": "\u13b6",
    "\u13b7": "\u13b7",
    "\u13b8": "\u13b8",
    "\u13b9": "\u13b9",
    "\u13ba": "\u13ba",
    "\u13bb": "\u13bb",
    "\u13bc": "\u13bc",
    "\u13bd": "\u13bd",
    "\u13be": "\u13be",
    "\u13bf": "\u13bf",
    "\u13c0": "\u13c0",
    "\u13c1": "\u13c1",
    "\u13c2": "\u13c2",
    "\u13c3": "\u13c3",
    "\u13c4": "\u13c4",
    "\u13c5": "\u13c5",
    "\u13c6": "\u13c6",
    "\u13c7": "\u13c7",
    "\u13c8": "\u13c8",
    "\u13c9": "\u13c9",
    "\u13ca": "\u13ca",
    "\u13cb": "\u13cb",
    "\u13cc": "\u13cc",
    "\u13cd": "\u13cd",
    "\u13ce": "\u13ce",
    "\u13cf": "\u13cf",
    "\u13d0": "\u13d0",
    "\u13d1": "\u13d1",
    "\u13d2": "\u13d2",
    "\u13d3": "\u13d3",
    "\u13d4": "\u13d4",
    "\u13d5": "\u13d5",
    "\u13d6": "\u13d6",
    "\u13d7": "\u13d7",
    "\u13d8": "\u13d8",
    "\u13d9": "\u13d9",
    "\u13da": "\u13da",
    "\u13db": "\u13db",
    "\u13dc": "\u13dc",
    "\u13dd": "\u13dd",
    "\u13de": "\u13de",
    "\u13df": "\u13df",
    "\u13e0": "\u13e0",
    "\u13e1": "\u13e1",
    "\u13e2": "\u13e2",
    "\u13e3": "\u13e3",
    "\u13e4": "\u13e4",
    "\u13e5": "\u13e5",
    "\u13e6": "\u13e6",
    "\u13e7": "\u13e7",
    "\u13e8": "\u13e8",
    "\u13e9": "\u13e9",
    "\u13ea": "\u13ea",
    "\u13eb": "\u13eb",
    "\u13ec": "\u13ec",
    "\u13ed": "\u13ed",
    "\u13ee": "\u13ee",
    "\u13ef": "\u13ef",
    "\u13f0": "\u13f0",
    "\u13f1": "\u13f1",
    "\u13f2": "\u13f2",
    "\u13f3": "\u13f3",
    "\u13f4": "\u13f4",
    "\u13f5": "\u13f5",
    "\u13f8": "\u13f0",
    "\u13f9": "\u13f1",
    "\u13fa": "\u13f2",
    "\u13fb": "\u13f3",
    "\u13fc": "\u13f4",
    "\u13fd": "\u13f5",
    "\u1c80": "\u0432",
    "\u1c81": "\u0434",
    "\u1c82": "\u043e",
    "\u1c83": "\u0441",
    "\u1c84": "\u0442",
    "\u1c85": "\u0442",
    "\u1c86": "\u044a",
    "\u1c87": "\u0463",
    "\u1c88": "\ua64b",
    "\u1e96": "h\u0331",
    "\u1e97": "t\u0308",
    "\u1e98": "w\u030a",
    "\u1e99": "y\u030a",
    "\u1e9a": "a\u02be",
    "\u1e9b": "\u1e61",
    "\u1e9e": "ss",
    "\u1f50": "\u03c5\u0313",
    "\u1f52": "\u03c5\u0313\u0300",
    "\u1f54": "\u03c5\u0313\u0301",
    "\u1f56": "\u03c5\u0313\u0342",
    "\u1f80": "\u1f00\u03b9",
    "\u1f81": "\u1f01\u03b9",
    "\u1f82": "\u1f02\u03b9",
    "\u1f83": "\u1f03\u03b9",
    "\u1f84": "\u1f04\u03b9",
    "\u1f85": "\u1f05\u03b9",
    "\u1f86": "\u1f06\u03b9",
    "\u1f87": "\u1f07\u03b9",
    "\u1f88": "\u1f00\u03b9",
    "\u1f89": "\u1f01\u03b9",
    "\u1f8a": "\u1f02\u03b9",
    "\u1f8b": "\u1f03\u03b9",
    "\u1f8c": "\u1f04\u03b9",
    "\u1f8d": "\u1f05\u03b9",
    "\u1f8e": "\u1f06\u03b9",
    "\u1f8f": "\u1f07\u03b9",
    "\u1f90": "\u1f20\u03b9",
    "\u1f91": "\u1f21\u03b9",
    "\u1f92": "\u1f22\u03b9",
    "\u1f93": "\u1f23\u03b9",
    "\u1f94": "\u1f24\u03b9",
    "\u1f95": "\u1f25\u03b9",
    "\u1f96": "\u1f26\u03b9",
    "\u1f97": "\u1f27\u03b9",
    "\u1f98": "\u1f20\u03b9",
    "\u1f99": "\u1f21\u03b9",
    "\u1f9a": "\u1f22\u03b9",
    "\u1f9b": "\u1f23\u03b9",
    "\u1f9c": "\u1f24\u03b9",
    "\u1f9d": "\u1f25\u03b9",
    "\u1f9e": "\u1f26\u03b9",
    "\u1f9f": "\u1f27\u03b9",
    "\u1fa0": "\u1f60\u03b9",
    "\u1fa1": "\u1f61\u03b9",
    "\u1fa2": "\u1f62\u03b9",
    "\u1fa3": "\u1f63\u03b9",
    "\u1fa4": "\u1f64\u03b9",
    "\u1fa5": "\u1f65\u03b9",
    "\u1fa6": "\u1f66\u03b9",
    "\u1fa7": "\u1f67\u03b9",
    "\u1fa8": "\u1f60\u03b9",
    "\u1fa9": "\u1f61\u03b9",
    "\u1faa": "\u1f62\u03b9",
    "\u1fab": "\u1f63\u03b9",
    "\u1fac": "\u1f64\u03b9",
    "\u1fad": "\u1f65\u03b9",
    "\u1fae": "\u1f66\u03b9",
    "\u1faf": "\u1f67\u03b9",
    "\u1fb2": "\u1f70\u03b9",
    "\u1fb3": "\u03b1\u03b9",
    "\u1fb4": "\u03ac\u03b9",
    "\u1fb6": "\u03b1\u0342",
    "\u1fb7": "\u03b1\u0342\u03b9",
    "\u1fbc": "\u03b1\u03b9",
    "\u1fbe": "\u03b9",
    "\u1fc2": "\u1f74\u03b9",
    "\u1fc3": "\u03b7\u03b9",
    "\u1fc4": "\u03ae\u03b9",
    "\u1fc6": "\u03b7\u0342",
    "\u1fc7": "\u03b7\u0342\u03b9",
    "\u1fcc": "\u03b7\u03b9",
    "\u1fd2": "\u03b9\u0308\u0300",
    "\u1fd3": "\u03b9\u0308\u0301",
    "\u1fd6": "\u03b9\u0342",
    "\u1fd7": "\u03b9\u0308\u0342",
    "\u1fe2": "\u03c5\u0308\u0300",
    "\u1fe3": "\u03c5\u0308\u0301",
    "\u1fe4": "\u03c1\u0313",
    "\u1fe6": "\u03c5\u0342",
    "\u1fe7": "\u03c5\u0308\u0342",
    "\u1ff2": "\u1f7c\u03b9",
    "\u1ff3": "\u03c9\u03b9",
    "\u1ff4": "\u03ce\u03b9",
    "\u1ff6": "\u03c9\u0342",
    "\u1ff7": "\u03c9\u0342\u03b9",
    "\u1ffc": "\u03c9\u03b9",
    "\uab70": "\u13a0",
    "\uab71": "\u13a1",
    "\uab72": "\u13a2",
    "\uab73": "\u13a3",
    "\uab74": "\u13a4",
    "\uab75": "\u13a5",
    "\uab76": "\u13a6",
    "\uab77": "\u13a7",
    "\uab78": "\u13a8",
    "\uab79": "\u13a9",
    "\uab7a": "\u13aa",
    "\uab7b": "\u13ab",
    "\uab7c": "\u13ac",
    "\uab7d": "\u13ad",
    "\uab7e": "\u13ae",
    "\uab7f": "\u13af",
    "\uab80": "\u13b0",
    "\uab81": "\u13b1",
    "\uab82": "\u13b2",
    "\uab83": "\u13b3",
    "\uab84": "\u13b4",
    "\uab85": "\u13b5",
    "\uab86": "\u13b6",
    "\uab87": "\u13b7",
    "\uab88": "\u13b8",
    "\uab89": "\u13b9",
    "\uab8a": "\u13ba",
    "\uab8b": "\u13bb",
    "\uab8c": "\u13bc",
    "\uab8d": "\u13bd",
    "\uab8e": "\u13be",
    "\uab8f": "\u13bf",
    "\uab90": "\u13c0",
    "\uab91": "\u13c1",
    "\uab92": "\u13c2",
    "\uab93": "\u13c3",
    "\uab94": "\u13c4",
    "\uab95": "\u13c5",
    "\uab96": "\u13c6",
    "\uab97": "\u13c7",
    "\uab98": "\u13c8",
    "\uab99": "\u13c9",
    "\uab9a": "\u13ca",
    "\uab9b": "\u13cb",
    "\uab9c": "\u13cc",
    "\uab9d": "\u13cd",
    "\uab9e": "\u13ce",
    "\uab9f": "\u13cf",
    "\uaba0": "\u13d0",
    "\uaba1": "\u13d1",
    "\uaba2": "\u13d2",
    "\uaba3": "\u13d3",
    "\uaba4": "\u13d4",
    "\uaba5": "\u13d5",
    "\uaba6": "\u13d6",
    "\uaba7": "\u13d7",
    "\uaba8": "\u13d8",
    "\uaba9": "\u13d9",
    "\uabaa": "\u13da",
    "\uabab": "\u13db",
    "\uabac": "\u13dc",
    "\uabad": "\u13dd",
    "\uabae": "\u13de",
    "\uabaf": "\u13df",
    "\uabb0": "\u13e0",
    "\uabb1": "\u13e1",
    "\uabb2": "\u13e2",
    "\uabb3": "\u13e3",
    "\uabb4": "\u13e4",
    "\uabb5": "\u13e5",
    "\uabb6": "\u13e6",
    "\uabb7": "\u13e7",
    "\uabb8": "\u13e8",
    "\uabb9": "\u13e9",
    "\uabba": "\u13ea",
    "\uabbb": "\u13eb",
    "\uabbc": "\u13ec",
    "\uabbd": "\u13ed",
    "\uabbe": "\u13ee",
    "\uabbf": "\u13ef",
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
    "\ufb13": "\u0574\u0576",
    "\ufb14": "\u0574\u0565",
    "\ufb15": "\u0574\u056b",
    "\ufb16": "\u057e\u0576",
    "\ufb17": "\u0574\u056d",
}


def preview_highlight_script(
    query: str,
    revision: int,
    *,
    generation: int = 0,
    note_id: str = "",
    scroll_to_first: bool = False,
) -> str:
    """Generate guarded JavaScript using CSS Highlights, without changing the DOM.

    The caller uses the preview's globally increasing render revision and applies
    this after ``ready``. CSS Highlight ranges preserve all source anchors, event
    handlers and layout; clearing a query only removes this helper's highlight.
    """
    payload = json.dumps(
        {
            "query": normalize_text(query),
            "revision": int(revision),
            "generation": int(generation),
            "noteId": note_id,
            "scroll": bool(scroll_to_first),
            "fold": _CASEFOLD_EXCEPTIONS,
        },
        ensure_ascii=True,
    )
    return _PREVIEW_SCRIPT.replace("__MARKNOTES_PAYLOAD__", payload)


_PREVIEW_SCRIPT = r"""
(() => {
  "use strict";
  const request = __MARKNOTES_PAYLOAD__;
  const content = document.getElementById("content");
  if (!content || !window.previewApi ||
      window.previewApi.metrics().revision !== request.revision) {
    return {applied: false, reason: "revision"};
  }
  const previous = window.__marknotesSearchHighlight;
  if (previous && previous.revision === request.revision &&
      previous.generation > request.generation) {
    return {applied: false, reason: "generation"};
  }
  if (!window.CSS || !CSS.highlights || !window.Highlight || !Intl.Segmenter) {
    return {applied: false, reason: "unsupported"};
  }
  window.__marknotesSearchHighlight = request;
  const name = "marknotes-library-search";
  CSS.highlights.delete(name);
  if (!request.query) return {applied: true, count: 0};
  if (!document.getElementById("marknotes-search-highlight-style")) {
    const style = document.createElement("style");
    style.id = "marknotes-search-highlight-style";
    style.textContent = "::highlight(marknotes-library-search) {" +
      "background-color:#f4d35e;color:#202020;text-shadow:none;}";
    document.head.appendChild(style);
  }
  const segmenter = new Intl.Segmenter(undefined, {granularity: "grapheme"});
  const fold = text => Array.from(text.normalize("NFKC"),
    character => Object.hasOwn(request.fold, character) ? request.fold[character]
      : character.toLowerCase()).join("");
  const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || !node.data ||
          parent.closest("script,style,textarea,button,[hidden],.katex-mathml") ||
          !parent.getClientRects().length || getComputedStyle(parent).visibility === "hidden") {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    }
  });
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
    run.nodes.push({node, start: run.text.length, end: run.text.length + node.data.length});
    run.text += node.data;
  }
  const ranges = [];
  let truncated = false;
  for (const current of runs) {
    let normalized = "";
    const starts = [];
    const ends = [];
    for (const piece of segmenter.segment(current.text)) {
      const transformed = fold(piece.segment);
      normalized += transformed;
      for (let i = 0; i < transformed.length; i++) {
        starts.push(piece.index);
        ends.push(piece.index + piece.segment.length);
      }
    }
    let offset = 0;
    let lastEnd = -1;
    while ((offset = normalized.indexOf(request.query, offset)) >= 0) {
      const start = starts[offset];
      const end = ends[offset + request.query.length - 1];
      offset += request.query.length;
      if (start < lastEnd) continue;
      lastEnd = end;
      const first = current.nodes.find(part => part.end > start);
      const last = current.nodes.find(part => part.end >= end);
      if (!first || !last) continue;
      const range = new Range();
      range.setStart(first.node, start - first.start);
      range.setEnd(last.node, end - last.start);
      ranges.push(range);
      if (ranges.length >= 5000) { truncated = true; break; }
    }
    if (truncated) break;
  }
  CSS.highlights.set(name, new Highlight(...ranges));
  if (request.scroll && ranges.length) {
    const rect = ranges[0].getBoundingClientRect();
    window.scrollTo({top: window.scrollY + rect.top - window.innerHeight / 3,
      left: window.scrollX, behavior: "instant"});
  }
  return {applied: true, count: ranges.length, truncated};
})();
"""
