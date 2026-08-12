from __future__ import annotations

import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

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
from core.skill_manager import SkillManager
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
        assert window.windowTitle() == "Local Prompt Studio v2.0.0-alpha.1"
        assert window.profile_category.currentData() == "video"
        assert window.profile_model.currentData() == "minimax_h3"
        assert window.profile_variant.currentData() == "base"
        assert [
            window.profile_model.itemData(index)
            for index in range(window.profile_model.count())
        ] == ["ltx_2_3", "minimax_h3", "wan_2_2"]
        assert [window.mode.itemData(index) for index in range(window.mode.count())] == [
            "T2VA",
            "I2VA",
            "FL2VA",
            "L2VA",
            "Ref2VA",
        ]
        assert window.legacy_video_settings_group.isHidden() is False
        assert "未設定" in window.readiness.text()
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_model_switch_repopulates_variants_tasks_controls_and_persists(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )

        window.profile_model.setCurrentIndex(window.profile_model.findData("wan_2_2"))
        app.processEvents()
        assert window.profile.manifest.id == "wan_2_2"
        assert [
            window.profile_variant.itemData(index)
            for index in range(window.profile_variant.count())
        ] == ["a14b"]
        assert [window.mode.itemData(index) for index in range(window.mode.count())] == [
            "T2V",
            "I2V",
        ]
        assert window.legacy_video_settings_group.isHidden() is True
        assert window.mode_group.isHidden() is True
        assert "H3 Skill" not in window.readiness.text()
        assert manager.load().selected_profile == "wan_2_2"
        assert manager.load().selected_variant == "a14b"

        window.profile_model.setCurrentIndex(window.profile_model.findData("ltx_2_3"))
        app.processEvents()
        assert window.profile.manifest.id == "ltx_2_3"
        assert [
            window.profile_variant.itemData(index)
            for index in range(window.profile_variant.count())
        ] == ["dev", "distilled_1_1"]
        assert window.profile_variant.currentData() == "distilled_1_1"
        window.profile_variant.setCurrentIndex(window.profile_variant.findData("dev"))
        assert manager.load().selected_profile == "ltx_2_3"
        assert manager.load().selected_variant == "dev"

        window.profile_model.setCurrentIndex(window.profile_model.findData("minimax_h3"))
        app.processEvents()
        assert window.profile.manifest.id == "minimax_h3"
        assert window.legacy_video_settings_group.isHidden() is False
        assert "H3 Skill" in window.readiness.text()
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_image_category_selects_krea_2_and_repopulates_variants_and_task(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    mock, url = start_mock_server(
        response_text='A small storefront sign reading "月夜珈琲" at night.'
    )
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )

        image_index = window.profile_category.findData("image")
        assert image_index >= 0
        window.profile_category.setCurrentIndex(image_index)
        app.processEvents()

        assert window.profile_category.currentData() == "image"
        assert window.profile_model.currentData() == "krea_2"
        assert window.profile.manifest.id == "krea_2"
        assert [
            window.profile_variant.itemData(index)
            for index in range(window.profile_variant.count())
        ] == ["raw", "turbo"]
        assert window.profile_variant.currentData() == "turbo"
        assert [window.mode.itemData(index) for index in range(window.mode.count())] == [
            "T2I",
        ]
        assert window.legacy_video_settings_group.isHidden() is True
        assert window.mode_group.isHidden() is True
        assert "H3 Skill" not in window.readiness.text()
        assert manager.load().selected_profile == "krea_2"
        assert manager.load().selected_variant == "turbo"

        window.request_text.setPlainText("[text:ja] 月夜珈琲")
        window.generate()
        assert window.worker is not None
        deadline = time.monotonic() + 5
        while window.worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert window.worker.isRunning() is False
        assert "月夜珈琲" in window.output_text.toPlainText()

        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_unavailable_configured_profile_falls_back_to_minimax_h3(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(selected_profile="missing_profile", selected_variant="missing"))
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        assert window.profile.manifest.id == "minimax_h3"
        assert window.profile_model.currentData() == "minimax_h3"
        assert window.profile_variant.currentData() == "base"
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_h3_generation_requires_skill_but_wan_and_ltx_do_not(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args),
    )
    mock, url = start_mock_server()
    try:
        h3_manager = ConfigManager(tmp_path / "h3")
        h3_manager.save(AppConfig(selected_profile="minimax_h3", selected_variant="base"))
        h3 = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=h3_manager,
            server_url=url,
        )
        h3.request_text.setPlainText("A subject moves.")
        h3.generate()
        assert h3.worker is None
        assert warnings
        assert h3.tr("error.h3_skill_required") in warnings[-1]
        h3.close()
        app.processEvents()

        for profile_id, variant_id in (
            ("wan_2_2", "a14b"),
            ("ltx_2_3", "distilled_1_1"),
        ):
            manager = ConfigManager(tmp_path / profile_id)
            manager.save(
                AppConfig(selected_profile=profile_id, selected_variant=variant_id)
            )
            window = MainWindow(
                project_root=PROJECT_ROOT,
                config_manager=manager,
                server_url=url,
            )
            window.request_text.setPlainText("A subject moves.")
            window.generate()
            assert window.worker is not None
            deadline = time.monotonic() + 5
            while window.worker.isRunning() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            app.processEvents()
            assert window.worker.isRunning() is False
            assert window.output_text.toPlainText()
            window.close()
            app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_main_window_uses_persisted_english_and_japanese_locales(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        english_manager = ConfigManager(tmp_path / "english")
        english_manager.save(AppConfig(ui_locale="en-US"))
        english = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=english_manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        assert english.generate_button.text() == "Generate Prompt"
        assert english.settings_action.text() == "Settings"
        assert english.profile_category.currentData() == "video"
        assert "LLM Model: Not set" in english.readiness.text()
        assert english.duration.suffix() == " sec"
        english.close()
        app.processEvents()

        japanese_manager = ConfigManager(tmp_path / "japanese")
        japanese_manager.save(AppConfig(ui_locale="ja-JP"))
        japanese = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=japanese_manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        assert japanese.generate_button.text() == "Promptを生成"
        assert japanese.settings_action.text() == "設定"
        assert japanese.profile_category.currentData() == "video"
        assert "LLMモデル: 未設定" in japanese.readiness.text()
        assert japanese.duration.suffix() == " 秒"
        japanese.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_settings_language_selection_persists_stable_locale_id(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(ui_locale="ja-JP"))
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )
    dialog = SettingsDialog(manager, PROJECT_ROOT)
    try:
        dialog.ui_locale.setCurrentIndex(dialog.ui_locale.findData("en-US"))
        dialog.accept()
        assert manager.load().ui_locale == "en-US"
        assert manager.path.read_text(encoding="utf-8").count('"en-US"') == 1
        assert messages
    finally:
        dialog.close()
        app.processEvents()


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
        assert not window.send_comfyui_button.isEnabled()
        assert not window.regenerate_button.isEnabled()
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
        assert window.send_comfyui_button.isEnabled()
        assert window.regenerate_button.isEnabled()

        window.regenerate_button.click()
        assert window.worker is not None and window.worker.isRunning()
        assert not window.send_comfyui_button.isEnabled()
        deadline = time.monotonic() + 5
        while window.worker is not None and window.worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert window.worker is not None and not window.worker.isRunning()
        assert window.send_comfyui_button.isEnabled()
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_cancel_generation_remains_available_with_send_ui(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server(delay=0.2)
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.request_text.setPlainText("短いキャンセルテスト")
        window.generate()
        worker = window.worker
        assert worker is not None and worker.isRunning()
        assert window.cancel_button.isEnabled()

        window.cancel_generation()
        assert worker.isInterruptionRequested()
        deadline = time.monotonic() + 5
        while worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()

        assert not worker.isRunning()
        assert window.output_text.toPlainText() == ""
        assert window.generate_button.isEnabled()
        assert not window.cancel_button.isEnabled()
        assert not window.send_comfyui_button.isEnabled()
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


def test_first_run_setup_can_finish_without_optional_h3_skill(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    model = tmp_path / "qwen3-4b-q4_k_m.gguf"
    model.write_bytes(b"GGUF")
    manager = ConfigManager(tmp_path / "data")
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.detect_vulkan_devices",
        lambda self: [],
    )
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.runtime_available",
        lambda self, backend: True,
    )
    dialog = SetupDialog(manager, PROJECT_ROOT)
    try:
        dialog.model_path.setText(str(model))
        assert SkillManager(dialog.skill_path).status().valid is False
        dialog.accept()
        saved = manager.load()
        assert saved.setup_completed is True
        assert saved.model_path == str(model)
        assert saved.inference_backend == BACKEND_CPU
        assert "MiniMax H3" in dialog.localization.tr("setup.h3_skill_optional")
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
