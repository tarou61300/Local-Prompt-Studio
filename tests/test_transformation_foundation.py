from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.literal_content import parse_literal_content
from core.profile_loader import ProfileLoader
from core.profile_models import PromptComponents
from core.prompt_engine import H3Reference, PromptEngine, PromptSettings
from core.protected_terms import normalize_protected_terms
from core.renderers import (
    LITERAL_CONTENT_NOT_PRESERVED,
    PROTECTED_TERM_NOT_PRESERVED,
    NaturalLanguageRenderer,
    TransformationError,
    VideoNarrativeRenderer,
)
from core.skill_manager import SkillManager


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def _profile():
    return ProfileLoader(ROOT / "profiles", ROOT / ".tmp-unused").discover().profiles[
        "minimax_h3"
    ]


def _named_profile(profile_id: str):
    return ProfileLoader(ROOT / "profiles", ROOT / ".tmp-unused").discover().profiles[
        profile_id
    ]


def test_literal_parser_preserves_japanese_english_and_multiple_values():
    value = "[speech:ja] おかえりなさい。\n[speech:en] Welcome home.\n[text:ja] 月夜珈琲\n[text:en] OPEN"
    literals = parse_literal_content(value)
    assert [item.text for item in literals] == [
        "おかえりなさい。",
        "Welcome home.",
        "月夜珈琲",
        "OPEN",
    ]


def test_literal_syntax_is_only_recognized_at_line_start():
    assert parse_literal_content("Describe [speech:ja] こんにちは") == ()
    assert parse_literal_content("  [speech:ja] こんにちは")[0].text == "こんにちは"


def test_h3_profile_policy_and_skill_are_in_same_request():
    engine = PromptEngine(SkillManager(SKILL), _profile(), "base")
    request = "[speech:ja] おかえりなさい。\nA person enters."
    payload = engine.request_payload(request, PromptSettings(processing="Faithful"))
    system = payload["messages"][0]["content"]
    assert "CORE TRANSFORMATION POLICY" in system
    assert "SELECTED PROFILE (minimax_h3 v1.0.0)" in system
    assert "EXTERNAL PROMPT SKILL" in system
    assert "おかえりなさい。" in system
    assert payload["messages"][1]["content"] == request


def test_literal_and_protected_term_exact_validation(caplog):
    engine = PromptEngine(SkillManager(SKILL), _profile(), "base")
    settings = PromptSettings(protected_terms=("ginntuinn", "<lora:character:1.0>"))
    request = "[text:ja] 月夜珈琲"
    result = engine.finalize_output(
        request,
        settings,
        "月夜珈琲 sign, ginntuinn, <lora:character:1.0>",
    )
    assert "月夜珈琲" in result.positive
    with pytest.raises(TransformationError) as literal_error:
        engine.finalize_output(request, settings, "translated sign, ginntuinn, <lora:character:1.0>")
    assert literal_error.value.code == LITERAL_CONTENT_NOT_PRESERVED
    with pytest.raises(TransformationError) as protected_error:
        engine.finalize_output(request, settings, "月夜珈琲 sign, gin tu inn")
    assert protected_error.value.code == PROTECTED_TERM_NOT_PRESERVED
    assert "月夜珈琲" not in caplog.text
    assert "ginntuinn" not in caplog.text


def test_length_guidance_is_warning_only_and_never_changes_text():
    profile = _profile()
    variant = profile.variant()
    guidance = replace(
        variant.length_guidance,
        unit="words",
        recommended_minimum=2,
        recommended_maximum=3,
    )
    renderer = VideoNarrativeRenderer()
    short_variant = replace(variant, length_guidance=guidance)
    long_text = "one two three four explicit details"
    result = renderer.render(long_text, short_variant, (), ())
    assert result.positive == long_text
    assert result.warnings == ("PROMPT_LONGER_THAN_RECOMMENDED",)
    in_range = renderer.render("one two three", short_variant, (), ())
    assert in_range.warnings == ()

    hard_guidance = replace(guidance, hard_maximum=4)
    hard_variant = replace(variant, length_guidance=hard_guidance)
    hard_result = renderer.render(long_text, hard_variant, (), ())
    assert hard_result.positive == long_text
    assert hard_result.warnings == ("PROMPT_EXCEEDS_HARD_MAXIMUM",)


def test_required_and_recommended_components_are_assembled_deterministically():
    variant = _profile().variant()
    variant = replace(
        variant,
        required_prompt=PromptComponents(
            positive_prefix=("required-start",),
            positive_suffix=("required-end",),
            negative_prefix=("required-negative",),
        ),
        recommended_prompt=PromptComponents(
            positive_prefix=("recommended-start",),
            positive_suffix=("recommended-end",),
            negative_suffix=("recommended-negative",),
        ),
    )
    result = VideoNarrativeRenderer().render("generated", variant, (), ())
    assert result.positive == (
        "required-start recommended-start generated recommended-end required-end"
    )
    assert result.negative == "required-negative recommended-negative"


def test_second_profile_can_use_core_engine_without_h3_dependency_or_model_branch():
    profile = _profile()
    generic_manifest = replace(
        profile.manifest,
        id="second_video",
        external_dependencies=(),
    )
    generic_profile = replace(profile, manifest=generic_manifest)
    engine = PromptEngine(None, generic_profile, "base")

    payload = engine.request_payload("A test.", PromptSettings())

    system = payload["messages"][0]["content"]
    assert "SELECTED PROFILE (second_video v1.0.0)" in system
    assert "EXTERNAL PROMPT SKILL" not in system


@pytest.mark.parametrize("profile_id", ["wan_2_2", "ltx_2_3"])
def test_supplied_non_h3_profiles_work_without_skill_or_legacy_h3_controls(profile_id):
    profile = _named_profile(profile_id)
    engine = PromptEngine(None, profile)
    settings = PromptSettings(
        mode="T2V",
        duration=999,
        camera="unused-camera",
        shot="unused-shot",
        motion="unused-motion",
        start_frame_note="unused-start",
        end_frame_note="unused-end",
        references=[H3Reference("Unsupported", 99, "unused-reference")],
    )

    payload = engine.request_payload("A subject moves.", settings)
    system = payload["messages"][0]["content"]

    assert f"SELECTED PROFILE ({profile_id} v1.0.0)" in system
    assert "Task: T2V" in system
    assert "Prompt Processing: Faithful" in system
    assert "EXTERNAL PROMPT SKILL" not in system
    assert "Duration:" not in system
    assert "Camera:" not in system
    assert "Shot:" not in system
    assert "Motion:" not in system
    assert "Audio:" not in system
    assert "unused-start" not in system
    assert "unused-reference" not in system


def test_h3_profile_without_skill_fails_dependency_closed():
    engine = PromptEngine(None, _named_profile("minimax_h3"), "base")

    with pytest.raises(ValueError, match="PROFILE_EXTERNAL_DEPENDENCY_MISSING"):
        engine.request_payload("A test.", PromptSettings())


def test_h3_legacy_duration_validation_remains_enabled():
    engine = PromptEngine(SkillManager(SKILL), _named_profile("minimax_h3"), "base")

    with pytest.raises(ValueError, match="Duration"):
        engine.request_payload("A test.", PromptSettings(duration=999))


def test_ltx_200_word_guidance_warns_without_mutating_output():
    engine = PromptEngine(None, _named_profile("ltx_2_3"), "distilled_1_1")
    generated = " ".join(f"word{index}" for index in range(201))

    result = engine.finalize_output("A test.", PromptSettings(mode="T2V"), generated)

    assert result.positive == generated
    assert result.warnings == ("PROMPT_LONGER_THAN_RECOMMENDED",)


def test_krea_2_uses_natural_language_renderer_without_h3_skill_or_controls():
    profile = _named_profile("krea_2")
    engine = PromptEngine(None, profile, "turbo")
    settings = PromptSettings(
        mode="T2I",
        duration=999,
        camera="unused-camera",
        shot="unused-shot",
        motion="unused-motion",
        start_frame_note="unused-start",
        end_frame_note="unused-end",
        references=[H3Reference("Unsupported", 99, "unused-reference")],
    )

    payload = engine.request_payload("A red fox in snow.", settings)
    system = payload["messages"][0]["content"]

    assert "SELECTED PROFILE (krea_2 v1.0.0)" in system
    assert "Task: T2I" in system
    assert "EXTERNAL PROMPT SKILL" not in system
    assert "Duration:" not in system
    assert "Camera:" not in system
    assert "Audio:" not in system
    assert "unused-reference" not in system

    result = engine.finalize_output(
        "[text:ja] 月夜珈琲",
        PromptSettings(mode="T2I"),
        'A small storefront sign reading "月夜珈琲" at night.',
    )
    assert result.positive == 'A small storefront sign reading "月夜珈琲" at night.'
    assert result.negative is None
    assert result.warnings == ()


def test_natural_language_renderer_preserves_fixed_component_contract():
    variant = _named_profile("krea_2").variant("turbo")
    variant = replace(
        variant,
        required_prompt=PromptComponents(
            positive_prefix=("required-start",),
            positive_suffix=("required-end",),
        ),
        recommended_prompt=PromptComponents(
            positive_prefix=("recommended-start",),
            positive_suffix=("recommended-end",),
        ),
    )
    result = NaturalLanguageRenderer().render("generated", variant, (), ())
    assert result.positive == (
        "required-start recommended-start generated recommended-end required-end"
    )
    assert result.negative is None


def test_protected_term_normalization_never_splits_or_translates():
    terms = normalize_protected_terms(["ginntuinn", "<lora:character:1.0>"])
    assert [term.text for term in terms] == ["ginntuinn", "<lora:character:1.0>"]


@pytest.mark.parametrize("mode", ["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"])
@pytest.mark.parametrize("processing", ["Faithful", "Balanced", "Creative"])
def test_all_existing_h3_modes_and_processing_modes_remain_supported(mode, processing):
    engine = PromptEngine(SkillManager(SKILL), _profile(), "base")
    payload = engine.request_payload("A test.", PromptSettings(mode=mode, processing=processing))
    assert f"Mode: {mode}" in payload["messages"][0]["content"]
