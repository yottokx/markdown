# Offline preview libraries

This directory contains unmodified browser distribution files downloaded from the
publishers' npm packages. The editor does not download these libraries at runtime.

| Library | Pinned version | Classic script | Browser global | License |
| --- | --- | --- | --- | --- |
| KaTeX | 0.18.7 | `katex/katex.min.js` | `window.katex` | MIT |
| Mermaid | 11.17.2 | `mermaid/mermaid.min.js` | `window.mermaid` | MIT, with bundled dependency licenses |

Load `katex/katex.min.css` alongside its unchanged `katex/fonts/` directory. All 60
font files referenced by the upstream distribution are included. No auto-render
extension is required when rendering the editor's parsed math nodes with the
KaTeX API.

Mermaid uses its official self-contained classic bundle. It exports
`globalThis.mermaid`, includes its normal diagram implementations, and contains no
JavaScript `import()` calls or external chunk references. No ES-module loading or
`file://` dynamic-import permissions are required. Optional externally registered
plugins/icon packs are not included. Mermaid's generated SVG and asynchronous
render lifecycle are handled by the editor's own preview code.

`katex/LICENSE` and `mermaid/LICENSE` are the upstream project licenses. The
Mermaid bundle retains the publishers' original embedded notices. Additional full
license texts for the 60 exact bundled dependency versions identified in Mermaid's
official source map are under `mermaid/licenses/`; they include MIT, ISC, BSD, and
DOMPurify's Apache-2.0/MPL-2.0 terms. FastDom ships its complete license in README,
so that upstream README is preserved intact in its license directory.

## Provenance and verification

`MANIFEST.json` records the pinned versions, official archive URLs, npm SHA-512
integrity values, archive SHA-256 values, and every vendored file's byte count and
SHA-256. Main browser entrypoints and fonts are byte-for-byte official artifacts;
no local minification or bundling was performed.

From the repository root, verify the inventory without accessing the network:

```powershell
uv run python tools/update_preview_vendor.py
```

To reproduce the installed assets and license texts from the pinned official npm
archives:

```powershell
uv run python tools/update_preview_vendor.py --refresh
```

The maintenance script uses only Python's standard library. It does not install
Node packages or create `node_modules`. Updating to another upstream release
requires reviewing and changing the explicit version and archive SHA-256 pins in
the script, then rerunning the preview and offline-rendering tests.

Official sources checked when these versions were selected:

- [KaTeX browser installation and self-hosting](https://katex.org/docs/browser)
- [KaTeX 0.18.7 registry metadata](https://registry.npmjs.org/katex/0.18.7)
- [KaTeX 0.18.7 npm archive](https://registry.npmjs.org/katex/-/katex-0.18.7.tgz)
- [Mermaid user guide](https://mermaid.js.org/intro/getting-started.html)
- [Mermaid 11.17.2 registry metadata](https://registry.npmjs.org/mermaid/11.17.2)
- [Mermaid 11.17.2 npm archive](https://registry.npmjs.org/mermaid/-/mermaid-11.17.2.tgz)
