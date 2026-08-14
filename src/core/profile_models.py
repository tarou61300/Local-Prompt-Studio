from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ProfileSource:
    type: str
    url: str | None = None
    verified_at: str | None = None


@dataclass(frozen=True, slots=True)
class PromptComponents:
    positive_prefix: tuple[str, ...] = ()
    positive_suffix: tuple[str, ...] = ()
    negative_prefix: tuple[str, ...] = ()
    negative_suffix: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LengthGuidance:
    unit: str | None = None
    recommended_minimum: int | None = None
    recommended_maximum: int | None = None
    hard_maximum: int | None = None
    source_level: str | None = None
    source_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileVariant:
    id: str
    name: str
    target_model_version: str | None
    descriptions: dict[str, str]
    required_prompt: PromptComponents
    recommended_prompt: PromptComponents
    optional_prompt: PromptComponents
    length_guidance: LengthGuidance
    inference_recommendations: dict[str, Any]
    sources: tuple[ProfileSource, ...]
    path: Path

    def description(self, locale_id: str) -> str:
        return self.descriptions.get(
            locale_id,
            self.descriptions.get("en-US", ""),
        )


@dataclass(frozen=True, slots=True)
class ProfileManifest:
    schema_version: int
    id: str
    name: str
    profile_version: str
    category: str
    renderer: str
    output_language: str
    default_variant: str
    supported_tasks: tuple[str, ...]
    capabilities: dict[str, Any]
    description_key: str | None
    instructions_file: str
    external_dependencies: tuple[dict[str, Any], ...]
    sources: tuple[ProfileSource, ...]


@dataclass(frozen=True, slots=True)
class LoadedProfile:
    manifest: ProfileManifest
    instructions: str
    variants: dict[str, ProfileVariant]
    root: Path
    layer: str
    content_hash: str

    def variant(self, variant_id: str | None = None) -> ProfileVariant:
        return self.variants[variant_id or self.manifest.default_variant]

    def requires_dependency(self, kind: str) -> bool:
        return any(
            dependency.get("kind") == kind and dependency.get("required") is True
            for dependency in self.manifest.external_dependencies
        )


@dataclass(frozen=True, slots=True)
class ProfileLoadError:
    code: str
    source: str


@dataclass(slots=True)
class ProfileCatalog:
    profiles: dict[str, LoadedProfile] = field(default_factory=dict)
    custom_profiles: dict[str, LoadedProfile] = field(default_factory=dict)
    errors: list[ProfileLoadError] = field(default_factory=list)

    def get(self, profile_id: str) -> LoadedProfile:
        if profile_id in self.profiles:
            return self.profiles[profile_id]
        if profile_id in self.custom_profiles:
            return self.custom_profiles[profile_id]
        raise KeyError(profile_id)
