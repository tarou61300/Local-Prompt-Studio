from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .inference_backends import BACKEND_CPU, BACKENDS, GPU_LAYERS_AUTO, normalize_backend_id


PORTABLE_WRITE_ERROR = (
    "この場所には設定を書き込めません。Downloads、Documents、Desktop等の"
    "書き込み可能なフォルダへ解凍してください。"
)
CONFIG_VERSION = 5
DEFAULT_CONTEXT_SIZE = 8192
DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"
CONTEXT_PRESETS = (
    (4096, "Low Memory"),
    (8192, "Recommended"),
    (16384, "Large"),
    (32768, "Very Large / high memory"),
)


def default_user_data_dir() -> Path:
    """Return the project-local writable data directory used by source runs."""
    return Path(__file__).resolve().parents[2] / ".dev-data"


@dataclass(slots=True)
class AppConfig:
    config_version: int = CONFIG_VERSION
    model_path: str = ""
    inference_backend: str = BACKEND_CPU
    backend_device: str = ""
    cpu_threads: int = 0  # 0 means Auto
    gpu_layers: int = GPU_LAYERS_AUTO
    context_size: int = DEFAULT_CONTEXT_SIZE
    skill_location: str = ""
    history_enabled: bool = False
    theme: str = "System"
    setup_completed: bool = False
    comfyui_url: str = field(default=DEFAULT_COMFYUI_URL, repr=False)
    ui_locale: str = "ja-JP"
    selected_profile: str = "minimax_h3"
    selected_variant: str = "base"

    def normalized(self) -> "AppConfig":
        self.inference_backend = normalize_backend_id(self.inference_backend)
        if self.inference_backend not in BACKENDS:
            self.inference_backend = BACKEND_CPU
        self.backend_device = str(self.backend_device).strip()
        self.cpu_threads = max(0, int(self.cpu_threads))
        self.gpu_layers = max(GPU_LAYERS_AUTO, int(self.gpu_layers))
        self.context_size = max(2048, int(self.context_size))
        self.theme = self.theme if self.theme in {"System", "Light", "Dark"} else "System"
        if not isinstance(self.comfyui_url, str) or not self.comfyui_url.strip():
            self.comfyui_url = DEFAULT_COMFYUI_URL
        else:
            self.comfyui_url = self.comfyui_url.strip()
        self.ui_locale = self.ui_locale if self.ui_locale in {"en-US", "ja-JP"} else "ja-JP"
        if not isinstance(self.selected_profile, str) or not self.selected_profile.strip():
            self.selected_profile = "minimax_h3"
        if not isinstance(self.selected_variant, str) or not self.selected_variant.strip():
            self.selected_variant = "base"
        return self


class ConfigManager:
    """Loads and atomically saves non-secret application preferences as JSON."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_user_data_dir()
        self.path = self.data_dir / "config.json"

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            raw: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
            stored_version = int(raw.get("config_version", 1))
            # v1 used 32768 as its default. Migrate that unsafe default for existing users.
            if stored_version < 2 and int(raw.get("context_size", 32768)) == 32768:
                raw["context_size"] = DEFAULT_CONTEXT_SIZE
            if stored_version < 3:
                legacy_device = str(raw.pop("inference_device", "CPU"))
                # The old NVIDIA option was never shipped with a validated runtime.
                # Migrate it to the safe CPU fallback; Vulkan must be selected explicitly.
                raw["inference_backend"] = (
                    "vulkan" if legacy_device.strip().lower() == "vulkan gpu" else BACKEND_CPU
                )
                raw.setdefault("backend_device", "")
                if int(raw.get("gpu_layers", 0)) == 0:
                    raw["gpu_layers"] = GPU_LAYERS_AUTO
            if stored_version < 4:
                raw.setdefault("comfyui_url", DEFAULT_COMFYUI_URL)
            if stored_version < 5:
                # v1 had a Japanese-first UI. Preserve that experience on upgrade.
                raw.setdefault("ui_locale", "ja-JP")
                raw.setdefault("selected_profile", "minimax_h3")
                raw.setdefault("selected_variant", "base")
            raw["config_version"] = CONFIG_VERSION
            allowed = {item.name for item in fields(AppConfig)}
            values = {key: value for key, value in raw.items() if key in allowed}
            return AppConfig(**values).normalized()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A damaged settings file must never prevent the window from opening.
            return AppConfig()

    def ensure_writable(self) -> None:
        """Fail before the GUI workflow if portable data cannot be written."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="write-test-", suffix=".tmp", dir=self.data_dir
        )
        os.close(file_descriptor)
        Path(temporary_name).unlink()

    def save(self, config: AppConfig) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(config.normalized()), ensure_ascii=False, indent=2)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="config-", suffix=".tmp", dir=self.data_dir
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.write("\n")
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def reset(self) -> AppConfig:
        config = AppConfig()
        self.save(config)
        return config
