"""Application icons and the stable Windows taskbar identity."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon

RESOURCE_DIR = Path(__file__).resolve().parent / "resources"
WINDOWS_APP_USER_MODEL_ID = "Yotto.MarkNotes"
_windows_identity_set = False


def set_windows_app_user_model_id() -> None:
    """Set before native windows exist so Python launches use their own taskbar group."""
    global _windows_identity_set
    if sys.platform != "win32" or _windows_identity_set:
        return
    import ctypes

    try:
        setter = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        result = setter(WINDOWS_APP_USER_MODEL_ID)
        if result < 0:
            logging.getLogger(__name__).warning("Windows AppUserModelID failed: %#x", result)
            return
        _windows_identity_set = True
    except OSError:
        logging.getLogger(__name__).warning("Windows AppUserModelID is unavailable", exc_info=True)


def application_icon() -> QIcon:
    """The same resource paths work in a source checkout and a PyInstaller bundle."""
    icon = QIcon(str(RESOURCE_DIR / "app-icon.ico"))
    # Keep the full PNG resolution available for larger and high-DPI surfaces.
    icon.addFile(str(RESOURCE_DIR / "app-icon.png"))
    return icon
