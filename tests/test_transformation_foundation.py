from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from core.literal_content import parse_literal_content
from core.profile_loader import ProfileLoader
from core.profile_models import PromptComponents
from core.prompt_engine import H3Reference, PromptEngine, PromptSettings, clean_model_output
from core.protected_terms import normalize_protected_terms
from core.renderers import (
    DANBOORU_OUTPUT_INVALID,
    LITERAL_CONTENT_NOT_PRESERVED,
    PROTECTED_TERM_NOT_PRESERVED,
    DanbooruTagsRenderer,
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


def test_anima_renderer_assembles_order_normalization_and_negative_deterministically():
    variant = _named_profile("anima").variant("turbo_v1_0")
    generated = """{
      "quality_meta_year_safety": ["YEAR_2025", "explicit"],
      "subject_count": ["1GIRL"],
      "character": ["Fern_(Sousou_no_Frieren)"],
      "series": ["Sousou_no_Frieren"],
      "artist": ["Some_Artist"],
      "general": ["SILVER_HAIR", "blue_eyes", "score_7", "(CHIBI:2)"],
      "negative": ["bad_hands", "blurry"]
    }"""
    result = DanbooruTagsRenderer().render(generated, variant, (), ())

    assert result.positive == (
        "masterpiece, best quality, score_7, year 2025, explicit, 1girl, "
        "fern (sousou no frieren), sousou no frieren, @some artist, "
        "silver hair, blue eyes, (chibi:2)"
    )
    assert result.negative == (
        "worst quality, low quality, score_1, score_2, score_3, artist name, "
        "blurry, jpeg artifacts, chromatic aberration, bad hands"
    )
    assert result.warnings == ()


def test_anima_renderer_preserves_literal_and_protected_terms_without_normalizing():
    request = "[text:ja] 月夜珈琲"
    literals = parse_literal_content(request)
    protected = normalize_protected_terms(["My_Custom_LoRA"])
    variant = _named_profile("anima").variant("base_v1_0")
    generated = """{
      "general": ["月夜珈琲", "My_Custom_LoRA", "SILVER_HAIR"],
      "negative": []
    }"""

    result = DanbooruTagsRenderer().render(
        generated,
        variant,
        literals,
        protected,
    )

    assert "月夜珈琲" in result.positive
    assert "My_Custom_LoRA" in result.positive
    assert "silver hair" in result.positive


def test_anima_renderer_rejects_invalid_structured_output():
    variant = _named_profile("anima").variant("turbo_v1_0")

    with pytest.raises(TransformationError) as exc:
        DanbooruTagsRenderer().render("1girl, silver hair", variant, (), ())

    assert exc.value.code == DANBOORU_OUTPUT_INVALID


def test_anima_prompt_engine_requests_renderer_specific_json_contract():
    engine = PromptEngine(None, _named_profile("anima"), "turbo_v1_0")
    payload = engine.request_payload(
        "銀髪の女性。青い目。",
        PromptSettings(mode="T2I", processing="Faithful"),
    )
    system = payload["messages"][0]["content"]

    assert "SELECTED PROFILE (anima v1.0.0)" in system
    assert '"quality_meta_year_safety"' in system
    assert '"subject_count"' in system
    assert '"negative"' in system
    assert "valid JSON object" in system
    assert payload["response_format"] == {"type": "json_object"}
    assert "EXTERNAL PROMPT SKILL" not in system



def test_anima_renderer_accepts_json_code_fence_and_harmless_wrapper_text():
    variant = _named_profile("anima").variant("turbo_v1_0")
    fenced = """```json
{
  "subject_count": ["1girl"],
  "general": ["silver_hair", "blue_eyes"],
  "negative": []
}
```"""
    fenced_result = DanbooruTagsRenderer().render(clean_model_output(fenced), variant, (), ())
    assert "1girl" in fenced_result.positive
    assert "silver hair" in fenced_result.positive

    wrapped = """Here is the requested JSON object:
{
  "subject_count": ["1girl"],
  "general": ["long_hair", "school_uniform"],
  "negative": []
}"""
    wrapped_result = DanbooruTagsRenderer().render(wrapped, variant, (), ())
    assert "long hair" in wrapped_result.positive
    assert "school uniform" in wrapped_result.positive

def test_anima_aesthetic_omits_score_tags_from_fixed_recommendations():
    profile = _named_profile("anima")
    variant = profile.variant("aesthetic_v1_1")
    generated = """{
      "subject_count": ["1girl"],
      "general": ["solo"],
      "negative": []
    }"""
    result = DanbooruTagsRenderer().render(generated, variant, (), ())

    assert result.positive == "masterpiece, best quality, safe, 1girl, solo"
    assert "score_" not in result.positive
    assert "score_" not in (result.negative or "")


def test_anima_explicit_safety_tag_replaces_conflicting_default_safe():
    variant = _named_profile("anima").variant("turbo_v1_0")
    generated = """{
      "quality_meta_year_safety": ["explicit"],
      "subject_count": ["1girl"],
      "general": ["solo"],
      "negative": []
    }"""

    result = DanbooruTagsRenderer().render(generated, variant, (), ())

    assert "explicit" in result.positive
    assert ", safe," not in f", {result.positive},"


def test_protected_term_normalization_never_splits_or_translates():
    terms = normalize_protected_terms(["ginntuinn", "<lora:character:1.0>"])
    assert [term.text for term in terms] == ["ginntuinn", "<lora:character:1.0>"]


@pytest.mark.parametrize("mode", ["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"])
@pytest.mark.parametrize("processing", ["Faithful", "Balanced", "Creative"])
def test_all_existing_h3_modes_and_processing_modes_remain_supported(mode, processing):
    engine = PromptEngine(SkillManager(SKILL), _profile(), "base")
    payload = engine.request_payload("A test.", PromptSettings(mode=mode, processing=processing))
    assert f"Mode: {mode}" in payload["messages"][0]["content"]
