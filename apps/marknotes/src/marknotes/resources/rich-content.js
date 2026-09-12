/* Render only parser-generated placeholders, keeping raw document HTML inert. */
(() => {
  "use strict";

  const sources = new WeakMap();
  const cache = new Map();
  let queue = Promise.resolve();
  let nextId = 0;

  function sourceOf(node) {
    if (!sources.has(node)) sources.set(node, node.textContent);
    return sources.get(node);
  }

  function reportError(node, source, label, error) {
    const message = String(error?.message || error || "描画ライブラリを読み込めませんでした");
    node.replaceChildren();
    node.classList.add("render-error");
    node.dataset.renderState = "error";
    node.setAttribute("title", message);
    const detail = document.createElement("span");
    detail.className = "render-error-message";
    detail.textContent = `${label}を描画できません: ${message.slice(0, 350)}`;
    const original = document.createElement(node.dataset.renderKind === "math-inline" ? "code" : "pre");
    original.className = "render-error-source";
    original.textContent = source;
    node.append(detail, original);
  }

  function mermaidOptions(dark) {
    return {
      startOnLoad: false,
      securityLevel: "strict",
      suppressErrorRendering: true,
      theme: dark ? "dark" : "default",
      fontFamily: '"Segoe UI", "Yu Gothic UI", "Meiryo", sans-serif',
      htmlLabels: false,
      flowchart: { htmlLabels: false, useMaxWidth: true },
      maxTextSize: 100000,
      maxEdges: 1000,
      // Diagram directives must not loosen trust or inject CSS into the shell.
      secure: ["secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges",
        "suppressErrorRendering", "theme", "themeCSS", "themeVariables", "fontFamily",
        "htmlLabels", "flowchart"],
    };
  }

  if (window.mermaid) window.mermaid.initialize(mermaidOptions(false));

  async function drawDiagram(node, source, dark, ordinal, isCurrent, onLayout) {
    if (!isCurrent()) return;
    const key = JSON.stringify([dark, ordinal, source]);
    let svg = cache.get(key);
    if (svg === undefined) {
      if (!window.mermaid) throw new Error("Mermaidを読み込めませんでした");
      window.mermaid.initialize(mermaidOptions(dark));
      const id = `md-diagram-${++nextId}`;
      // Mermaid needs an attached measuring container. Keep it outside the
      // scrolling document, with a real width, and remove it on every outcome.
      const host = document.createElement("div");
      host.className = "diagram-measuring-host";
      host.style.width = `${Math.max(240, node.clientWidth || 640)}px`;
      document.body.append(host);
      try {
        ({ svg } = await window.mermaid.render(id, source, host));
      } finally {
        host.remove();
      }
      if (!isCurrent()) return;
      // Separate ordinal keys avoid duplicate SVG IDs in repeated diagrams.
      cache.set(key, svg);
      while (cache.size > 32) cache.delete(cache.keys().next().value);
    }
    if (!isCurrent() || !node.isConnected) return;
    node.innerHTML = svg;
    node.classList.remove("render-error");
    node.removeAttribute("title");
    node.dataset.renderState = "ready";
    node.dataset.renderTheme = dark ? "dark" : "light";
    onLayout();
  }

  async function render(root, dark, isCurrent, onLayout) {
    const diagrams = [];
    let ordinal = 0;
    for (const node of root.querySelectorAll("[data-render-kind]")) {
      if (!isCurrent()) return;
      const source = sourceOf(node);
      const kind = node.dataset.renderKind;
      if (kind === "mermaid") {
        const index = ordinal++;
        node.dataset.renderState = "pending";
        // A single queue prevents global Mermaid configuration from changing
        // underneath an in-flight diagram; obsolete requests are skipped.
        const job = queue.then(async () => {
          if (!isCurrent()) return;
          try {
            await drawDiagram(node, source, dark, index, isCurrent, onLayout);
          } catch (error) {
            if (isCurrent() && node.isConnected) {
              reportError(node, source, "Mermaid", error);
              onLayout();
            }
          }
        });
        queue = job.catch(() => {});
        diagrams.push(job);
      } else if (kind === "math-inline" || kind === "math-block") {
        try {
          if (!window.katex) throw new Error("KaTeXを読み込めませんでした");
          window.katex.render(source, node, {
            displayMode: kind === "math-block",
            output: "htmlAndMathml",
            throwOnError: true,
            trust: false,
            strict: "ignore",
            maxExpand: 1000,
            maxSize: 100,
            macros: {},
          });
          node.classList.remove("render-error");
          node.removeAttribute("title");
          node.dataset.renderState = "ready";
        } catch (error) {
          reportError(node, source, "数式", error);
        }
      }
    }
    onLayout();
    await Promise.all(diagrams);
    if (document.fonts) await document.fonts.ready;
  }

  window.richContent = { render };
})();
