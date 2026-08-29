from __future__ import annotations

import os
from pathlib import Path
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QTextCharFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.prompt_translation_dialog import PromptTranslationDialog
from app.request_guide import request_guide_entries
from app.workers import TranslationThread
from core.prompt_engine import PromptEngine, PromptSettings
from core.config_manager import AppConfig, ConfigManager
from core.localization import Localization
from core.protected_terms import normalize_protected_terms
from core.renderers import MiniMaxH3Renderer, RendererContext
from core.prompt_translation import (
    UI_LOCALE_TO_SOURCE,
    SOURCE_TO_UI_LOCALE,
    PromptTranslationService,
    TRANSLATION_STRUCTURE_NOT_PRESERVED,
    protected_spans,
)
from core.skill_manager import SkillManager
from mock_server import start_mock_server


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def _tr(locale_id: str = "en-US"):
    return Localization(ROOT / "locales", locale_id).tr


def test_translation_masks_and_restores_all_protected_span_types():
    service = PromptTranslationService()
    source = (
        "<Subject 1>\n"
        "subject_definitions: Synthetic subject\n"
        "[Shot 1] 00:10.000 A synthetic scene.\n"
        "[speech:ja]こんにちは[/speech]\n"
        "[text:ja]月夜珈琲[/text]\n"
        "Keep SYNTHETIC_TOKEN unchanged."
    )
    request = service.request_payload(
        source,
        SOURCE_TO_UI_LOCALE,
        protected_terms=("SYNTHETIC_TOKEN",),
    )
    masked = request.payload["messages"][-1]["content"]

    for protected in (
        "<Subject 1>",
        "subject_definitions:",
        "[Shot 1]",
        "00:10.000",
        "[speech:ja]こんにちは[/speech]",
        "こんにちは",
        "[text:ja]月夜珈琲[/text]",
        "月夜珈琲",
        "SYNTHETIC_TOKEN",
    ):
        assert protected not in masked
    assert "__LPS_STRUCTURE_" in masked

    translated_masked = masked.replace(
        "A synthetic scene.",
        "架空の場面。",
    ).replace(
        "Keep  unchanged.",
        " を変更しない。",
    )
    result = service.finalize_response(
        translated_masked,
        request,
        protected_terms=("SYNTHETIC_TOKEN",),
    )
    for protected in (
        "<Subject 1>",
        "subject_definitions:",
        "[Shot 1]",
        "00:10.000",
        "[speech:ja]こんにちは[/speech]",
        "[text:ja]月夜珈琲[/text]",
        "SYNTHETIC_TOKEN",
    ):
        assert protected in result


def test_translation_rejects_missing_or_reordered_structure_placeholder():
    service = PromptTranslationService()
    request = service.request_payload(
        "<Picture 1> [Shot 1] 00:01.000 synthetic text",
        SOURCE_TO_UI_LOCALE,
    )
    masked = request.payload["messages"][-1]["content"]
    first = request.placeholders[0][0]

    with pytest.raises(ValueError, match=TRANSLATION_STRUCTURE_NOT_PRESERVED):
        service.finalize_response(masked.replace(first, ""), request)

    reversed_placeholders = masked
    names = [name for name, _value in request.placeholders]
    reversed_placeholders = reversed_placeholders.replace(names[0], "__TEMP__")
    reversed_placeholders = reversed_placeholders.replace(names[-1], names[0])
    reversed_placeholders = reversed_placeholders.replace("__TEMP__", names[-1])
    with pytest.raises(ValueError, match=TRANSLATION_STRUCTURE_NOT_PRESERVED):
        service.finalize_response(reversed_placeholders, request)


def test_protection_off_keeps_user_structural_edits_as_current_content():
    service = PromptTranslationService()
    user_edited = "[Shot 2] 00:08.000 Synthetic scene."
    request = service.request_payload(
        user_edited,
        SOURCE_TO_UI_LOCALE,
        structure_protection=False,
    )
    assert request.placeholders == ()
    assert request.payload["messages"][-1]["content"] == user_edited
    result = service.finalize_response(
        "[Shot 2] 00:08.000 架空の場面。",
        request,
    )
    assert "[Shot 2]" in result
    assert "00:08.000" in result
    assert "[Shot 1]" not in result
    assert "00:10.000" not in result
    assert "faithful prompt translator" in request.payload["messages"][0]["content"]
    with pytest.raises(
        ValueError,
        match=TRANSLATION_STRUCTURE_NOT_PRESERVED,
    ):
        service.finalize_response(
            "[Shot 3] 00:08.000 Translator-changed scene.",
            request,
        )


def test_translation_dialog_last_real_user_edit_wins_without_programmatic_loop():
    app = QApplication.instance() or QApplication([])
    dialog = PromptTranslationDialog(
        _tr(),
        "[Shot 1] Synthetic scene.",
        protected_terms=("SYNTHETIC_TOKEN",),
        debounce_ms=1000,
    )
    initial_revision = dialog.revision
    dialog.schedule_translation(SOURCE_TO_UI_LOCALE)
    dialog._debounce.stop()
    original_revision = dialog.revision

    assert dialog.apply_translation_result(
        original_revision,
        SOURCE_TO_UI_LOCALE,
        "[Shot 1] 架空の場面。",
    )
    assert dialog.revision == original_revision
    assert dialog.last_direction == SOURCE_TO_UI_LOCALE

    dialog.translated_edit.setPlainText("[Shot 1] 更新された場面。")
    dialog._debounce.stop()
    japanese_revision = dialog.revision
    assert japanese_revision > original_revision
    assert dialog.last_direction == UI_LOCALE_TO_SOURCE

    assert not dialog.apply_translation_result(
        original_revision,
        SOURCE_TO_UI_LOCALE,
        "[Shot 1] 古い応答。",
    )
    assert "更新された" in dialog.translated_edit.toPlainText()

    assert dialog.apply_translation_result(
        japanese_revision,
        UI_LOCALE_TO_SOURCE,
        "[Shot 1] Updated scene.",
    )
    assert dialog.revision == japanese_revision
    assert dialog.last_direction == UI_LOCALE_TO_SOURCE
    assert initial_revision == 0
    dialog.close()
    app.processEvents()


def test_translation_dialog_debounces_user_edits():
    app = QApplication.instance() or QApplication([])
    dialog = PromptTranslationDialog(
        _tr(),
        "Synthetic scene.",
        debounce_ms=1000,
    )
    emitted = []
    dialog.translation_requested.connect(
        lambda revision, direction, text, protection: emitted.append(
            (revision, direction, text, protection)
        )
    )
    dialog.auto_translate.setChecked(True)
    dialog.original_edit.setPlainText("Synthetic scene updated.")
    assert emitted == []
    assert dialog._debounce.interval() == 1000
    QTest.qWait(1050)
    app.processEvents()
    assert len(emitted) == 1
    assert emitted[0][1] == SOURCE_TO_UI_LOCALE
    dialog.close()


def test_dialog_blocks_structural_user_edit_on_and_keeps_it_off():
    app = QApplication.instance() or QApplication([])
    dialog = PromptTranslationDialog(
        _tr(),
        "[Shot 1] 00:10.000 Synthetic scene.",
        debounce_ms=1000,
    )
    dialog.original_edit.setPlainText("[Shot 2] 00:08.000 Synthetic scene.")
    assert dialog.original_text().startswith("[Shot 1] 00:10.000")

    dialog.structure_protection.setChecked(False)
    dialog._debounce.stop()
    dialog.original_edit.setPlainText("[Shot 2] 00:08.000 Synthetic scene.")
    dialog._debounce.stop()
    assert dialog.original_text().startswith("[Shot 2] 00:08.000")
    assert dialog.protection_warning.isVisible() is False
    dialog.show()
    app.processEvents()
    assert dialog.protection_warning.isVisible() is True
    dialog.close()


def test_request_guide_is_localized_neutral_and_appends_without_overwrite(tmp_path):
    ja_entries = request_guide_entries(_tr("ja-JP"), profile_id="minimax_h3")
    en_entries = request_guide_entries(_tr("en-US"), profile_id="anima")
    zh_entries = request_guide_entries(_tr("zh-CN"), profile_id="wan_2_2")
    ru_entries = request_guide_entries(_tr("ru-RU"), profile_id="ltx_2_3")
    ko_entries = request_guide_entries(_tr("ko-KR"), profile_id="minimax_h3")
    assert [entry.key for entry in ja_entries] == [
        "time",
        "fixed_camera",
        "cut",
        "speech",
        "visible_text",
    ]
    assert [entry.key for entry in en_entries] == [entry.key for entry in ja_entries]
    assert [entry.key for entry in zh_entries] == [entry.key for entry in ja_entries]
    assert [entry.key for entry in ru_entries] == [entry.key for entry in ja_entries]
    assert [entry.key for entry in ko_entries] == [entry.key for entry in ja_entries]
    assert "[speech:ja]" in next(item.example for item in ja_entries if item.key == "speech")
    assert "[text:ja]" in next(item.example for item in ja_entries if item.key == "visible_text")
    assert "[speech:zh]" in next(
        item.example for item in zh_entries if item.key == "speech"
    )
    assert "[text:zh]" in next(
        item.example for item in zh_entries if item.key == "visible_text"
    )
    assert "[speech:ru]" in next(
        item.example for item in ru_entries if item.key == "speech"
    )
    assert "[text:ru]" in next(
        item.example for item in ru_entries if item.key == "visible_text"
    )
    assert "[speech:ko]" in next(
        item.example for item in ko_entries if item.key == "speech"
    )
    assert "[text:ko]" in next(
        item.example for item in ko_entries if item.key == "visible_text"
    )
    assert all(
        "[Shot" not in item.example
        for item in (*ja_entries, *en_entries, *zh_entries, *ru_entries, *ko_entries)
    )

    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=SKILL,
        )
        window.request_text.setPlainText("Existing request.")
        window._insert_request_guide("time")
        text = window.request_text.toPlainText()
        assert text.startswith("Existing request.")
        assert next(item.example for item in ja_entries if item.key == "time") in text
        assert window.request_guide_button.menu().actions()
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_translation_editor_cancel_apply_and_reopen_protection(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        manager = ConfigManager(tmp_path)
        manager.save(AppConfig(ui_locale="ko-KR"))
        window = MainWindow(
            project_root=ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=SKILL,
        )
        window.show()
        app.processEvents()
        initial = "[Shot 1] Synthetic original."
        window.output_text.setPlainText(initial)
        window._open_prompt_translation()
        app.processEvents()
        first = window.translation_dialog
        assert first is not None
        assert first.initial_original_text == initial
        assert first.structure_protection.isChecked()
        first.original_edit.set_programmatic_text("[Shot 1] Cancelled edit.")
        first.reject()
        for _attempt in range(200):
            app.processEvents()
            if not window._translation_active:
                break
            QTest.qWait(10)
        assert not window._translation_active
        assert window.output_text.toPlainText() == initial

        window._open_prompt_translation()
        app.processEvents()
        second = window.translation_dialog
        assert second is not None
        assert second.auto_translate.text() == "자동 번역"
        assert second.structure_protection.isChecked()
        second.structure_protection.setChecked(False)
        second._debounce.stop()
        second.original_edit.setPlainText("[Shot 2] Applied edit.")
        second._debounce.stop()
        second.accept()
        for _attempt in range(200):
            app.processEvents()
            if not window._translation_active:
                break
            QTest.qWait(10)
        assert not window._translation_active
        assert window.output_text.toPlainText() == "[Shot 2] Applied edit."

        window._open_prompt_translation()
        app.processEvents()
        third = window.translation_dialog
        assert third is not None
        assert third.structure_protection.isChecked()
        third.reject()
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()




def test_both_translation_directions_use_faithful_target_language_rules():
    service = PromptTranslationService()
    to_ja = service.request_payload(
        "Synthetic scene.",
        SOURCE_TO_UI_LOCALE,
    )
    to_original = service.request_payload(
        "架空の場面。",
        UI_LOCALE_TO_SOURCE,
    )
    assert "Translate only from English into Japanese" in to_ja.payload["messages"][0]["content"]
    assert "Translate only from Japanese into English" in to_original.payload["messages"][0]["content"]
    assert service.finalize_response("架空の場面。", to_ja) == "架空の場面。"
    assert service.finalize_response("Synthetic scene.", to_original) == "Synthetic scene."


def test_chinese_translation_directions_use_registry_language_names():
    service = PromptTranslationService()
    to_chinese = service.request_payload(
        "Synthetic scene.",
        SOURCE_TO_UI_LOCALE,
        source_language_code="en",
        ui_locale_id="zh-CN",
    )
    to_english = service.request_payload(
        "合成场景。",
        UI_LOCALE_TO_SOURCE,
        source_language_code="en",
        ui_locale_id="zh-CN",
    )

    assert to_chinese.source_language_name == "English"
    assert to_chinese.target_language_name == "Simplified Chinese"
    assert "from English into Simplified Chinese" in to_chinese.payload["messages"][0]["content"]
    assert to_english.source_language_name == "Simplified Chinese"
    assert to_english.target_language_name == "English"
    assert "from Simplified Chinese into English" in to_english.payload["messages"][0]["content"]


def test_russian_translation_directions_use_registry_language_names():
    service = PromptTranslationService()
    to_russian = service.request_payload(
        "Synthetic scene.",
        SOURCE_TO_UI_LOCALE,
        source_language_code="en",
        ui_locale_id="ru-RU",
    )
    to_english = service.request_payload(
        "Синтетическая сцена.",
        UI_LOCALE_TO_SOURCE,
        source_language_code="en",
        ui_locale_id="ru-RU",
    )

    assert to_russian.source_language_name == "English"
    assert to_russian.target_language_name == "Russian"
    assert "from English into Russian" in to_russian.payload["messages"][0]["content"]
    assert to_english.source_language_name == "Russian"
    assert to_english.target_language_name == "English"
    assert "from Russian into English" in to_english.payload["messages"][0]["content"]


def test_korean_translation_directions_use_registry_language_names():
    service = PromptTranslationService()
    to_korean = service.request_payload(
        "Synthetic scene.",
        SOURCE_TO_UI_LOCALE,
        source_language_code="en",
        ui_locale_id="ko-KR",
    )
    to_english = service.request_payload(
        "합성 장면.",
        UI_LOCALE_TO_SOURCE,
        source_language_code="en",
        ui_locale_id="ko-KR",
    )

    assert to_korean.source_language_name == "English"
    assert to_korean.target_language_name == "Korean"
    assert "from English into Korean" in to_korean.payload["messages"][0]["content"]
    assert to_english.source_language_name == "Korean"
    assert to_english.target_language_name == "English"
    assert "from Korean into English" in to_english.payload["messages"][0]["content"]


@pytest.mark.parametrize(
    ("ui_locale_id", "translated_sentence"),
    [
        ("ja-JP", "架空の場面。"),
        ("zh-CN", "合成场景。"),
        ("ru-RU", "Синтетическая сцена."),
        ("ko-KR", "합성 장면."),
    ],
)
def test_translation_structure_is_preserved_for_each_translation_locale(
    ui_locale_id,
    translated_sentence,
):
    service = PromptTranslationService()
    source = (
        "<Subject 1>\n"
        "subject_definitions: Synthetic subject\n"
        "<Picture 1> [Shot 1] 00:10.000 Synthetic scene.\n"
        "[speech:ja]こんにちは[/speech] [text:en]OPEN[/text]\n"
        "Keep SYNTHETIC_TOKEN unchanged."
    )
    request = service.request_payload(
        source,
        SOURCE_TO_UI_LOCALE,
        source_language_code="en",
        ui_locale_id=ui_locale_id,
        protected_terms=("SYNTHETIC_TOKEN",),
    )
    masked = request.payload["messages"][-1]["content"]
    translated = masked.replace("Synthetic scene.", translated_sentence)

    result = service.finalize_response(
        translated,
        request,
        protected_terms=("SYNTHETIC_TOKEN",),
    )

    for protected in (
        "<Subject 1>",
        "subject_definitions:",
        "<Picture 1>",
        "[Shot 1]",
        "00:10.000",
        "[speech:ja]こんにちは[/speech]",
        "[text:en]OPEN[/text]",
        "SYNTHETIC_TOKEN",
    ):
        assert protected in result


@pytest.mark.parametrize(
    ("locale_id", "expected_visible"),
    [
        ("en-US", False),
        ("ja-JP", True),
        ("zh-CN", True),
        ("ru-RU", True),
        ("ko-KR", True),
    ],
)
def test_translation_button_visibility_compares_ui_and_profile_languages(
    tmp_path,
    locale_id,
    expected_visible,
):
    app = QApplication.instance() or QApplication([])
    manager = ConfigManager(tmp_path / locale_id)
    config = manager.load()
    config.ui_locale = locale_id
    manager.save(config)
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=ROOT,
            config_manager=manager,
            server_url=url,
            dev_skill_path=SKILL,
            localization=Localization(ROOT / "locales", locale_id),
        )
        window.show()
        app.processEvents()
        assert window.profile is not None
        assert window.profile.manifest.output_language == "en"
        assert window.edit_prompt_button.isVisible() is expected_visible
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def test_translation_worker_reuses_prompt_model_and_one_server(tmp_path):
    model = tmp_path / "synthetic-prompt-model.gguf"
    model.write_bytes(b"GGUF")

    class FakeServer:
        def __init__(self):
            self.starts = []
            self.preflights = 0
            self.generations = 0

        def start(self, model_path, **options):
            self.starts.append((str(model_path), options))

        def preflight_context(self, payload, context_size):
            self.preflights += 1
            return 10, 10

        def generate(self, payload, timeout):
            self.generations += 1
            return payload["messages"][-1]["content"].replace(
                "Synthetic scene.",
                "架空の場面。",
            )

    server = FakeServer()
    worker = TranslationThread(
        service=PromptTranslationService(),
        server=server,
        config=AppConfig(model_path=str(model), context_size=4096),
        source_text="[Shot 1] Synthetic scene.",
        direction=SOURCE_TO_UI_LOCALE,
        protected_terms=(),
        structure_protection=True,
        revision=7,
        mock_mode=False,
    )
    results = []
    worker.result_ready.connect(
        lambda revision, direction, text: results.append(
            (revision, direction, text)
        )
    )
    worker.run()

    assert len(server.starts) == 1
    assert server.starts[0][0] == str(model.resolve())
    assert server.preflights == 1
    assert server.generations == 1
    assert results == [
        (7, SOURCE_TO_UI_LOCALE, "[Shot 1] 架空の場面。")
    ]


def test_minimax_h3_intent_guardrails_do_not_freeze_skill_output_syntax():
    renderer = MiniMaxH3Renderer()
    analysis = renderer.analyze_request(
        "A fixed-camera scene with an explicit ten-second action."
    )
    instructions = renderer.system_instructions(
        RendererContext(
            task="T2VA",
            processing="Faithful",
            output_language="en",
            variant_id="base",
            camera="Static camera",
        ),
        analysis,
        normalize_protected_terms([]),
    )
    for rule in (
        "timing change alone never implies a shot change",
        "Do not invent camera movement",
        "camera is fixed/static",
        "Preserve every explicit time",
        "Do not infer live-action, 2D, 3D, cinematic, watercolor",
        "Skill and selected Profile are authoritative",
    ):
        assert rule in instructions
    final_guardrails = renderer.post_external_intent_guardrails(
        RendererContext(
            task="T2VA",
            processing="Faithful",
            output_language="en",
            variant_id="base",
        )
    )
    assert "FINAL INTENT-PRESERVATION OVERRIDE" in final_guardrails
    for frozen_format in (
        "At MM:SS.mmm",
        "integrated_multimodal_description",
        "detailed_description:",
        "[Shot N]",
    ):
        assert frozen_format not in instructions
        assert frozen_format not in final_guardrails

def test_protected_spans_are_highlighted_without_changing_prompt_text():
    app = QApplication.instance() or QApplication([])
    source = (
        "<Subject 1> <Picture 1>\n"
        "subject_definitions: SYNTHETIC_TOKEN\n"
        "[Shot 1] 00:10.000 A synthetic scene.\n"
        "[speech:ja]こんにちは[/speech]"
    )
    dialog = PromptTranslationDialog(
        _tr(),
        source,
        protected_terms=("SYNTHETIC_TOKEN",),
    )
    expected = {
        "<Subject 1>",
        "<Picture 1>",
        "subject_definitions:",
        "SYNTHETIC_TOKEN",
        "[Shot 1]",
        "00:10.000",
        "[speech:ja]こんにちは[/speech]",
    }

    assert dialog.original_text() == source
    assert {span.text for span in dialog.original_edit.highlighted_spans} == expected
    assert len(dialog.original_edit.extraSelections()) == len(expected)
    assert all(
        selection.format.underlineStyle()
        == QTextCharFormat.UnderlineStyle.SingleUnderline
        for selection in dialog.original_edit.extraSelections()
    )

    translated = source.replace("A synthetic scene.", "架空の場面。")
    assert dialog.apply_translation_result(
        0,
        SOURCE_TO_UI_LOCALE,
        translated,
    )
    assert dialog.translated_edit.toPlainText() == translated
    assert {span.text for span in dialog.translated_edit.highlighted_spans} == expected
    assert dialog.original_text() == source
    dialog.close()
    app.processEvents()


def test_protection_visual_state_and_reenabled_baseline_follow_current_structure():
    app = QApplication.instance() or QApplication([])
    dialog = PromptTranslationDialog(
        _tr(),
        "[Shot 1] 00:10.000 Synthetic scene.",
    )
    dialog.structure_protection.setChecked(False)
    dialog._debounce.stop()

    assert all(
        selection.format.underlineStyle()
        == QTextCharFormat.UnderlineStyle.DotLine
        for selection in dialog.original_edit.extraSelections()
    )
    dialog.original_edit.setPlainText("[Shot 2] 00:08.000 Synthetic scene.")
    dialog._debounce.stop()
    assert dialog.original_text().startswith("[Shot 2] 00:08.000")
    assert {span.text for span in dialog.original_edit.highlighted_spans} >= {
        "[Shot 2]",
        "00:08.000",
    }

    dialog.structure_protection.setChecked(True)
    dialog._debounce.stop()
    assert all(
        selection.format.underlineStyle()
        == QTextCharFormat.UnderlineStyle.SingleUnderline
        for selection in dialog.original_edit.extraSelections()
    )
    dialog.original_edit.setPlainText("[Shot 3] 00:06.000 Synthetic scene.")
    assert dialog.original_text().startswith("[Shot 2] 00:08.000")
    assert {span.text for span in dialog.original_edit.highlighted_spans} >= {
        "[Shot 2]",
        "00:08.000",
    }
    dialog.close()
    app.processEvents()


def test_auto_translate_off_waits_for_manual_update_and_uses_last_real_edit():
    app = QApplication.instance() or QApplication([])
    dialog = PromptTranslationDialog(
        _tr(),
        "Synthetic original.",
        debounce_ms=50,
    )
    emitted = []
    dialog.translation_requested.connect(
        lambda revision, direction, text, protection: emitted.append(
            (revision, direction, text, protection)
        )
    )
    assert not dialog.auto_translate.isChecked()
    dialog.original_edit.setPlainText("Synthetic original edited.")
    QTest.qWait(80)
    app.processEvents()
    assert emitted == []
    assert dialog.last_direction == SOURCE_TO_UI_LOCALE
    assert dialog.source_label.text() == "Source: Original Prompt"

    dialog.update_translation_button.click()
    assert len(emitted) == 1
    first_revision = emitted[0][0]
    assert emitted[0][1:3] == (
        SOURCE_TO_UI_LOCALE,
        "Synthetic original edited.",
    )

    dialog.translated_edit.setPlainText("架空の日本語編集。")
    QTest.qWait(80)
    app.processEvents()
    assert len(emitted) == 1
    assert dialog.last_direction == UI_LOCALE_TO_SOURCE
    assert dialog.source_label.text() == "Source: Translation"
    dialog.update_translation_button.click()
    assert len(emitted) == 2
    latest_revision = emitted[-1][0]
    assert latest_revision > first_revision
    assert emitted[-1][1:3] == (
        UI_LOCALE_TO_SOURCE,
        "架空の日本語編集。",
    )
    assert not dialog.apply_translation_result(
        first_revision,
        SOURCE_TO_UI_LOCALE,
        "古い応答。",
    )
    assert dialog.apply_translation_result(
        latest_revision,
        UI_LOCALE_TO_SOURCE,
        "Final synthetic original.",
    )
    assert dialog.original_text() == "Final synthetic original."
    assert len(emitted) == 2
    dialog.close()
    app.processEvents()


def test_manual_update_is_immediate_with_auto_translate_on():
    app = QApplication.instance() or QApplication([])
    dialog = PromptTranslationDialog(
        _tr(),
        "Synthetic original.",
        debounce_ms=1000,
    )
    emitted = []
    dialog.translation_requested.connect(
        lambda revision, direction, text, protection: emitted.append(
            (revision, direction, text, protection)
        )
    )
    dialog.auto_translate.setChecked(True)
    dialog.original_edit.setPlainText("Immediate synthetic edit.")
    assert emitted == []
    dialog.update_translation_button.click()
    assert len(emitted) == 1
    assert emitted[0][1:3] == (
        SOURCE_TO_UI_LOCALE,
        "Immediate synthetic edit.",
    )
    QTest.qWait(1050)
    app.processEvents()
    assert len(emitted) == 1
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("locale_id", "edit_label", "auto_label", "update_label", "source_label"),
    [
        (
            "ja-JP",
            "翻訳付き編集",
            "自動翻訳",
            "翻訳を更新",
            "同期元: Original Prompt",
        ),
        (
            "en-US",
            "Translate & Edit",
            "Auto translate",
            "Update Translation",
            "Source: Original Prompt",
        ),
        (
            "zh-CN",
            "翻译并编辑",
            "自动翻译",
            "更新翻译",
            "来源：Original Prompt",
        ),
        (
            "ru-RU",
            "Перевести и редактировать",
            "Автоперевод",
            "Обновить перевод",
            "Источник: исходный промпт",
        ),
        (
            "ko-KR",
            "번역 및 편집",
            "자동 번역",
            "번역 업데이트",
            "원본: 원본 프롬프트",
        ),
    ],
)
def test_translation_editor_controls_are_localized(
    locale_id,
    edit_label,
    auto_label,
    update_label,
    source_label,
):
    app = QApplication.instance() or QApplication([])
    localization = Localization(ROOT / "locales", locale_id)
    dialog = PromptTranslationDialog(
        localization.tr,
        "Synthetic original.",
    )
    assert localization.tr("translation.edit") == edit_label
    assert dialog.auto_translate.text() == auto_label
    assert dialog.update_translation_button.text() == update_label
    assert dialog.source_label.text() == source_label
    assert dialog.protection_legend.text()
    dialog.close()
    app.processEvents()


def test_translation_edit_main_action_has_normal_button_click_target(tmp_path):
    app = QApplication.instance() or QApplication([])
    mock, url = start_mock_server()
    try:
        window = MainWindow(
            project_root=ROOT,
            config_manager=ConfigManager(tmp_path),
            server_url=url,
            dev_skill_path=SKILL,
        )
        assert window.edit_prompt_button.text() == "翻訳付き編集"
        assert window.edit_prompt_button.minimumWidth() >= 150
        assert window.edit_prompt_button.minimumHeight() >= 30
        window.close()
        app.processEvents()
    finally:
        mock.shutdown()
        mock.server_close()


def _assembled_h3_system_with_example_skill(tmp_path, request, settings):
    skill = tmp_path / "synthetic-h3-skill"
    shutil.copytree(SKILL, skill)
    example = (
        "FORMAT EXAMPLE ONLY:\n"
        "[Shot 1] Live-action, cinematic. The camera tracks the subject.\n"
    )
    (skill / "references" / "base-en.txt").write_text(
        example,
        encoding="utf-8",
    )
    return PromptEngine(SkillManager(skill)).build_messages(request, settings)


def test_h3_unspecified_style_and_camera_guardrail_follows_skill_examples(tmp_path):
    request = (
        "動画の0-10秒間は女性が道を歩く。"
        "動画の10-15秒の間は歩いていた女性が走るようになる。"
    )
    messages = _assembled_h3_system_with_example_skill(
        tmp_path,
        request,
        PromptSettings(
            processing="Faithful",
            camera="Free",
            shot="Single continuous shot",
        ),
    )
    system = messages[0]["content"]
    assert system.startswith("CORE TRANSFORMATION POLICY")
    assert system.index("FORMAT EXAMPLE ONLY") < system.index(
        "FINAL INTENT-PRESERVATION OVERRIDE"
    )
    final = system[system.index("FINAL INTENT-PRESERVATION OVERRIDE") :]
    assert "examples are non-normative" in final
    assert "Otherwise omit it" in final
    assert "Free: it does not request tracking" in final
    assert "one continuous shot unless the user explicitly requests a cut" in final
    assert "change from walking to running do not by themselves request a cut" in final
    assert messages[1] == {"role": "user", "content": request}


def test_h3_fixed_camera_guardrail_is_final_instruction(tmp_path):
    messages = _assembled_h3_system_with_example_skill(
        tmp_path,
        "最初から最後まで固定カメラ。カット変更なし。",
        PromptSettings(
            processing="Faithful",
            camera="Static camera",
            shot="Single continuous shot",
        ),
    )
    system = messages[0]["content"]
    final = system[system.index("FINAL INTENT-PRESERVATION OVERRIDE") :]
    assert "static camera from beginning to end" in final
    assert "add no camera movement or reframing" in final
    assert system.rfind("FINAL INTENT-PRESERVATION OVERRIDE") > system.index(
        "The camera tracks the subject"
    )


@pytest.mark.parametrize(
    "case_input",
    [
        "実写映像。0～10秒は女性が歩く。",
        "10秒で横からのカメラへカット変更する。",
    ],
)
def test_h3_explicit_style_or_cut_is_preserved_by_final_guardrail(tmp_path, case_input):
    messages = _assembled_h3_system_with_example_skill(
        tmp_path,
        case_input,
        PromptSettings(processing="Faithful"),
    )
    final = messages[0]["content"][
        messages[0]["content"].index("FINAL INTENT-PRESERVATION OVERRIDE") :
    ]
    assert "Preserve every explicitly requested medium/style" in final
    assert messages[1] == {"role": "user", "content": case_input}

def test_editor_runs_exactly_one_initial_translation_while_auto_remains_off():
    app = QApplication.instance() or QApplication([])
    dialog = PromptTranslationDialog(
        _tr(),
        "Synthetic original.",
        debounce_ms=50,
    )
    emitted = []
    dialog.translation_requested.connect(
        lambda revision, direction, text, protection: emitted.append(
            (revision, direction, text, protection)
        )
    )
    assert not dialog.auto_translate.isChecked()

    dialog.show()
    app.processEvents()
    assert len(emitted) == 1
    initial_revision = emitted[0][0]
    assert emitted[0][1:3] == (
        SOURCE_TO_UI_LOCALE,
        "Synthetic original.",
    )
    assert dialog.apply_translation_result(
        initial_revision,
        SOURCE_TO_UI_LOCALE,
        "架空の初回翻訳。",
    )
    assert not dialog.auto_translate.isChecked()

    dialog.hide()
    dialog.show()
    QTest.qWait(80)
    app.processEvents()
    assert len(emitted) == 1

    dialog.original_edit.setPlainText("Edited while auto is off.")
    QTest.qWait(80)
    app.processEvents()
    assert len(emitted) == 1
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize(
    "alignment",
    [
        (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        ),
        (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            "Picture 2 (from Shot 3) aligns with the 15.00-second mark of the target video."
        ),
        (
            "How the reference pictures align with the target video — "
            "<Picture 1> (from [Shot 2]) aligns with the 10.00-second mark of the target video."
        ),
    ],
)
def test_skill_locked_alignment_templates_are_single_protected_spans(alignment):
    spans = protected_spans(alignment)
    assert len(spans) == 1
    assert spans[0].text == alignment

    service = PromptTranslationService()
    request = service.request_payload(alignment, SOURCE_TO_UI_LOCALE)
    masked = request.payload["messages"][-1]["content"]
    assert alignment not in masked
    assert masked == "__LPS_STRUCTURE_0000__"
    assert service.finalize_response(masked, request) == alignment


def test_t2va_has_no_alignment_prelude_and_descriptive_medium_is_not_syntax():
    prompt = (
        "integrated_multimodal_description: "
        "[Shot 1] Live-action, cinematic, a woman walks.\n\n"
        "overall_soundscape: Footsteps.\n\n"
        "non_diegetic_music: None."
    )
    spans = protected_spans(prompt)
    protected = {span.text for span in spans}

    assert "integrated_multimodal_description:" in protected
    assert "overall_soundscape:" in protected
    assert "non_diegetic_music:" in protected
    assert "[Shot 1]" in protected
    assert "Live-action" not in protected
    assert "cinematic" not in protected
    assert not any(
        span.text.startswith("For the target video")
        or span.text.startswith("How the reference pictures align")
        for span in spans
    )


@pytest.mark.parametrize(
    "case_input",
    [
        "2D anime illustration. A woman walks through a city.",
        "Live-action photorealistic footage. A woman walks through a city.",
        "A live-action person moves through a 2D illustrated background.",
    ],
)
def test_h3_explicit_visual_medium_cannot_be_reversed_or_collapsed(
    tmp_path,
    case_input,
):
    messages = _assembled_h3_system_with_example_skill(
        tmp_path,
        case_input,
        PromptSettings(processing="Faithful"),
    )
    final = messages[0]["content"][
        messages[0]["content"].index("FINAL INTENT-PRESERVATION OVERRIDE") :
    ]
    assert "Never reverse or convert an explicitly requested visual medium" in final
    assert "never turn it into live-action or photorealistic content" in final
    assert "never turn it into 2D, anime, or illustration" in final
    assert "preserve that exact combination" in final
    assert messages[1] == {"role": "user", "content": case_input}

def test_h3_explicit_temporal_intervals_are_preserved_without_fixed_notation(
    tmp_path,
):
    request = "0～10秒は歩く、10～15秒は走る。"
    messages = _assembled_h3_system_with_example_skill(
        tmp_path,
        request,
        PromptSettings(processing="Faithful"),
    )
    system = messages[0]["content"]
    final = system[system.index("FINAL INTENT-PRESERVATION OVERRIDE") :]

    assert "every explicitly stated temporal interval as meaning" in final
    assert "each start time, end time, and interval-to-action association" in final
    assert "never omit, merge, or replace one interval with another" in final
    assert "current Skill/Profile determine the syntax or natural-language notation" in final
    for fixed_notation in (
        "From 00:00.000 to 00:10.000",
        "At MM:SS.mmm",
        "00:00.000-00:10.000",
    ):
        assert fixed_notation not in final
    assert messages[1] == {"role": "user", "content": request}
