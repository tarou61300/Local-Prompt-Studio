from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.profile_loader import (
    PROFILE_DUPLICATE_ID,
    PROFILE_UNKNOWN_RENDERER,
    PROFILE_UNSAFE_PATH,
    PROFILE_UNSUPPORTED_SCHEMA,
    ProfileLoader,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_ROOT = PROJECT_ROOT / "profiles"


def _manifest(profile_id: str, **changes):
    value = {
        "schema_version": 1,
        "id": profile_id,
        "name": profile_id,
        "profile_version": "1.0.0",
        "category": "video",
        "renderer": "minimax_h3",
        "output_language": "en",
        "default_variant": "base",
        "supported_tasks": ["T2VA"],
        "capabilities": {},
        "instructions_file": "instructions.md",
        "sources": [{"type": "custom"}],
    }
    value.update(changes)
    return value


def _write_profile(root: Path, profile_id: str, **manifest_changes) -> Path:
    profile = root / "video" / profile_id
    (profile / "variants").mkdir(parents=True)
    (profile / "manifest.json").write_text(
        json.dumps(_manifest(profile_id, **manifest_changes)), encoding="utf-8"
    )
    (profile / "instructions.md").write_text("instructions", encoding="utf-8")
    (profile / "variants" / "base.json").write_text(
        json.dumps(
            {
                "id": "base",
                "name": "Base",
                "target_model_version": None,
                "required_prompt": {},
                "recommended_prompt": {},
                "optional_prompt": {},
                "length_guidance": {},
                "inference_recommendations": {},
                "sources": [{"type": "custom"}],
            }
        ),
        encoding="utf-8",
    )
    return profile


def test_builtin_h3_profile_discovery_and_manifest():
    catalog = ProfileLoader(BUILTIN_ROOT, PROJECT_ROOT / ".tmp-unused").discover()
    assert set(catalog.profiles) == {"minimax_h3", "wan_2_2", "ltx_2_3", "krea_2", "anima"}
    assert catalog.errors == []
    profile = catalog.profiles["minimax_h3"]
    assert profile.manifest.schema_version == 1
    assert profile.manifest.renderer == "minimax_h3"
    assert profile.manifest.supported_tasks == ("T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA")
    assert profile.variant().id == "base"
    assert profile.variant().optional_prompt.positive_prefix == ()
    assert profile.variant().length_guidance.unit is None
    assert profile.manifest.capabilities["legacy_h3_controls"] is True
    assert "MiniMax H3" in profile.variant().description("en-US")
    assert "標準Prompt規則" in profile.variant().description("ja-JP")


def test_all_builtin_variants_provide_english_and_japanese_descriptions():
    profiles = ProfileLoader(BUILTIN_ROOT, PROJECT_ROOT / ".tmp-unused").discover().profiles

    variants = [
        variant
        for profile in profiles.values()
        for variant in profile.variants.values()
    ]
    assert len(variants) == 9
    for variant in variants:
        assert variant.description("en-US")
        assert variant.description("ja-JP")
        assert variant.description("unsupported-locale") == variant.description("en-US")


def test_supplied_wan_profile_metadata_and_dependency_contract():
    profile = ProfileLoader(BUILTIN_ROOT, PROJECT_ROOT / ".tmp-unused").discover().profiles[
        "wan_2_2"
    ]

    assert profile.manifest.default_variant == "a14b"
    assert profile.manifest.supported_tasks == ("T2V", "I2V")
    assert profile.variant().id == "a14b"
    assert profile.manifest.renderer == "wan_2_2"
    assert profile.requires_dependency("prompt_skill") is False
    assert profile.variant().length_guidance.unit is None


def test_supplied_ltx_profile_metadata_and_dependency_contract():
    profile = ProfileLoader(BUILTIN_ROOT, PROJECT_ROOT / ".tmp-unused").discover().profiles[
        "ltx_2_3"
    ]

    assert profile.manifest.default_variant == "distilled_1_1"
    assert profile.manifest.supported_tasks == ("T2V", "I2V")
    assert set(profile.variants) == {"dev", "distilled_1_1"}
    assert profile.variant().id == "distilled_1_1"
    assert profile.manifest.renderer == "ltx_2_3"
    assert profile.requires_dependency("prompt_skill") is False
    assert profile.variants["dev"].length_guidance.recommended_maximum == 200
    assert profile.variants["distilled_1_1"].length_guidance.recommended_maximum == 200


def test_krea_2_profile_metadata_and_variant_contract():
    profile = ProfileLoader(BUILTIN_ROOT, PROJECT_ROOT / ".tmp-unused").discover().profiles[
        "krea_2"
    ]

    assert profile.manifest.category == "image"
    assert profile.manifest.default_variant == "turbo"
    assert profile.manifest.supported_tasks == ("T2I",)
    assert set(profile.variants) == {"raw", "turbo"}
    assert profile.variant().id == "turbo"
    assert profile.manifest.renderer == "krea_2"
    assert profile.requires_dependency("prompt_skill") is False
    assert profile.manifest.capabilities["separate_negative_prompt"] is False
    assert profile.variant().length_guidance.unit is None
    assert profile.variants["raw"].inference_recommendations["steps"] == 52
    assert profile.variants["raw"].inference_recommendations["cfg"] == 3.5
    assert profile.variants["turbo"].inference_recommendations["steps"] == 8
    assert profile.variants["turbo"].inference_recommendations["cfg"] == 0.0
    assert profile.variants["turbo"].inference_recommendations["mu"] == 1.15


def test_anima_profile_metadata_and_variant_contract():
    profile = ProfileLoader(BUILTIN_ROOT, PROJECT_ROOT / ".tmp-unused").discover().profiles[
        "anima"
    ]

    assert profile.manifest.category == "image"
    assert profile.manifest.renderer == "anima"
    assert profile.manifest.capabilities["adaptive_prompting"] == [
        "natural",
        "tag",
        "hybrid",
    ]
    assert profile.manifest.default_variant == "turbo_v1_0"
    assert profile.manifest.supported_tasks == ("T2I",)
    assert profile.requires_dependency("prompt_skill") is False
    assert profile.manifest.capabilities["separate_negative_prompt"] is True
    assert set(profile.variants) == {
        "base_v1_0",
        "aesthetic_v1_1",
        "turbo_v1_0",
    }

    base = profile.variants["base_v1_0"]
    aesthetic = profile.variants["aesthetic_v1_1"]
    turbo = profile.variants["turbo_v1_0"]
    assert base.recommended_prompt.positive_prefix == (
        "masterpiece",
        "best quality",
        "score_7",
        "safe",
    )
    assert base.optional_prompt.positive_prefix == ()
    assert "score_1" not in aesthetic.recommended_prompt.negative_prefix
    assert aesthetic.recommended_prompt.positive_prefix == (
        "masterpiece",
        "best quality",
        "safe",
    )
    assert turbo.inference_recommendations["steps_min"] == 8
    assert turbo.inference_recommendations["steps_max"] == 12
    assert turbo.inference_recommendations["cfg"] == 1.0
    assert turbo.inference_recommendations["distilled"] is True


def test_official_update_takes_precedence_over_builtin(tmp_path):
    builtin = tmp_path / "builtin"
    shutil.copytree(BUILTIN_ROOT, builtin)
    official = tmp_path / "data" / "profiles" / "official"
    profile = _write_profile(official, "minimax_h3")
    raw = json.loads((profile / "manifest.json").read_text(encoding="utf-8"))
    raw["name"] = "Updated H3"
    (profile / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    catalog = ProfileLoader(builtin, tmp_path / "data").discover()
    assert catalog.profiles["minimax_h3"].manifest.name == "Updated H3"
    assert catalog.profiles["minimax_h3"].layer == "official"


def test_custom_profile_is_discovered_separately(tmp_path):
    _write_profile(tmp_path / "data" / "profiles" / "custom", "custom_video")
    catalog = ProfileLoader(BUILTIN_ROOT, tmp_path / "data").discover()
    assert "minimax_h3" in catalog.profiles
    assert "custom_video" in catalog.custom_profiles
    assert catalog.custom_profiles["custom_video"].variant().description("ja-JP") == ""


def test_broken_custom_profile_isolated(tmp_path):
    profile = tmp_path / "data" / "profiles" / "custom" / "video" / "broken"
    profile.mkdir(parents=True)
    (profile / "manifest.json").write_text("not-json", encoding="utf-8")
    catalog = ProfileLoader(BUILTIN_ROOT, tmp_path / "data").discover()
    assert "minimax_h3" in catalog.profiles
    assert catalog.errors
    assert catalog.custom_profiles == {}


def test_unknown_renderer_schema_and_unsafe_path_rejected(tmp_path):
    custom = tmp_path / "data" / "profiles" / "custom"
    _write_profile(custom, "unknown_renderer", renderer="not_a_renderer")
    _write_profile(custom, "wrong_schema", schema_version=2)
    _write_profile(custom, "unsafe_path", instructions_file="../outside.md")
    catalog = ProfileLoader(BUILTIN_ROOT, tmp_path / "data").discover()
    codes = {error.code for error in catalog.errors}
    assert PROFILE_UNKNOWN_RENDERER in codes
    assert PROFILE_UNSUPPORTED_SCHEMA in codes
    assert PROFILE_UNSAFE_PATH in codes


def test_duplicate_custom_id_does_not_override_builtin(tmp_path):
    _write_profile(tmp_path / "data" / "profiles" / "custom", "minimax_h3")
    catalog = ProfileLoader(BUILTIN_ROOT, tmp_path / "data").discover()
    assert catalog.profiles["minimax_h3"].layer == "builtin"
    assert any(error.code == PROFILE_DUPLICATE_ID for error in catalog.errors)


def test_executable_profile_content_is_rejected(tmp_path):
    profile = _write_profile(
        tmp_path / "data" / "profiles" / "custom", "unsafe_code"
    )
    (profile / "run.py").write_text("raise RuntimeError", encoding="utf-8")
    catalog = ProfileLoader(BUILTIN_ROOT, tmp_path / "data").discover()
    assert "unsafe_code" not in catalog.custom_profiles
    assert any(error.code == PROFILE_UNSAFE_PATH for error in catalog.errors)


def test_symlinked_profile_directory_is_rejected(tmp_path, monkeypatch):
    link = _write_profile(
        tmp_path / "data" / "profiles" / "custom",
        "linked_profile",
    )
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == link or original_is_symlink(path),
    )

    catalog = ProfileLoader(BUILTIN_ROOT, tmp_path / "data").discover()

    assert "linked_profile" not in catalog.custom_profiles
    assert any(error.code == PROFILE_UNSAFE_PATH for error in catalog.errors)


def test_malformed_external_dependency_metadata_is_rejected(tmp_path):
    custom = tmp_path / "data" / "profiles" / "custom"
    _write_profile(
        custom,
        "bad_dependency",
        external_dependencies=[
            {
                "id": "skill",
                "kind": "prompt_skill",
                "required": "yes",
                "bundled": False,
            }
        ],
    )
    catalog = ProfileLoader(BUILTIN_ROOT, tmp_path / "data").discover()
    assert "bad_dependency" not in catalog.custom_profiles


def test_profile_loading_supports_japanese_and_spaces_in_path(tmp_path):
    builtin = tmp_path / "AIツール Profile Data" / "profiles"
    shutil.copytree(BUILTIN_ROOT, builtin)
    data_dir = tmp_path / "利用者データ 空白"
    catalog = ProfileLoader(builtin, data_dir).discover()
    assert catalog.profiles["minimax_h3"].manifest.name == "MiniMax H3"
    assert catalog.errors == []
