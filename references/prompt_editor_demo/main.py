from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from desktop_llm.secrets import ApiKeyStore
from desktop_llm.storage import SettingsStore
from desktop_llm.theme import APP_STYLESHEET

from .editor import PromptEditorWindow


def desktop_llm_data_directory() -> Path:
    location = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    return Path(location)


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("DesktopLlm")
    app.setApplicationName("Desktop LLM")
    app.setApplicationDisplayName("Prompt Editor Demo")
    app.setQuitOnLastWindowClosed(True)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    store = SettingsStore(desktop_llm_data_directory() / "settings.sqlite3")
    window = PromptEditorWindow(store, ApiKeyStore())
    window.show()
    window.raise_()
    window.activateWindow()
    window.editor.setFocus()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

