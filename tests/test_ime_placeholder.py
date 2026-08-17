from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from core.config_manager import AppConfig, ConfigManager
from core.localization import Localization
from mock_server import start_mock_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def _send_preedit(app: QApplication, widget, text: str) -> None:
    QApplication.sendEvent(widget, QInputMethodEvent(text, []))
    app.processEvents()


def _send_commit(app: QApplication, widget, text: str) -> None:
    event = QInputMethodEvent("", [])
    event.setCommitString(text)
    QApplication.sendEvent(widget, event)
    app.processEvents()


@pytest.mark.parametrize("locale_id", ("ja-JP", "en-US"))
@pytest.mark.parametrize(
    ("widget_name", "placeholder_key"),
    (
        ("request_text", "input.placeholder"),
        ("chat_input", "chat.placeholder"),
    ),
)
def test_multiline_placeholder_tracks_document_and_ime_preedit(
    tmp_path,
    locale_id: str,
    widget_name: str,
    placeholder_key: str,
):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(ui_locale=locale_id))
    mock, url = start_mock_server()
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url=url,
        dev_skill_path=SKILL_FIXTURE,
    )
    try:
        widget = (
            window.request_text
            if widget_name == "request_text"
            else window.chat_page.input_text
        )
        expected = Localization(PROJECT_ROOT / "locales", locale_id).tr(
            placeholder_key
        )
        window.show()
        widget.setFocus()
        app.processEvents()

        assert widget.toPlainText() == ""
        assert widget.placeholderText() == expected

        _send_preedit(app, widget, "あいう")
        assert widget.toPlainText() == ""
        assert widget.placeholderText() == ""

        _send_commit(app, widget, "あいう")
        assert widget.toPlainText() == "あいう"
        assert widget.placeholderText() == ""

        widget.clear()
        app.processEvents()
        assert widget.placeholderText() == expected

        _send_preedit(app, widget, "かき")
        assert widget.toPlainText() == ""
        assert widget.placeholderText() == ""
        _send_preedit(app, widget, "")
        assert widget.toPlainText() == ""
        assert widget.placeholderText() == expected

        widget.insertPlainText("direct input")
        app.processEvents()
        assert widget.placeholderText() == ""
        widget.clear()
        assert widget.placeholderText() == expected

        QApplication.clipboard().setText("pasted input")
        widget.paste()
        app.processEvents()
        assert widget.toPlainText() == "pasted input"
        assert widget.placeholderText() == ""
        widget.clear()
        assert widget.placeholderText() == expected
    finally:
        window.close()
        app.processEvents()
        mock.shutdown()
        mock.server_close()
