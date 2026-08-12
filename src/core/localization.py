from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


DEFAULT_LOCALE = "en-US"
SUPPORTED_LOCALES = ("en-US", "ja-JP")
_LOGGER = logging.getLogger(__name__)


class Localization:
    """Small UTF-8 JSON locale loader with English fallback."""

    def __init__(self, locale_root: Path | str, locale_id: str = DEFAULT_LOCALE) -> None:
        self.locale_root = Path(locale_root)
        self.locale_id = locale_id if locale_id in SUPPORTED_LOCALES else DEFAULT_LOCALE
        self._english = self._read_locale(DEFAULT_LOCALE)
        self._selected = (
            self._english if self.locale_id == DEFAULT_LOCALE else self._read_locale(self.locale_id)
        )

    def _read_locale(self, locale_id: str) -> dict[str, str]:
        path = self.locale_root / f"{locale_id}.json"
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("locale root must be an object")
            return {
                str(key): value
                for key, value in raw.items()
                if isinstance(key, str) and isinstance(value, str)
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Locale load failed", extra={"locale_id": locale_id, "error": type(exc).__name__})
            return {}

    def tr(self, key: str, **values: object) -> str:
        text = self._selected.get(key, self._english.get(key, key))
        try:
            return text.format(**values)
        except (KeyError, ValueError):
            return self._english.get(key, key)
