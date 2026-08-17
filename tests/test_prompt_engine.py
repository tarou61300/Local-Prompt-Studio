from __future__ import annotations

from pathlib import Path

import pytest

from core.prompt_engine import H3Reference, PromptEngine, PromptSettings, clean_model_output
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
