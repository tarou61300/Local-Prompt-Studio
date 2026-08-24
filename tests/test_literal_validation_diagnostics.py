from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.main_window import literal_validation_user_message
from app.workers import GenerationThread, generation_error_message
from core.config_manager import AppConfig, ConfigManager
from core.localization import Localization
from core.profile_loader import ProfileLoader
from core.prompt_engine import PromptEngine, PromptSettings
from core.renderers import (
    LITERAL_CONTENT_NOT_PRESERVED,
    TransformationError,
    parse_literal_validation_error,
)
from core.skill_manager import SkillManager
from mock_server import start_mock_server


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def _engine() -> PromptEngine:
    profile = ProfileLoader(
        ROOT / "profiles",
        ROOT / ".tmp-unused",
    ).discover().profiles["minimax_h3"]
    return PromptEngine(SkillManager(SKILL), profile, "base")


def _literal_error(
    request: str,
    settings: PromptSettings,
    generated: str = "A generated prompt without the required literal.",
) -> TransformationError:
    with pytest.raises(TransformationError) as caught:
        _engine().finalize_output(request, settings, generated)
    assert caught.value.code == LITERAL_CONTENT_NOT_PRESERVED
    assert caught.value.literal_diagnostics is not None
    return caught.value


def test_request_paired_literal_diagnostic_has_role_type_length_and_hash():
    literal = "月夜珈琲"
    error = _literal_error(
        f"看板に[text:ja]{literal}[/text]と表示する。",
        PromptSettings(mode="T2VA"),
    )

    details = error.literal_diagnostics
    assert details is not None
    assert details.detected_count == 1
    assert details.missing_count == 1
    assert details.missing[0].source_role == "request"
    assert details.missing[0].detection_type == "paired"
    assert details.missing[0].character_count == len(literal)
    assert details.missing[0].short_hash == hashlib.sha256(
        literal.encode("utf-8")
    ).hexdigest()[:8]


def test_common_supplement_quote_diagnostic_has_common_role_and_quote_type():
    literal = "共通補足の秘密文字列"
    error = _literal_error(
        "A quiet street.",
        PromptSettings(
            mode="T2VA",
            common_supplement=f"看板に「{literal}」と書かれている。",
        ),
    )

    item = error.literal_diagnostics.missing[0]
    assert item.source_role == "common_supplement"
    assert item.detection_type == "quote"


def test_start_image_supplement_literal_diagnostic_has_start_role():
    error = _literal_error(
        "A person moves between two images.",
        PromptSettings(
            mode="FL2VA",
            start_frame_note="[speech:ja]開始画像の発話[/speech]",
        ),
    )

    assert error.literal_diagnostics.missing[0].source_role == "start_supplement"


def test_end_image_supplement_legacy_literal_diagnostic_has_end_role():
    error = _literal_error(
        "A person moves between two images.",
        PromptSettings(
            mode="FL2VA",
            end_frame_note="[text:ja] 終了画像の表示",
        ),
    )

    item = error.literal_diagnostics.missing[0]
    assert item.source_role == "end_supplement"
    assert item.detection_type == "legacy"


def test_multiple_missing_literals_report_total_count_and_every_item():
    error = _literal_error(
        "[speech:ja]Request発話[/speech]",
        PromptSettings(
            mode="FL2VA",
            common_supplement="看板に「共通表示」と書かれている。",
            start_frame_note="[text:ja]開始表示[/text]",
            end_frame_note="[speech:ja] 終了発話",
        ),
    )

    details = error.literal_diagnostics
    assert details.detected_count == 4
    assert details.missing_count == 4
    assert {
        (item.source_role, item.detection_type) for item in details.missing
    } == {
        ("request", "paired"),
        ("common_supplement", "quote"),
        ("start_supplement", "paired"),
        ("end_supplement", "legacy"),
    }


def test_successful_validation_does_not_emit_failure_diagnostics(caplog):
    caplog.set_level(logging.WARNING, logger="app.workers")
    request = "[speech:ja]成功時の発話[/speech]"

    result = _engine().finalize_output(
        request,
        PromptSettings(mode="T2VA"),
        "The speaker says 成功時の発話.",
    )

    assert "成功時の発話" in result.positive
    assert "Literal Content validation failed" not in caplog.text


def test_error_signal_and_log_never_include_literal_body(caplog):
    literal = "絶対に診断へ表示しない秘密本文"
    error = _literal_error(
        f"[text:ja]{literal}[/text]",
        PromptSettings(mode="T2VA"),
    )
    caplog.set_level(logging.WARNING, logger="app.workers")

    message = generation_error_message(error)
    parsed = parse_literal_validation_error(message)

    assert parsed == error.literal_diagnostics
    assert literal not in message
    assert literal not in caplog.text
    assert "request/paired/" in caplog.text


def test_generation_thread_emits_parseable_safe_literal_diagnostics():
    literal = "worker signalへ出してはいけない本文"

    class Server:
        @staticmethod
        def generate(payload, timeout):
            return "A generated prompt without the required literal."

    thread = GenerationThread(
        engine=_engine(),
        server=Server(),
        config=AppConfig(),
        request_text=f"[speech:ja]{literal}[/speech]",
        settings=PromptSettings(mode="T2VA"),
        mock_mode=True,
    )
    errors = []
    thread.error_occurred.connect(errors.append)

    thread.run()

    assert len(errors) == 1
    details = parse_literal_validation_error(errors[0])
    assert details is not None
    assert details.missing[0].source_role == "request"
    assert literal not in errors[0]


def test_main_window_shows_diagnostics_and_keeps_invalid_output_unavailable(
    tmp_path,
    monkeypatch,
):
    literal = "MainWindowへ表示しない本文"
    error = _literal_error(
        f"[text:ja]{literal}[/text]",
        PromptSettings(mode="T2VA"),
    )
    safe_message = generation_error_message(error)
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(ui_locale="ja-JP"))
    mock, url = start_mock_server()
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    app = QApplication.instance() or QApplication([])
    try:
        from app.main_window import MainWindow

        window = MainWindow(
            project_root=ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=SKILL,
        )
        window.output_text.setPlainText("stale invalid output")
        window.negative_output_text.setPlainText("stale negative output")

        window._generation_error(safe_message)

        assert window.output_text.toPlainText() == ""
        assert window.negative_output_text.toPlainText() == ""
        assert not window.copy_button.isEnabled()
        assert not window.send_comfyui_button.isEnabled()
        assert len(warnings) == 1
        displayed = warnings[0][0][2]
        assert "検出されたLiteral: 1件" in displayed
        assert "発生元: Request" in displayed
        assert error.literal_diagnostics.missing[0].short_hash in displayed
        assert literal not in displayed
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


@pytest.mark.parametrize(
    ("locale_id", "expected"),
    [
        (
            "ja-JP",
            (
                "検出されたLiteral: 1件",
                "保持できなかったLiteral: 1件",
                "発生元: Request",
                "検出方法: 開始・終了marker",
                "文字数:",
                "識別ID:",
                "折りたたまれている補足欄",
            ),
        ),
        (
            "en-US",
            (
                "Detected Literals: 1",
                "Missing Literals: 1",
                "Source: Request",
                "Detection: paired start/end marker",
                "Character count:",
                "Identifier:",
                "collapsed supplement field",
            ),
        ),
    ],
)
def test_localized_user_diagnostic_is_actionable_and_non_sensitive(
    locale_id,
    expected,
):
    literal = "locale表示へ出してはいけない本文"
    error = _literal_error(
        f"[speech:ja]{literal}[/speech]",
        PromptSettings(mode="T2VA"),
    )
    message = generation_error_message(error)

    displayed = literal_validation_user_message(
        Localization(ROOT / "locales", locale_id),
        message,
    )

    assert displayed is not None
    assert all(fragment in displayed for fragment in expected)
    assert error.literal_diagnostics.missing[0].short_hash in displayed
    assert literal not in displayed
