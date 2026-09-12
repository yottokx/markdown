"""Self-contained, script-free export of rendered Markdown and its dependencies."""

from __future__ import annotations

import base64
import binascii
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from pathlib import Path

import tinycss2
from bs4 import BeautifulSoup
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImageReader

from md_editor.image_sources import parse_srcset

RESOURCE_DIR = Path(__file__).resolve().parent / "resources"
MAX_RESOURCE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_RESOURCES = 256
MAX_SECONDS = 30
MAX_PIXELS = 40_000_000
MAX_DEPTH = 24
_BAD_ELEMENTS = {"script", "iframe", "object", "embed", "applet", "base", "meta", "template"}
_REMOVE_CLASSES = {"code-boundary", "fence-boundary", "diagram-measuring-host"}
_MATH_CLASSES = {"katex", "katex-display"}
_ACTIVE_ATTRIBUTES = {"srcdoc", "formaction", "action", "ping", "autofocus"}
_SVG_URL_ATTRIBUTES = {
    "fill",
    "stroke",
    "filter",
    "clip-path",
    "mask",
    "marker-start",
    "marker-mid",
    "marker-end",
    "cursor",
    "color-profile",
}
ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


@dataclass(frozen=True, slots=True)
class ExportIssue:
    reference: str
    reason: str


class ExportDependencyError(ValueError):
    def __init__(self, issues: list[ExportIssue]):
        self.issues = list(dict.fromkeys(issues))
        super().__init__(
            "出力に必要なファイルを埋め込めません:\n"
            + "\n".join(f"・{issue.reference}: {issue.reason}" for issue in self.issues)
        )


class ExportCancelledError(RuntimeError):
    """The output request was cancelled before producing a document."""


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in {"http", "https"}:
            raise OSError("HTTP以外へのリダイレクトには対応していません")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


def _style_text(css: str) -> str:
    """Keep CSS inside an HTML raw-text STYLE element."""
    return re.sub(r"</style", r"\\3c /style", css, flags=re.IGNORECASE)


def _safe_navigation(url: str) -> bool:
    compact = re.sub(r"[\x00-\x20]", "", url)
    # Navigation targets are preserved links, not resources to embed. The Qt
    # snapshot resolves local Markdown links to file URIs before reaching us.
    return urllib.parse.urlsplit(compact).scheme.lower() in {"", "http", "https", "mailto", "file"}


class _Exporter:
    def __init__(self, fetch_remote: bool, cancelled: Callable[[], bool] | None):
        self.fetch_remote = fetch_remote
        self.cancelled = cancelled
        self.started = time.monotonic()
        self.total_bytes = 0
        self.cache: dict[str, tuple[bytes, str]] = {}
        self.embedded: dict[str, str] = {}
        self.active: set[str] = set()
        self.requested: set[str] = set()
        self.issues: list[ExportIssue] = []

    def check(self):
        if self.cancelled and self.cancelled():
            raise ExportCancelledError("出力を中止しました")
        if time.monotonic() - self.started > MAX_SECONDS:
            raise OSError("出力処理の制限時間を超えました")

    def issue(self, reference: str, reason: str):
        self.issues.append(ExportIssue(reference, reason))

    def resolve(self, reference: str, base: str) -> str:
        self.check()
        reference = reference.strip()
        if not reference:
            raise ValueError("参照先が空です")
        if reference.startswith("#"):
            return reference
        if re.match(r"^[A-Za-z]:[\\/]", reference):
            reference = Path(reference).resolve().as_uri()
        if reference.startswith("//"):
            reference = urllib.parse.urljoin(
                base if base.startswith("http") else "https:", reference
            )
        target = urllib.parse.urljoin(base, reference)
        parts = urllib.parse.urlsplit(target)
        if parts.scheme.lower() not in {"file", "http", "https", "data"}:
            raise ValueError(f"未対応の参照形式です: {parts.scheme or '(なし)'}")
        if base.startswith(("http:", "https:")) and parts.scheme.lower() == "file":
            raise ValueError("外部コンテンツからローカルファイルを参照できません")
        return target

    def load(self, url: str) -> tuple[bytes, str]:
        self.check()
        key = urllib.parse.urldefrag(url)[0]
        if key in self.cache:
            return self.cache[key]
        if key not in self.requested and len(self.requested) >= MAX_RESOURCES:
            raise OSError("依存ファイル数の上限を超えました")
        self.requested.add(key)
        scheme = urllib.parse.urlsplit(key).scheme.lower()
        if scheme == "data":
            header, separator, payload = key.partition(",")
            if not separator:
                raise ValueError("data URIが不正です")
            if len(payload) > MAX_RESOURCE_BYTES * 4:
                raise OSError("埋め込みデータのサイズ上限を超えました")
            try:
                raw = urllib.parse.unquote_to_bytes(payload)
                data = base64.b64decode(raw, validate=True) if ";base64" in header.lower() else raw
            except (ValueError, binascii.Error) as exc:
                raise ValueError("data URIのデータが不正です") from exc
            final = key
        elif scheme == "file":
            parts = urllib.parse.urlsplit(key)
            if parts.netloc not in {"", "localhost"}:
                raise ValueError("ネットワーク共有はローカル依存として取り込めません")
            path = Path(urllib.request.url2pathname(parts.path))
            if path.stat().st_size > MAX_RESOURCE_BYTES:
                raise OSError("ファイルのサイズ上限を超えました")
            data, final = path.read_bytes(), key
        else:
            if not self.fetch_remote:
                raise ValueError("外部URLの取得が無効です")
            timeout = min(10.0, max(0.1, MAX_SECONDS - (time.monotonic() - self.started)))
            request = urllib.request.Request(key, headers={"User-Agent": "MarkdownEditor-Export/1"})
            with urllib.request.build_opener(_Redirects()).open(
                request, timeout=timeout
            ) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_RESOURCE_BYTES:
                    raise OSError("外部ファイルのサイズ上限を超えました")
                chunks = []
                size = 0
                read = response.read1 if hasattr(response, "read1") else response.read
                while True:
                    self.check()
                    chunk = read(min(65536, MAX_RESOURCE_BYTES + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESOURCE_BYTES:
                        raise OSError("外部ファイルのサイズ上限を超えました")
                data = b"".join(chunks)
                final = response.geturl()
        self.check()
        if len(data) > MAX_RESOURCE_BYTES:
            raise OSError("ファイルのサイズ上限を超えました")
        self.total_bytes += len(data)
        if self.total_bytes > MAX_TOTAL_BYTES:
            raise OSError("依存ファイルの合計サイズ上限を超えました")
        self.cache[key] = data, final
        return data, final

    def mime(self, data: bytes, expected: str) -> str:
        prefix = data.lstrip(b"\xef\xbb\xbf \t\r\n")
        if prefix.startswith((b"<svg", b"<?xml", b"<!--")):
            root = self.svg_tree(data)
            if _local_name(root.tag) == "svg":
                return "image/svg+xml"
        font_magic = {
            b"wOFF": "font/woff",
            b"wOF2": "font/woff2",
            b"\x00\x01\x00\x00": "font/ttf",
            b"OTTO": "font/otf",
            b"ttcf": "font/collection",
        }
        if data[:4] in font_magic:
            if expected == "image":
                raise ValueError("画像を参照していますが、ファイルの内容はフォントです")
            return font_magic[data[:4]]
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        reader.setDecideFormatFromContent(True)
        formats = {
            "png": "image/png",
            "jpeg": "image/jpeg",
            "jpg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
            "bmp": "image/bmp",
            "tif": "image/tiff",
            "tiff": "image/tiff",
            "ico": "image/x-icon",
        }
        format_name = bytes(reader.format()).decode("ascii", errors="ignore").lower()
        if format_name not in formats:
            raise ValueError("画像・フォントとして認識できないファイルです")
        size = reader.size()
        if not size.isValid() or not reader.canRead():
            raise ValueError("画像・フォントとして認識できないファイルです")
        if size.width() * size.height() > MAX_PIXELS:
            raise ValueError("画像の画素数上限を超えました")
        if reader.read().isNull():
            raise ValueError("画像ファイルが破損しています")
        return formats[format_name]

    @staticmethod
    def svg_tree(data: bytes):
        if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.IGNORECASE):
            raise ValueError("SVGのDOCTYPE・ENTITY宣言には対応していません")
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise ValueError("SVGのXMLが不正です") from exc
        if _local_name(root.tag) != "svg":
            raise ValueError("SVG画像ではありません")
        return root

    def embed(self, reference: str, base: str, expected: str = "asset") -> str:
        try:
            url = self.resolve(reference, base)
            if url.startswith("#"):
                return url
            if url in self.active:
                raise ValueError("依存ファイルが循環参照しています")
            if len(self.active) >= MAX_DEPTH:
                raise ValueError("依存ファイルの入れ子の上限を超えました")
            key = f"{expected}:{url}"
            if key in self.embedded:
                return self.embedded[key]
            data, origin = self.load(url)
            mime = self.mime(data, expected)
            if mime == "image/svg+xml":
                self.active.add(url)
                try:
                    data = self.clean_svg(data, origin)
                finally:
                    self.active.remove(url)
            result = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
            fragment = urllib.parse.urlsplit(url).fragment
            if fragment:
                result += "#" + fragment
            self.embedded[key] = result
            return result
        except ExportCancelledError:
            raise
        except (OSError, ValueError, urllib.error.URLError) as exc:
            self.issue(reference, str(exc))
            return ""

    def css_file(self, reference: str, base: str) -> str:
        try:
            url = self.resolve(reference, base)
            if url in self.active:
                raise ValueError("CSSのimportが循環参照しています")
            if len(self.active) >= MAX_DEPTH:
                raise ValueError("依存ファイルの入れ子の上限を超えました")
            data, origin = self.load(url)
            self.active.add(url)
            try:
                rules, _encoding = tinycss2.parse_stylesheet_bytes(data)
                return self.css_rules(rules, origin)
            finally:
                self.active.remove(url)
        except ExportCancelledError:
            raise
        except (OSError, ValueError, urllib.error.URLError) as exc:
            self.issue(reference, str(exc))
            return ""

    def css(self, css: str, base: str, *, stylesheet: bool = True) -> str:
        self.check()
        if stylesheet:
            return self.css_rules(tinycss2.parse_stylesheet(css), base)
        tokens = tinycss2.parse_component_value_list(css)
        return self.css_values(tokens, base)

    def css_import(self, rule, base: str) -> str:
        significant = [
            token for token in rule.prelude if token.type not in {"whitespace", "comment"}
        ]
        if not significant:
            self.issue("@import", "importの参照先がありません")
            return ""
        first = significant.pop(0)
        if first.type in {"url", "string"}:
            reference = first.value
        elif first.type == "function" and first.lower_name == "url":
            try:
                reference = self.url_function(first)
            except ValueError as exc:
                self.issue("@import", str(exc))
                return ""
        else:
            self.issue("@import", "importの参照形式に対応していません")
            return ""
        css = self.css_file(reference, base)
        # Preserve modern import conditions while removing the remote request.
        wrappers = []
        if significant and significant[0].type == "ident" and significant[0].value == "layer":
            significant.pop(0)
            wrappers.append(("layer", ""))
        elif (
            significant
            and significant[0].type == "function"
            and significant[0].lower_name == "layer"
        ):
            wrappers.append(("layer", tinycss2.serialize(significant.pop(0).arguments)))
        if (
            significant
            and significant[0].type == "function"
            and significant[0].lower_name == "supports"
        ):
            wrappers.append(
                ("supports", "(" + tinycss2.serialize(significant.pop(0).arguments) + ")")
            )
        if significant:
            wrappers.append(
                ("media", " ".join(tinycss2.serialize([token]) for token in significant))
            )
        for name, condition in reversed(wrappers):
            css = f"@{name} {condition}{{{css}}}"
        return css

    def css_rules(self, rules, base: str) -> str:
        result = []
        for rule in rules:
            self.check()
            if rule.type == "error":
                self.issue(base, f"CSS構文エラー: {rule.message}")
                continue
            if rule.type == "at-rule" and rule.lower_at_keyword == "import":
                result.append(self.css_import(rule, base))
                continue
            if rule.type == "at-rule" and rule.lower_at_keyword == "charset":
                continue
            if rule.type == "at-rule" and rule.lower_at_keyword == "namespace":
                result.append(tinycss2.serialize([rule]))
                continue
            if hasattr(rule, "prelude"):
                rule.prelude = tinycss2.parse_component_value_list(
                    self.css_values(rule.prelude, base)
                )
            if getattr(rule, "content", None) is not None:
                rule.content = tinycss2.parse_component_value_list(
                    self.css_values(rule.content, base)
                )
            result.append(tinycss2.serialize([rule]))
        return "".join(result)

    @staticmethod
    def url_function(token) -> str:
        args = [item for item in token.arguments if item.type not in {"whitespace", "comment"}]
        if len(args) != 1 or args[0].type not in {"string", "url", "ident"}:
            raise ValueError("CSSのurl()が不正です")
        return args[0].value

    def css_values(self, tokens, base: str, *, image_set: bool = False) -> str:
        result = []
        for token in tokens:
            self.check()
            if token.type == "error":
                self.issue(base, f"CSS構文エラー: {token.message}")
                continue
            if token.type == "url":
                result.append('url("' + self.embed(token.value, base) + '")')
            elif token.type == "string" and image_set:
                result.append('url("' + self.embed(token.value, base, "image") + '")')
            elif token.type == "function":
                if token.lower_name == "url":
                    try:
                        value = self.url_function(token)
                        result.append('url("' + self.embed(value, base) + '")')
                    except ValueError as exc:
                        self.issue(base, str(exc))
                elif token.lower_name in {"expression", "-moz-binding"}:
                    self.issue(base, "実行可能なCSSには対応していません")
                else:
                    inner = self.css_values(
                        token.arguments,
                        base,
                        image_set=token.lower_name in {"image-set", "-webkit-image-set"},
                    )
                    result.append(token.name + "(" + inner + ")")
            elif hasattr(token, "content"):
                before, after = {
                    "{} block": ("{", "}"),
                    "() block": ("(", ")"),
                    "[] block": ("[", "]"),
                }[token.type]
                result.append(before + self.css_values(token.content, base) + after)
            else:
                result.append(tinycss2.serialize([token]))
        return "".join(result)

    def srcset(self, value: str, base: str) -> str:
        candidates = []
        for url, descriptor in parse_srcset(value):
            if descriptor and not re.fullmatch(
                r"(?:[1-9]\d*w|(?:\d+(?:\.\d*)?|\.\d+)x)", descriptor
            ):
                self.issue(url, f"srcsetの指定に対応していません: {descriptor}")
            embedded = self.embed(url, base, "image")
            candidates.append(embedded + (" " + descriptor if descriptor else ""))
        return ", ".join(candidates)

    def clean_svg(self, data: bytes, base: str) -> bytes:
        root = self.svg_tree(data)
        for parent in root.iter():
            self.check()
            for child in list(parent):
                if _local_name(child.tag) in _BAD_ELEMENTS:
                    parent.remove(child)
        for element in root.iter():
            self.check()
            name = _local_name(element.tag)
            if name == "style":
                element.text = self.css(element.text or "", base)
            for attribute, value in list(element.attrib.items()):
                local = _local_name(attribute)
                if local.startswith("on") or local in _ACTIVE_ATTRIBUTES:
                    del element.attrib[attribute]
                elif local == "style" or (local in _SVG_URL_ATTRIBUTES and "url(" in value.lower()):
                    element.set(attribute, self.css(value, base, stylesheet=False))
                elif local == "base":
                    self.issue(value, "SVG内のxml:baseには対応していません")
                elif local == "srcset":
                    element.set(attribute, self.srcset(value, base))
                elif local in {"src", "poster", "background"}:
                    if name in {"img", "image", "input"} or local in {"poster", "background"}:
                        element.set(attribute, self.embed(value, base, "image"))
                    else:
                        self.issue(value, f"SVG内の{name}要素のメディア依存には対応していません")
                elif local == "href":
                    if name in {"image", "feimage"}:
                        element.set(attribute, self.embed(value, base, "image"))
                    elif name == "a":
                        if not _safe_navigation(value):
                            del element.attrib[attribute]
                    elif value and not value.startswith("#"):
                        self.issue(value, "SVGの外部要素参照には対応していません")
        return ET.tostring(root, encoding="utf-8")

    def fragment(self, fragment: str, base: str) -> tuple[str, bool]:
        soup = BeautifulSoup(fragment, "html.parser")
        needs_math = False
        for tag in list(soup.find_all(True)):
            self.check()
            if tag.name is None or tag.attrs is None:
                # Removing a copy control also destroys its nested SVG/labels.
                continue
            classes = set(tag.get("class", []))
            name = tag.name.lower()
            preview_code_ui = tag.get("data-preview-code-ui")
            if (
                name in _BAD_ELEMENTS
                or (name == "button" and preview_code_ui == "copy")
                or tag.get("id") == "eof-spacer"
                or classes & _REMOVE_CLASSES
            ):
                tag.decompose()
                continue
            if classes & _MATH_CLASSES:
                needs_math = True
            if name == "div" and preview_code_ui == "block":
                # The preview's copy controls own this wrapper and its spacing.
                # Preserve the pre/code subtree, including highlighted spans.
                tag.unwrap()
                continue
            if name == "style":
                tag.string = _style_text(self.css(tag.string or tag.get_text(), base))
            if name == "link":
                if "stylesheet" in tag.get("rel", []):
                    style = soup.new_tag("style")
                    style.string = _style_text(self.css_file(tag.get("href", ""), base))
                    tag.replace_with(style)
                else:
                    tag.decompose()
                continue
            for attribute, value in list(tag.attrs.items()):
                attribute = attribute.lower()
                if (
                    attribute.startswith(("on", "data-source-"))
                    or attribute in {"data-code-source", "data-preview-code-ui"}
                    or attribute in _ACTIVE_ATTRIBUTES
                ):
                    del tag.attrs[attribute]
                    continue
                if not isinstance(value, str):
                    continue
                if attribute == "style" or (
                    attribute in _SVG_URL_ATTRIBUTES and "url(" in value.lower()
                ):
                    tag[attribute] = self.css(value, base, stylesheet=False)
                elif attribute == "xml:base":
                    self.issue(value, "SVG内のxml:baseには対応していません")
                elif attribute == "srcset":
                    tag[attribute] = self.srcset(value, base)
                elif attribute in {"src", "poster", "background"}:
                    if name in {"img", "image", "input"} or attribute in {"poster", "background"}:
                        tag[attribute] = self.embed(value, base, "image")
                    else:
                        self.issue(value, f"{name}要素のメディア依存には対応していません")
                elif attribute in {"href", "xlink:href"}:
                    if name in {"image", "feimage"}:
                        tag[attribute] = self.embed(value, base, "image")
                    elif name == "a":
                        if not _safe_navigation(value):
                            del tag.attrs[attribute]
                    elif value and not value.startswith("#"):
                        self.issue(value, f"{name}要素の外部参照には対応していません")
            if name == "img":
                tag.attrs.pop("loading", None)
            if name in {"input", "button", "select", "textarea"}:
                tag["disabled"] = ""
        return str(soup), needs_math


def build_export_html(
    fragment: str,
    base_dir: Path,
    title: str,
    css_path: Path | None = None,
    fetch_remote: bool = False,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Build one relocatable HTML file or report every unresolved dependency.

    Rendering Mermaid and KaTeX is the caller's responsibility. No JavaScript,
    application bridge, preview spacer or source-line attributes are exported.
    """
    exporter = _Exporter(fetch_remote, cancelled)
    base = Path(base_dir).resolve().as_uri().rstrip("/") + "/"
    try:
        body, needs_math = exporter.fragment(fragment, base)
        css = exporter.css_file((RESOURCE_DIR / "export.css").as_uri(), base)
        css += "\n" + exporter.css_file((RESOURCE_DIR / "code-highlight.css").as_uri(), base)
        if needs_math:
            css += "\n" + exporter.css_file(
                (RESOURCE_DIR / "vendor" / "katex" / "katex.min.css").as_uri(), base
            )
        if css_path is not None:
            css += "\n" + exporter.css_file(Path(css_path).resolve().as_uri(), base)
        exporter.check()
    except (OSError, ValueError) as exc:
        exporter.issue("HTML出力", str(exc))
    if exporter.issues:
        raise ExportDependencyError(exporter.issues)
    css = _style_text(css)
    csp = (
        "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
        "img-src data:; font-src data:; media-src data:; object-src 'none'; "
        "frame-src 'none'; base-uri 'none'; form-action 'none'"
    )
    return (
        '<!doctype html>\n<html lang="ja" data-theme="light"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{css}</style></head>"
        f'<body><main id="content">{body}</main></body></html>\n'
    )
