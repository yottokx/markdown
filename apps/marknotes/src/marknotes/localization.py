"""Install the Japanese translations shipped with the current Qt runtime."""

from __future__ import annotations

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication

_TRANSLATOR_ATTRIBUTE = "_marknotes_japanese_translator"


def install_japanese_translation(application: QApplication) -> bool:
    """Translate Qt's standard controls once, retaining the translator for the app.

    Resolve the translation directory from Qt so the same code also works with a
    bundled runtime. A missing optional catalog leaves Qt's original labels usable.
    """
    if getattr(application, _TRANSLATOR_ATTRIBUTE, None) is not None:
        return True
    translator = QTranslator(application)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load("qtbase_ja.qm", directory):
        translator.deleteLater()
        return False
    if not application.installTranslator(translator):
        translator.deleteLater()
        return False
    translator.setObjectName("mdEditorQtJapaneseTranslator")
    setattr(application, _TRANSLATOR_ATTRIBUTE, translator)
    return True
