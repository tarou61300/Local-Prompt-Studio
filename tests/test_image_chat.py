from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage, QImageWriter
from PySide6.QtWidgets import QApplication

from app.chat_page import ChatPage
from app.main_window import MainWindow
from app.workers import ChatThread
from core.chat_attachments import ChatImageAttachment, ChatImageError
from core.chat_engine import ChatEngine
from core.config_manager import AppConfig, ConfigManager
from core.llama_manager import LlamaConnectionError, LlamaError
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


def _write_image(
    path: Path,
    kind: str = "png",
    *,
    size: tuple[int, int] = (16, 12),
    alpha: int = 255,
) -> bytes:
    image = QImage(*size, QImage.Format.Format_ARGB32)
    image.fill(QColor(40, 120, 220, alpha))
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buffer, kind.encode("ascii"))
    writer.setQuality(75)
    assert writer.write(image), writer.errorString()
    del writer
    buffer.close()
    payload = bytes(encoded)
    path.write_bytes(payload)
    return payload


@pytest.mark.parametrize(
    ("suffix", "kind", "mime"),
    (
        (".png", "png", "image/png"),
        (".jpg", "jpeg", "image/jpeg"),
        (".jpeg", "jpeg", "image/jpeg"),
        (".webp", "webp", "image/png"),
    ),
)
def test_supported_images_are_memory_only_and_use_magic_mime(tmp_path, suffix, kind, mime):
    image_path = tmp_path / f"画像 sample{suffix}"
    original = _write_image(image_path, kind)

    attachment = ChatImageAttachment.from_file(image_path)
    data_url = attachment.data_url()

    payload_bytes = base64.b64decode(data_url.split(",", 1)[1])
    assert attachment.filename == image_path.name
    assert attachment.mime_type == mime
    assert attachment.size_bytes == len(payload_bytes)
    assert attachment.source_path == str(image_path.resolve())
    if kind == "webp":
        assert payload_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert payload_bytes != original
    else:
        assert payload_bytes == original
    assert image_path.read_bytes() == original
    assert attachment.source_path not in repr(attachment)
    assert "base64" not in repr(attachment)
    assert list(tmp_path.iterdir()) == [image_path]


def test_image_extension_and_content_must_agree(tmp_path):
    mismatched = tmp_path / "fake.png"
    _write_image(mismatched, "jpeg")
    unsupported = tmp_path / "image.gif"
    unsupported.write_bytes(b"GIF89a")
    with pytest.raises(ChatImageError, match="CHAT_IMAGE_DECODE_FAILED"):
        ChatImageAttachment.from_file(mismatched)
    with pytest.raises(ChatImageError, match="CHAT_IMAGE_UNSUPPORTED_FORMAT"):
        ChatImageAttachment.from_file(unsupported)


ANIMATED_WEBP_BYTES = base64.b64decode(
    "UklGRhgBAABXRUJQVlA4WAoAAAASAAAAAwAAAwAAQU5JTQYAAAD/////AABBTk1G"
    "cgAAAAAAAAAAAAMAAAMAAPQBAABWUDhMWgAAAC8DwAAAZ6CobSMmvfE//lQnDQVt"
    "JCnHIOXR87t5UaS0kRQod5Jg6b+qw36FwPwH8HmQKT0L3NgJFMpBVy36b+6EAwe"
    "FkKxQ76PynFJIJ4QQQsgyov/xvmxNA0FOTUZyAAAAAAAAAAAAAwAAAwAA9AEAAFZ"
    "QOExaAAAALwPAAABnoKhtIya98T/+VCcNBW0kKccg5dHzu3lRpLSRFCh3kmDpv6r"
    "DfoXA/AfweZApPQvc2AkUykFXLfpv7oQDB4WQrFDvo/KcUkgnhBBCyDKi//G+bE0D"
)


def test_transparent_static_webp_normalizes_to_png_payload_and_preserves_alpha(tmp_path):
    image_path = tmp_path / "transparent.webp"
    original = _write_image(image_path, "webp", alpha=64)

    attachment = ChatImageAttachment.from_file(image_path)
    decoded = QImage()
    assert decoded.loadFromData(attachment.image_bytes)
    payload = ChatEngine().request_payload(
        [{"role": "user", "content": "Describe it.", "image": attachment}]
    )
    image_url = payload["messages"][-1]["content"][0]["image_url"]["url"]

    assert attachment.mime_type == "image/png"
    assert attachment.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert image_url.startswith("data:image/png;base64,")
    assert decoded.hasAlphaChannel()
    assert decoded.pixelColor(0, 0).alpha() == 64
    assert image_path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [image_path]


def test_animated_webp_is_explicitly_rejected_in_both_locales(tmp_path):
    image_path = tmp_path / "animated.webp"
    image_path.write_bytes(ANIMATED_WEBP_BYTES)

    with pytest.raises(
        ChatImageError,
        match="CHAT_IMAGE_ANIMATED_WEBP_UNSUPPORTED",
    ):
        ChatImageAttachment.from_file(image_path)

    assert "アニメーションWebPには対応していません" in Localization(
        PROJECT_ROOT / "locales", "ja-JP"
    ).tr("chat.error.animated_webp")
    assert "Animated WebP is not supported" in Localization(
        PROJECT_ROOT / "locales", "en-US"
    ).tr("chat.error.animated_webp")
    assert image_path.read_bytes() == ANIMATED_WEBP_BYTES


@pytest.mark.parametrize(
    ("locale_id", "expected"),
    (
        ("ja-JP", "アニメーションWebPには対応していません"),
        ("en-US", "Animated WebP is not supported"),
    ),
)
def test_animated_webp_attachment_error_is_localized_in_main_window(
    tmp_path, locale_id, expected
):
    app = _app()
    image_path = tmp_path / "animated.webp"
    image_path.write_bytes(ANIMATED_WEBP_BYTES)
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"
    model.write_bytes(b"GGUF")
    mmproj.write_bytes(b"GGUF")
    manager = ConfigManager(tmp_path / "data")
    config = AppConfig(model_path=str(model), ui_locale=locale_id)
    config.set_mmproj_for_model(model, mmproj)
    manager.save(config)
    localization = Localization(PROJECT_ROOT / "locales", locale_id)
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:54321",
        dev_skill_path=SKILL_FIXTURE,
        localization=localization,
    )
    try:
        window._attach_chat_image(str(image_path))
        assert window.chat_page.attachment is None
        assert expected in window.chat_page.status_label.text()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize(
    "contents",
    (
        b"not an image",
        b"RIFF\x10\x00\x00\x00WEBPbroken-webp",
    ),
)
def test_corrupt_or_extension_only_webp_fails_safely(tmp_path, contents):
    image_path = tmp_path / "broken.webp"
    image_path.write_bytes(contents)

    with pytest.raises(ChatImageError, match="CHAT_IMAGE_DECODE_FAILED"):
        ChatImageAttachment.from_file(image_path)


@pytest.mark.parametrize(
    ("size", "is_wide"),
    (((1600, 400), True), ((400, 1600), False)),
)
def test_webp_preview_preserves_large_image_aspect_ratio(tmp_path, size, is_wide):
    app = _app()
    image_path = tmp_path / ("wide large.webp" if is_wide else "tall large.webp")
    _write_image(image_path, "webp", size=size)
    page = ChatPage(Localization(PROJECT_ROOT / "locales", "ja-JP").tr)
    page.show()
    try:
        page.set_attachment(ChatImageAttachment.from_file(image_path))
        app.processEvents()
        preview = page.attachment_thumbnail.pixmap()
        assert preview is not None
        assert not preview.isNull()
        assert preview.width() <= 96
        assert preview.height() <= 72
        assert (preview.width() > preview.height()) is is_wide
    finally:
        page.close()


@pytest.mark.parametrize("locale_id", ("ja-JP", "en-US"))
def test_image_picker_filter_includes_webp(locale_id):
    image_filter = Localization(PROJECT_ROOT / "locales", locale_id).tr(
        "chat.image.filter"
    )
    assert image_filter.endswith("(*.png *.jpg *.jpeg *.webp)")


def test_chat_engine_builds_b9637_image_url_payload_and_retains_image_context(tmp_path):
    image_path = tmp_path / "scene.png"
    raw = _write_image(image_path)
    attachment = ChatImageAttachment.from_file(image_path)
    conversation = [
        {"role": "user", "content": "What is shown?", "image": attachment},
        {"role": "assistant", "content": "A test scene."},
        {"role": "user", "content": "Describe its colors."},
    ]

    payload = ChatEngine().request_payload(conversation)
    image_part, text_part = payload["messages"][1]["content"]

    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1]) == raw
    assert text_part == {"type": "text", "text": "What is shown?"}
    assert payload["messages"][-1] == {"role": "user", "content": "Describe its colors."}
    assert "http://" not in image_part["image_url"]["url"]
    assert "https://" not in image_part["image_url"]["url"]


def test_image_only_uses_internal_instruction_without_mutating_message(tmp_path):
    image_path = tmp_path / "scene.webp"
    _write_image(image_path, "webp")
    message = {
        "role": "user",
        "content": "",
        "image": ChatImageAttachment.from_file(image_path),
    }
    payload = ChatEngine("Describe neutrally.").request_payload([message])
    assert payload["messages"][-1]["content"][-1] == {
        "type": "text",
        "text": "Describe neutrally.",
    }
    assert message["content"] == ""


def test_attachment_chip_replaces_removes_and_allows_image_only_send(tmp_path):
    app = _app()
    tr = Localization(PROJECT_ROOT / "locales", "ja-JP").tr
    page = ChatPage(tr)
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.jpg"
    _write_image(first_path)
    _write_image(second_path, "jpeg")
    first = ChatImageAttachment.from_file(first_path)
    second = ChatImageAttachment.from_file(second_path)
    sent = []
    settings_requests = []
    page.send_requested.connect(lambda text, image: sent.append((text, image)))
    page.settings_requested.connect(lambda: settings_requests.append(True))
    page.show()
    app.processEvents()

    page.set_attachment(first)
    assert page.attachment is first
    assert page.attachment_label.text() == "first.png"
    assert page.send_button.isEnabled()
    page.set_attachment(second)
    assert page.attachment is second
    assert page.attachment_label.text() == "second.jpg"
    page.send_button.click()
    assert sent == [("", second)]
    page.remove_attachment_button.click()
    assert page.attachment is None
    assert not page.send_button.isEnabled()
    page.show_mmproj_guidance("mmproj required")
    page.open_settings_button.click()
    assert settings_requests == [True]
    page.close()


def test_no_mmproj_blocks_file_dialog_and_shows_settings_guidance(tmp_path, monkeypatch):
    app = _app()
    manager = ConfigManager(tmp_path / "data")
    manager.save(AppConfig(model_path=str(tmp_path / "model.gguf")))
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:54321",
        dev_skill_path=SKILL_FIXTURE,
    )
    monkeypatch.setattr(
        "app.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("file dialog must not open without mmproj")
        ),
    )
    try:
        window.show()
        window.main_tabs.setCurrentWidget(window.chat_page)
        window.chat_page.image_button.click()
        app.processEvents()
        assert window.chat_page.attachment is None
        assert window.chat_page.mmproj_guidance.isVisibleTo(window.chat_page)
        assert "mmproj" in window.chat_page.mmproj_guidance_label.text()
        assert window.chat_page.open_settings_button.isVisibleTo(window.chat_page)
    finally:
        window.close()
        app.processEvents()


def test_failed_image_turn_rolls_back_context_and_can_return_to_text_only(tmp_path):
    app = _app()
    image_path = tmp_path / "failed.png"
    _write_image(image_path)
    attachment = ChatImageAttachment.from_file(image_path)
    manager = ConfigManager(tmp_path / "data")
    manager.save(AppConfig(model_path=str(tmp_path / "model.gguf")))
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:54321",
        dev_skill_path=SKILL_FIXTURE,
    )
    try:
        window.show()
        window.main_tabs.setCurrentWidget(window.chat_page)
        window.chat_page.set_attachment(attachment)
        pending = {"role": "user", "content": "Describe it.", "image": attachment}
        window.chat_messages.append(pending)
        window._pending_chat_user_message = pending
        window._pending_chat_message_widget = window.chat_page.add_message(
            "user", "Describe it.", image_filename=attachment.filename
        )

        window._chat_error("CHAT_MMPROJ_LOAD_FAILED")
        app.processEvents()

        assert window.chat_messages == []
        assert window.chat_page.attachment is attachment
        assert window.chat_page.input_text.toPlainText() == "Describe it."
        assert window.chat_page.findChildren(
            type(window.chat_page.image_state_label), "chat_message_image"
        ) == []
        window.chat_page.clear_attachment()
        assert window.chat_page.send_button.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_image_chat_success_updates_ui_context_and_never_persists_image(
    tmp_path, monkeypatch, caplog
):
    app = _app()
    image_path = tmp_path / "private-scene.png"
    _write_image(image_path)
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF")
    data_dir = tmp_path / "portable-data"
    manager = ConfigManager(data_dir)
    config = AppConfig(model_path=str(model))
    config.set_mmproj_for_model(model, mmproj)
    manager.save(config)
    mock, url = start_mock_server(response_text="A neutral image description.")
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url=url,
        dev_skill_path=SKILL_FIXTURE,
    )
    monkeypatch.setattr(
        "app.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(image_path), "Images"),
    )
    caplog.set_level(logging.DEBUG)
    try:
        window.show()
        window.main_tabs.setCurrentWidget(window.chat_page)
        window.chat_page.image_button.click()
        assert window.chat_page.attachment is not None
        window.chat_page.input_text.setPlainText("Describe this image.")
        window.chat_page.send_button.click()
        _wait_until(app, lambda: not window._chat_active)

        content = mock.received_payloads[-1]["messages"][-1]["content"]
        assert content[0]["type"] == "image_url"
        assert content[1] == {"type": "text", "text": "Describe this image."}
        assert window.chat_page.attachment is None
        assert window.tr("chat.image_state.available") in window.chat_page.image_state_label.text()
        image_labels = window.chat_page.findChildren(type(window.chat_page.image_state_label), "chat_message_image")
        assert any("private-scene.png" in label.text() for label in image_labels)

        mock.response_text = "The colors remain neutral."
        window.chat_page.input_text.setPlainText("What colors are visible?")
        window.chat_page.send_button.click()
        _wait_until(app, lambda: not window._chat_active)
        second_messages = mock.received_payloads[-1]["messages"]
        assert isinstance(second_messages[1]["content"], list)
        assert second_messages[-1]["content"] == "What colors are visible?"
        assert len(window.chat_messages) == 4
        assert not (data_dir / image_path.name).exists()
        assert str(image_path) not in caplog.text
        assert "data:image/png;base64" not in caplog.text

        window.chat_page.new_chat_button.click()
        assert window.chat_messages == []
        assert window.chat_page.attachment is None
    finally:
        window.close()
        app.processEvents()
        mock.shutdown()
        mock.server_close()


class _MultimodalFakeServer:
    def __init__(self, *, failure: Exception | None = None, start_failure: bool = False):
        self.starts = []
        self.payloads = []
        self.states = {}
        self.failure = failure
        self.start_failure = start_failure

    def start(self, model_path, **settings):
        self.starts.append((str(model_path), settings))
        if self.start_failure:
            raise LlamaError("start failed")

    def preflight_context(self, payload, context_size):
        return 100, 512

    def generate(self, payload, timeout):
        self.payloads.append(payload)
        if self.failure is not None:
            raise self.failure
        return "image answer"

    def mark_multimodal_state(self, model_path, mmproj_path, state):
        self.states[(str(model_path), str(mmproj_path))] = state


def test_worker_requests_mmproj_reuses_past_image_context_and_marks_available(
    tmp_path, monkeypatch
):
    image_path = tmp_path / "scene.png"
    _write_image(image_path)
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF")
    config = AppConfig(model_path=str(model))
    config.set_mmproj_for_model(model, mmproj)
    monkeypatch.setattr(
        "app.workers.validate_model", lambda path: SimpleNamespace(path=Path(path))
    )
    image = ChatImageAttachment.from_file(image_path)
    conversation = [{"role": "user", "content": "describe", "image": image}]
    server = _MultimodalFakeServer()
    first = ChatThread(
        engine=ChatEngine(),
        server=server,
        config=config,
        conversation=conversation,
        mock_mode=False,
    )
    results = []
    first.result_ready.connect(results.append)
    first.run()
    assert server.starts[0][1]["mmproj_path"] == str(mmproj.resolve())
    assert server.states[(str(model), str(mmproj.resolve()))] == "available"
    assert results == ["image answer"]

    conversation.extend(
        [
            {"role": "assistant", "content": "image answer"},
            {"role": "user", "content": "follow up"},
        ]
    )
    second = ChatThread(
        engine=ChatEngine(),
        server=server,
        config=config,
        conversation=conversation,
        mock_mode=False,
    )
    second.run()
    assert server.starts[1][1]["mmproj_path"] == str(mmproj.resolve())
    assert isinstance(server.payloads[-1]["messages"][1]["content"], list)


def test_worker_multimodal_errors_are_explicit_and_never_fall_back(tmp_path, monkeypatch):
    image_path = tmp_path / "scene.png"
    _write_image(image_path)
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF")
    config = AppConfig(model_path=str(model))
    config.set_mmproj_for_model(model, mmproj)
    monkeypatch.setattr(
        "app.workers.validate_model", lambda path: SimpleNamespace(path=Path(path))
    )
    conversation = [
        {
            "role": "user",
            "content": "describe",
            "image": ChatImageAttachment.from_file(image_path),
        }
    ]

    unsupported = _MultimodalFakeServer(
        failure=LlamaConnectionError(
            "image input is not supported - provide the mmproj",
            status=400,
            error_type="invalid_request_error",
        )
    )
    thread = ChatThread(
        engine=ChatEngine(),
        server=unsupported,
        config=config,
        conversation=conversation,
        mock_mode=False,
    )
    errors = []
    thread.error_occurred.connect(errors.append)
    thread.run()
    assert errors == ["CHAT_IMAGE_UNSUPPORTED"]
    assert unsupported.states[(str(model), str(mmproj.resolve()))] == "unsupported"
    assert len(unsupported.payloads) == 1

    load_failure = _MultimodalFakeServer(start_failure=True)
    thread = ChatThread(
        engine=ChatEngine(),
        server=load_failure,
        config=config,
        conversation=conversation,
        mock_mode=False,
    )
    errors = []
    thread.error_occurred.connect(errors.append)
    thread.run()
    assert errors == ["CHAT_MMPROJ_LOAD_FAILED"]
    assert load_failure.states[(str(model), str(mmproj.resolve()))] == "load_error"
    assert load_failure.payloads == []
