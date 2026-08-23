from __future__ import annotations

import json
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QScrollArea,
)

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
from core.prompt_engine import PromptEngine
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
        assert window.windowTitle() == "Local Prompt Studio v2.1.1"
        assert window.profile_category.currentData() == "video"
        assert window.profile_model.currentData() == "minimax_h3"
        assert window.profile_variant.currentData() == "base"
        assert window.auto_quality_tags.isChecked() is True
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
        assert "[speech:ja]こんにちは[/speech]" in window.literal_hint.text()
        assert "[text:ja]月夜珈琲[/text]" in window.request_text.toolTip()
        assert all(
            "renderer" not in widget.objectName().casefold()
            for widget in window.findChildren(QComboBox)
        )
        assert "未設定" in window.readiness.text()
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_main_window_uses_two_columns_scrollable_settings_and_fixed_actions(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.resize(1118, 846)
        window.show()
        app.processEvents()

        assert window.main_splitter.orientation() == Qt.Orientation.Horizontal
        assert isinstance(window.left_settings_scroll, QScrollArea)
        assert window.left_settings_scroll.widgetResizable() is True
        assert (
            window.left_settings_scroll.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.left_settings_scroll.horizontalScrollBar().maximum() == 0
        assert 270 <= window.left_settings_scroll.minimumWidth() <= 290

        profile_group = window.findChild(type(window.output_group), "profile_group")
        video_group = window.findChild(type(window.output_group), "video_settings_group")
        request_group = window.findChild(type(window.output_group), "request_group")
        assert window.left_settings_scroll.isAncestorOf(profile_group)
        assert window.left_settings_scroll.isAncestorOf(video_group)
        assert not window.left_settings_scroll.isAncestorOf(window.mode_section)
        assert window.right_workspace.isAncestorOf(window.mode_section)
        assert window.right_workspace.isAncestorOf(request_group)
        assert window.right_workspace.isAncestorOf(window.output_group)
        assert not window.left_settings_scroll.isAncestorOf(window.action_bar)

        video_positions = [
            widget.geometry().top()
            for widget in (
                window.duration,
                window.motion,
                window.camera,
                window.shot,
            )
        ]
        assert video_positions == sorted(video_positions)
        assert len(set(video_positions)) == 4
        assert all(
            widget.width() >= 220
            for widget in (
                window.duration,
                window.motion,
                window.camera,
                window.shot,
            )
        )
        assert window.motion.width() >= (
            window.motion.fontMetrics().horizontalAdvance(window.motion.currentText())
            + 40
        )

        assert all(
            combo.height() >= 30
            for combo in (
                window.profile_category,
                window.profile_model,
                window.profile_variant,
                window.mode,
                window.processing,
            )
        )
        assert window.request_text.minimumHeight() >= 100
        assert window.output_text.minimumHeight() >= 100
        assert window.action_bar.isVisible()
        assert window.action_bar.geometry().bottom() < window.status_label.geometry().top()

        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_system_details_start_collapsed_and_toggle_without_hiding_summary(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.show()
        app.processEvents()

        assert window.system_summary.text()
        assert window.system_summary.isVisible()
        assert window.system_details_toggle.isChecked() is False
        assert window.system_details_group.isHidden() is True
        assert window.system_details_toggle.arrowType() == Qt.ArrowType.RightArrow

        window.system_details_toggle.click()
        app.processEvents()
        assert window.system_details_group.isVisible()
        assert window.system_details_toggle.arrowType() == Qt.ArrowType.DownArrow
        assert window.system_summary.isVisible()

        window.system_details_toggle.click()
        app.processEvents()
        assert window.system_details_group.isHidden()
        assert window.system_details_toggle.arrowType() == Qt.ArrowType.RightArrow

        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_compact_system_summary_keeps_memory_warning_visible(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    model = tmp_path / "qwen3-4b-q4_k_m.gguf"
    model.write_bytes(b"GGUF-test")
    manager = ConfigManager(tmp_path / "data")
    manager.save(AppConfig(model_path=str(model), context_size=4096))
    monkeypatch.setattr(
        "app.main_window.get_system_memory",
        lambda: MemoryInfo(total_bytes=int(12.7 * GIB), available_bytes=int(5.3 * GIB)),
    )
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.show()
        app.processEvents()

        assert window.system_details_group.isHidden()
        assert "Qwen3-4B Q4_K_M" in window.system_summary.text()
        assert "RAM: 5.3 / 12.7 GB" in window.system_summary.text()
        assert "⚠" in window.system_summary.text()
        assert "メモリ警告" in window.system_summary.text()
        assert "4096" in window.memory_status.text()

        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_mode_supplement_starts_collapsed_and_preserves_entered_text(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.mode.setCurrentText("I2VA")
        window.show()
        app.processEvents()
        assert window.mode_section.isVisible()
        assert window.right_workspace.isAncestorOf(window.mode_section)
        assert not window.left_settings_scroll.isAncestorOf(window.mode_section)
        assert (
            window.mode_section.geometry().bottom()
            < window.workspace_splitter.geometry().top()
        )
        assert window.mode_supplement_toggle.isChecked() is False
        assert window.mode_group.isHidden()
        assert window.mode_section.height() <= (
            window.mode_supplement_toggle.sizeHint().height() + 8
        )

        window.start_note.setPlainText("keep this start-frame note")
        window.mode_supplement_toggle.click()
        app.processEvents()
        assert window.mode_group.isVisible()
        assert window.start_note.isVisible()
        assert window.request_text.height() >= window.request_text.minimumHeight()
        assert window.output_text.height() >= window.output_text.minimumHeight()
        assert window.start_note.toPlainText() == "keep this start-frame note"

        window.mode_supplement_toggle.click()
        app.processEvents()
        assert window.mode_group.isHidden()
        assert window.start_note.toPlainText() == "keep this start-frame note"
        assert window._collect_settings().start_frame_note == "keep this start-frame note"

        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_target_window_sizes_keep_workspace_and_mode_supplement_usable(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.mode.setCurrentText("FL2VA")
        window.show()

        request_group = window.findChild(type(window.output_group), "request_group")

        def assert_request_helper_geometry() -> None:
            request_layout = request_group.layout()
            assert request_layout.indexOf(window.request_text) >= 0
            assert request_layout.indexOf(window.literal_hint) >= 0
            assert window.literal_hint.height() >= window.literal_hint.minimumHeight()
            assert (
                window.request_text.geometry().bottom() + request_layout.spacing()
                < window.literal_hint.geometry().top()
            )
            assert window.literal_hint.geometry().bottom() <= (
                request_group.height() - request_layout.contentsMargins().bottom()
            )

        for width, height in ((1366, 768), (1118, 846), (1280, 720), (1920, 1080)):
            window.resize(width, height)
            app.processEvents()
            assert window.size().width() == width
            assert window.size().height() == height
            assert 270 <= window.main_splitter.sizes()[0] <= 310
            assert window.motion.width() >= 220
            assert window.motion.height() >= 30
            assert window.request_text.height() >= window.request_text.minimumHeight()
            assert window.output_text.height() >= window.output_text.minimumHeight()
            assert window.action_bar.isVisible()
            assert window.mode_group.isHidden()
            assert_request_helper_geometry()

            window.mode_supplement_toggle.setChecked(True)
            app.processEvents()
            assert window.mode_group.isVisible()
            supplement_editors = (window.common_note, window.start_note, window.end_note)
            for editor in supplement_editors:
                assert editor.isVisible()
                assert editor.minimumHeight() >= 70
                assert editor.height() >= editor.minimumHeight()
                assert editor.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
            for previous, following in zip(supplement_editors, supplement_editors[1:]):
                assert previous.geometry().bottom() < following.geometry().top()
            window.common_note.setPlainText("long supplement line\n" * 30)
            app.processEvents()
            assert window.common_note.verticalScrollBar().maximum() > 0
            assert window.request_text.height() >= window.request_text.minimumHeight()
            assert window.output_text.height() >= window.output_text.minimumHeight()
            assert window.mode_section.geometry().bottom() < window.workspace_splitter.geometry().top()
            scroll_bottom = window.mode_notes_scroll.mapTo(
                window.mode_section,
                window.mode_notes_scroll.rect().bottomLeft(),
            ).y()
            group_bottom = window.mode_group.mapTo(
                window.mode_section,
                window.mode_group.rect().bottomLeft(),
            ).y()
            assert scroll_bottom < window.mode_section.height()
            assert group_bottom < window.mode_section.height()
            assert window.action_bar.isVisibleTo(window.prompt_page)
            assert_request_helper_geometry()
            window.mode_supplement_toggle.setChecked(False)
            app.processEvents()

        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_unload_model_button_stops_only_owned_server_and_preserves_text(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()

    class FakeOwnedProcess:
        def __init__(self):
            self.running = True
            self.terminated = False

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.terminated = True
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
        assert window.unload_model_button.isEnabled() is False

        owned = FakeOwnedProcess()
        window.server.process = owned
        window.server.base_url = "http://127.0.0.1:12345"
        window.request_text.setPlainText("request must remain")
        window.output_text.setPlainText("prompt must remain")
        window._update_unload_button_state()
        assert window.unload_model_button.isEnabled()

        window._generation_active = True
        window._update_unload_button_state()
        assert window.unload_model_button.isEnabled() is False
        window._generation_active = False
        window._update_unload_button_state()
        assert window.unload_model_button.isEnabled()

        window.unload_model_button.click()
        assert owned.terminated is True
        assert window.server.process is None
        assert window.server.base_url is None
        assert window.unload_model_button.isEnabled() is False
        assert window.request_text.toPlainText() == "request must remain"
        assert window.output_text.toPlainText() == "prompt must remain"
        assert window.status_label.text() == window.tr("model.unloaded")

        window.server.base_url = "http://127.0.0.1:54321"
        window._update_unload_button_state()
        assert window.unload_model_button.isEnabled() is False
        window._unload_model()
        assert window.server.base_url == "http://127.0.0.1:54321"

        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_generate_reloads_model_after_unload(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    model = tmp_path / "qwen3-4b-q4_k_m.gguf"
    model.write_bytes(b"GGUF-test")
    manager = ConfigManager(tmp_path / "data")
    manager.save(AppConfig(model_path=str(model), setup_completed=True))
    mock, url = start_mock_server()

    class FakeManagedServer:
        def __init__(self):
            self.running = True
            self.base_url = "http://127.0.0.1:12345"
            self.stop_calls = 0
            self.start_calls = 0

        @property
        def is_owned_server_running(self):
            return self.running

        def runtime_available(self, backend):
            return True

        def start(self, model_path, **settings):
            self.start_calls += 1
            self.running = True
            self.base_url = "http://127.0.0.1:12345"
            return self.base_url

        def preflight_context(self, payload, context_size):
            return 10, int(payload["max_tokens"])

        def generate(self, payload, timeout):
            time.sleep(0.05)
            return "A cinematic shot of a woman walking through rain."

        def stop(self):
            self.stop_calls += 1
            self.running = False
            self.base_url = None

        def cancel(self):
            self.stop()

    fake_server = FakeManagedServer()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.mock_mode = False
        window.server = fake_server
        monkeypatch.setattr(window, "_memory_warnings", lambda assessment=None: [])
        window.request_text.setPlainText("A woman walks through rain.")
        window.output_text.setPlainText("existing editable prompt")
        window._update_unload_button_state()

        window.unload_model_button.click()
        assert fake_server.stop_calls == 1
        assert fake_server.running is False
        assert window.request_text.toPlainText() == "A woman walks through rain."
        assert window.output_text.toPlainText() == "existing editable prompt"
        assert manager.load().config_version == 7

        window.generate()
        assert window._generation_active is True
        assert window.unload_model_button.isEnabled() is False
        assert window.worker is not None
        deadline = time.monotonic() + 5
        while window.worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()

        assert window.worker.isRunning() is False
        assert fake_server.start_calls == 1
        assert fake_server.running is True
        assert window.unload_model_button.isEnabled()
        assert window.request_text.toPlainText() == "A woman walks through rain."
        assert window.output_text.toPlainText()
        assert manager.load().config_version == 7

        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_copy_negative_save_txt_and_clear_actions_remain_available(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path / "data"),
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.profile_category.setCurrentIndex(window.profile_category.findData("image"))
        window.profile_model.setCurrentIndex(window.profile_model.findData("anima"))
        app.processEvents()

        window.request_text.setPlainText("request")
        window.output_text.setPlainText("positive prompt")
        window.negative_output_text.setPlainText("negative prompt")
        window.copy_button.click()
        assert QApplication.clipboard().text() == "positive prompt"
        window.copy_negative_button.click()
        assert QApplication.clipboard().text() == "negative prompt"

        saved = tmp_path / "saved prompt.txt"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(saved), "Text File (*.txt)"),
        )
        window._save_output()
        assert saved.read_text(encoding="utf-8") == (
            "[Positive]\npositive prompt\n\n[Negative]\nnegative prompt"
        )

        window._clear_text()
        assert window.request_text.toPlainText() == ""
        assert window.output_text.toPlainText() == ""
        assert window.negative_output_text.toPlainText() == ""
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_generation_failure_clears_stale_output_and_disables_copy_and_send(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server(response_text="invalid hybrid output")
    warnings = []
    bridge_calls = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    def forbidden_bridge(url_value):
        bridge_calls.append(url_value)
        raise AssertionError("stale output must not reach ComfyUI")

    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=FIXTURE,
            bridge_service_factory=forbidden_bridge,
        )
        window.profile_category.setCurrentIndex(window.profile_category.findData("image"))
        window.profile_model.setCurrentIndex(window.profile_model.findData("anima"))
        app.processEvents()

        window.output_text.setPlainText("stale positive")
        window.negative_output_text.setPlainText("stale negative")
        assert window.copy_button.isEnabled()
        assert window.copy_negative_button.isEnabled()
        assert window.send_comfyui_button.isEnabled()

        window.request_text.setPlainText(
            "ginntuinn, 1girl, silver hair. A young woman is sitting beside a café "
            "window, wearing a white dress and smiling gently at the viewer."
        )
        window.generate()
        assert window.worker is not None
        assert window.output_text.toPlainText() == ""
        assert window.negative_output_text.toPlainText() == ""
        assert not window.copy_button.isEnabled()
        assert not window.copy_negative_button.isEnabled()
        assert not window.send_comfyui_button.isEnabled()

        deadline = time.monotonic() + 5
        while window.worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()

        assert not window.worker.isRunning()
        assert warnings
        assert window.output_text.toPlainText() == ""
        assert window.negative_output_text.toPlainText() == ""
        assert not window.copy_button.isEnabled()
        assert not window.copy_negative_button.isEnabled()
        assert not window.send_comfyui_button.isEnabled()

        QApplication.clipboard().setText("clipboard sentinel")
        window.copy_button.click()
        window.copy_negative_button.click()
        window.send_comfyui_button.click()
        assert QApplication.clipboard().text() == "clipboard sentinel"
        assert bridge_calls == []
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
        assert window.negative_output_group.isHidden() is True
        assert window.copy_negative_button.isHidden() is True
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
        assert [
            window.profile_model.itemData(index)
            for index in range(window.profile_model.count())
        ] == ["anima", "krea_2"]
        window.profile_model.setCurrentIndex(window.profile_model.findData("krea_2"))
        app.processEvents()
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


def test_image_category_selects_anima_and_shows_separate_negative_output(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    response = json.dumps(
        {
            "subject_count": ["1girl"],
            "general": ["silver_hair", "blue_eyes", "月夜珈琲"],
            "negative": ["bad_hands"],
        },
        ensure_ascii=False,
    )
    mock, url = start_mock_server(response_text=response)
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

        anima_index = window.profile_model.findData("anima")
        assert anima_index >= 0
        window.profile_model.setCurrentIndex(anima_index)
        app.processEvents()

        assert window.profile.manifest.id == "anima"
        assert [
            window.profile_variant.itemData(index)
            for index in range(window.profile_variant.count())
        ] == ["aesthetic_v1_1", "base_v1_0", "turbo_v1_0"]
        assert window.profile_variant.currentData() == "turbo_v1_0"
        assert [window.mode.itemData(index) for index in range(window.mode.count())] == [
            "T2I",
        ]
        assert window.legacy_video_settings_group.isHidden() is True
        assert window.mode_group.isHidden() is True
        assert window.negative_output_group.isHidden() is False
        assert window.copy_negative_button.isHidden() is False
        assert "Positive" in window.output_group.title() or "ポジティブ" in window.output_group.title()
        assert "H3 Skill" not in window.readiness.text()

        window.request_text.setPlainText("1girl, silver_hair\n[text:ja] 月夜珈琲")
        window.generate()
        assert window.worker is not None
        deadline = time.monotonic() + 5
        while window.worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()

        assert window.worker.isRunning() is False
        assert "masterpiece, best quality" in window.output_text.toPlainText()
        assert "score_" not in window.output_text.toPlainText()
        assert "safe" not in window.output_text.toPlainText().split(", ")
        assert "月夜珈琲" in window.output_text.toPlainText()
        assert "worst quality" in window.negative_output_text.toPlainText()
        assert "bad hands" in window.negative_output_text.toPlainText()
        assert "Positive Prompt only" in window.send_comfyui_button.toolTip() or "ポジティブPromptのみ" in window.send_comfyui_button.toolTip()
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
        english_style_label = english.processing.parentWidget().layout().labelForField(
            english.processing
        )
        assert english_style_label.text() == "Prompt Transformation Style"
        assert english.auto_quality_tags.text() == "Automatically add quality tags"
        assert english.system_details_toggle.text() == "Details"
        assert english.system_details_group.title() == "System details"
        assert english.unload_model_button.text() == "Unload model"
        assert "RAM/GPU memory" in english.unload_model_button.toolTip()
        assert english.mode_supplement_toggle.text() == "Mode supplement"
        assert english.profile_variant_help.text()
        assert english.prompt_style_help.text()
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
        japanese_style_label = japanese.processing.parentWidget().layout().labelForField(
            japanese.processing
        )
        assert japanese_style_label.text() == "Prompt変換スタイル"
        assert japanese.auto_quality_tags.text() == "品質タグを自動追加"
        assert japanese.system_details_toggle.text() == "詳細"
        assert japanese.system_details_group.title() == "システム詳細"
        assert japanese.unload_model_button.text() == "モデルをアンロード"
        assert "RAM/GPUメモリ" in japanese.unload_model_button.toolTip()
        assert japanese.mode_supplement_toggle.text() == "モード補足"
        assert "標準Prompt規則" in japanese.profile_variant_help.text()
        assert japanese.prompt_style_help.text()
        japanese.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_auto_quality_tags_checkbox_persists_and_enters_prompt_settings(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(auto_quality_tags=False))
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )

        assert window.auto_quality_tags.isChecked() is False
        assert window._collect_settings().auto_quality_tags is False

        window.auto_quality_tags.setChecked(True)
        app.processEvents()

        assert manager.load().auto_quality_tags is True
        assert window._collect_settings().auto_quality_tags is True
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_variant_and_renderer_help_update_without_renderer_selection_ui(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(ui_locale="en-US"))
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )

        assert "MiniMax H3" in window.profile_variant_help.text()
        faithful = window.prompt_style_help.text()
        assert faithful
        assert window.profile_variant.toolTip() == window.profile_variant_help.text()
        assert window.processing.toolTip() == window.prompt_style_help.text()

        window.processing.setCurrentText("Balanced")
        app.processEvents()
        assert "H3" in window.prompt_style_help.text()
        assert window.prompt_style_help.text() != faithful

        window.profile_model.setCurrentIndex(window.profile_model.findData("ltx_2_3"))
        app.processEvents()
        assert "Distilled 1.1" in window.profile_variant_help.text()
        assert "LTX" in window.prompt_style_help.text()
        distilled_help = window.profile_variant_help.text()

        window.profile_variant.setCurrentIndex(window.profile_variant.findData("dev"))
        app.processEvents()
        assert "22B Dev" in window.profile_variant_help.text()
        assert window.profile_variant_help.text() != distilled_help

        window.profile_category.setCurrentIndex(window.profile_category.findData("image"))
        app.processEvents()
        window.profile_model.setCurrentIndex(window.profile_model.findData("krea_2"))
        app.processEvents()
        assert "8-step" in window.profile_variant_help.text()
        assert "Krea" in window.prompt_style_help.text()
        assert window.findChild(QComboBox, "renderer") is None

        window.close()
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


def test_h3_visual_style_control_manages_only_its_own_request_prefix(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        manager = ConfigManager(tmp_path / "japanese")
        manager.save(AppConfig(ui_locale="ja-JP"))
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.show()
        app.processEvents()

        assert window.visual_style_button.isVisible()
        assert window.mode_controls.isAncestorOf(window.visual_style_button)
        assert window.visual_style_button.geometry().left() > (
            window.mode_supplement_toggle.geometry().right()
        )
        assert window.visual_style_button.text() == "映像スタイル: 未指定"
        assert window.visual_style_actions["unspecified"].isChecked()
        assert [
            action.data() for action in window.request_guide_button.menu().actions()
        ] == ["time", "fixed_camera", "cut", "speech", "visible_text"]

        body = (
            "動画の0-10秒間は女性が歩く。\n"
            "動画の10-15秒間は女性が走る。\n"
            + "長いRequest本文を保持する。" * 20
        )
        window.request_text.setPlainText(body)
        window.visual_style_actions["2d_animation"].trigger()
        assert window.request_text.toPlainText() == f"2Dアニメーション。\n{body}"
        assert window.visual_style_button.text() == "映像スタイル: 2Dアニメーション"

        window.visual_style_actions["live_action"].trigger()
        assert window.request_text.toPlainText() == f"実写映像。\n{body}"
        assert window.request_text.toPlainText().count("実写映像。") == 1
        window.visual_style_actions["3d_cg"].trigger()
        assert window.request_text.toPlainText() == f"3D CG映像。\n{body}"
        assert "実写映像。" not in window.request_text.toPlainText()
        window.visual_style_actions["unspecified"].trigger()
        assert window.request_text.toPlainText() == body

        user_style = "手描き風の2Dアニメーションで、女性が街を歩く。"
        window.request_text.setPlainText(user_style)
        window.visual_style_actions["2d_animation"].trigger()
        window.visual_style_actions["unspecified"].trigger()
        assert window.request_text.toPlainText() == user_style

        window.request_text.setPlainText(body)
        window.visual_style_actions["2d_animation"].trigger()
        pasted_request = "2Dアニメーション。\nユーザーが全文を貼り付けたRequest。"
        window.request_text.setPlainText(pasted_request)
        assert window._managed_visual_style_block is None
        window.visual_style_actions["unspecified"].trigger()
        assert window.request_text.toPlainText() == pasted_request

        window.request_text.setPlainText(body)
        window.visual_style_actions["2d_animation"].trigger()
        user_edited = window.request_text.toPlainText().replace(
            "2Dアニメーション。", "ユーザーが編集した映像表現。", 1
        )
        window.request_text.setPlainText(user_edited)
        window.visual_style_actions["live_action"].trigger()
        assert window.request_text.toPlainText() == f"実写映像。\n{user_edited}"
        window.visual_style_actions["unspecified"].trigger()
        assert window.request_text.toPlainText() == user_edited

        window.request_text.setPlainText(body)
        window.visual_style_actions["3d_cg"].trigger()
        request = window.request_text.toPlainText()
        engine = PromptEngine(
            window.skill_manager,
            window.profile,
            str(window.profile_variant.currentData()),
        )
        assert engine.build_messages(request, window._collect_settings())[1] == {
            "role": "user",
            "content": request,
        }
        window.close()
        app.processEvents()

        english_manager = ConfigManager(tmp_path / "english")
        english_manager.save(AppConfig(ui_locale="en-US"))
        english = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=english_manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        assert english.visual_style_button.text() == "Visual Style: Unspecified"
        assert [
            english.visual_style_actions[key].text()
            for key in ("unspecified", "2d_animation", "live_action", "3d_cg")
        ] == ["Unspecified", "2D Animation", "Live Action", "3D CG"]
        english.request_text.setPlainText("A woman walks, then runs.")
        english.visual_style_actions["live_action"].trigger()
        assert english.request_text.toPlainText() == (
            "Live-action footage.\nA woman walks, then runs."
        )
        english.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()
