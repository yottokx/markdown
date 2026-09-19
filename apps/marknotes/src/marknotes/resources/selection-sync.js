/* Bidirectional selection mapping. Only the active pane owns user interaction. */
(() => {
  "use strict";

  window.createSelectionSync = (content, notify) => {
    let revision = -1;
    let sequence = 0;
    let sourceLength = 0;
    let definitions = [];
    let mapped = [];
    let ready = false;
    let desired = null;
    let observed = null;
    let browserOwnsSelection = false;
    let reportFrame = 0;

    const integer = value => Number.isSafeInteger(value);
    const point = (node, offset) => ({ node, offset });
    const compare = (left, right) => {
      if (left.node === right.node) return left.offset - right.offset;
      const a = new Range();
      const b = new Range();
      a.setStart(left.node, left.offset);
      a.collapse(true);
      b.setStart(right.node, right.offset);
      b.collapse(true);
      return a.compareBoundaryPoints(Range.START_TO_START, b);
    };
    const startOf = range => point(range.startContainer, range.startOffset);
    const endOf = range => point(range.endContainer, range.endOffset);
    const fingerprint = selection => selection ? {
      anchor: selection.anchorNode, anchorOffset: selection.anchorOffset,
      focus: selection.focusNode, focusOffset: selection.focusOffset,
      count: selection.rangeCount,
    } : null;
    const sameSelection = (left, right) => left && right &&
      left.anchor === right.anchor && left.anchorOffset === right.anchorOffset &&
      left.focus === right.focus && left.focusOffset === right.focusOffset &&
      left.count === right.count;
    const visible = element => element && element.getClientRects().length &&
      !element.closest("button,script,style,textarea,[hidden],.katex-mathml") &&
      getComputedStyle(element).visibility !== "hidden";

    function clearSelection() {
      const selection = window.getSelection();
      if (selection) selection.removeAllRanges();
      observed = fingerprint(selection);
    }

    function textParts(element) {
      const parts = [];
      let length = 0;
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          return node.data && visible(node.parentElement)
            ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        },
      });
      let node;
      while ((node = walker.nextNode())) {
        parts.push({ node, start: length, end: length + node.length });
        length += node.length;
      }
      return { parts, length };
    }

    function domPoint(entry, offset, end) {
      const part = end
        ? entry.parts.findLast(item => item.start < offset)
        : entry.parts.find(item => item.end > offset);
      if (!part) return null;
      return point(part.node, Math.max(0, Math.min(part.node.length, offset - part.start)));
    }

    function offsetAt(entry, boundary) {
      for (const part of entry.parts) {
        if (part.node === boundary.node) {
          return part.start + Math.max(0, Math.min(part.node.length, boundary.offset));
        }
        if (compare(boundary, point(part.node, 0)) <= 0) return part.start;
        if (compare(boundary, point(part.node, part.node.length)) < 0) return part.start;
      }
      return entry.length;
    }

    function rangeFor(entry, segment) {
      const range = new Range();
      if (entry.atomic) {
        if (entry.length) range.selectNodeContents(entry.element);
        else range.selectNode(entry.element);
      } else {
        const start = domPoint(entry, segment[0], false);
        const end = domPoint(entry, segment[1], true);
        if (!start || !end) return null;
        range.setStart(start.node, start.offset);
        range.setEnd(end.node, end.offset);
      }
      return range;
    }

    function sourceForBoundary(entry, segment, boundary, end) {
      if (entry.atomic) return end ? segment[3] : segment[2];
      const offset = Math.max(segment[0], Math.min(segment[1], offsetAt(entry, boundary)));
      if (segment[1] - segment[0] === segment[3] - segment[2]) {
        return segment[2] + offset - segment[0];
      }
      if (offset === segment[0]) return segment[2];
      if (offset === segment[1]) return segment[3];
      return end ? segment[3] : segment[2];
    }

    function sourceSelection(selection) {
      if (!selection || selection.rangeCount !== 1) {
        return [-1, -1];
      }
      const range = selection.getRangeAt(0);
      // Select All may use body/element boundary points outside the content
      // element. Intersect mapped ranges instead of rejecting those endpoints.
      if (!range.intersectsNode(content)) return [-1, -1];
      const start = startOf(range);
      const end = endOf(range);
      let first = null;
      let last = null;
      for (const entry of mapped) {
        for (const segment of entry.segments) {
          const current = rangeFor(entry, segment);
          if (!current) continue;
          const currentStart = startOf(current);
          const currentEnd = endOf(current);
          if (range.collapsed) {
            if (compare(start, currentStart) >= 0 && compare(start, currentEnd) <= 0) {
              const caret = sourceForBoundary(entry, segment, start,
                entry.atomic && compare(start, currentEnd) === 0);
              return [caret, caret];
            }
            continue;
          }
          if (compare(end, currentStart) <= 0 || compare(start, currentEnd) >= 0) continue;
          const low = sourceForBoundary(entry, segment,
            compare(start, currentStart) > 0 ? start : currentStart, false);
          const high = sourceForBoundary(entry, segment,
            compare(end, currentEnd) < 0 ? end : currentEnd, true);
          first = first === null ? low : Math.min(first, low);
          last = last === null ? high : Math.max(last, high);
        }
      }
      if (first === null || last === null) return [-1, -1];
      const backwards = compare(point(selection.anchorNode, selection.anchorOffset),
        point(selection.focusNode, selection.focusOffset)) > 0;
      return backwards ? [last, first] : [first, last];
    }

    function applyDesired() {
      if (!ready || !desired) return;
      const { anchor, position, scroll } = desired;
      if (anchor === position || anchor < 0 || position < 0) {
        clearSelection();
        return;
      }
      const low = Math.min(anchor, position);
      const high = Math.max(anchor, position);
      const ranges = [];
      for (const entry of mapped) {
        for (const segment of entry.segments) {
          if (high <= segment[2] || low >= segment[3]) continue;
          let selected = segment;
          if (!entry.atomic && segment[1] - segment[0] === segment[3] - segment[2]) {
            selected = [segment[0] + Math.max(low, segment[2]) - segment[2],
              segment[0] + Math.min(high, segment[3]) - segment[2], segment[2], segment[3]];
          }
          const range = rangeFor(entry, selected);
          if (range && !range.collapsed) ranges.push(range);
        }
      }
      if (!ranges.length) {
        clearSelection();
        return;
      }
      ranges.sort((a, b) => compare(startOf(a), startOf(b)));
      const start = startOf(ranges[0]);
      let end = endOf(ranges[0]);
      for (const range of ranges) if (compare(endOf(range), end) > 0) end = endOf(range);
      const selection = window.getSelection();
      const x = window.scrollX;
      const y = window.scrollY;
      if (anchor > position) selection.setBaseAndExtent(end.node, end.offset, start.node, start.offset);
      else selection.setBaseAndExtent(start.node, start.offset, end.node, end.offset);
      observed = fingerprint(selection);
      if (scroll) {
        const focus = new Range();
        const focusPoint = anchor > position ? start : end;
        focus.setStart(focusPoint.node, focusPoint.offset);
        focus.collapse(true);
        const rect = focus.getBoundingClientRect();
        if (rect.top < 0 || rect.bottom > window.innerHeight) {
          window.scrollTo({ left: x, top: y + rect.top - window.innerHeight / 3,
            behavior: "instant" });
        }
      } else if (window.scrollX !== x || window.scrollY !== y) {
        window.scrollTo({ left: x, top: y, behavior: "instant" });
      }
    }

    function rebuild() {
      const elements = new Map();
      for (const element of content.querySelectorAll("[data-selection-id]")) {
        const id = element.dataset.selectionId;
        elements.set(id, elements.has(id) ? null : element);
      }
      mapped = [];
      for (const definition of definitions) {
        if (!definition || typeof definition.id !== "string") continue;
        const element = elements.get(definition.id);
        if (!visible(element)) continue;
        const entry = { element, ...textParts(element), atomic: definition.atomic === true };
        if (entry.atomic) {
          const { start, end } = definition;
          if (!integer(start) || !integer(end) || start < 0 || start >= end || end > sourceLength) {
            continue;
          }
          entry.segments = [[0, entry.length, start, end]];
        } else {
          entry.segments = (Array.isArray(definition.segments) ? definition.segments : [])
            .filter(part => Array.isArray(part) && part.length === 4 && part.every(integer) &&
              part[0] >= 0 && part[0] < part[1] && part[1] <= entry.length &&
              part[2] >= 0 && part[2] < part[3] && part[3] <= sourceLength);
        }
        if (entry.segments.length) mapped.push(entry);
      }
      ready = true;
      applyDesired();
    }

    function reportSelection(selection) {
      observed = fingerprint(selection);
      const [anchor, position] = sourceSelection(selection);
      desired = { anchor, position, scroll: false };
      browserOwnsSelection = document.hasFocus();
      notify(anchor, position, revision, sequence);
    }

    window.addEventListener("blur", () => { browserOwnsSelection = false; });
    document.addEventListener("selectionchange", () => {
      if (!ready || reportFrame) return;
      reportFrame = requestAnimationFrame(() => {
        reportFrame = 0;
        if (!ready) return;
        const selection = window.getSelection();
        const current = fingerprint(selection);
        if (sameSelection(current, observed)) return;
        reportSelection(selection);
      });
    });

    return {
      setDocument(map, length, nextRevision) {
        if (reportFrame) cancelAnimationFrame(reportFrame);
        reportFrame = 0;
        ready = false;
        revision = nextRevision;
        sourceLength = integer(length) && length >= 0 ? length : 0;
        definitions = Array.isArray(map) ? map : [];
        mapped = [];
        desired = null;
        browserOwnsSelection = false;
        clearSelection();
      },
      beginRender() { ready = false; },
      rebuild,
      apply(anchor, position, requestRevision, requestSequence, scroll = false) {
        if (requestRevision !== revision || !integer(requestSequence) || requestSequence < sequence ||
            !integer(anchor) || !integer(position) || anchor < 0 || position < 0 ||
            anchor > sourceLength || position > sourceLength) return false;
        sequence = requestSequence;
        const selection = window.getSelection();
        // A queued source update may arrive after a new browser drag. Register
        // its sequence, but send the newer native range back without replacing
        // it. Merely focusing the preview does not claim selection ownership.
        if (ready && document.hasFocus() && (browserOwnsSelection ||
            !sameSelection(fingerprint(selection), observed))) {
          reportSelection(selection);
          return true;
        }
        desired = { anchor, position, scroll: Boolean(scroll) };
        applyDesired();
        return true;
      },
    };
  };
})();
