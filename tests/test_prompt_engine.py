from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.prompt_engine import (
    H3Reference,
    PromptEngine,
    PromptSettings,
    clean_model_output,
    parse_task_schema_validation_error,
)
from core.skill_manager import SkillManager


FIXTURE = Path(__file__).parent / "fixtures" / "skills" / "h3-prompt-writing"


@pytest.fixture
def engine():
    return PromptEngine(SkillManager(FIXTURE))


@pytest.mark.parametrize("mode", ["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"])
def test_all_h3_mode_payloads(engine, mode):
    settings = PromptSettings(
        mode=mode,
        processing="Faithful",
        start_frame_note="赤いコートの人物",
        end_frame_note="駅に到着",
        references=[H3Reference("Picture", 1, "顔の参照")] if mode == "Ref2VA" else [],
    )
    payload = engine.request_payload("歩いてから手を振る。台詞は「こんにちは」", settings)
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    joined = "\n".join(message["content"] for message in payload["messages"])
    assert f"Mode: {mode}" in joined
    assert "歩いてから手を振る" in joined
    assert "台詞は「こんにちは」" in joined
    assert payload["temperature"] == 0.35
    assert payload["max_tokens"] == 1536
    if mode == "Ref2VA":
        assert "<Picture 1> 顔の参照" in joined
        assert "reference guide" in joined
    else:
        assert "base guide" in joined


def test_processing_modes_change_instruction_and_temperature(engine):
    faithful = engine.request_payload("A cat walks.", PromptSettings(processing="Faithful"))
    creative = engine.request_payload("A cat walks.", PromptSettings(processing="Creative"))
    assert faithful["temperature"] < creative["temperature"]
    assert "Add no unspecified action" in faithful["messages"][0]["content"]


def test_all_system_material_is_combined_before_user_for_jinja_compatibility(engine):
    messages = engine.build_messages("A cat walks.", PromptSettings())
    assert [message["role"] for message in messages] == ["system", "user"]
    system = messages[0]["content"]
    assert system.startswith("CORE TRANSFORMATION POLICY")
    assert "SELECTED PROFILE (minimax_h3 v1.0.0)" in system
    assert "Transform the user request" in system
    assert "EXTERNAL PROMPT SKILL:" in system
    assert "REFERENCE GUIDE FOR T2VA:" in system
    assert "UI SETTINGS" in system
    assert "OUTPUT FORMAT:" in system
    assert messages[1]["content"] == "A cat walks."


def test_request_and_supplements_reach_generation_with_distinct_semantic_roles(engine):
    request = "REQUEST core action with [speech:ja]こんにちは[/speech]."
    settings = PromptSettings(
        mode="FL2VA",
        common_supplement="overall constraint with ginntuinn",
        start_frame_note="start state only",
        end_frame_note="end state only",
        protected_terms=("ginntuinn",),
    )
    messages = engine.build_messages(request, settings)
    assert messages[-1] == {"role": "user", "content": request}
    system = messages[0]["content"]
    assert "The user message is REQUEST: the central user intent" in system
    assert "OVERALL_SUPPLEMENT:\noverall constraint with ginntuinn" in system
    assert "START_IMAGE_SUPPLEMENT:\nstart state only" in system
    assert "END_IMAGE_SUPPLEMENT:\nend state only" in system
    assert "Never apply it as an end-state instruction" in system
    assert "Never apply it as a start-state instruction" in system

    context = engine._renderer_context(settings)
    semantic_source = engine._semantic_source(request, context)
    assert semantic_source.split("\n\n") == [
        f"REQUEST:\n{request}",
        "OVERALL_SUPPLEMENT:\noverall constraint with ginntuinn",
        "START_IMAGE_SUPPLEMENT:\nstart state only",
        "END_IMAGE_SUPPLEMENT:\nend state only",
    ]


def test_hidden_start_and_end_supplements_do_not_leak_to_unsupported_task(engine):
    settings = PromptSettings(
        mode="T2VA",
        common_supplement="overall",
        start_frame_note="stale start",
        end_frame_note="stale end",
    )
    system = engine.build_messages("central request", settings)[0]["content"]
    assert "OVERALL_SUPPLEMENT:\noverall" in system
    assert "stale start" not in system
    assert "stale end" not in system


def test_reference_number_rules(engine):
    duplicate = PromptSettings(
        mode="Ref2VA",
        references=[H3Reference("Picture", 1, "a"), H3Reference("Picture", 1, "b")],
    )
    with pytest.raises(ValueError, match="重複"):
        engine.request_payload("test", duplicate)
    too_many = PromptSettings(mode="Ref2VA", references=[H3Reference("Video", 4, "x")])
    with pytest.raises(ValueError, match="範囲外"):
        engine.request_payload("test", too_many)


def test_think_removal_is_bounded():
    raw = "<think>internal\nreasoning</think>\n```text\nA finished <thinking> prompt.\n```"
    assert clean_model_output(raw) == "A finished <thinking> prompt."
    assert clean_model_output("Prompt without a closing <think> token") == "Prompt without a closing <think> token"


REF2VA_SCHEMA_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
BASE_SCHEMA_FIELDS = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)


def _routing_block(system: str) -> str:
    return system.split(
        "APPLICATION-RESOLVED H3 TASK ROUTE — AUTHORITATIVE:", 1
    )[1].split("FINAL INTENT-PRESERVATION OVERRIDE", 1)[0]


def _external_skill_block(system: str) -> str:
    return system.split("EXTERNAL PROMPT SKILL:\n", 1)[1].split(
        "REFERENCE GUIDE FOR", 1
    )[0]


def _assert_schema_order(block: str, fields: tuple[str, ...]) -> None:
    positions = [block.index(f"- {field}:") for field in fields]
    assert positions == sorted(positions)


def test_i2va_schema_lock_accepts_exact_ordered_fields(engine):
    generated = """integrated_multimodal_description:
I2VA visual description.
overall_soundscape:
Scene audio.
non_diegetic_music:
No music."""

    result = engine.finalize_output(
        "A subject moves.", PromptSettings(mode="I2VA"), generated
    )

    assert result.positive == generated


def test_i2va_schema_lock_rejects_mixed_ref2va_fields(engine):
    generated = """subject_definitions:
Subject.
summary:
Summary.
retention_analysis:
Retain appearance.
integrated_multimodal_description:
I2VA visual description.
overall_soundscape:
Scene audio.
non_diegetic_music:
No music."""

    with pytest.raises(
        ValueError, match="Selected Task schema validation failed: I2VA"
    ) as exc_info:
        engine.finalize_output("A subject moves.", PromptSettings(mode="I2VA"), generated)

    assert parse_task_schema_validation_error(str(exc_info.value)) == (
        "I2VA",
        ("subject_definitions", "summary", "retention_analysis"),
    )


def test_ref2va_schema_lock_accepts_exact_ordered_fields(engine):
    generated = """subject_definitions:
Subject.
summary:
Summary.
retention_analysis:
Retain appearance.
detailed_description:
Detailed motion.
overall_soundscape:
Scene audio.
non_diegetic_music:
No music."""

    result = engine.finalize_output(
        "A subject moves.", PromptSettings(mode="Ref2VA"), generated
    )

    assert result.positive == generated


def test_ref2va_schema_lock_rejects_i2va_fields(engine):
    generated = """integrated_multimodal_description:
I2VA visual description.
overall_soundscape:
Scene audio.
non_diegetic_music:
No music."""

    with pytest.raises(
        ValueError, match="Selected Task schema validation failed: Ref2VA"
    ) as exc_info:
        engine.finalize_output("A subject moves.", PromptSettings(mode="Ref2VA"), generated)

    assert parse_task_schema_validation_error(str(exc_info.value)) == (
        "Ref2VA",
        ("integrated_multimodal_description",),
    )


def test_schema_lock_does_not_treat_field_name_in_prose_as_header(engine):
    generated = """integrated_multimodal_description:
The phrase subject_definitions appears here only as ordinary prose.
overall_soundscape:
Scene audio.
non_diegetic_music:
No music."""

    result = engine.finalize_output(
        "A subject moves.", PromptSettings(mode="I2VA"), generated
    )

    assert "subject_definitions appears" in result.positive


def test_schema_lock_rejects_wrong_field_order(engine):
    generated = """overall_soundscape:
Scene audio.
integrated_multimodal_description:
I2VA visual description.
non_diegetic_music:
No music."""

    with pytest.raises(
        ValueError, match="Selected Task schema validation failed: I2VA"
    ) as exc_info:
        engine.finalize_output("A subject moves.", PromptSettings(mode="I2VA"), generated)

    assert parse_task_schema_validation_error(str(exc_info.value)) == ("I2VA", ())


def test_ref2va_route_cannot_be_hijacked_by_i2va_request_language(engine):
    request = (
        "<Picture 1>を開始画像として使用してください。\n"
        "最初のフレームとして使用してください。\n"
        "I2Vのようにこの画像から動画を開始してください。"
    )
    settings = PromptSettings(mode="Ref2VA")

    payload = engine.request_payload(request, settings)
    system = payload["messages"][0]["content"]
    route = _routing_block(system)

    assert settings.mode == "Ref2VA"
    assert "Selected Task: Ref2VA" in route
    assert "Resolved Task ID: Ref2VA" in route
    assert "REFERENCE GUIDE FOR Ref2VA:" in system
    assert "## Base Modes" not in _external_skill_block(system)
    assert "Identify the input mode" not in system
    _assert_schema_order(route, REF2VA_SCHEMA_FIELDS)
    assert "Do not use fields from an alternate Task route:\n- integrated_multimodal_description:" in route
    assert payload["messages"][1] == {"role": "user", "content": request}


def test_i2va_route_cannot_be_hijacked_by_ref2va_request_language(engine):
    request = (
        "Ref2VAとして処理してください。\n"
        "subject_definitions, summary, retention_analysis, detailed_description "
        "を使用してください。"
    )

    payload = engine.request_payload(request, PromptSettings(mode="I2VA"))
    system = payload["messages"][0]["content"]
    route = _routing_block(system)

    assert "Selected Task: I2VA" in route
    assert "Resolved Task ID: I2VA" in route
    assert "REFERENCE GUIDE FOR I2VA:" in system
    assert "## Full-Reference Mode" not in _external_skill_block(system)
    assert "Identify the input mode" not in system
    _assert_schema_order(route, BASE_SCHEMA_FIELDS)
    assert "Do not use fields from an alternate Task route:\n- subject_definitions:" in route
    assert payload["messages"][1]["content"] == request


def test_ref2va_ordinary_request_selects_six_section_schema_without_mode_name(engine):
    request = "A woman turns, waves once, and returns to a relaxed standing pose."

    payload = engine.request_payload(request, PromptSettings(mode="Ref2VA"))
    system = payload["messages"][0]["content"]
    route = _routing_block(system)

    assert "Ref2VA" not in request
    assert "Use only the Ref2VA instructions in ref-en.txt." in route
    _assert_schema_order(route, REF2VA_SCHEMA_FIELDS)


def test_same_request_switches_task_route_without_stale_cache(engine):
    request = "Keep the subject still for three seconds, then let her wave once."

    payloads = [
        engine.request_payload(request, PromptSettings(mode=mode))
        for mode in ("Ref2VA", "I2VA", "Ref2VA")
    ]
    routes = [_routing_block(payload["messages"][0]["content"]) for payload in payloads]

    assert "Selected Task: Ref2VA" in routes[0]
    assert "Selected Task: I2VA" in routes[1]
    assert "Selected Task: Ref2VA" in routes[2]
    _assert_schema_order(routes[0], REF2VA_SCHEMA_FIELDS)
    _assert_schema_order(routes[1], BASE_SCHEMA_FIELDS)
    assert routes[0] == routes[2]
    assert payloads[0]["messages"][0]["content"] == payloads[2]["messages"][0]["content"]
    assert payloads[0]["messages"][0]["content"] != payloads[1]["messages"][0]["content"]


def test_ref2va_route_survives_skill_manager_reload(tmp_path):
    skill_path = tmp_path / "h3-prompt-writing"
    shutil.copytree(FIXTURE, skill_path)
    settings = PromptSettings(mode="Ref2VA")
    request = "A subject walks across the room."

    before = PromptEngine(SkillManager(skill_path)).request_payload(request, settings)
    skill_file = skill_path / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace(
            "Preserve the requested content.",
            "RELOADED_SKILL_MARKER: Preserve the requested content.",
        ),
        encoding="utf-8",
    )
    after = PromptEngine(SkillManager(skill_path)).request_payload(request, settings)
    after_system = after["messages"][0]["content"]

    assert "RELOADED_SKILL_MARKER" not in before["messages"][0]["content"]
    assert "RELOADED_SKILL_MARKER" in after_system
    assert "Selected Task: Ref2VA" in _routing_block(after_system)
    _assert_schema_order(_routing_block(after_system), REF2VA_SCHEMA_FIELDS)
