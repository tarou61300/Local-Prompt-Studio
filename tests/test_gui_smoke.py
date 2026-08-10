from __future__ import annotations

import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.setup_dialog import SetupDialog
from app.settings_dialog import SettingsDialog
from core.config_manager import AppConfig
from core.config_manager import CONTEXT_PRESETS
from core.config_manager import ConfigManager
from core.inference_backends import (
    BACKEND_CPU,
    BACKEND_VULKAN,
    GPU_LAYERS_AUTO,
    BackendDevice,
)
from core.system_memory import GIB, MemoryInfo
from mock_server import start_mock_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def test_main_window_constructs_without_model(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        assert window.windowTitle() == "MMH3 Prompt Builder v1.0.0"
        assert "未設定" in window.readiness.text()
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_generate_button_flow_uses_mock_without_model(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server(delay=0.2)
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.request_text.setPlainText("女性が手を振る。台詞は「またね」")
        window.generate()
        assert window.worker is not None and window.worker.isRunning()
        assert window.request_text.isEnabled()
        window.duration.setValue(11)
        app.processEvents()
        assert window.duration.value() == 11
        deadline = time.monotonic() + 5
        while window.worker is not None and window.worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert window.worker is not None and not window.worker.isRunning()
        assert window.output_text.toPlainText().startswith("A 10-second")
        assert "「またね」" in window.output_text.toPlainText()
        assert "<think>" not in window.output_text.toPlainText()
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_settings_context_presets_and_default(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(ConfigManager(tmp_path), PROJECT_ROOT)
    try:
        assert [dialog.context_size.itemData(index) for index in range(4)] == [
            value for value, _ in CONTEXT_PRESETS
        ]
        assert dialog.context_size.currentData() == 8192
        assert "Recommended" in dialog.context_size.currentText()
        assert "Very Large" in dialog.context_size.itemText(3)
    finally:
        dialog.close()
        app.processEvents()


def test_packaged_setup_installs_skill_only_in_portable_data(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    external_skill = tmp_path / "external" / "h3-prompt-writing"
    manager = ConfigManager(tmp_path / "portable" / "data")
    manager.save(AppConfig(skill_location=str(external_skill)))
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.detect_vulkan_devices",
        lambda self: [],
    )
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.runtime_available",
        lambda self, backend: True,
    )
    dialog = SetupDialog(
        manager,
        PROJECT_ROOT,
        enforce_portable_skill_storage=True,
    )
    try:
        assert dialog.skill_path == manager.data_dir / "skills" / "h3-prompt-writing"
        assert dialog.skill_path != external_skill
        assert not external_skill.exists()
    finally:
        dialog.close()
        app.processEvents()


def test_settings_model_change_updates_metadata_warning_and_refreshes_ram(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    measurements: list[int] = []

    def fake_memory():
        measurements.append(1)
        return MemoryInfo(total_bytes=6 * GIB, available_bytes=1 * GIB)

    monkeypatch.setattr("app.settings_dialog.get_system_memory", fake_memory)
    model_8b = tmp_path / "Qwen3-8B-Q4_K_M.gguf"
    model_4b = tmp_path / "qwen3-4b-q4_k_m.gguf"
    model_8b.write_bytes(b"8" * 1024)
    model_4b.write_bytes(b"4" * 2048)
    dialog = SettingsDialog(ConfigManager(tmp_path / "data"), PROJECT_ROOT)
    try:
        dialog.model_path.setText(str(model_8b))
        assert "Qwen3-8B Q4_K_M" in dialog.model_info.text()
        assert "1,024 bytes" in dialog.model_info.text()

        before_model_change = len(measurements)
        dialog.model_path.setText(str(model_4b))
        assert len(measurements) > before_model_change
        assert "Qwen3-4B Q4_K_M" in dialog.model_info.text()
        assert "qwen3-4b-q4_k_m.gguf" in dialog.model_info.text()
        assert "2,048 bytes" in dialog.model_info.text()
        assert "Qwen3-4B Q4_K_M" in dialog.memory_info.text()
        assert "Qwen3-8B" not in dialog.memory_info.text()

        before_context_change = len(measurements)
        dialog.context_size.setCurrentIndex(dialog.context_size.findData(4096))
        assert len(measurements) > before_context_change
        assert "Context: 4096" in dialog.memory_info.text()
    finally:
        dialog.close()
        app.processEvents()


def test_generate_preflight_uses_fresh_ram_and_labels_total_separately(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    measurements: list[MemoryInfo] = []

    def fake_memory():
        value = MemoryInfo(total_bytes=int(12.7 * GIB), available_bytes=int(5.3 * GIB))
        measurements.append(value)
        return value

    monkeypatch.setattr("app.main_window.get_system_memory", fake_memory)
    monkeypatch.setattr("app.main_window.GenerationThread.start", lambda self: None)
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(skill_location=str(FIXTURE), setup_completed=True))
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:1",
        dev_skill_path=FIXTURE,
    )
    try:
        assert "Available 5.3 GB" in window.memory_status.text()
        assert "Total 12.7 GB" in window.memory_status.text()
        baseline = len(measurements)
        window.request_text.setPlainText("短いテスト")
        window.generate()
        assert len(measurements) >= baseline + 2
    finally:
        window.close()
        app.processEvents()


def test_settings_switches_between_cpu_and_vulkan_with_auto_layers(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.detect_vulkan_devices",
        lambda self: [BackendDevice("Vulkan0", "AMD Radeon Graphics", is_uma=True)],
    )
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.runtime_available",
        lambda self, backend: True,
    )
    manager = ConfigManager(tmp_path)
    dialog = SettingsDialog(manager, PROJECT_ROOT)
    try:
        assert dialog.backend.currentData() == BACKEND_CPU
        vulkan_index = dialog.backend.findData(BACKEND_VULKAN)
        dialog.backend.setCurrentIndex(vulkan_index)
        assert dialog.backend.currentData() == BACKEND_VULKAN
        assert "AMD / Intel / NVIDIA" in dialog.backend.currentText()
        assert dialog.backend_device.currentData() == "Vulkan0"
        assert "AMD Radeon Graphics (Vulkan0)" in dialog.backend_info.text()
        assert "UMA" in dialog.backend_info.text()
        assert dialog.gpu_layers.value() == GPU_LAYERS_AUTO
        assert dialog.gpu_layers.text() == "Auto"
        dialog.accept()
        saved = manager.load()
        assert saved.inference_backend == BACKEND_VULKAN
        assert saved.backend_device == "Vulkan0"
        assert saved.gpu_layers == GPU_LAYERS_AUTO
    finally:
        dialog.close()
        app.processEvents()


def test_window_close_terminates_only_owned_llama_server(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()

    class FakeOwnedProcess:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout):
            return 0

        def kill(self):
            self.running = False

    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        owned = FakeOwnedProcess()
        window.server.process = owned
        window.close()
        app.processEvents()
        assert owned.running is False
        assert window.server.process is None
    finally:
        mock.shutdown()
        mock.server_close()


def test_settings_disables_vulkan_when_llama_reports_no_device(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.detect_vulkan_devices",
        lambda self: [],
    )
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.runtime_available",
        lambda self, backend: True,
    )
    dialog = SettingsDialog(ConfigManager(tmp_path), PROJECT_ROOT)
    try:
        vulkan_index = dialog.backend.findData(BACKEND_VULKAN)
        assert dialog.backend.model().item(vulkan_index).isEnabled() is False
        assert dialog.backend.currentData() == BACKEND_CPU
        assert "検出されていません" in dialog.backend_info.text()
    finally:
        dialog.close()
        app.processEvents()
