from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QSizePolicy

from app.main_window import MainWindow
from app.workers import ChatThread
from core.chat_engine import CHAT_SYSTEM_PROMPT, ChatEngine
from core.config_manager import AppConfig, ConfigManager
from core.history_manager import HistoryManager
from core.llama_manager import LlamaContextError
from core.localization import Localization
from core.profile_loader import ProfileLoader
from core.prompt_engine import PromptEngine, PromptSettings
from core.renderers import RendererRegistry
from mock_server import start_mock_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def _window(tmp_path, *, response: str = "A neutral assistant response.", delay: float = 0.0):
    server, url = start_mock_server(response_text=response, delay=delay)
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=ConfigManager(tmp_path),
        server_url=url,
        dev_skill_path=SKILL_FIXTURE,
    )
    return window, server


def _close(window: MainWindow, server, app: QApplication) -> None:
    window.close()
    app.processEvents()
    server.shutdown()
    server.server_close()


def test_fixed_tabs_dynamic_prompt_title_and_state_survive_navigation(tmp_path):
    app = _app()
    window, server = _window(tmp_path)
    try:
        assert window.main_tabs.count() == 3
        assert window.main_tabs.tabsClosable() is False
        assert window.main_tabs.tabText(0) == "MiniMax H3 / T2VA"
        assert window.main_tabs.tabToolTip(0) == "MiniMax H3 / T2VA"
        assert window.main_tabs.tabText(1) == window.tr("tabs.ai_chat")
        assert window.main_tabs.tabText(2) == window.tr("tabs.prompt_library")

        window.request_text.setPlainText("request stays")
        window.common_note.setPlainText("supplement stays")
        window.chat_page.input_text.setPlainText("draft stays")
        window.main_tabs.setCurrentIndex(1)
        window.main_tabs.setCurrentIndex(0)
        assert window.request_text.toPlainText() == "request stays"
        assert window.common_note.toPlainText() == "supplement stays"
        assert window.chat_page.input_text.toPlainText() == "draft stays"

        window.mode.setCurrentText("FL2VA")
        assert window.main_tabs.tabText(0) == "MiniMax H3 / FL2VA"
        window._select_chat_target_profile("wan_2_2")
        assert window.main_tabs.tabText(0) == "Wan 2.2 / T2V"
        window._select_chat_target_task("I2V")
        assert window.main_tabs.tabText(0) == "Wan 2.2 / I2V"
        assert window.request_text.toPlainText() == "request stays"
        assert window.common_note.toPlainText() == "supplement stays"
    finally:
        _close(window, server, app)


def test_browser_like_tab_style_is_compact_and_workspace_remains_usable(tmp_path):
    app = _app()
    window, server = _window(tmp_path)
    try:
        window.show()
        tab_bar = window.main_tabs.tabBar()
        assert tab_bar.objectName() == "main_mode_tab_bar"
        assert window.main_tabs.currentIndex() == 0
        assert tab_bar.count() == 3
        style = window.main_tabs.styleSheet()
        assert "border: 1px solid palette(mid)" in style
        assert "border-bottom-color: palette(window)" in style
        assert "font-weight: 600" in style
        assert "palette(midlight)" in style

        for width, height in ((1366, 768), (1118, 846)):
            window.resize(width, height)
            app.processEvents()
            prompt_rect = tab_bar.tabRect(0)
            chat_rect = tab_bar.tabRect(1)
            assert 32 <= prompt_rect.height() <= 40
            assert 32 <= chat_rect.height() <= 40
            assert prompt_rect.width() > 100
            assert chat_rect.width() > 60
            assert window.request_text.height() >= window.request_text.minimumHeight()
            assert window.output_text.height() >= window.output_text.minimumHeight()
            assert window.action_bar.isVisibleTo(window.prompt_page)

            window.main_tabs.setCurrentIndex(1)
            app.processEvents()
            assert window.main_tabs.currentIndex() == 1
            assert window.chat_page.conversation_scroll.height() > 200
            window.main_tabs.setCurrentIndex(0)

        window.mode.setCurrentText("FL2VA")
        assert window.main_tabs.tabText(0) == "MiniMax H3 / FL2VA"
        assert window.main_tabs.tabToolTip(0) == "MiniMax H3 / FL2VA"
    finally:
        _close(window, server, app)


def test_chat_conversation_precedes_compact_input_and_remains_scrollable(tmp_path):
    app = _app()
    window, server = _window(tmp_path)
    try:
        window.resize(1118, 846)
        window.main_tabs.setCurrentWidget(window.chat_page)
        window.show()
        app.processEvents()

        page = window.chat_page
        assert page.conversation_scroll.geometry().bottom() < page.input_group.geometry().top()
        assert (
            page.input_group.sizePolicy().horizontalPolicy()
            == QSizePolicy.Policy.Expanding
        )
        assert 120 <= page.input_group.height() <= 160
        assert (
            page.conversation_scroll.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Expanding
        )
        assert page.conversation_scroll.height() > page.input_group.height()

        assert not page.send_button.isEnabled()
        assert not page.cancel_button.isEnabled()
        assert page.new_chat_button.isEnabled()
        page.input_text.setPlainText("message ready to send")
        assert page.send_button.isEnabled()
        page.set_busy(any_llm_busy=True, chat_busy=True)
        assert not page.send_button.isEnabled()
        assert page.cancel_button.isEnabled()
        assert not page.new_chat_button.isEnabled()
        page.set_busy(any_llm_busy=False, chat_busy=False)
        assert page.send_button.isEnabled()
        assert not page.cancel_button.isEnabled()
        assert page.new_chat_button.isEnabled()

        long_text = "A long wrapped chat message. " * 40
        for index in range(12):
            page.add_message("user" if index % 2 == 0 else "assistant", long_text)
        app.processEvents()
        app.processEvents()
        scroll_bar = page.conversation_scroll.verticalScrollBar()
        assert scroll_bar.maximum() > 0
        page.add_message("assistant", "latest assistant response")
        app.processEvents()
        app.processEvents()
        assert scroll_bar.value() == scroll_bar.maximum()
    finally:
        _close(window, server, app)


def test_common_supplement_exists_for_every_profile_task_and_capabilities(tmp_path):
    app = _app()
    window, server = _window(tmp_path)
    try:
        for profile in window._available_profiles():
            window._select_chat_target_profile(profile.manifest.id)
            for task in profile.manifest.supported_tasks:
                window._select_chat_target_task(task)
                window.mode_supplement_toggle.setChecked(True)
                app.processEvents()
                assert window.mode_section.isVisibleTo(window.prompt_page)
                assert window.common_note.isVisibleTo(window.prompt_page)
                keys = {
                    window.chat_page.destination.itemData(index)
                    for index in range(window.chat_page.destination.count())
                }
                assert "request" in keys
                assert "common" in keys
                start, end, _refs = window._supplement_capabilities(task)
                assert ("start" in keys) is start
                assert ("end" in keys) is end
    finally:
        _close(window, server, app)


def test_overall_supplement_has_an_explicit_role_without_mutating_request(tmp_path):
    registry = RendererRegistry()
    catalog = ProfileLoader(PROJECT_ROOT / "profiles", tmp_path, registry).discover()
    profile = catalog.get("krea_2")
    engine = PromptEngine(None, profile, "turbo", registry)
    request = "A portrait by a café window."
    supplement = "Keep [text:ja]月夜珈琲[/text] and ginntuinn exact."
    settings = PromptSettings(
        mode="T2I",
        processing="Faithful",
        common_supplement=supplement,
        protected_terms=("ginntuinn",),
    )
    payload = engine.request_payload(request, settings)
    user_content = payload["messages"][-1]["content"]
    system_content = payload["messages"][0]["content"]
    assert user_content == request
    assert "OVERALL_SUPPLEMENT:" in system_content
    assert supplement in system_content
    assert "must not replace the request" in system_content
    assert request == "A portrait by a café window."
    rendered = engine.finalize_output(
        request,
        settings,
        'A portrait by a café window with a sign reading "月夜珈琲" and ginntuinn.',
    )
    assert "月夜珈琲" in rendered.positive
    assert "ginntuinn" in rendered.positive


def test_chat_engine_uses_neutral_messages_and_full_conversation():
    conversation = [
        {"role": "user", "content": "What is Qwen?"},
        {"role": "assistant", "content": "It is a model family."},
        {"role": "user", "content": "Tell me about the 4B version."},
    ]
    payload = ChatEngine().request_payload(conversation)
    assert payload["messages"][0] == {
        "role": "system",
        "content": CHAT_SYSTEM_PROMPT,
    }
    assert payload["messages"][1:] == conversation
    assert "SELECTED PROFILE" not in str(payload)
    assert "Renderer" not in str(payload)
    assert payload["stream"] is False


def test_chat_two_turns_copy_and_new_chat_only_clear_chat(tmp_path):
    app = _app()
    window, server = _window(tmp_path, response="First answer")
    try:
        def renderer_must_not_run(*_args, **_kwargs):
            raise AssertionError("AI Chat must not invoke a Prompt Renderer")

        window.renderer_registry.get = renderer_must_not_run
        window.request_text.setPlainText("prompt request")
        window.common_note.setPlainText("prompt supplement")
        window.chat_page.input_text.setPlainText("First question")
        window.chat_page.send_button.click()
        _wait_until(app, lambda: not window._chat_active)
        assert window.chat_messages == [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
        ]
        assert window.chat_worker is None
        assert server.received_payloads[-1]["messages"][-1]["content"] == "First question"

        server.response_text = "Second answer"
        window.chat_page.input_text.setPlainText("Follow-up question")
        window.chat_page.send_button.click()
        _wait_until(app, lambda: not window._chat_active)
        messages = server.received_payloads[-1]["messages"]
        assert [message["content"] for message in messages[1:]] == [
            "First question",
            "First answer",
            "Follow-up question",
        ]
        copy_buttons = window.chat_page.findChildren(QPushButton, "chat_copy_button")
        copy_buttons[-1].click()
        assert QApplication.clipboard().text() == "Second answer"

        window.chat_page.new_chat_button.click()
        app.processEvents()
        assert window.chat_messages == []
        assert window.request_text.toPlainText() == "prompt request"
        assert window.common_note.toPlainText() == "prompt supplement"
        assert window.profile.manifest.id == "minimax_h3"
    finally:
        _close(window, server, app)


def test_chat_and_prompt_share_one_manager_and_exclusive_busy_state(tmp_path):
    app = _app()
    window, server = _window(tmp_path, delay=0.25)
    try:
        window.chat_page.input_text.setPlainText("slow chat")
        window.chat_page.send_button.click()
        _wait_until(app, lambda: window._chat_active)
        assert window.chat_worker is not None
        assert window.chat_worker.server is window.server
        assert not window.chat_page.send_button.isEnabled()
        assert window.chat_page.cancel_button.isEnabled()
        assert not window.generate_button.isEnabled()
        assert not window.unload_model_button.isEnabled()
        assert not window.chat_page.unload_button.isEnabled()
        assert window.main_tabs.isEnabled()
        window.main_tabs.setCurrentIndex(0)
        assert window.main_tabs.currentWidget() is window.prompt_page
        window.generate()
        assert window.worker is None
        window.chat_page.cancel_button.click()
        _wait_until(app, lambda: not window._chat_active)

        window.request_text.setPlainText("A rainy street")
        window.generate()
        _wait_until(app, lambda: window._generation_active)
        assert not window.chat_page.send_button.isEnabled()
        assert not window.unload_model_button.isEnabled()
        window.main_tabs.setCurrentIndex(1)
        assert window.main_tabs.currentWidget() is window.chat_page
        window.cancel_generation()
        _wait_until(app, lambda: not window._generation_active)
    finally:
        _close(window, server, app)


def test_transfer_appends_notifies_opens_and_focuses_without_selection(tmp_path):
    app = _app()
    answer = "Complete assistant answer."
    window, server = _window(
        tmp_path,
        response=f"[TRANSFER_CONTENT]\n{answer}\n[/TRANSFER_CONTENT]",
    )
    try:
        window.show()
        app.processEvents()
        window.mode.setCurrentText("FL2VA")
        window.main_tabs.setCurrentWidget(window.chat_page)
        window.common_note.setPlainText("existing text")
        window.chat_page.add_message("assistant", answer)
        transfer_buttons = window.chat_page.findChildren(
            QPushButton, "chat_transfer_button"
        )
        transfer_buttons[-1].click()
        _wait_until(app, lambda: not window._chat_active)
        assert window.chat_page.transfer_panel.isVisibleTo(window.chat_page)
        assert window.chat_page.transfer_content.toPlainText() == answer
        assert "MiniMax H3 / FL2VA" in window.chat_page.target_label.text()
        keys = [
            window.chat_page.destination.itemData(index)
            for index in range(window.chat_page.destination.count())
        ]
        assert keys == ["request", "common", "start", "end"]
        window.request_text.setPlainText("existing request")
        window.chat_page.destination.setCurrentIndex(
            window.chat_page.destination.findData("request")
        )
        window.chat_page.transfer_button.click()
        assert window.request_text.toPlainText() == f"existing request\n\n{answer}"
        window.chat_page.open_transfer_panel(answer)
        window.chat_page.destination.setCurrentIndex(
            window.chat_page.destination.findData("common")
        )
        window.chat_page.transfer_button.click()
        assert window.common_note.toPlainText() == f"existing text\n\n{answer}"
        assert window.main_tabs.currentWidget() is window.chat_page
        assert window.chat_page.notification.isVisibleTo(window.chat_page)
        assert "MiniMax H3 / FL2VA" in window.chat_page.notification_label.text()

        open_button = window.chat_page.notification.findChild(QPushButton)
        open_button.click()
        app.processEvents()
        assert window.main_tabs.currentWidget() is window.prompt_page
        assert window.mode_supplement_toggle.isChecked()
        assert window.common_note.hasFocus()
        assert not window.common_note.textCursor().hasSelection()
        assert window.common_note.toPlainText() == f"existing text\n\n{answer}"
    finally:
        _close(window, server, app)


def test_chat_target_change_updates_real_prompt_target_and_preserves_data(tmp_path):
    app = _app()
    window, server = _window(tmp_path)
    try:
        window.request_text.setPlainText("request")
        window.common_note.setPlainText("common")
        window.start_note.setPlainText("start")
        window.end_note.setPlainText("end")
        window.chat_page.open_transfer_panel("answer")
        window.chat_page.change_target_button.setChecked(True)
        wan_index = window.chat_page.target_profile.findData("wan_2_2")
        window.chat_page.target_profile.setCurrentIndex(wan_index)
        app.processEvents()
        assert window.profile_model.currentData() == "wan_2_2"
        assert window.profile_category.currentData() == "video"
        assert window.profile_variant.currentData() == "a14b"
        i2v_index = window.chat_page.target_task.findData("I2V")
        window.chat_page.target_task.setCurrentIndex(i2v_index)
        app.processEvents()
        assert window.mode.currentData() == "I2V"
        assert window.main_tabs.tabText(0) == "Wan 2.2 / I2V"
        assert "Wan 2.2 / I2V" in window.chat_page.target_label.text()
        assert window.request_text.toPlainText() == "request"
        assert window.common_note.toPlainText() == "common"
        assert window.start_note.toPlainText() == "start"
        assert window.end_note.toPlainText() == "end"
        keys = {
            window.chat_page.destination.itemData(index)
            for index in range(window.chat_page.destination.count())
        }
        assert keys == {"request", "common", "start"}
    finally:
        _close(window, server, app)


def test_chat_context_overflow_is_explicit_and_not_truncated(monkeypatch):
    class ContextFailingServer:
        def start(self, *args, **kwargs):
            return "http://127.0.0.1:1"

        def preflight_context(self, payload, context_size):
            assert len(payload["messages"]) == 4
            raise LlamaContextError("too long")

        def generate(self, payload, timeout):
            raise AssertionError("generate must not run")

    monkeypatch.setattr(
        "app.workers.validate_model",
        lambda path: SimpleNamespace(path=Path(path)),
    )
    errors: list[str] = []
    thread = ChatThread(
        engine=ChatEngine(),
        server=ContextFailingServer(),
        config=AppConfig(model_path="model.gguf", context_size=4096),
        conversation=[
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ],
        mock_mode=False,
    )
    thread.error_occurred.connect(errors.append)
    thread.run()
    assert errors == ["CHAT_CONTEXT_OVERFLOW"]


def test_history_records_common_supplement_and_chat_is_not_persisted(tmp_path, caplog):
    database = tmp_path / "history.sqlite3"
    history = HistoryManager(database)
    history.add(
        enabled=True,
        mode="T2I",
        request="request",
        output="output",
        common_supplement="reproduction note",
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT common_supplement FROM history"
        ).fetchone() == ("reproduction note",)

    app = _app()
    private_dir = tmp_path / "private-chat"
    window, server = _window(private_dir, response="PRIVATE_ASSISTANT_BODY")
    try:
        caplog.set_level(logging.DEBUG)
        window.chat_page.input_text.setPlainText("PRIVATE_USER_BODY")
        window.chat_page.send_button.click()
        _wait_until(app, lambda: not window._chat_active)
        assert "PRIVATE_USER_BODY" not in caplog.text
        assert "PRIVATE_ASSISTANT_BODY" not in caplog.text
        assert not (private_dir / "history.sqlite3").exists()
    finally:
        _close(window, server, app)


def test_chat_localization_is_complete():
    japanese = Localization(PROJECT_ROOT / "locales", "ja-JP")
    english = Localization(PROJECT_ROOT / "locales", "en-US")
    assert japanese.tr("tabs.ai_chat") == "AIチャット"
    assert japanese.tr("mode.common_note") == "全体についての補足"
    assert english.tr("tabs.ai_chat") == "AI Chat"
    assert english.tr("mode.common_note") == "Overall Supplement"
    assert "context limit" in english.tr("chat.error.context")
