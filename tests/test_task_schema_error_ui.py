from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.main_window import MainWindow, task_schema_validation_user_message
from core.config_manager import AppConfig, ConfigManager
from core.localization import Localization
from mock_server import start_mock_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


@pytest.mark.parametrize(
    ("locale_id", "error", "task", "fields", "guidance"),
    (
        (
            "ja-JP",
            "Selected Task schema validation failed: I2VA; "
            "fields=subject_definitions,summary,retention_analysis",
            "I2VA",
            ("subject_definitions", "summary", "retention_analysis"),
            "左側の「対象プロファイル」",
        ),
        (
            "en-US",
            "Selected Task schema validation failed: Ref2VA; "
            "fields=integrated_multimodal_description",
            "Ref2VA",
            ("integrated_multimodal_description",),
            "Target Profile on the left",
        ),
    ),
)
def test_task_schema_error_is_localized_and_invalid_output_remains_unavailable(
    tmp_path,
    monkeypatch,
    locale_id,
    error,
    task,
    fields,
    guidance,
):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(ui_locale=locale_id))
    mock, url = start_mock_server()
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    try:
        window = MainWindow(
            project_root=PROJECT_ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=FIXTURE,
        )
        window.output_text.setPlainText("stale invalid output")
        window.negative_output_text.setPlainText("stale negative output")

        window._generation_error(error)

        assert window.output_text.toPlainText() == ""
        assert window.negative_output_text.toPlainText() == ""
        assert not window.copy_button.isEnabled()
        assert not window.send_comfyui_button.isEnabled()
        assert len(warnings) == 1
        shown = warnings[0][0][2]
        assert task in shown
        assert guidance in shown
        assert all(field in shown for field in fields)
        assert "Request" in shown
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_non_schema_errors_are_not_reclassified_as_task_schema_errors():
    localization = Localization(PROJECT_ROOT / "locales", "en-US")

    assert task_schema_validation_user_message(
        localization,
        "AI Chat request failed",
    ) is None
