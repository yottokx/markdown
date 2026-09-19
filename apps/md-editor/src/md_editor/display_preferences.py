"""Persistent source typography and preview zoom without changing the document."""

import math

from PySide6.QtGui import QAction, QActionGroup, QFont
from PySide6.QtWidgets import QFontDialog, QInputDialog

SOURCE_FONT_KEY = "display/sourceFont"
PREVIEW_ZOOM_KEY = "display/previewZoom"
ZOOM_LEVELS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0, 5.0)


def default_source_font() -> QFont:
    font = QFont("Cascadia Mono", 11)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def source_font_from_settings(settings) -> QFont:
    font = default_source_font()
    saved = settings.value(SOURCE_FONT_KEY)
    # QFont.fromString also accepts a bare family name. Our preference always
    # contains QFont.toString's full record; a partial/corrupt value is not one.
    if isinstance(saved, str) and len(saved.split(",")) >= 10:
        candidate = QFont()
        if (
            candidate.fromString(str(saved))
            and candidate.family().strip()
            and math.isfinite(candidate.pointSizeF())
            and 6 <= candidate.pointSizeF() <= 72
        ):
            font = candidate
    return font


def preview_zoom_from_settings(settings) -> float:
    try:
        factor = float(settings.value(PREVIEW_ZOOM_KEY, 1.0))
    except (TypeError, ValueError, OverflowError):
        return 1.0
    return factor if math.isfinite(factor) and 0.25 <= factor <= 5.0 else 1.0


class DisplayPreferences:
    def init_display_preferences(self, view_menu):
        self.preview_zoom = 1.0
        self.source_font_action = QAction("ソースのフォント…", self)
        self.source_font_action.triggered.connect(self.choose_source_font)
        view_menu.addAction(self.source_font_action)
        self.reset_source_font_action = QAction("ソースのフォントを標準に戻す", self)
        self.reset_source_font_action.triggered.connect(
            lambda: self.set_source_font(default_source_font())
        )
        view_menu.addAction(self.reset_source_font_action)
        self.preview_zoom_menu = view_menu.addMenu("プレビューの倍率")
        self._preview_zoom_group = QActionGroup(self)
        self._preview_zoom_group.setExclusive(True)
        self.preview_zoom_actions = {}
        for factor in ZOOM_LEVELS:
            action = QAction(f"{factor * 100:g}%", self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked=False, value=factor: self.set_preview_zoom(value)
            )
            self._preview_zoom_group.addAction(action)
            self.preview_zoom_menu.addAction(action)
            self.preview_zoom_actions[factor] = action
        self.preview_zoom_menu.addSeparator()
        self.custom_preview_zoom_action = QAction("倍率を指定…", self)
        self.custom_preview_zoom_action.triggered.connect(self.choose_preview_zoom)
        self.preview_zoom_menu.addAction(self.custom_preview_zoom_action)
        view_menu.addSeparator()

    def load_display_preferences(self):
        self.set_source_font(source_font_from_settings(self.settings), persist=False)
        self.set_preview_zoom(preview_zoom_from_settings(self.settings), persist=False)

    def choose_source_font(self):
        accepted, font = QFontDialog.getFont(
            self.editor.font(),
            self,
            "ソースのフォント・文字サイズ",
            QFontDialog.FontDialogOption.DontUseNativeDialog,
        )
        if accepted:
            self.set_source_font(font)

    def set_source_font(self, font: QFont, *, persist=True):
        font = QFont(font)
        size = font.pointSizeF()
        if not font.family().strip() or not math.isfinite(size) or size <= 0:
            raise ValueError("A source font must have a family and a positive point size")
        font.setPointSizeF(max(6.0, min(72.0, size)))
        self.editor.set_source_font(font)
        self.source_font_action.setStatusTip(f"現在: {font.family()} / {font.pointSizeF():g} pt")
        if persist:
            self.settings.setValue(SOURCE_FONT_KEY, font.toString())

    def choose_preview_zoom(self):
        percent, accepted = QInputDialog.getInt(
            self,
            "プレビューの倍率",
            "表示倍率（%）",
            round(self.preview_zoom * 100),
            25,
            500,
            25,
        )
        if accepted:
            self.set_preview_zoom(percent / 100)

    def set_preview_zoom(self, factor: float, *, persist=True):
        factor = float(factor)
        if not math.isfinite(factor) or not 0.25 <= factor <= 5.0:
            raise ValueError("Preview zoom must be between 0.25 and 5.0")
        self.preview.set_zoom_factor(factor)
        self.preview_zoom = factor
        for value, action in self.preview_zoom_actions.items():
            action.setChecked(math.isclose(value, factor))
        self.custom_preview_zoom_action.setText(f"倍率を指定…（現在: {factor * 100:g}%）")
        if persist:
            self.settings.setValue(PREVIEW_ZOOM_KEY, factor)
