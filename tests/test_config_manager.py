from __future__ import annotations

import json
from pathlib import Path

from core.config_manager import AppConfig, ConfigManager, CONFIG_VERSION, DEFAULT_CONTEXT_SIZE
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
        skill_location=r"D:\Skills\h3-prompt-writing",
    )
    manager.save(expected)
    actual = manager.load()
    assert actual.model_path == expected.model_path
    assert actual.cpu_threads == 8
    assert actual.history_enabled is True
    assert manager.path.is_file()


def test_default_context_is_8192():
    assert AppConfig().context_size == 8192
    assert DEFAULT_CONTEXT_SIZE == 8192
    assert AppConfig().inference_backend == BACKEND_CPU
    assert AppConfig().gpu_layers == GPU_LAYERS_AUTO


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
    assert manager.load().theme == "System"


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
