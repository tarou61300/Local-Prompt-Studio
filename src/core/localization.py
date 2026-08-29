from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class LocaleDefinition:
    locale_id: str
    native_name: str
    llm_language_name: str
    output_language_code: str


LOCALE_DEFINITIONS = (
    LocaleDefinition("ja-JP", "日本語", "Japanese", "ja"),
    LocaleDefinition("en-US", "English", "English", "en"),
    LocaleDefinition("zh-CN", "简体中文", "Simplified Chinese", "zh"),
    LocaleDefinition("ru-RU", "Русский", "Russian", "ru"),
)
DEFAULT_UI_LOCALE = "ja-JP"
FALLBACK_LOCALE = "en-US"
SUPPORTED_LOCALES = tuple(item.locale_id for item in LOCALE_DEFINITIONS)
_LOCALE_BY_ID = {item.locale_id: item for item in LOCALE_DEFINITIONS}
_LOCALE_BY_LANGUAGE_CODE = {
    item.output_language_code: item for item in LOCALE_DEFINITIONS
}
_LOGGER = logging.getLogger(__name__)


def locale_definition(locale_id: str) -> LocaleDefinition:
    return _LOCALE_BY_ID.get(locale_id, _LOCALE_BY_ID[DEFAULT_UI_LOCALE])


def language_definition(language_code: str) -> LocaleDefinition | None:
    normalized = (
        str(language_code).strip().replace("_", "-").split("-", 1)[0].casefold()
    )
    return _LOCALE_BY_LANGUAGE_CODE.get(normalized)


def locale_matches_language(locale_id: str, language_code: str) -> bool:
    language = language_definition(language_code)
    return (
        language is not None
        and locale_definition(locale_id).output_language_code
        == language.output_language_code
    )


class Localization:
    """Small UTF-8 JSON locale loader with English fallback."""

    def __init__(
        self,
        locale_root: Path | str,
        locale_id: str = DEFAULT_UI_LOCALE,
    ) -> None:
        self.locale_root = Path(locale_root)
        self.locale_id = (
            locale_id if locale_id in SUPPORTED_LOCALES else DEFAULT_UI_LOCALE
        )
        self._english = self._read_locale(FALLBACK_LOCALE)
        self._selected = (
            self._english
            if self.locale_id == FALLBACK_LOCALE
            else self._read_locale(self.locale_id)
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
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            fallback = self._english.get(key, key)
            try:
                return fallback.format(**values)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                return fallback
