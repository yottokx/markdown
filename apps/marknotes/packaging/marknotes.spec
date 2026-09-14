# -*- mode: python ; coding: utf-8 -*-
"""Build a Windows onedir app, including the local preview and test assets."""

import os
import sys
from importlib.util import find_spec
from pathlib import Path


# Binary analysis must not resolve Qt dependencies against unrelated applications
# on the invoking shell's PATH (for example a separate Poppler/ICU distribution).
# Each package hook still supplies its own binary directories to PyInstaller.
if sys.platform == "win32":
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    runtime_dirs = [
        Path(sys.executable).parent,
        Path(sys.base_prefix),
        Path(sys.base_prefix) / "DLLs",
        system_root / "System32",
        system_root,
    ]
    for package in ("PySide6", "shiboken6"):
        spec = find_spec(package)
        if spec and spec.submodule_search_locations:
            runtime_dirs.extend(Path(path) for path in spec.submodule_search_locations)
    os.environ["PATH"] = os.pathsep.join(str(path) for path in runtime_dirs if path.is_dir())



project_root = Path(SPECPATH).parent
source_root = project_root / "src"
resource_root = source_root / "marknotes" / "resources"

# PyInstaller's PySide6 hooks collect QtWebEngineProcess, Qt resources, locales,
# platform plugins and the modules imported by the application. Do not copy the
# entire Python environment: only these hooks and our own resources are needed.
analysis = Analysis(
    [str(project_root / "packaging" / "launcher.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[(str(resource_root), "marknotes/resources")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="marknotes",
    icon=str(resource_root / "marknotes-icon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="marknotes",
)
