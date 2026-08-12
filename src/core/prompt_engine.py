from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .literal_content import parse_literal_content
from .profile_loader import ProfileLoader
from .profile_models import LoadedProfile, ProfileVariant
from .protected_terms import normalize_protected_terms
from .renderers import RenderResult, RendererRegistry
from .skill_manager import SkillManager


H3_MODES = ("T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA")
PROCESSING_MODES = ("Faithful", "Balanced", "Creative")
REFERENCE_LIMITS = {"Picture": 9, "Video": 3, "Audio": 3}
TEMPERATURES = {"Faithful": 0.35, "Balanced": 0.55, "Creative": 0.75}
DEFAULT_MAX_OUTPUT_TOKENS = 1536

CORE_TRANSFORMATION_POLICY = """CORE TRANSFORMATION POLICY (highest priority):
1. Preserve explicit semantic user intent.
2. Preserve Literal Content exactly, without translation, paraphrase, correction, romanization, punctuation normalization, or character changes.
3. Preserve Protected Terms exactly, without translation, splitting, correction, removal, or renaming.
4. Convert content into the format required by the selected model profile.
5. Apply profile recommendations only when they do not conflict with user intent.
6. Fixed profile components are assembled deterministically outside the LLM.
7. Treat recommended prompt length as guidance only.
8. Never remove, shorten, expand, or invent semantic content solely to reach a length target.
9. In Faithful mode, do not invent unspecified semantic details.
10. Prefer semantic preservation over profile conformity."""

PROCESSING_INSTRUCTIONS = {
    "Faithful": "Preserve every specified action and its order. Add no unspecified action or major detail.",
    "Balanced": "Preserve the request; add only minimal continuity, timing, natural motion, ambience, or camera clarity.",
    "Creative": "Preserve the central intent; you may add useful cinematic direction and natural visual detail.",
}


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


def clean_model_output(text: str) -> str:
    """Remove Qwen reasoning blocks and harmless wrappers without eating prompt text."""
    cleaned = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"^\s*```(?:text|markdown)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
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
        ui_block = self._ui_block(settings)
        external_materials = self._external_materials(settings)
        # Some embedded Jinja templates (including Qwen3.5 variants) accept a
        # system role only as the first message. Keep every instruction and the
        # Skill text unchanged, but place them in one leading system message.
        system_content = "\n\n".join(
            (
                CORE_TRANSFORMATION_POLICY,
                f"SELECTED PROFILE ({self.profile.manifest.id} v{self.profile.manifest.profile_version}):\n{self.profile.instructions}",
                *external_materials,
                ui_block,
                (
                    "OUTPUT FORMAT: "
                    + self.renderer_registry.get(
                        self.profile.manifest.renderer
                    ).llm_output_instruction(self.profile.manifest.output_language)
                ),
                self._preservation_block(request, settings),
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

    def _preservation_block(self, request: str, settings: PromptSettings) -> str:
        literals = parse_literal_content(request)
        protected = normalize_protected_terms(settings.protected_terms)
        lines = ["EXACT PRESERVATION REQUIREMENTS:"]
        if literals:
            lines.append("Copy each Literal Content value exactly into the finished prompt:")
            lines.extend(f"- {item.kind}:{item.language}: {item.text}" for item in literals)
        if protected:
            lines.append("Copy each Protected Term exactly into the finished prompt:")
            lines.extend(f"- {item.text}" for item in protected)
        if len(lines) == 1:
            lines.append("No explicit Literal Content or Protected Terms were supplied.")
        return "\n".join(lines)

    def request_payload(self, request: str, settings: PromptSettings) -> dict[str, Any]:
        return {
            "messages": self.build_messages(request, settings),
            "temperature": TEMPERATURES[settings.processing],
            "top_p": 0.9,
            "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "stream": False,
        }

    def finalize_output(
        self, request: str, settings: PromptSettings, generated: str
    ) -> RenderResult:
        renderer = self.renderer_registry.get(self.profile.manifest.renderer)
        return renderer.render(
            clean_model_output(generated),
            self.variant,
            parse_literal_content(request),
            normalize_protected_terms(settings.protected_terms),
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

    def _ui_block(self, settings: PromptSettings) -> str:
        if not self.profile.manifest.capabilities.get("legacy_h3_controls", False):
            return "\n".join(
                (
                    "UI SETTINGS (use only where they do not contradict the user):",
                    f"Task: {settings.mode}",
                    f"Prompt Processing: {settings.processing}",
                    f"Processing rule: {PROCESSING_INSTRUCTIONS[settings.processing]}",
                )
            )
        lines = [
            "UI SETTINGS (use only where they do not contradict the user):",
            f"Mode: {settings.mode}",
            f"Duration: {settings.duration} seconds",
            f"Prompt Processing: {settings.processing}",
            f"Processing rule: {PROCESSING_INSTRUCTIONS[settings.processing]}",
            f"Camera: {settings.camera}",
            f"Shot: {settings.shot}",
            f"Motion: {settings.motion}",
            "Audio: " + ", ".join(
                name
                for name, enabled in (
                    ("Environmental / scene audio", settings.environmental_audio),
                    ("Dialogue", settings.dialogue),
                    ("Background music", settings.background_music),
                )
                if enabled
            ),
        ]
        if settings.mode in {"I2VA", "FL2VA"} and settings.start_frame_note.strip():
            lines.append(f"Start image note: {settings.start_frame_note.strip()}")
        if settings.mode in {"FL2VA", "L2VA"} and settings.end_frame_note.strip():
            lines.append(f"End image note: {settings.end_frame_note.strip()}")
        if settings.mode == "Ref2VA":
            lines.append("References (the files themselves are not analyzed or sent):")
            for reference in settings.references:
                lines.append(f"{reference.tag()} {reference.description.strip()}")
        return "\n".join(lines)
