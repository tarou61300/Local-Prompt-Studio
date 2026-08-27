from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.config_manager import (
    AppConfig,
    ConfigManager,
    CONFIG_VERSION,
    DEFAULT_COMFYUI_URL,
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_PROMPT_LIBRARY_DETAIL_LINES,
    DEFAULT_PROMPT_LIBRARY_RESULT_ROWS,
    DEFAULT_PROMPT_LIBRARY_TAG_ROWS,
    THEME_DARK,
    THEME_NORMAL,
    normalize_model_path_key,
)
from core.inference_backends import BACKEND_CPU, BACKEND_VULKAN, GPU_LAYERS_AUTO


def test_default_manager_uses_project_local_dev_data():
    manager = ConfigManager()
    assert manager.data_dir == Path(__file__).resolve().parents[1] / ".dev-data"


def test_settings_save_and_load(tmp_path):
    manager = ConfigManager(tmp_path)
    expected = AppConfig(
        model_path=r"D:\Models\Qwen3-8B-Q4_K_M.gguf",
        cpu_threads=8,
        history_enabled=True,
        auto_quality_tags=False,
        skill_location=r"D:\Skills\h3-prompt-writing",
    )
    manager.save(expected)
    actual = manager.load()
    assert actual.model_path == expected.model_path
    assert actual.cpu_threads == 8
    assert actual.history_enabled is True
    assert actual.auto_quality_tags is False
    assert manager.path.is_file()
    stored_text = manager.path.read_text(encoding="utf-8")
    assert "client_credential" not in stored_text
    assert "Authorization" not in stored_text


def test_default_context_is_8192():
    assert CONFIG_VERSION == 8
    assert AppConfig().context_size == 8192
    assert DEFAULT_CONTEXT_SIZE == 8192
    assert AppConfig().comfyui_url == DEFAULT_COMFYUI_URL
    assert AppConfig().inference_backend == BACKEND_CPU
    assert AppConfig().gpu_layers == GPU_LAYERS_AUTO
    assert AppConfig().ui_locale == "ja-JP"
    assert AppConfig().selected_profile == "minimax_h3"
    assert AppConfig().selected_variant == "base"
    assert AppConfig().auto_quality_tags is True
    assert AppConfig().use_prompt_model_for_chat is True
    assert AppConfig().chat_model_path == ""
    assert AppConfig().model_mmproj_paths == {}
    assert AppConfig().theme == THEME_NORMAL
    assert AppConfig().prompt_library_tag_rows == DEFAULT_PROMPT_LIBRARY_TAG_ROWS
    assert AppConfig().prompt_library_result_rows == DEFAULT_PROMPT_LIBRARY_RESULT_ROWS
    assert (
        AppConfig().prompt_library_detail_lines
        == DEFAULT_PROMPT_LIBRARY_DETAIL_LINES
    )


@pytest.mark.parametrize(
    ("stored_theme", "expected"),
    [
        (None, THEME_NORMAL),
        ("System", THEME_NORMAL),
        ("system", THEME_NORMAL),
        ("Light", THEME_NORMAL),
        ("light", THEME_NORMAL),
        ("default", THEME_NORMAL),
        ("Normal", THEME_NORMAL),
        ("normal", THEME_NORMAL),
        ("Dark", THEME_DARK),
        ("dark", THEME_DARK),
        ("invalid", THEME_NORMAL),
    ],
)
def test_legacy_and_current_theme_values_normalize_safely(
    tmp_path,
    stored_theme,
    expected,
):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps({"config_version": CONFIG_VERSION, "theme": stored_theme}),
        encoding="utf-8",
    )

    assert manager.load().theme == expected


def test_dark_theme_round_trip_preserves_other_settings(tmp_path):
    manager = ConfigManager(tmp_path)
    manager.save(
        AppConfig(
            theme=THEME_DARK,
            context_size=16384,
            history_enabled=True,
            auto_quality_tags=False,
        )
    )

    loaded = manager.load()
    assert loaded.theme == THEME_DARK
    assert loaded.context_size == 16384
    assert loaded.history_enabled is True
    assert loaded.auto_quality_tags is False

def test_v6_config_migrates_chat_model_defaults_without_losing_values(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps(
            {
                "config_version": 6,
                "model_path": r"D:\Models\Prompt.gguf",
                "theme": "Dark",
                "history_enabled": True,
                "auto_quality_tags": False,
            }
        ),
        encoding="utf-8",
    )

    loaded = manager.load()

    assert loaded.config_version == CONFIG_VERSION
    assert loaded.model_path == r"D:\Models\Prompt.gguf"
    assert loaded.theme == THEME_DARK
    assert loaded.history_enabled is True
    assert loaded.auto_quality_tags is False
    assert loaded.use_prompt_model_for_chat is True
    assert loaded.chat_model_path == ""
    assert loaded.model_mmproj_paths == {}


def test_separate_chat_model_and_per_model_mmproj_round_trip(tmp_path):
    model_a = tmp_path / "models-a" / "same-name.gguf"
    model_b = tmp_path / "models-b" / "same-name.gguf"
    mmproj_a = tmp_path / "vision" / "a-mmproj.gguf"
    mmproj_b = tmp_path / "vision" / "b-mmproj.gguf"
    config = AppConfig(
        model_path=str(model_a),
        use_prompt_model_for_chat=False,
        chat_model_path=str(model_b),
    )
    config.set_mmproj_for_model(model_a, mmproj_a)
    config.set_mmproj_for_model(model_b, mmproj_b)
    manager = ConfigManager(tmp_path / "data")

    manager.save(config)
    loaded = manager.load()

    assert loaded.use_prompt_model_for_chat is False
    assert loaded.effective_chat_model_path() == str(model_b)
    assert loaded.mmproj_for_model(model_a) == str(mmproj_a.resolve())
    assert loaded.mmproj_for_model(model_b) == str(mmproj_b.resolve())
    assert len(loaded.model_mmproj_paths) == 2
    assert normalize_model_path_key(model_a) != normalize_model_path_key(model_b)


def test_malformed_mmproj_mapping_is_safely_discarded(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps({"config_version": 7, "model_mmproj_paths": ["not", "a", "map"]}),
        encoding="utf-8",
    )
    assert manager.load().model_mmproj_paths == {}


def test_v1_default_context_is_migrated(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps({"config_version": 1, "context_size": 32768, "setup_completed": True}),
        encoding="utf-8",
    )
    migrated = manager.load()
    assert migrated.config_version == CONFIG_VERSION
    assert migrated.context_size == 8192
    assert migrated.setup_completed is True
    assert migrated.comfyui_url == DEFAULT_COMFYUI_URL


def test_current_version_keeps_explicit_32768(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps({"config_version": CONFIG_VERSION, "context_size": 32768}),
        encoding="utf-8",
    )
    assert manager.load().context_size == 32768


def test_damaged_or_unknown_settings_are_safe(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text("not-json", encoding="utf-8")
    assert manager.load() == AppConfig()
    manager.path.write_text(json.dumps({"unknown": 1, "theme": "Invalid"}), encoding="utf-8")
    assert manager.load().theme == THEME_NORMAL
    manager.path.write_text(json.dumps({"config_version": 4, "comfyui_url": None}), encoding="utf-8")
    assert manager.load().comfyui_url == DEFAULT_COMFYUI_URL


def test_v2_nvidia_setting_migrates_to_safe_cpu_fallback(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps(
            {
                "config_version": 2,
                "inference_device": "NVIDIA GPU",
                "gpu_layers": -1,
            }
        ),
        encoding="utf-8",
    )
    migrated = manager.load()
    assert migrated.config_version == CONFIG_VERSION
    assert migrated.inference_backend == BACKEND_CPU
    assert migrated.comfyui_url == DEFAULT_COMFYUI_URL


def test_v3_config_migrates_to_current_with_default_comfyui_url(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps({"config_version": 3, "theme": "Dark"}),
        encoding="utf-8",
    )
    migrated = manager.load()
    assert migrated.config_version == CONFIG_VERSION
    assert migrated.theme == THEME_DARK
    assert migrated.comfyui_url == DEFAULT_COMFYUI_URL


def test_existing_v4_comfyui_url_is_preserved_and_unknown_fields_are_filtered(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps(
            {
                "config_version": 4,
                "comfyui_url": "https://remote.example.com",
                "unknown_future_setting": "ignored",
            }
        ),
        encoding="utf-8",
    )
    loaded = manager.load()
    assert loaded.config_version == CONFIG_VERSION
    assert loaded.comfyui_url == "https://remote.example.com"
    assert loaded.ui_locale == "ja-JP"
    assert not hasattr(loaded, "unknown_future_setting")


def test_v5_locale_and_profile_ids_round_trip(tmp_path):
    manager = ConfigManager(tmp_path)
    manager.save(
        AppConfig(
            ui_locale="en-US",
            selected_profile="minimax_h3",
            selected_variant="base",
        )
    )
    loaded = manager.load()
    assert loaded.ui_locale == "en-US"
    assert loaded.selected_profile == "minimax_h3"
    assert loaded.selected_variant == "base"


def test_v5_config_migrates_with_auto_quality_tags_enabled(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps({"config_version": 5, "auto_quality_tags": None}),
        encoding="utf-8",
    )

    loaded = manager.load()

    assert loaded.config_version == CONFIG_VERSION
    assert loaded.auto_quality_tags is True


def test_vulkan_backend_device_and_explicit_layers_round_trip(tmp_path):
    manager = ConfigManager(tmp_path)
    manager.save(
        AppConfig(
            inference_backend=BACKEND_VULKAN,
            backend_device="Vulkan0",
            gpu_layers=20,
        )
    )
    loaded = manager.load()
    assert loaded.inference_backend == BACKEND_VULKAN
    assert loaded.backend_device == "Vulkan0"
    assert loaded.gpu_layers == 20

def test_prompt_library_display_settings_normalize_and_migrate(tmp_path):
    manager = ConfigManager(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager.path.write_text(
        json.dumps(
            {
                "config_version": 7,
                "prompt_library_tag_rows": 99,
                "prompt_library_result_rows": 0,
                "prompt_library_detail_lines": "invalid",
            }
        ),
        encoding="utf-8",
    )

    loaded = manager.load()

    assert loaded.config_version == CONFIG_VERSION
    assert loaded.prompt_library_tag_rows == 15
    assert loaded.prompt_library_result_rows == 2
    assert loaded.prompt_library_detail_lines == DEFAULT_PROMPT_LIBRARY_DETAIL_LINES
