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
    assert system.startswith("You transform a user's request")
    assert "OFFICIAL H3 SKILL:" in system
    assert "REFERENCE GUIDE FOR T2VA:" in system
    assert "UI SETTINGS" in system
    assert "OUTPUT FORMAT:" in system
    assert messages[1]["content"] == "A cat walks."


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
