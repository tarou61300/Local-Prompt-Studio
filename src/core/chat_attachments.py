from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path


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
