from __future__ import annotations

import logging
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QFrame, QPushButton

from app.chat_page import ChatMessageWidget, ChatPage
from app.main_window import MainWindow
from core.chat_attachments import ChatImageAttachment
from core.chat_renderers import PromptTransferRenderer, ReferenceImageRenderer
from core.config_manager import AppConfig, ConfigManager
from core.localization import Localization
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


def _write_png(path: Path) -> None:
    image = QImage(120, 60, QImage.Format.Format_RGB32)
    image.fill(0x3A6EA5)
    assert image.save(str(path), "PNG")


def _configured_window(tmp_path: Path, *, response: str):
    image = tmp_path / "猫 sample.png"
    _write_png(image)
    model = tmp_path / "vision-model.gguf"
    mmproj = tmp_path / "vision-mmproj.gguf"
    mmproj.write_bytes(b"GGUF")
    manager = ConfigManager(tmp_path / "data")
    config = AppConfig(model_path=str(model), ui_locale="ja-JP")
    config.set_mmproj_for_model(model, mmproj)
    manager.save(config)
    server, url = start_mock_server(response_text=response)
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url=url,
        dev_skill_path=SKILL_FIXTURE,
    )
    return window, server, image


def test_drag_drop_uses_shared_attach_path_and_displays_aspect_thumbnail(tmp_path):
    app = _app()
    window, server, image = _configured_window(tmp_path, response="unused")
    try:
        window.show()
        window.main_tabs.setCurrentWidget(window.chat_page)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(image))])
        enter = QDragEnterEvent(
            QPoint(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(window.chat_page, enter)
        assert enter.isAccepted()
        assert window.chat_page.drop_hint.isVisibleTo(window.chat_page)
        drop = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(window.chat_page, drop)
        app.processEvents()

        assert window.chat_page.attachment is not None
        assert window.chat_page.attachment.filename == image.name
        pixmap = window.chat_page.attachment_thumbnail.pixmap()
        assert not pixmap.isNull()
        assert pixmap.width() == 96
        assert pixmap.height() == 48
        assert not window.chat_page.drop_hint.isVisible()
        window.chat_page.remove_attachment_button.click()
        assert window.chat_page.attachment is None
    finally:
        window.close()
        app.processEvents()
        server.shutdown()
        server.server_close()


def test_normal_and_reference_analysis_keep_draft_and_reference_is_transfer_ready(
    tmp_path, caplog
):
    app = _app()
    normal_answer = "The image shows a blue rectangular field."
    window, server, image = _configured_window(tmp_path, response=normal_answer)
    caplog.set_level(logging.DEBUG)
    try:
        window.show()
        window.main_tabs.setCurrentWidget(window.chat_page)
        window.mode.setCurrentText("FL2VA")
        window._attach_chat_image(str(image))
        attachment = window.chat_page.attachment
        assert attachment is not None
        window.chat_page.input_text.setPlainText("このdraftは保持する")

        window.chat_page.analyze_button.click()
        _wait_until(app, lambda: not window._chat_active)
        assert window.chat_page.input_text.toPlainText() == "このdraftは保持する"
        assert window.chat_page.attachment is attachment
        normal_content = server.received_payloads[-1]["messages"][-1]["content"]
        assert normal_content[-1] == {
            "type": "text",
            "text": "この画像を解析してください",
        }

        model_response = """[REFERENCE_IMAGE]
SUBJECT:
- one blue rectangle
COMPOSITION:
- horizontal 2:1 frame
[/REFERENCE_IMAGE]"""
        reference = """SUBJECT:
- one blue rectangle
COMPOSITION:
- horizontal 2:1 frame"""
        server.response_text = model_response
        before_reference = len(server.received_payloads)
        window.chat_page.reference_analyze_button.click()
        _wait_until(app, lambda: not window._chat_active)
        assert len(server.received_payloads) == before_reference + 1
        assert window.chat_page.input_text.toPlainText() == "このdraftは保持する"
        assert window.chat_page.attachment is attachment
        assert window.chat_messages[-1]["analysis_type"] == "reference_image"
        assert window.chat_messages[-1]["transfer_ready"] is True
        assert window.chat_messages[-1]["transfer_payload"] == reference

        messages = window.chat_page.findChildren(ChatMessageWidget)
        reference_message = [m for m in messages if m.analysis_type == "reference_image"][-1]
        transfer = reference_message.findChild(QPushButton, "chat_transfer_button")
        payload_count = len(server.received_payloads)
        transfer.click()
        assert window.chat_page.transfer_panel.isVisibleTo(window.chat_page)
        assert window.chat_page.transfer_content.toPlainText() == reference
        assert len(server.received_payloads) == payload_count

        edited = "SUBJECT:\n- one blue rectangle"
        window.chat_page.transfer_content.setPlainText(edited)
        common_index = window.chat_page.destination.findData("common")
        window.chat_page.destination.setCurrentIndex(common_index)
        window.chat_page.transfer_button.click()
        assert window.common_note.toPlainText() == edited

        for destination, widget in (
            ("start", window.start_note),
            ("end", window.end_note),
        ):
            reference_message.findChild(QPushButton, "chat_transfer_button").click()
            window.chat_page.transfer_content.setPlainText(f"{destination} edited")
            index = window.chat_page.destination.findData(destination)
            window.chat_page.destination.setCurrentIndex(index)
            window.chat_page.transfer_button.click()
            assert widget.toPlainText() == f"{destination} edited"

        wan_index = window.chat_page.target_profile.findData("wan_2_2")
        window.chat_page.target_profile.setCurrentIndex(wan_index)
        app.processEvents()
        transfer.click()
        assert window.chat_page.transfer_content.toPlainText() == reference
        assert len(server.received_payloads) == payload_count
        assert "data:image" not in caplog.text
        assert str(image) not in caplog.text
        assert reference not in caplog.text
    finally:
        window.close()
        app.processEvents()
        server.shutdown()
        server.server_close()


def test_reference_renderer_is_model_independent_and_transfer_renderer_keeps_facts(
    tmp_path, monkeypatch
):
    image = tmp_path / "reference.png"
    _write_png(image)
    attachment = ChatImageAttachment.from_file(image)
    monkeypatch.setattr(
        "core.renderers.RendererRegistry.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("generation-model renderer must not be called")
        ),
    )
    payload = ReferenceImageRenderer().request_payload(
        [{"role": "user", "content": "visible label", "image": attachment}]
    )
    assert payload["messages"][-1]["content"][0]["type"] == "image_url"
    system = payload["messages"][0]["content"]
    assert "model-independent" in system
    assert "quality tags" in system
    rendered = ReferenceImageRenderer.finalize_response("SUBJECT:\n- blue rectangle")
    assert rendered == "SUBJECT:\n- blue rectangle"
    assert "[REFERENCE_IMAGE]" not in rendered
    assert "[/REFERENCE_IMAGE]" not in rendered
    legacy = ReferenceImageRenderer.finalize_response(
        "[REFERENCE_IMAGE]\nAPPEARANCE:\n- silver fur\n[/REFERENCE_IMAGE]"
    )
    assert legacy == "APPEARANCE:\n- silver fur"

    source = (
        "はい、わかりました。ginntuinn wears a red coat beside café 月夜珈琲. "
        "The duration is 10 seconds.\n必要なら別案も作成できます。"
    )
    transfer_payload = PromptTransferRenderer().request_payload(
        [{"role": "user", "content": source}]
    )
    assert source in transfer_payload["messages"][-1]["content"]
    result = PromptTransferRenderer.finalize_response(
        "[TRANSFER_CONTENT]\n"
        "はい、わかりました。ginntuinn wears a red coat beside café 月夜珈琲. "
        "The duration is 10 seconds.\n"
        "必要なら別案も作成できます。\n"
        "[/TRANSFER_CONTENT]"
    )
    assert "はい、わかりました" not in result
    assert "必要なら" not in result
    assert "ginntuinn" in result
    assert "red coat" in result
    assert "月夜珈琲" in result
    assert "10 seconds" in result


@pytest.mark.parametrize(
    ("locale", "analyze", "reference", "drop", "transfer"),
    (
        ("ja-JP", "通常解析", "Prompt参照用解析", "ここに画像をドロップ", "転送内容"),
        ("en-US", "Analyze", "Prompt Reference Analysis", "Drop image here", "Transfer content"),
    ),
)
def test_image_reference_controls_are_localized(
    locale, analyze, reference, drop, transfer
):
    app = _app()
    page = ChatPage(Localization(PROJECT_ROOT / "locales", locale).tr)
    try:
        assert page.analyze_button.text() == analyze
        assert page.reference_analyze_button.text() == reference
        assert page.drop_hint.text() == drop
        labels = [label.text() for label in page.transfer_panel.findChildren(type(page.drop_hint))]
        assert transfer in labels
        assert page.transfer_panel.frameShape() == QFrame.Shape.StyledPanel
        assert "palette(mid)" in page.transfer_panel.styleSheet()
        assert page.transfer_content.isReadOnly() is False
        page.open_transfer_panel("SUBJECT:\n- test")
        assert not page.transfer_panel.isHidden()
        page.close_transfer_button.click()
        assert page.transfer_panel.isHidden()
    finally:
        page.close()
        app.processEvents()
