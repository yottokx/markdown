"""Bounded, opt-in diagnostics for the packaged application's real WebEngine."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from md_editor.app import MainWindow


_INSPECT = """
(() => {
    const api = window.previewApi;
    const content = document.getElementById('content');
    if (!api || !content) return JSON.stringify({ready: false});
    const metrics = api.metrics();
    return JSON.stringify({
        ready: true,
        metrics: {
            revision: metrics.revision,
            lineCount: metrics.lineCount,
            source: metrics.source,
            measuredSource: metrics.measuredSource,
            maxSource: metrics.maxSource,
            scrollY: metrics.scrollY,
            maxScroll: metrics.maxScroll,
            viewportHeight: metrics.viewportHeight,
            contentHeight: metrics.contentHeight,
            anchorCount: metrics.anchors.length
        },
        textLength: content.innerText.trim().length,
        images: Array.from(content.querySelectorAll('img')).map(img => ({
            complete: img.complete,
            naturalWidth: img.naturalWidth,
            naturalHeight: img.naturalHeight
        })),
        headings: Array.from(content.querySelectorAll('h1,h2,h3,h4,h5,h6'))
            .map(node => Number(node.dataset.sourceLine))
            .filter(Number.isFinite)
    });
})()
"""


class _SmokeDiagnostic(QObject):
    def __init__(self, window: MainWindow, output: Path) -> None:
        super().__init__(window)
        self.window = window
        self.output = output.expanduser().resolve()
        self.done = False
        self.targets: list[int] = []
        self.index = 0
        self.report: dict[str, object] = {
            "ok": False,
            "frozen": bool(getattr(sys, "frozen", False)),
            "opened_path": str(window.path) if window.path is not None else None,
            "line_count": window.editor.blockCount(),
            "checks": [],
            "errors": [],
        }
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._timeout)
        self.deadline.start(15_000)
        QTimer.singleShot(0, self._wait_ready)

    def _timeout(self) -> None:
        self._error("Timed out after 15 seconds waiting for preview and scroll checks.")
        self._finish()

    def _error(self, message: str) -> None:
        self.report["errors"].append(message)

    def _wait_ready(self) -> None:
        if self.done:
            return
        if self.window._rendered_revision != self.window._revision:
            QTimer.singleShot(50, self._wait_ready)
            return
        self.window.preview.page().runJavaScript(_INSPECT, self._initial_state)

    def _decode(self, raw: object) -> dict | None:
        if self.done:
            return None
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
            if not isinstance(value, dict):
                raise TypeError("JavaScript did not return a JSON object")
            return value
        except (ValueError, TypeError) as exc:
            self._error(f"Could not read WebEngine diagnostics: {exc}")
            self._finish()
            return None

    def _initial_state(self, raw: object) -> None:
        state = self._decode(raw)
        if state is None:
            return
        if not state.get("ready"):
            QTimer.singleShot(100, self._wait_ready)
            return
        images = state["images"]
        if any(not image["complete"] for image in images):
            QTimer.singleShot(100, self._wait_ready)
            return
        self.report["initial"] = state
        if not state["textLength"]:
            self._error("The rendered document has no visible text.")
        if any(image["naturalWidth"] <= 0 for image in images):
            self._error("At least one document image failed to load.")
        if state["metrics"]["revision"] != self.window._revision:
            self._error("The DOM revision differs from the opened document.")
        if state["metrics"]["lineCount"] != self.window.editor.blockCount():
            self._error("The source and preview line counts differ.")
        if self.report["errors"]:
            self._finish()
            return
        last = self.window.editor.blockCount() - 1
        headings = [int(line) for line in state["headings"] if 0 < line < last]
        middle = min(headings, key=lambda line: abs(line - last / 2)) if headings else last // 2
        self.targets = [0, middle, last]
        self._move_next()

    def _move_next(self) -> None:
        if self.done:
            return
        if self.index >= len(self.targets):
            self._finish()
            return
        self.window.editor.scroll_to_source(float(self.targets[self.index]))
        QTimer.singleShot(150, self._inspect_position)

    def _inspect_position(self) -> None:
        if not self.done:
            self.window.preview.page().runJavaScript(_INSPECT, self._check_position)

    def _check_position(self, raw: object) -> None:
        state = self._decode(raw)
        if state is None:
            return
        if not state.get("ready"):
            self._error("The preview API disappeared during a scroll check.")
            self._finish()
            return
        target = self.targets[self.index]
        editor_source = self.window.editor.source_position()
        metrics = state["metrics"]
        check_errors = []
        for label, actual, expected in (
            ("editor versus target", editor_source, target),
            ("preview versus editor", metrics["source"], editor_source),
            ("measured preview versus editor", metrics["measuredSource"], editor_source),
        ):
            if not math.isfinite(actual) or abs(actual - expected) > 1:
                check_errors.append(f"{label}: {actual} versus {expected}")
        if not state["textLength"]:
            check_errors.append("The DOM text disappeared.")
        if any(not image["complete"] or image["naturalWidth"] <= 0 for image in state["images"]):
            check_errors.append("A document image is not loaded.")
        self.report["checks"].append(
            {
                "target": target,
                "editor_source": editor_source,
                "metrics": metrics,
                "ok": not check_errors,
                "errors": check_errors,
            }
        )
        for error in check_errors:
            self._error(f"At source line {target}: {error}")
        if self.index == 0:
            try:
                self.output.parent.mkdir(parents=True, exist_ok=True)
                screenshot = self.output.with_suffix(".png")
                if self.window.grab().save(str(screenshot)):
                    self.report["screenshot"] = str(screenshot)
            except OSError:
                pass  # The diagnostic image is optional; the JSON is required.
        self.index += 1
        self._move_next()

    def _finish(self) -> None:
        if self.done:
            return
        self.done = True
        self.deadline.stop()
        self.report["ok"] = not self.report["errors"] and len(self.report["checks"]) == 3
        code = 0 if self.report["ok"] else 1
        try:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text(
                json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            code = 1
        QApplication.exit(code)


def start_smoke(window: MainWindow, output: Path) -> None:
    """Check the real DOM, image loading and three scroll positions, then exit.

    Only call this for an explicitly supplied diagnostic output path. The QObject
    parent keeps the asynchronous diagnostic alive until the application exits.
    """
    _SmokeDiagnostic(window, output)
