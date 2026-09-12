/* Source coordinates are zero-based logical lines, with fractional progress.
 * All scroll operations use the same monotone, measured anchor map. */
(() => {
  "use strict";

  const content = document.getElementById("content");
  const spacer = document.getElementById("eof-spacer");
  const state = {
    revision: -1,
    lineCount: 1,
    maxSource: 0,
    anchors: [{ source: 0, y: 0 }],
    source: 0,
    suppressedAtY: null,
    layoutFrame: 0,
    reportFrame: 0,
    pendingReady: false,
    pendingViewRestore: null,
    viewRestoreToken: -1,
    rendering: false,
    renderGeneration: 0,
    bridge: null,
  };

  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const lastLine = () => Math.max(0, state.lineCount - 1);
  const maxSource = () => state.maxSource;
  const scrollLimit = () => Math.max(0, document.documentElement.scrollHeight - window.innerHeight);

  function interpolate(points, value, key, resultKey) {
    if (!points.length) return 0;
    if (value <= points[0][key]) return points[0][resultKey];
    if (value >= points[points.length - 1][key]) return points[points.length - 1][resultKey];
    let low = 0;
    let high = points.length - 1;
    while (high - low > 1) {
      const middle = (low + high) >> 1;
      if (points[middle][key] <= value) low = middle;
      else high = middle;
    }
    const a = points[low];
    const b = points[high];
    const span = b[key] - a[key];
    if (span <= 0) return b[resultKey];
    return a[resultKey] + (b[resultKey] - a[resultKey]) * ((value - a[key]) / span);
  }

  const yForSource = source => interpolate(state.anchors, clamp(source, 0, maxSource()), "source", "y");
  const sourceForY = y => clamp(interpolate(state.anchors, y, "y", "source"), 0, maxSource());

  function moveToSource(source) {
    state.source = clamp(Number(source) || 0, 0, maxSource());
    const target = clamp(yForSource(state.source), 0, scrollLimit());
    window.scrollTo({ top: target, left: window.scrollX, behavior: "instant" });
    // Keep only an expected position, never a timed suppression window. A wheel
    // or scrollbar gesture to another position is reported immediately.
    state.suppressedAtY = window.scrollY;
  }

  function measureAnchors() {
    const lineHeight = parseFloat(getComputedStyle(content).lineHeight) || 26;
    const candidates = [];
    let finalLeaf = null;
    for (const element of content.querySelectorAll("[data-source-line][data-source-end]")) {
      const source = Number(element.dataset.sourceLine);
      const end = Number(element.dataset.sourceEnd);
      if (!Number.isFinite(source) || !Number.isFinite(end) || end <= source) continue;
      const rect = element.getBoundingClientRect();
      // display:none nodes must not flatten all subsequent content anchors.
      if (!element.getClientRects().length) continue;
      const top = rect.top + window.scrollY;
      const bottom = rect.bottom + window.scrollY;
      if (source <= lastLine() && end > lastLine() && rect.height > 0) {
        finalLeaf = { element, rect, bottom };
      }
      let depth = 0;
      for (let parent = element.parentElement; parent && parent !== content; parent = parent.parentElement) depth++;
      candidates.push({ source, y: top, priority: 10000 + depth });
      candidates.push({ source: end, y: bottom, priority: depth });
    }
    candidates.sort((a, b) => a.source - b.source || b.priority - a.priority || a.y - b.y);
    const points = [];
    for (const candidate of candidates) {
      if (candidate.source < 0 || candidate.source > state.lineCount) continue;
      if (points.length && candidate.source === points[points.length - 1].source) continue;
      points.push({ source: candidate.source, y: Math.max(0, candidate.y) });
    }
    if (!points.length) {
      points.push({ source: 0, y: 0 });
    } else if (points[0].source > 0) {
      points.unshift({ source: 0, y: 0 });
    } else {
      // Keep the document's initial padding visible at the beginning.
      points[0].y = 0;
    }

    // Multiple invisible markers (fences, table separators, nested containers)
    // can share a visual boundary. Give each source interval a tiny positive
    // extent so both maps remain invertible and never move backwards.
    for (let index = 1; index < points.length; index++) {
      const previous = points[index - 1];
      points[index].y = Math.max(points[index].y, previous.y + (points[index].source - previous.source));
    }

    const last = lastLine();
    const finalPoint = points[points.length - 1];
    if (finalPoint.source < state.lineCount) {
      points.push({
        source: state.lineCount,
        y: finalPoint.y + (state.lineCount - finalPoint.source) * lineHeight,
      });
    }
    const lastLineY = interpolate(points, last, "source", "y");
    let terminalY = lastLineY;
    if (finalLeaf) {
      const { element, rect, bottom } = finalLeaf;
      const style = getComputedStyle(element);
      let visibleLine = parseFloat(style.lineHeight) || lineHeight;
      const extraHeight = value => (parseFloat(value) || 0);
      let boxTop = extraHeight(style.paddingTop) + extraHeight(style.borderTopWidth);
      let boxBottom = extraHeight(style.paddingBottom) + extraHeight(style.borderBottomWidth);
      if (element.tagName === "TR") {
        // A normal table row includes cell padding and borders. Those are not
        // another visual text line; a genuinely wrapped row can still scroll.
        for (const cell of element.cells) {
          const cellStyle = getComputedStyle(cell);
          visibleLine = Math.max(visibleLine, parseFloat(cellStyle.lineHeight) || lineHeight);
          boxTop = Math.max(boxTop, extraHeight(cellStyle.paddingTop) + extraHeight(cellStyle.borderTopWidth));
          boxBottom = Math.max(boxBottom, extraHeight(cellStyle.paddingBottom) + extraHeight(cellStyle.borderBottomWidth));
        }
      }
      if (rect.height - boxTop - boxBottom > visibleLine + 1) {
        terminalY = Math.max(lastLineY, bottom - boxBottom - visibleLine);
      }
    }
    const endY = interpolate(points, state.lineCount, "source", "y");
    terminalY = Math.min(terminalY, endY - (terminalY > lastLineY ? 0.001 : 0));
    state.maxSource = Math.max(last, Math.min(
      state.lineCount - 1e-6, interpolate(points, terminalY, "y", "source")
    ));
    state.anchors = points.filter(point => point.source < state.maxSource);
    state.anchors.push({ source: state.maxSource, y: terminalY });

    // The spacer exists only in this HTML shell. The Markdown source and
    // rendered fragment remain unchanged, including trailing blank lines.
    const spacerTop = spacer.getBoundingClientRect().top + window.scrollY;
    spacer.style.height = `${Math.max(0, Math.ceil(terminalY + window.innerHeight - spacerTop))}px`;
  }

  function scheduleLayout() {
    if (state.layoutFrame) return;
    state.layoutFrame = requestAnimationFrame(() => {
      state.layoutFrame = 0;
      const restore = state.pendingViewRestore;
      const preservedSource = restore && restore.revision === state.revision
        ? restore.source : state.source;
      measureAnchors();
      moveToSource(preservedSource);
      if (state.pendingReady && !state.rendering && state.bridge) {
        state.pendingReady = false;
        state.bridge.documentReady(state.revision);
      }
      if (restore && restore.revision === state.revision && !state.rendering && state.bridge) {
        state.pendingViewRestore = null;
        state.bridge.viewRestored(state.source, state.revision, restore.token);
      }
    });
  }

  function renderSpecialContent() {
    const generation = ++state.renderGeneration;
    const isCurrent = () => generation === state.renderGeneration;
    state.rendering = true;
    state.pendingReady = true;
    Promise.resolve(window.richContent.render(
      content, document.documentElement.dataset.theme === "dark", isCurrent, scheduleLayout
    )).catch(error => {
      // Individual TeX/diagram errors are shown at their source placeholders.
      console.error("Special-content rendering failed", error);
    }).finally(() => {
      if (!isCurrent()) return;
      state.rendering = false;
      scheduleLayout();
    });
  }

  function installCodeCopyButtons() {
    const revision = state.revision;
    for (const code of content.querySelectorAll("pre > code[data-code-source]")) {
      const pre = code.parentElement;
      const wrapper = document.createElement("div");
      wrapper.className = "code-block";
      wrapper.dataset.previewCodeUi = "block";
      pre.replaceWith(wrapper);
      wrapper.append(pre);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "code-copy-button";
      button.dataset.previewCodeUi = "copy";
      button.title = "コードをコピー";
      button.setAttribute("aria-label", "コードをコピー");
      // These static icons are part of the local shell, never document HTML.
      button.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><g class="copy-glyph"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></g><path class="copied-glyph" d="m5 12 4 4L19 6"/></svg>';
      const source = code.dataset.codeSource;
      button.disabled = !source;
      if (!source) {
        button.title = "コピーするコードがありません";
        button.setAttribute("aria-label", button.title);
      }
      let resetTimer = 0;
      let pending = false;
      button.addEventListener("click", () => {
        if (pending || !button.isConnected || revision !== state.revision || !state.bridge) return;
        pending = true;
        state.bridge.copyCode(source, revision, success => {
          pending = false;
          if (!button.isConnected || revision !== state.revision) return;
          clearTimeout(resetTimer);
          button.classList.toggle("is-copied", success);
          button.title = success ? "コピーしました" : "コピーできませんでした";
          button.setAttribute("aria-label", button.title);
          resetTimer = setTimeout(() => {
            if (!button.isConnected) return;
            button.classList.remove("is-copied");
            button.title = "コードをコピー";
            button.setAttribute("aria-label", button.title);
          }, 1800);
        });
      });
      wrapper.append(button);
    }
  }

  function setDocument(payload) {
    const revision = Number(payload.revision);
    if (!Number.isInteger(revision) || revision < state.revision) return false;
    state.revision = revision;
    if (state.pendingViewRestore && state.pendingViewRestore.revision < revision) {
      state.pendingViewRestore = null;
    }
    state.lineCount = Math.max(1, Number(payload.lineCount) || 1);
    state.source = clamp(state.source, 0, state.lineCount - 1e-6);
    // Resolve images ourselves instead of changing <base>, which would also
    // redirect the shell's own resource URLs and in-document fragment links.
    content.innerHTML = payload.html || "";
    installCodeCopyButtons();
    if (payload.baseUrl) {
      for (const img of content.querySelectorAll("img[src]")) {
        try {
          const url = new URL(img.getAttribute("src"), payload.baseUrl);
          if (url.protocol === "file:" || url.protocol === "data:") img.src = url.href;
        } catch (_) { /* Broken image paths are rendered as broken images. */ }
      }
      for (const link of content.querySelectorAll("a[href]")) {
        const href = link.getAttribute("href");
        if (href && !href.startsWith("#")) {
          try { link.href = new URL(href, payload.baseUrl).href; } catch (_) { /* Keep original. */ }
        }
      }
    }
    renderSpecialContent();
    scheduleLayout();
    return true;
  }

  window.addEventListener("scroll", () => {
    if (state.pendingViewRestore) return;
    const y = window.scrollY;
    if (state.suppressedAtY !== null && Math.abs(y - state.suppressedAtY) <= 0.75) return;
    state.suppressedAtY = null;
    state.source = sourceForY(y);
    if (state.reportFrame) return;
    state.reportFrame = requestAnimationFrame(() => {
      state.reportFrame = 0;
      if (state.pendingViewRestore) return;
      if (state.suppressedAtY !== null && Math.abs(window.scrollY - state.suppressedAtY) <= 0.75) return;
      state.source = sourceForY(window.scrollY);
      if (state.bridge) state.bridge.sourceScrolled(state.source, state.revision);
    });
  }, { passive: true });
  window.addEventListener("resize", scheduleLayout);
  content.addEventListener("load", scheduleLayout, true);
  content.addEventListener("error", scheduleLayout, true);
  new ResizeObserver(scheduleLayout).observe(content);
  if (document.fonts) document.fonts.ready.then(scheduleLayout);

  window.previewApi = {
    setDocument,
    setTheme(dark) {
      const theme = dark ? "dark" : "light";
      if (document.documentElement.dataset.theme !== theme) {
        document.documentElement.dataset.theme = theme;
        renderSpecialContent();
      }
      scheduleLayout();
    },
    restoreView(source, revision, token) {
      source = Number(source);
      revision = Number(revision);
      token = Number(token);
      if (revision !== state.revision || !Number.isFinite(source)
          || !Number.isInteger(token) || token < state.viewRestoreToken) return false;
      state.viewRestoreToken = token;
      state.pendingViewRestore = {
        source: clamp(source, 0, state.lineCount - 1e-6), revision, token
      };
      // A scroll report queued before hiding must not survive the restore.
      if (state.reportFrame) {
        cancelAnimationFrame(state.reportFrame);
        state.reportFrame = 0;
      }
      scheduleLayout();
      return true;
    },
    scrollToSource(source, revision) {
      if (Number(revision) !== state.revision || state.pendingViewRestore) return false;
      if (state.layoutFrame && state.pendingReady) {
        state.source = clamp(Number(source) || 0, 0, state.lineCount - 1e-6);
      } else {
        moveToSource(source);
      }
      return true;
    },
    metrics() {
      return {
        revision: state.revision,
        rendering: state.rendering,
        lineCount: state.lineCount,
        maxSource: state.maxSource,
        anchors: state.anchors.map(point => ({ ...point })),
        source: state.source,
        measuredSource: sourceForY(window.scrollY),
        scrollY: window.scrollY,
        maxScroll: scrollLimit(),
        viewportHeight: window.innerHeight,
        contentHeight: content.getBoundingClientRect().height,
      };
    },
  };

  if (window.qt && window.qt.webChannelTransport && typeof QWebChannel !== "undefined") {
    new QWebChannel(window.qt.webChannelTransport, channel => {
      state.bridge = channel.objects.previewBridge;
      state.bridge.shellReady();
      if (state.pendingReady || state.pendingViewRestore) scheduleLayout();
    });
  }
})();
