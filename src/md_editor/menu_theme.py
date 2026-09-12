"""Theme popups by their owning window, including Qt's standard edit menus."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QWidget


@dataclass(frozen=True)
class _MenuTheme:
    palette: QPalette
    font: QFont
    stylesheet: str


def _owner_theme(widget: QWidget) -> _MenuTheme | None:
    # A popup/dialog is itself a window, so window() would stop too early.
    # Follow QWidget ownership through editor fields, dialogs and submenus.
    parent = widget.parentWidget()
    while parent is not None:
        theme = getattr(parent, "_md_menu_theme", None)
        if theme is not None:
            return theme
        parent = parent.parentWidget()
    return None


def _style_menu(menu: QMenu) -> None:
    theme = _owner_theme(menu)
    if theme is None or getattr(menu, "_md_applied_menu_theme", None) is theme:
        return
    # Store first: setStyleSheet can synchronously generate another Polish.
    menu._md_applied_menu_theme = theme
    menu.setPalette(theme.palette)
    # Source and table cell editors use code fonts; popups use the UI font.
    menu.setFont(theme.font)
    menu.setStyleSheet(theme.stylesheet)


class _MenuThemeFilter(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(watched, QMenu) and event.type() in {
            QEvent.Type.Polish,
            QEvent.Type.Show,
        }:
            # Standard QLineEdit/QPlainTextEdit menus are made inside Qt. Their
            # normal ownership, actions and automatic deletion stay intact.
            _style_menu(watched)
        return False


def apply_window_menu_theme(window: QWidget) -> None:
    """Refresh this window's menus after changing its palette.

    The application filter only observes QMenu creation/display. It never
    changes QApplication's palette, font or stylesheet, so another document
    window can use a different theme at the same time.
    """
    application = QApplication.instance()
    if application is None:
        return
    if not hasattr(application, "_md_menu_theme_filter"):
        observer = _MenuThemeFilter(application)
        application._md_menu_theme_filter = observer
        application.installEventFilter(observer)

    colors = QPalette(window.palette())
    background = colors.color(QPalette.ColorRole.Window)
    text = colors.color(QPalette.ColorRole.WindowText).name()
    highlight = colors.color(QPalette.ColorRole.Highlight).name()
    selected_text = colors.color(QPalette.ColorRole.HighlightedText).name()
    disabled = colors.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name()
    border = background.lighter(155) if background.lightness() < 128 else background.darker(116)
    # Resolve colors now. palette() expressions can retain a prior popup's
    # colors under Qt's stylesheet style after a light/dark theme switch.
    stylesheet = f"""
        QMenu {{
            background-color: {background.name()}; color: {text};
            border: 1px solid {border.name()}; padding: 4px 0px;
        }}
        QMenu::item {{
            background-color: transparent; padding: 5px 28px 5px 24px;
        }}
        QMenu::item:selected {{
            background-color: {highlight}; color: {selected_text};
        }}
        QMenu::item:disabled {{ color: {disabled}; }}
        QMenu::separator {{
            background-color: {border.name()}; height: 1px; margin: 4px 8px;
        }}
    """
    window._md_menu_theme = _MenuTheme(colors, QFont(QApplication.font("QMenu")), stylesheet)
    # Persistent application menus and any currently open context menus must
    # update too; the event filter covers those created after this point.
    for menu in window.findChildren(QMenu):
        _style_menu(menu)
