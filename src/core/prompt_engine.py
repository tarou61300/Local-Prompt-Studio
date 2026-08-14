from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .profile_loader import ProfileLoader
from .profile_models import LoadedProfile, ProfileVariant
from .protected_terms import normalize_protected_terms
from .renderers import RenderResult, RendererContext, RendererRegistry
from .skill_manager import SkillManager


H3_MODES = ("T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA")
PROCESSING_MODES = ("Faithful", "Balanced", "Creative")
REFERENCE_LIMITS = {"Picture": 9, "Video": 3, "Audio": 3}
TEMPERATURES = {"Faithful": 0.35, "Balanced": 0.55, "Creative": 0.75}
DEFAULT_MAX_OUTPUT_TOKENS = 1536

@dataclass(frozen=True, slots=True)
class H3Reference:
    kind: str
    number: int
    description: str

    def tag(self) -> str:
        return f"<{self.kind} {self.number}>"


@dataclass(slots=True)
class PromptSettings:
    mode: str = "T2VA"
    duration: int = 10
    processing: str = "Faithful"
    camera: str = "Free"
    shot: str = "Single continuous shot"
    motion: str = "Natural"
    environmental_audio: bool = True
    dialogue: bool = True
    background_music: bool = False
    start_frame_note: str = ""
    end_frame_note: str = ""
    references: list[H3Reference] = field(default_factory=list)
    protected_terms: tuple[str, ...] = ()
    auto_quality_tags: bool = True


def clean_model_output(text: str) -> str:
    """Remove Qwen reasoning blocks and harmless wrappers without eating prompt text."""
    cleaned = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"^\s*```(?:text|markdown|json)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


class PromptEngine:
    def __init__(
        self,
        skill_manager: SkillManager | None,
        profile: LoadedProfile | None = None,
        variant_id: str | None = None,
        renderer_registry: RendererRegistry | None = None,
    ) -> None:
        self.skill_manager = skill_manager
        self.renderer_registry = renderer_registry or RendererRegistry()
        self.profile = profile or self._default_profile()
        self.variant: ProfileVariant = self.profile.variant(variant_id)

    def _default_profile(self) -> LoadedProfile:
        resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        catalog = ProfileLoader(
            resource_root / "profiles",
            resource_root / ".profile-loader-no-data",
            self.renderer_registry,
        ).discover()
        try:
            return catalog.profiles["minimax_h3"]
        except KeyError as exc:
            raise ValueError("PROFILE_INVALID") from exc

    def build_messages(self, request: str, settings: PromptSettings) -> list[dict[str, str]]:
        if not request.strip():
            raise ValueError("Requestを入力してください。")
        self._validate(settings)
        renderer = self.renderer_registry.get(self.profile.manifest.renderer)
        analysis = renderer.analyze_request(request)
        protected_terms = normalize_protected_terms(settings.protected_terms)
        external_materials = self._external_materials(settings)
        # Some embedded Jinja templates (including Qwen3.5 variants) accept a
        # system role only as the first message. Keep every instruction and the
        # Skill text unchanged, but place them in one leading system message.
        system_content = "\n\n".join(
            (
                renderer.system_instructions(
                    self._renderer_context(settings),
                    analysis,
                    protected_terms,
                ),
                f"SELECTED PROFILE ({self.profile.manifest.id} v{self.profile.manifest.profile_version})",
                *external_materials,
            )
        )
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": request},
        ]

    def _external_materials(self, settings: PromptSettings) -> tuple[str, ...]:
        blocks: list[str] = []
        for dependency in self.profile.manifest.external_dependencies:
            if dependency.get("kind") != "prompt_skill" or not dependency.get(
                "required", False
            ):
                continue
            if self.skill_manager is None:
                raise ValueError("PROFILE_EXTERNAL_DEPENDENCY_MISSING")
            blocks.extend(
                (
                    f"EXTERNAL PROMPT SKILL:\n{self.skill_manager.load_skill()}",
                    f"REFERENCE GUIDE FOR {settings.mode}:\n{self.skill_manager.reference_for_mode(settings.mode)}",
                )
            )
        return tuple(blocks)

    def request_payload(self, request: str, settings: PromptSettings) -> dict[str, Any]:
        renderer = self.renderer_registry.get(self.profile.manifest.renderer)
        analysis = renderer.analyze_request(request)
        payload: dict[str, Any] = {
            "messages": self.build_messages(request, settings),
            "temperature": TEMPERATURES[settings.processing],
            "top_p": 0.9,
            "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "stream": False,
        }
        payload.update(renderer.request_payload_overrides(analysis))
        return payload

    def finalize_output(
        self, request: str, settings: PromptSettings, generated: str
    ) -> RenderResult:
        renderer = self.renderer_registry.get(self.profile.manifest.renderer)
        analysis = renderer.analyze_request(request)
        return renderer.render(
            clean_model_output(generated),
            self.variant,
            analysis.literals,
            normalize_protected_terms(settings.protected_terms),
            input_mode=analysis.input_mode,
            source_request=request,
            auto_quality_tags=settings.auto_quality_tags,
        )

    def _validate(self, settings: PromptSettings) -> None:
        if settings.mode not in self.profile.manifest.supported_tasks:
            raise ValueError(f"PROFILE_UNSUPPORTED_TASK: {settings.mode}")
        if settings.processing not in PROCESSING_MODES:
            raise ValueError(f"未対応のPrompt Processingです: {settings.processing}")
        if not self.profile.manifest.capabilities.get("legacy_h3_controls", False):
            return
        if not 4 <= settings.duration <= 15:
            raise ValueError("Durationは4～15秒で指定してください。")
        counts = {kind: 0 for kind in REFERENCE_LIMITS}
        used: set[tuple[str, int]] = set()
        for reference in settings.references:
            if reference.kind not in REFERENCE_LIMITS:
                raise ValueError(f"未対応のReference typeです: {reference.kind}")
            if reference.number < 1 or reference.number > REFERENCE_LIMITS[reference.kind]:
                raise ValueError(f"{reference.kind}番号が範囲外です。")
            key = (reference.kind, reference.number)
            if key in used:
                raise ValueError(f"Reference番号が重複しています: {reference.tag()}")
            used.add(key)
            counts[reference.kind] += 1

    def _renderer_context(self, settings: PromptSettings) -> RendererContext:
        return RendererContext(
            task=settings.mode,
            processing=settings.processing,
            output_language=self.profile.manifest.output_language,
            variant_id=self.variant.id,
            profile_instructions=self.profile.instructions,
            duration=settings.duration,
            camera=settings.camera,
            shot=settings.shot,
            motion=settings.motion,
            environmental_audio=settings.environmental_audio,
            dialogue=settings.dialogue,
            background_music=settings.background_music,
            start_frame_note=settings.start_frame_note.strip(),
            end_frame_note=settings.end_frame_note.strip(),
            references=tuple(
                f"{reference.tag()} {reference.description.strip()}"
                for reference in settings.references
            ),
            auto_quality_tags=settings.auto_quality_tags,
        )
