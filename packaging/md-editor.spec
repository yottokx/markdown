# -*- mode: python ; coding: utf-8 -*-
"""Build a Windows onedir app, including the local preview and test assets."""

from pathlib import Path


project_root = Path(SPECPATH).parent
source_root = project_root / "src"
resource_root = source_root / "md_editor" / "resources"

# PyInstaller's PySide6 hooks collect QtWebEngineProcess, Qt resources, locales,
# platform plugins and the modules imported by the application. Do not copy the
# entire Python environment: only these hooks and our own resources are needed.
analysis = Analysis(
    [str(project_root / "packaging" / "launcher.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[(str(resource_root), "md_editor/resources")],
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
    name="md-editor",
    icon=str(resource_root / "app-icon.ico"),
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
    name="md-editor",
)
