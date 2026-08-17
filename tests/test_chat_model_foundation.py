from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.settings_dialog import SettingsDialog
from core.config_manager import AppConfig, ConfigManager
from core.localization import Localization


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _BridgeService:
    def has_valid_credential(self) -> bool:
        return False

    def invalidate_credentials(self) -> None:
        return None


def _bridge_factory(_url: str) -> _BridgeService:
    return _BridgeService()


def test_settings_switches_chat_model_controls_and_restores_model_mmproj(
    tmp_path, monkeypatch
):
    _app()
    monkeypatch.setattr(
        "app.settings_dialog.LlamaServerManager.detect_vulkan_devices", lambda self: []
    )
    monkeypatch.setattr(
        "app.settings_dialog.LlamaServerManager.runtime_available", lambda self, backend: True
    )
    prompt_model = tmp_path / "prompt.gguf"
    chat_model = tmp_path / "chat.gguf"
    third_model = tmp_path / "third.gguf"
    prompt_mmproj = tmp_path / "prompt-mmproj.gguf"
    chat_mmproj = tmp_path / "chat-mmproj.gguf"
    third_mmproj = tmp_path / "third-mmproj.gguf"
    config = AppConfig(
        model_path=str(prompt_model),
        use_prompt_model_for_chat=True,
        chat_model_path=str(chat_model),
    )
    config.set_mmproj_for_model(prompt_model, prompt_mmproj)
    config.set_mmproj_for_model(chat_model, chat_mmproj)
    manager = ConfigManager(tmp_path / "data")
    manager.save(config)
    dialog = SettingsDialog(
        manager,
        PROJECT_ROOT,
        bridge_service_factory=_bridge_factory,
    )
    try:
        assert dialog.use_prompt_model_for_chat.isChecked()
        assert not dialog.chat_model_path.isEnabled()
        assert not dialog.chat_model_browse.isEnabled()
        assert dialog.mmproj_path.text() == str(prompt_mmproj.resolve())

        dialog.use_prompt_model_for_chat.setChecked(False)
        assert dialog.chat_model_path.isEnabled()
        assert dialog.chat_model_browse.isEnabled()
        assert dialog.mmproj_path.text() == str(chat_mmproj.resolve())

        dialog.chat_model_path.setText(str(third_model))
        dialog._switch_mmproj_target()
        assert dialog.mmproj_path.text() == ""
        dialog.mmproj_path.setText(str(third_mmproj))

        dialog.use_prompt_model_for_chat.setChecked(True)
        assert dialog.mmproj_path.text() == str(prompt_mmproj.resolve())
        dialog.use_prompt_model_for_chat.setChecked(False)
        assert dialog.mmproj_path.text() == str(third_mmproj.resolve())
        dialog.accept()

        saved = manager.load()
        assert saved.use_prompt_model_for_chat is False
        assert saved.chat_model_path == str(third_model)
        assert saved.mmproj_for_model(prompt_model) == str(prompt_mmproj.resolve())
        assert saved.mmproj_for_model(chat_model) == str(chat_mmproj.resolve())
        assert saved.mmproj_for_model(third_model) == str(third_mmproj.resolve())
    finally:
        dialog.close()


def test_chat_model_and_image_state_are_config_projection_not_name_inference(tmp_path):
    app = _app()
    model = tmp_path / ("not-a-vlm-name-" + "x" * 100 + ".gguf")
    configured_mmproj = tmp_path / "vision.gguf"
    configured_mmproj.write_bytes(b"GGUF")
    manager = ConfigManager(tmp_path / "data")
    config = AppConfig(
        model_path=str(tmp_path / "prompt.gguf"),
        use_prompt_model_for_chat=False,
        chat_model_path=str(model),
    )
    config.set_mmproj_for_model(model, configured_mmproj)
    manager.save(config)
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:54321",
        dev_skill_path=SKILL_FIXTURE,
    )
    try:
        window.resize(1118, 846)
        window.main_tabs.setCurrentWidget(window.chat_page)
        window.show()
        app.processEvents()
        assert model.name in window.chat_page.model_label.text()
        assert window.chat_page.model_label.toolTip() == str(model)
        assert window.tr("chat.image_state.configured") in window.chat_page.image_state_label.text()
        assert window.chat_page.image_state_label.toolTip() == str(configured_mmproj.resolve())
        assert window.chat_page.model_bar.width() <= window.chat_page.width()

        config.set_mmproj_for_model(model, "")
        manager.save(config)
        window.config = manager.load()
        window._update_chat_model_status()
        assert window.tr("chat.image_state.unset") in window.chat_page.image_state_label.text()

        config.set_mmproj_for_model(model, tmp_path / "missing-mmproj.gguf")
        manager.save(config)
        window.config = manager.load()
        window._update_chat_model_status()
        assert window.tr("chat.image_state.load_error") in window.chat_page.image_state_label.text()
        assert window.tr("chat.image_state.unsupported") not in window.chat_page.image_state_label.text()
        assert window.tr("chat.image_state.available") not in window.chat_page.image_state_label.text()
    finally:
        window.close()
        app.processEvents()


def test_chat_unload_uses_shared_owned_manager_and_preserves_all_text(tmp_path):
    app = _app()
    manager = ConfigManager(tmp_path / "data")
    manager.save(AppConfig(model_path=str(tmp_path / "prompt.gguf")))
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:54321",
        dev_skill_path=SKILL_FIXTURE,
    )

    class FakeManagedServer:
        running = True
        stop_calls = 0

        @property
        def is_owned_server_running(self):
            return self.running

        def stop(self):
            self.stop_calls += 1
            self.running = False

    fake_server = FakeManagedServer()
    window.server = fake_server
    window.request_text.setPlainText("prompt request")
    window.output_text.setPlainText("generated prompt")
    window.common_note.setPlainText("supplement")
    window.chat_page.input_text.setPlainText("chat draft")
    window.chat_messages = [{"role": "user", "content": "conversation"}]
    window._update_unload_button_state()
    try:
        assert window.chat_page.unload_button.isEnabled()
        window.chat_page.unload_button.click()
        assert fake_server.stop_calls == 1
        assert not window.chat_page.unload_button.isEnabled()
        assert window.request_text.toPlainText() == "prompt request"
        assert window.output_text.toPlainText() == "generated prompt"
        assert window.common_note.toPlainText() == "supplement"
        assert window.chat_page.input_text.toPlainText() == "chat draft"
        assert window.chat_messages == [{"role": "user", "content": "conversation"}]
    finally:
        window.close()
        app.processEvents()


def test_chat_model_foundation_localization_is_complete():
    ja = Localization(PROJECT_ROOT / "locales", "ja-JP")
    en = Localization(PROJECT_ROOT / "locales", "en-US")
    assert ja.tr("settings.chat_model.use_prompt") == "Prompt生成と同じモデルを使用"
    assert ja.tr("chat.image_state.configured") == "設定済み"
    assert en.tr("settings.chat_model.title") == "AI Chat Model"
    assert en.tr("chat.image_state.unset") == "Not configured"
    assert en.tr("chat.unload") == "Unload model"
