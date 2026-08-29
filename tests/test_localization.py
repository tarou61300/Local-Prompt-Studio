from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from string import Formatter

from core.config_manager import ConfigManager
from core.localization import (
    DEFAULT_UI_LOCALE,
    FALLBACK_LOCALE,
    LOCALE_DEFINITIONS,
    SUPPORTED_LOCALES,
    Localization,
    locale_definition,
)


ROOT = Path(__file__).resolve().parents[1]
LOCALE_ROOT = ROOT / "locales"


def _load_without_duplicate_keys(path: Path):
    pairs: list[tuple[str, object]] = []

    def collect(values):
        pairs.extend(values)
        return dict(values)

    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=collect)
    duplicates = [
        key
        for key, count in Counter(key for key, _value in pairs).items()
        if count > 1
    ]
    return data, duplicates


def _placeholders(value: str) -> Counter[str]:
    return Counter(
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(value)
        if field_name is not None
    )


def test_all_supported_locales_load():
    assert Localization(LOCALE_ROOT, "en-US").tr("common.generate") == "Generate Prompt"
    assert Localization(LOCALE_ROOT, "ja-JP").tr("common.generate") == "Promptを生成"
    assert Localization(LOCALE_ROOT, "zh-CN").tr("common.generate") == "生成Prompt"
    assert "Wan 2.2" in Localization(LOCALE_ROOT, "en-US").tr(
        "profile.wan_2_2.description"
    )
    assert "时" in Localization(LOCALE_ROOT, "zh-CN").tr(
        "profile.ltx_2_3.description"
    )
    assert "時系列" in Localization(LOCALE_ROOT, "ja-JP").tr(
        "profile.ltx_2_3.description"
    )
    assert Localization(LOCALE_ROOT, "en-US").tr("profile.task") == "Task"
    assert (
        Localization(LOCALE_ROOT, "en-US").tr("profile.style")
        == "Prompt Transformation Style"
    )
    assert Localization(LOCALE_ROOT, "ja-JP").tr("profile.style") == "Prompt変換スタイル"
    assert Localization(LOCALE_ROOT, "zh-CN").tr("profile.style") == "Prompt转换风格"
    assert "Krea 2" in Localization(LOCALE_ROOT, "en-US").tr(
        "profile.krea_2.description"
    )
    assert "画像生成" in Localization(LOCALE_ROOT, "ja-JP").tr(
        "profile.krea_2.description"
    )
    assert "Anima" in Localization(LOCALE_ROOT, "en-US").tr(
        "profile.anima.description"
    )
    assert "Hybrid" in Localization(LOCALE_ROOT, "ja-JP").tr(
        "profile.anima.description"
    )
    assert (
        Localization(LOCALE_ROOT, "en-US").tr("output.negative")
        == "Negative Prompt (editable)"
    )
    assert "ネガティブ" in Localization(LOCALE_ROOT, "ja-JP").tr("output.negative")
    assert Localization(LOCALE_ROOT, "en-US").tr("input.guide") == "Input guide"
    assert Localization(LOCALE_ROOT, "ja-JP").tr("input.guide") == "入力ガイド"
    assert "Translating" in Localization(LOCALE_ROOT, "en-US").tr(
        "translation.status.translating"
    )
    assert "翻訳中" in Localization(LOCALE_ROOT, "ja-JP").tr(
        "translation.status.translating"
    )


def test_locale_registry_and_files_match_canonical_keys_placeholders_and_order():
    assert DEFAULT_UI_LOCALE == "ja-JP"
    assert FALLBACK_LOCALE == "en-US"
    assert SUPPORTED_LOCALES == ("ja-JP", "en-US", "zh-CN")
    assert [item.native_name for item in LOCALE_DEFINITIONS] == [
        "日本語",
        "English",
        "简体中文",
    ]
    english, duplicates = _load_without_duplicate_keys(LOCALE_ROOT / "en-US.json")
    assert duplicates == []
    assert isinstance(english, dict)
    expected_placeholders = {
        key: _placeholders(value) for key, value in english.items()
    }
    for locale_id in SUPPORTED_LOCALES:
        path = LOCALE_ROOT / f"{locale_id}.json"
        assert path.is_file()
        values, duplicates = _load_without_duplicate_keys(path)
        assert duplicates == []
        assert list(values) == list(english)
        assert all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in values.items()
        )
        assert {
            key: _placeholders(value) for key, value in values.items()
        } == expected_placeholders


def test_missing_japanese_key_falls_back_to_english(tmp_path):
    (tmp_path / "en-US.json").write_text(
        json.dumps({"only.english": "fallback"}), encoding="utf-8"
    )
    (tmp_path / "ja-JP.json").write_text("{}", encoding="utf-8")
    assert Localization(tmp_path, "ja-JP").tr("only.english") == "fallback"


def test_malformed_locale_is_safe(tmp_path):
    (tmp_path / "en-US.json").write_text(
        json.dumps({"safe": "English"}), encoding="utf-8"
    )
    (tmp_path / "ja-JP.json").write_text("not-json", encoding="utf-8")
    localization = Localization(tmp_path, "ja-JP")
    assert localization.tr("safe") == "English"
    assert localization.tr("missing.key") == "missing.key"


def test_locale_format_failure_retries_english_with_the_same_values(tmp_path):
    (tmp_path / "en-US.json").write_text(
        json.dumps({"message": "English {name}"}), encoding="utf-8"
    )
    (tmp_path / "ja-JP.json").write_text(
        json.dumps({"message": "壊れた {missing}"}), encoding="utf-8"
    )
    localization = Localization(tmp_path, "ja-JP")
    assert localization.tr("message", name="fallback") == "English fallback"
    assert localization.tr("message") == "English {name}"


def test_unknown_locale_uses_japanese_ui_default(tmp_path):
    (tmp_path / "en-US.json").write_text(
        json.dumps({"value": "English"}), encoding="utf-8"
    )
    (tmp_path / "ja-JP.json").write_text(
        json.dumps({"value": "日本語"}, ensure_ascii=False), encoding="utf-8"
    )
    localization = Localization(tmp_path, "unsupported")
    assert localization.locale_id == "ja-JP"
    assert localization.tr("value") == "日本語"
    assert locale_definition("unsupported").locale_id == "ja-JP"


def test_existing_config_migrates_to_japanese_and_chinese_locale_persists(tmp_path):
    manager = ConfigManager(tmp_path)
    manager.path.parent.mkdir(parents=True, exist_ok=True)
    manager.path.write_text('{"config_version": 4}', encoding="utf-8")
    assert manager.load().ui_locale == "ja-JP"
    config = manager.load()
    config.ui_locale = "zh-CN"
    manager.save(config)
    assert manager.load().ui_locale == "zh-CN"
