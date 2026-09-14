"""GUI-owned note documents and ordered background work for the notebook."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QPlainTextDocumentLayout

from .assets import markdown_destinations, rewrite_destinations
from .highlighter import MarkdownHighlighter
from .indentation import MarkdownContext
from .managed_assets import resolve_managed_asset


class BackgroundJobs(QObject):
    """Only immutable Python values cross the worker/GUI boundary."""

    completed = Signal(int, object, object)

    def __init__(self, parent=None, *, name="marknotes-save"):
        super().__init__(parent)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self._callbacks = {}
        self._sequence = 0
        self._closed = False
        self.completed.connect(self._complete)

    def submit(self, function, callback):
        if self._closed:
            return
        self._sequence += 1
        token = self._sequence
        self._callbacks[token] = callback
        future = self.executor.submit(function)

        def done(result):
            try:
                value, error = result.result(), None
            except Exception as exc:  # noqa: BLE001 -- report worker failures on the GUI thread
                value, error = None, exc
            if not self._closed:
                self.completed.emit(token, value, error)

        future.add_done_callback(done)
        return token

    def _complete(self, token, value, error):
        callback = self._callbacks.pop(token, None)
        if callback is not None and not self._closed:
            callback(value, error)

    def shutdown(self):
        self._closed = True
        self._callbacks.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)


class NoteDocument:
    """Adapter for existing editing/export tools; SQLite owns the Markdown."""

    is_managed_note = True
    path = None
    encoding = "utf-8"
    newline = "LF"
    mixed_newlines = False
    bom = b""
    encoding_warning = ""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()
        self._rename_journals = []

    def close(self):
        pass

    def remember_references(self, text, **kwargs):
        # Managed assets never change roots, and old names are retained for Undo.
        pass

    def normalize_references(self, text):
        return text

    def managed_image_path(self, url):
        return resolve_managed_asset(url, self.base_dir)

    def image_reference_states(self, text):
        return tuple(
            (destination, self.managed_image_path(destination.url), True)
            for destination in markdown_destinations(text)
        )

    def reserved_image_paths(self):
        root = self.base_dir / "assets"
        return set(root.iterdir()) if root.is_dir() else set()

    def rewrite_image_paths(self, text, replacements, *, base_dir=None):
        base = base_dir or self.base_dir

        def replace(destination):
            origin = self.managed_image_path(destination.url)
            target = replacements.get(origin)
            if target is None:
                return None
            return quote(target.relative_to(base).as_posix(), safe="/-._~")

        return rewrite_destinations(text, replace)


@dataclass
class NoteSession:
    id: str
    document: QTextDocument
    context: MarkdownContext
    highlighter: MarkdownHighlighter
    adapter: NoteDocument
    revision: int
    saved_revision: int
    last_text: str
    view: dict = field(default_factory=dict)
    pending_assets: int = 0

    @classmethod
    def from_note(cls, note, base_dir, view, parent):
        document = QTextDocument(parent)
        document.setDocumentLayout(QPlainTextDocumentLayout(document))
        document.setPlainText(note.body)
        document.setModified(False)
        context = MarkdownContext(document)
        highlighter = MarkdownHighlighter(document, context)
        return cls(
            note.id,
            document,
            context,
            highlighter,
            NoteDocument(base_dir),
            note.revision,
            note.revision,
            note.body,
            view,
        )

    @property
    def dirty(self):
        return self.revision > self.saved_revision
