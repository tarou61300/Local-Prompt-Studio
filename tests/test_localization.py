from __future__ import annotations

import json

from core.config_manager import ConfigManager
from core.localization import Localization


def test_english_and_japanese_locales_load():
    root = __import__("pathlib").Path(__file__).resolve().parents[1] / "locales"
    assert Localization(root, "en-US").tr("common.generate") == "Generate Prompt"
    assert Localization(root, "ja-JP").tr("common.generate") == "Promptを生成"
    assert "Wan 2.2" in Localization(root, "en-US").tr(
        "profile.wan_2_2.description"
    )
    assert "時系列" in Localization(root, "ja-JP").tr(
        "profile.ltx_2_3.description"
    )
    assert Localization(root, "en-US").tr("profile.task") == "Task"
    assert "Krea 2" in Localization(root, "en-US").tr(
        "profile.krea_2.description"
    )
    assert "画像生成" in Localization(root, "ja-JP").tr(
        "profile.krea_2.description"
    )


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


def test_existing_config_migrates_to_japanese_and_locale_persists(tmp_path):
    manager = ConfigManager(tmp_path)
    manager.path.parent.mkdir(parents=True, exist_ok=True)
    manager.path.write_text('{"config_version": 4}', encoding="utf-8")
    assert manager.load().ui_locale == "ja-JP"
    config = manager.load()
    config.ui_locale = "en-US"
    manager.save(config)
    assert manager.load().ui_locale == "en-US"
