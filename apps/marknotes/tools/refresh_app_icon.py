"""Notify Explorer that this build's executable icon has changed.

The default refresh targets only the specified executable. An explicit fallback
can also invalidate the Shell's icon and thumbnail caches. Neither mode changes
file associations, removes cache files, or restarts Explorer.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _ShellFileInfo(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HANDLE),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> _Guid:
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _com_method(interface, index, result_type, *argument_types):
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(vtable[index])


def _check_hresult(result: int, operation: str) -> None:
    if result < 0:
        raise OSError(f"{operation} failed: 0x{result & 0xFFFFFFFF:08X}")


def _extract_icon_location(executable: Path, shell, ole) -> tuple[str, int, int]:
    """Read the Shell's actual cache key, which can be an opaque per-file identity."""
    shell.SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    shell.SHParseDisplayName.restype = ctypes.c_long
    shell.SHBindToParent.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_Guid),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell.SHBindToParent.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    pidl = ctypes.c_void_p()
    parent = ctypes.c_void_p()
    child = ctypes.c_void_p()
    extractor = ctypes.c_void_p()
    try:
        _check_hresult(
            shell.SHParseDisplayName(str(executable), None, ctypes.byref(pidl), 0, None),
            "SHParseDisplayName",
        )
        shell_folder = _Guid.parse("000214E6-0000-0000-C000-000000000046")
        _check_hresult(
            shell.SHBindToParent(
                pidl, ctypes.byref(shell_folder), ctypes.byref(parent), ctypes.byref(child)
            ),
            "SHBindToParent",
        )
        extract_icon = _Guid.parse("000214FA-0000-0000-C000-000000000046")
        get_ui_object = _com_method(
            parent,
            10,
            ctypes.c_long,
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(_Guid),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        _check_hresult(
            get_ui_object(
                parent,
                None,
                1,
                ctypes.byref(child),
                ctypes.byref(extract_icon),
                None,
                ctypes.byref(extractor),
            ),
            "IShellFolder::GetUIObjectOf",
        )
        get_location = _com_method(
            extractor,
            3,
            ctypes.c_long,
            wintypes.UINT,
            wintypes.LPWSTR,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(wintypes.UINT),
        )
        location = ctypes.create_unicode_buffer(32768)
        icon_index = ctypes.c_int()
        icon_flags = wintypes.UINT()
        # GIL_FORSHELL: obtain the same identity used by Explorer's folder view.
        result = get_location(
            extractor,
            0x2,
            location,
            len(location),
            ctypes.byref(icon_index),
            ctypes.byref(icon_flags),
        )
        _check_hresult(result, "IExtractIcon::GetIconLocation")
        if result != 0 or not location.value:
            raise ValueError("Explorer returned a fallback icon instead of this executable's icon.")
        # GIL_NOTFILENAME means the path/index are an opaque cache identity, not
        # an ExtractIcon file resource. Do not replace that identity with index 0.
        if not icon_flags.value & 0x8 and Path(location.value).resolve() != executable:
            raise ValueError("Explorer returned a different icon source.")
        return location.value, icon_index.value, icon_flags.value
    finally:
        for interface in (extractor, parent):
            if interface:
                _com_method(interface, 2, wintypes.ULONG)(interface)
        if pidl:
            ole.CoTaskMemFree(pidl)


def refresh_executable_icon(executable: Path, *, refresh_shell_cache: bool = False) -> dict:
    """Refresh an executable, optionally requesting a Shell-wide icon cache reload."""
    if sys.platform != "win32":
        raise OSError("Explorer icon refresh is available only on Windows.")
    executable = executable.resolve(strict=True)
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("Specify an existing executable.")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.OleDLL("ole32")
    ole.CoInitialize.argtypes = [ctypes.c_void_p]
    ole.CoInitialize.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    shell.SHGetFileInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(_ShellFileInfo),
        wintypes.UINT,
        wintypes.UINT,
    ]
    shell.SHGetFileInfoW.restype = ctypes.c_size_t
    shell.SHUpdateImageW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.UINT, ctypes.c_int]
    shell.SHUpdateImageW.restype = None
    shell.SHChangeNotify.argtypes = [ctypes.c_long, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
    shell.SHChangeNotify.restype = None
    initialized = ole.CoInitialize(None)
    try:
        icon_source, icon_index, icon_flags = _extract_icon_location(executable, shell, ole)
        index = _ShellFileInfo()
        if not shell.SHGetFileInfoW(
            str(executable), 0, ctypes.byref(index), ctypes.sizeof(index), 0x4000 | 0x1
        ):
            raise OSError("Explorer could not resolve the executable icon.")
        shell.SHUpdateImageW(icon_source, icon_index, icon_flags, index.iIcon)
        path_buffer = ctypes.create_unicode_buffer(str(executable))
        # SHCNE_UPDATEITEM, SHCNF_PATHW | SHCNF_FLUSH.
        shell.SHChangeNotify(
            0x2000, 0x0005 | 0x1000, ctypes.cast(path_buffer, ctypes.c_void_p), None
        )
        if refresh_shell_cache:
            # SHCNE_ASSOCCHANGED, SHCNF_IDLIST | SHCNF_FLUSH. This asks the Shell
            # to invalidate its icon/thumbnail cache; it does not edit associations.
            shell.SHChangeNotify(0x08000000, 0x1000, None, None)
        return {
            "executable": str(executable),
            "icon_location": icon_source,
            "icon_index": icon_index,
            "icon_flags": icon_flags,
            "system_image_index": index.iIcon,
            "notification_sent": True,
            "refresh_scope": (
                "shell_icon_and_thumbnail_cache" if refresh_shell_cache else "executable_only"
            ),
        }
    finally:
        if initialized in (0, 1):
            ole.CoUninitialize()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=PROJECT_ROOT / "dist/marknotes/marknotes.exe")
    parser.add_argument(
        "--refresh-shell-cache",
        action="store_true",
        help="Also reload Shell icon caches when the targeted refresh is insufficient.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            refresh_executable_icon(args.exe, refresh_shell_cache=args.refresh_shell_cache),
            ensure_ascii=False,
            indent=2,
        )
    )
