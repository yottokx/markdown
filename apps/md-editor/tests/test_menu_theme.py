"""Actual popups follow their document window without changing other windows."""

import pytest
from PySide6.QtCore import QPoint, QSettings
from PySide6.QtGui import QContextMenuEvent, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit

from md_editor import interactions, tables
from md_editor.app import MainWindow
from md_editor.tables import TableDialog
from md_editor.theme import palette


@pytest.fixture
def make_window(qtbot, tmp_path):
    windows = []

    def create(mode):
        settings = QSettings(
            str(tmp_path / f"window-{len(windows)}.ini"), QSettings.Format.IniFormat
        )
        settings.setValue("theme", mode)
        window = MainWindow(settings)
        qtbot.addWidget(window)
        window.resize(1000, 650)
        window.show()
        windows.append(window)
        return window

    return create


def assert_popup(menu, dark):
    menu.popup(QPoint(180, 160))
    try:
        colors = palette(dark)
        assert menu.palette().color(QPalette.ColorRole.Window) == colors.window().color()
        assert menu.palette().color(QPalette.ColorRole.WindowText) == colors.windowText().color()
        # Test the painted background too: a correct palette alone previously
        # left already-polished Fusion/QSS popups with their old light colors.
        image = menu.grab().toImage()
        scale = image.devicePixelRatio()
        assert image.pixelColor(round(3 * scale), round(3 * scale)) == colors.window().color()
        assert menu.font().family() == QApplication.font("QMenu").family()
        assert menu.font().pointSizeF() == QApplication.font("QMenu").pointSizeF()
    finally:
        menu.close()


def test_reused_menus_switch_theme_without_changing_other_window(make_window):
    dark_window = make_window("dark")
    light_window = make_window("light")
    application_palette = QApplication.palette()
    bar = dark_window.menuBar()
    bar_stylesheet = bar.styleSheet()
    dark_menu = bar.actions()[0].menu()
    light_menu = light_window.menuBar().actions()[0].menu()
    assert_popup(dark_menu, True)
    assert_popup(light_menu, False)
    assert_popup(dark_window.paste_format_menu, True)

    dark_window.apply_theme("light", persist=False)
    assert_popup(dark_menu, False)
    assert_popup(dark_window.paste_format_menu, False)
    light_window.apply_theme("dark", persist=False)
    assert_popup(light_menu, True)
    assert_popup(dark_menu, False)
    dark_window.apply_theme("dark", persist=False)
    assert_popup(dark_menu, True)
    assert bar.styleSheet() == bar_stylesheet
    assert QApplication.palette() == application_palette


def test_source_preview_standard_fields_and_table_header_menus_share_window_theme(
    make_window, qtbot, monkeypatch
):
    window = make_window("dark")
    observed = []

    class InspectedMenu(QMenu):
        def exec(self, _position):
            assert_popup(self, True)
            observed.append([action.text() for action in self.actions()])

    # Exercise the source and header production menu paths; popup performs
    # real Qt polish/show/paint without blocking in a native modal menu loop.
    monkeypatch.setattr(interactions, "QMenu", InspectedMenu)
    monkeypatch.setattr(tables, "QMenu", InspectedMenu)
    position = QPoint(20, 20)
    window.editor.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            position,
            window.editor.mapToGlobal(position),
        )
    )
    assert observed
    assert window.insert_menu.menuAction() in window.menuBar().actions()

    preview_menu = window.preview.view._create_context_menu(None)
    search_menu = window.search.query.createStandardContextMenu()
    for menu in (preview_menu, search_menu):
        assert_popup(menu, True)
        menu.deleteLater()

    dialog = TableDialog(window, rows=[["Header"], ["value"]], paste_mode=False)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.grid.editItem(dialog.grid.item(1, 0))
    cell_editor = dialog.grid.findChild(QPlainTextEdit)
    assert cell_editor is not None
    cell_menu = cell_editor.createStandardContextMenu()
    assert_popup(cell_menu, True)
    cell_menu.deleteLater()

    header = dialog.grid.verticalHeader()
    dialog._row_context_menu(
        QPoint(5, header.sectionViewportPosition(1) + header.sectionSize(1) // 2)
    )
    header = dialog.grid.horizontalHeader()
    dialog._column_context_menu(QPoint(header.sectionSize(0) // 2, 5))
    assert len(observed) == 3
    # A dialog is a separate Qt window, but its menus still follow the owning
    # document if that document's theme is changed while the dialog exists.
    window.apply_theme("light", persist=False)
    menu = dialog.grid.findChild(QPlainTextEdit).createStandardContextMenu()
    assert_popup(menu, False)
    menu.deleteLater()
    dialog.reject()
