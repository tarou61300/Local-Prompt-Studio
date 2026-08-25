from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImageReader, QImageWriter


SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class ChatImageError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _detected_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _webp_is_animated(data: bytes) -> bool:
    """Return whether a structurally valid WebP chunk declares animation."""
    if len(data) < 12 or not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
        return False
    offset = 12
    while offset + 8 <= len(data):
        chunk_type = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end > len(data):
            break
        if chunk_type in {b"ANIM", b"ANMF"}:
            return True
        if (
            chunk_type == b"VP8X"
            and chunk_size >= 1
            and data[payload_start] & 0x02
        ):
            return True
        offset = payload_end + (chunk_size & 1)
    return False


def _static_webp_as_png(data: bytes) -> bytes:
    """Decode one static WebP and encode it as PNG without touching disk."""
    if _webp_is_animated(data):
        raise ChatImageError("CHAT_IMAGE_ANIMATED_WEBP_UNSUPPORTED")

    source_data = QByteArray(data)
    source = QBuffer()
    source.setData(source_data)
    if not source.open(QIODevice.OpenModeFlag.ReadOnly):
        raise ChatImageError("CHAT_IMAGE_DECODE_FAILED")
    try:
        reader = QImageReader(source, b"webp")
        if not reader.canRead():
            raise ChatImageError("CHAT_IMAGE_DECODE_FAILED")
        if reader.supportsAnimation():
            raise ChatImageError("CHAT_IMAGE_ANIMATED_WEBP_UNSUPPORTED")
        image = reader.read()
    finally:
        source.close()
    if image.isNull():
        raise ChatImageError("CHAT_IMAGE_DECODE_FAILED")

    normalized_data = QByteArray()
    target = QBuffer(normalized_data)
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ChatImageError("CHAT_IMAGE_DECODE_FAILED")
    try:
        writer = QImageWriter(target, b"png")
        if not writer.write(image):
            raise ChatImageError("CHAT_IMAGE_DECODE_FAILED")
    finally:
        target.close()
    png_data = bytes(normalized_data)
    if _detected_image_mime(png_data) != "image/png":
        raise ChatImageError("CHAT_IMAGE_DECODE_FAILED")
    return png_data


@dataclass(frozen=True, slots=True)
class ChatImageAttachment:
    """One in-memory image attachment; raw data and paths never appear in repr/logs."""

    filename: str
    mime_type: str
    _data: bytes = field(repr=False)
    source_path: str = field(default="", repr=False)

    @classmethod
    def from_file(cls, path: Path | str) -> "ChatImageAttachment":
        image_path = Path(path)
        extension = image_path.suffix.lower()
        if extension not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ChatImageError("CHAT_IMAGE_UNSUPPORTED_FORMAT")
        try:
            data = image_path.read_bytes()
        except OSError as exc:
            raise ChatImageError("CHAT_IMAGE_READ_FAILED") from exc
        mime_type = _detected_image_mime(data)
        expected_mimes = {
            ".png": {"image/png"},
            ".jpg": {"image/jpeg"},
            ".jpeg": {"image/jpeg"},
            ".webp": {"image/webp"},
        }[extension]
        if mime_type not in expected_mimes:
            raise ChatImageError("CHAT_IMAGE_DECODE_FAILED")
        if mime_type == "image/webp":
            data = _static_webp_as_png(data)
            mime_type = "image/png"
        return cls(
            filename=image_path.name,
            mime_type=mime_type,
            _data=data,
            source_path=str(image_path.resolve(strict=False)),
        )

    @property
    def size_bytes(self) -> int:
        return len(self._data)

    @property
    def image_bytes(self) -> bytes:
        """Immutable in-memory bytes for local thumbnail decoding and localhost send."""
        return self._data

    def data_url(self) -> str:
        encoded = base64.b64encode(self._data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"
