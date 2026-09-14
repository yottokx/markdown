"""Package the generated PNG as a Windows ICO with multiple resolutions.

Run ``uv run python tools/build_app_icon.py`` after replacing marknotes-icon.png.
This only resizes and packages the artwork; it does not build the application.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

RESOURCES = Path(__file__).resolve().parents[1] / "src/marknotes/resources"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def build_icon(source: Path, destination: Path) -> None:
    original = QImage(str(source))
    if original.isNull() or original.width() != original.height():
        raise ValueError("The source must be a readable square image.")
    if original.width() < max(SIZES):
        raise ValueError("The source must be at least 256 by 256 pixels.")
    original = original.convertToFormat(QImage.Format.Format_ARGB32)
    frames = []
    for size in SIZES:
        frame = original.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not frame.save(buffer, "PNG"):
            raise OSError(f"Cannot encode the {size}px icon.")
        frames.append(bytes(buffer.data()))
    # Windows ICO accepts PNG frames, preserving the generated alpha channel.
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    offset = 6 + 16 * len(frames)
    for size, frame in zip(SIZES, frames, strict=True):
        directory.extend(
            struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(frame), offset)
        )
        offset += len(frame)
    destination.write_bytes(bytes(directory) + b"".join(frames))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=RESOURCES / "marknotes-icon.png")
    parser.add_argument("--output", type=Path, default=RESOURCES / "marknotes-icon.ico")
    arguments = parser.parse_args()
    build_icon(arguments.source, arguments.output)
    print(f"Created {arguments.output} ({', '.join(map(str, SIZES))}px)")
