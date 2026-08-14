from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.literal_content import parse_literal_content, remove_literal_markers
from core.profile_loader import ProfileLoader
from core.prompt_engine import PromptEngine, PromptSettings
from core.protected_terms import normalize_protected_terms
from core.renderers import (
    ANIMA_HYBRID_OUTPUT_INVALID,
    AnimaRenderer,
    Krea2Renderer,
    LTX23Renderer,
    MiniMaxH3Renderer,
    RendererContext,
    RendererRegistry,
    TransformationError,
    Wan22Renderer,
)
from core.skill_manager import SkillManager


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def _profiles():
    return ProfileLoader(ROOT / "profiles", ROOT / ".tmp-unused").discover().profiles


def _engine(profile_id: str, variant_id: str | None = None) -> PromptEngine:
    profile = _profiles()[profile_id]
    skill = SkillManager(SKILL) if profile_id == "minimax_h3" else None
    return PromptEngine(skill, profile, variant_id)


def test_registry_and_profiles_select_exactly_five_model_renderers():
    registry = RendererRegistry()
    expected = {
        "minimax_h3": MiniMaxH3Renderer,
        "wan_2_2": Wan22Renderer,
        "ltx_2_3": LTX23Renderer,
        "krea_2": Krea2Renderer,
        "anima": AnimaRenderer,
    }
    assert registry.ids == frozenset(expected)
    for profile_id, renderer_type in expected.items():
        profile = _profiles()[profile_id]
        assert profile.manifest.renderer == profile_id
        assert isinstance(registry.get(profile.manifest.renderer), renderer_type)


@pytest.mark.parametrize(
    ("profile_id", "task", "generated"),
    [
        ("minimax_h3", "T2VA", "A woman walks through rain."),
        ("wan_2_2", "T2V", "A woman walks through rain."),
        ("ltx_2_3", "T2V", "A woman walks through rain as footsteps echo."),
        ("krea_2", "T2I", "A natural-language illustration of a rainy street."),
    ],
)
def test_four_single_positive_renderers_never_create_negative(
    profile_id: str, task: str, generated: str
):
    result = _engine(profile_id).finalize_output(
        "A woman in rain.", PromptSettings(mode=task), generated
    )
    assert result.positive == generated
    assert result.negative is None


def test_krea_renderer_stays_natural_language_for_all_processing_modes():
    engine = _engine("krea_2", "turbo")
    for processing in ("Faithful", "Balanced", "Creative"):
        system = engine.request_payload(
            "A red fox in snow.",
            PromptSettings(mode="T2I", processing=processing),
        )["messages"][0]["content"]
        assert "KREA 2 RENDERER POLICY" in system
        assert "natural-language image prompt" in system
        assert "never Danbooru tags" in system
        assert f"Prompt Processing: {processing}" in system
        assert "response_format" not in engine.request_payload(
            "A red fox in snow.",
            PromptSettings(mode="T2I", processing=processing),
        )


@pytest.mark.parametrize(
    ("source_text", "expected"),
    [
        ("An anime girl walks under moonlight.", "natural"),
        ("1girl, silver_hair, blue_eyes", "tag"),
        ("masterpiece, 1girl. A girl walks under moonlight.", "hybrid"),
        (
            "ginntuinn, 1girl, silver hair. A young woman is sitting beside a café window, "
            "wearing a white dress and smiling gently at the viewer.",
            "hybrid",
        ),
    ],
)
def test_anima_detects_natural_tag_and_hybrid(source_text: str, expected: str):
    renderer = AnimaRenderer()
    assert renderer.input_mode(source_text) == expected
    analysis = renderer.analyze_request(source_text)
    assert analysis.input_mode == expected


def test_anima_natural_output_remains_prose_with_separate_negative():
    engine = _engine("anima", "base_v1_0")
    request = "An anime girl walks under moonlight."
    payload = engine.request_payload(request, PromptSettings(mode="T2I"))
    assert "Detected input mode: NATURAL" in payload["messages"][0]["content"]
    assert payload["response_format"] == {"type": "json_object"}
    result = engine.finalize_output(
        request,
        PromptSettings(mode="T2I"),
        json.dumps(
            {
                "positive": "An anime girl walks beneath moonlight. Her silver hair moves in the breeze.",
                "negative": "extra people",
            }
        ),
    )
    assert "An anime girl walks beneath moonlight." in result.positive
    assert "silver hair" in result.positive
    assert "worst quality" in (result.negative or "")
    assert "extra people" in (result.negative or "")


def test_anima_tag_output_preserves_tag_structure_and_variant_rules():
    engine = _engine("anima", "turbo_v1_0")
    request = "1girl, silver_hair, explicit"
    payload = engine.request_payload(request, PromptSettings(mode="T2I"))
    assert payload["response_format"] == {"type": "json_object"}
    result = engine.finalize_output(
        request,
        PromptSettings(mode="T2I"),
        json.dumps(
            {
                "quality_meta_year_safety": ["explicit"],
                "subject_count": ["1girl"],
                "general": ["silver_hair"],
                "negative": ["extra_people"],
            }
        ),
    )
    assert "score_7" in result.positive
    assert "silver hair" in result.positive
    assert "explicit" in result.positive
    assert "safe" not in result.positive.split(", ")
    assert "extra people" in (result.negative or "")


def test_anima_hybrid_output_preserves_trigger_tags_and_natural_prose():
    engine = _engine("anima", "turbo_v1_0")
    request = (
        "ginntuinn, 1girl, silver hair. A young woman is sitting beside a café window, "
        "wearing a white dress and smiling gently at the viewer."
    )
    settings = PromptSettings(mode="T2I", protected_terms=("ginntuinn",))
    payload = engine.request_payload(request, settings)
    assert "Detected input mode: HYBRID" in payload["messages"][0]["content"]
    assert "response_format" not in payload
    assert "Return plain text, not JSON" in payload["messages"][0]["content"]
    result = engine.finalize_output(
        request,
        settings,
        (
            "ANIMA_NATURAL:\n"
            "A young woman is sitting beside a café window, wearing a white dress and "
            "smiling gently at the viewer.\n"
            "ANIMA_NEGATIVE:\n"
        ),
    )
    assert result.positive.startswith(
        "masterpiece, best quality, score_7, safe, ginntuinn, 1girl, silver hair."
    )
    assert result.positive.endswith(
        "A young woman is sitting beside a café window, wearing a white dress and "
        "smiling gently at the viewer."
    )
    assert result.positive.count("ginntuinn") == 1
    assert result.negative == (
        "worst quality, low quality, score_1, score_2, score_3, artist name, "
        "blurry, jpeg artifacts, chromatic aberration"
    )


def test_anima_hybrid_has_a_dedicated_validator_not_the_tag_json_validator():
    engine = _engine("anima", "turbo_v1_0")
    request = "ginntuinn, 1girl. A young woman smiles."
    with pytest.raises(TransformationError) as exc:
        engine.finalize_output(
            request,
            PromptSettings(mode="T2I", protected_terms=("ginntuinn",)),
            '{"subject_count": ["1girl"], "natural": "A young woman smiles."}',
        )
    assert getattr(exc.value, "code", None) == ANIMA_HYBRID_OUTPUT_INVALID


def test_paired_and_legacy_literal_syntax_preserve_exact_multiline_bodies():
    value = (
        "[speech:ja]一行目\n二行目[/speech]\n"
        "[text:ja]月夜珈琲[/text]\n"
        "[speech:en] Legacy speech"
    )
    literals = parse_literal_content(value)
    assert [(item.kind, item.text, item.source) for item in literals] == [
        ("speech", "一行目\n二行目", "paired"),
        ("text", "月夜珈琲", "paired"),
        ("speech", "Legacy speech", "legacy"),
    ]
    stripped = remove_literal_markers(value)
    assert stripped == "一行目\n二行目\n月夜珈琲\nLegacy speech"


@pytest.mark.parametrize(
    "renderer",
    [MiniMaxH3Renderer(), Wan22Renderer(), LTX23Renderer(), Krea2Renderer(), AnimaRenderer()],
)
def test_each_renderer_interprets_speech_and_visible_text_context(renderer):
    analysis = renderer.analyze_request(
        "彼女が「こんにちは」と言う。\n看板に「営業中」と書かれている。"
    )
    assert [(item.kind, item.text) for item in analysis.literals] == [
        ("speech", "こんにちは"),
        ("text", "営業中"),
    ]


def test_explicit_literal_marker_has_priority_over_quote_context():
    analysis = Krea2Renderer().analyze_request(
        "[text:ja]彼女が「こんにちは」と言う[/text]"
    )
    assert [(item.kind, item.text, item.source) for item in analysis.literals] == [
        ("text", "彼女が「こんにちは」と言う", "paired")
    ]


def test_literal_body_does_not_change_anima_input_format_detection():
    renderer = AnimaRenderer()
    source = "[text:en]score_7[/text]\nAn illustrated girl walks under moonlight."
    assert renderer.input_mode(source) == "natural"


def test_mixed_explicit_speech_text_and_protected_terms_are_exact_and_markers_removed():
    engine = _engine("ltx_2_3", "distilled_1_1")
    request = (
        "[speech:ja]こんにちは[/speech]\n"
        "[text:ja]月夜珈琲[/text]\n"
        "Show Brand_X."
    )
    settings = PromptSettings(mode="T2V", protected_terms=("Brand_X",))
    generated = (
        "A woman says [speech:ja]こんにちは[/speech] beside a sign reading "
        "[text:ja]月夜珈琲[/text], with Brand_X visible."
    )
    result = engine.finalize_output(request, settings, generated)
    assert "こんにちは" in result.positive
    assert "月夜珈琲" in result.positive
    assert "Brand_X" in result.positive
    assert "[speech:ja]" not in result.positive
    assert "[text:ja]" not in result.positive


@pytest.mark.parametrize(
    ("renderer", "task"),
    [
        (MiniMaxH3Renderer(), "T2VA"),
        (Wan22Renderer(), "T2V"),
        (LTX23Renderer(), "T2V"),
        (Krea2Renderer(), "T2I"),
        (AnimaRenderer(), "T2I"),
    ],
)
@pytest.mark.parametrize("processing", ["Faithful", "Balanced", "Creative"])
def test_each_renderer_owns_all_processing_mode_rules(renderer, task: str, processing: str):
    analysis = renderer.analyze_request("A simple scene.")
    system = renderer.system_instructions(
        RendererContext(task, processing, "en", "test"),
        analysis,
        normalize_protected_terms([]),
    )
    assert f"Prompt Processing: {processing}" in system
    assert "Processing rule:" in system


def test_each_renderer_owns_model_specific_localized_prompt_style_descriptions():
    renderers = (
        MiniMaxH3Renderer(),
        Wan22Renderer(),
        LTX23Renderer(),
        Krea2Renderer(),
        AnimaRenderer(),
    )
    balanced_descriptions = []
    for renderer in renderers:
        english = [
            renderer.prompt_style_description(processing, "en-US")
            for processing in ("Faithful", "Balanced", "Creative")
        ]
        japanese = [
            renderer.prompt_style_description(processing, "ja-JP")
            for processing in ("Faithful", "Balanced", "Creative")
        ]
        assert all(english)
        assert all(japanese)
        assert len(set(english)) == 3
        assert len(set(japanese)) == 3
        assert english != japanese
        balanced_descriptions.append(english[1])
    assert len(set(balanced_descriptions)) == 5


def test_locales_contain_the_same_literal_syntax_examples():
    for locale in ("en-US", "ja-JP"):
        values = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
        hint = values["input.literal_hint"]
        assert "[speech:ja]こんにちは[/speech]" in hint
        assert "[text:ja]月夜珈琲[/text]" in hint
