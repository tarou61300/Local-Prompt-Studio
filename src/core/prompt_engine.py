from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .profile_loader import ProfileLoader
from .profile_models import LoadedProfile, ProfileVariant
from .protected_terms import normalize_protected_terms
from .renderers import RenderResult, RendererAnalysis, RendererContext, RendererRegistry
from .skill_manager import (
    BASE_SCHEMA_FIELDS,
    REFERENCE_SCHEMA_FIELDS,
    SkillManager,
)


H3_MODES = ("T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA")
PROCESSING_MODES = ("Faithful", "Balanced", "Creative")
REFERENCE_LIMITS = {"Picture": 9, "Video": 3, "Audio": 3}
TEMPERATURES = {"Faithful": 0.35, "Balanced": 0.55, "Creative": 0.75}
DEFAULT_MAX_OUTPUT_TOKENS = 1536
TASK_SCHEMA_VALIDATION_FAILED = "Selected Task schema validation failed"
_H3_TASK_SCHEMAS = {
    "I2VA": BASE_SCHEMA_FIELDS,
    "Ref2VA": REFERENCE_SCHEMA_FIELDS,
}
_H3_SCHEMA_FIELDS = tuple(dict.fromkeys((*BASE_SCHEMA_FIELDS, *REFERENCE_SCHEMA_FIELDS)))
_H3_SCHEMA_FIELD_RE = re.compile(
    rf"^[ \t]*(?P<field>{'|'.join(_H3_SCHEMA_FIELDS)})[ \t]*:",
    flags=re.IGNORECASE | re.MULTILINE,
)
_TASK_SCHEMA_ERROR_RE = re.compile(
    rf"^{re.escape(TASK_SCHEMA_VALIDATION_FAILED)}: "
    rf"(?P<task>{'|'.join(re.escape(task) for task in _H3_TASK_SCHEMAS)})"
    r"(?:; fields=(?P<fields>[a-z0-9_,]+))?$"
)


def parse_task_schema_validation_error(
    message: str,
) -> tuple[str, tuple[str, ...]] | None:
    """Return safe Task Schema Lock details from its stable error message."""
    match = _TASK_SCHEMA_ERROR_RE.fullmatch(message)
    if match is None:
        return None
    fields = tuple(
        field
        for field in (match.group("fields") or "").split(",")
        if field in _H3_SCHEMA_FIELDS
    )
    return match.group("task"), tuple(dict.fromkeys(fields))


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
    common_supplement: str = ""
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
        context = self._renderer_context(settings)
        analysis = self._analyze_roles(renderer, request, context)
        protected_terms = normalize_protected_terms(settings.protected_terms)
        external_materials = self._external_materials(settings)
        # Some embedded Jinja templates (including Qwen3.5 variants) accept a
        # system role only as the first message. Keep every instruction and the
        # Skill text unchanged, but place them in one leading system message.
        post_external = getattr(renderer, "post_external_intent_guardrails", None)
        final_intent_guardrails = (
            post_external(context) if callable(post_external) else ""
        )
        system_content = "\n\n".join(
            block
            for block in (
                renderer.system_instructions(
                    context,
                    analysis,
                    protected_terms,
                ),
                self._input_role_material(context),
                f"SELECTED PROFILE ({self.profile.manifest.id} v{self.profile.manifest.profile_version})",
                *external_materials,
                final_intent_guardrails,
            )
            if block
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
            route = self.skill_manager.prompt_route_for_mode(settings.mode)
            blocks.extend(
                (
                    f"EXTERNAL PROMPT SKILL:\n{route.skill_text}",
                    f"REFERENCE GUIDE FOR {route.task}:\n{route.reference_text}",
                    route.routing_instruction(),
                )
            )
        return tuple(blocks)

    def request_payload(self, request: str, settings: PromptSettings) -> dict[str, Any]:
        renderer = self.renderer_registry.get(self.profile.manifest.renderer)
        context = self._renderer_context(settings)
        analysis = self._analyze_roles(renderer, request, context)
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
        context = self._renderer_context(settings)
        semantic_source = self._semantic_source(request, context)
        analysis = self._analyze_roles(renderer, request, context)
        cleaned = clean_model_output(generated)
        if self.profile.manifest.renderer == "minimax_h3":
            self._validate_h3_task_schema(cleaned, settings.mode)
        return renderer.render(
            cleaned,
            self.variant,
            analysis.literals,
            normalize_protected_terms(settings.protected_terms),
            input_mode=analysis.input_mode,
            source_request=semantic_source,
            auto_quality_tags=settings.auto_quality_tags,
        )

    @staticmethod
    def _validate_h3_task_schema(generated: str, mode: str) -> None:
        expected = _H3_TASK_SCHEMAS.get(mode)
        if expected is None:
            return
        expected_fields = tuple(field.casefold() for field in expected)
        actual = tuple(
            match.group("field").casefold()
            for match in _H3_SCHEMA_FIELD_RE.finditer(generated)
        )
        if actual != expected_fields:
            unexpected = tuple(
                dict.fromkeys(field for field in actual if field not in expected_fields)
            )
            field_details = f"; fields={','.join(unexpected)}" if unexpected else ""
            raise ValueError(
                f"{TASK_SCHEMA_VALIDATION_FAILED}: {mode}{field_details}"
            )

    @staticmethod
    def _semantic_source(request: str, context: RendererContext) -> str:
        """Keep input roles explicit for analysis and final preservation audits."""
        supplements = (
            ("OVERALL_SUPPLEMENT", context.overall_supplement),
            ("START_IMAGE_SUPPLEMENT", context.start_frame_note),
            ("END_IMAGE_SUPPLEMENT", context.end_frame_note),
        )
        if not any(value for _label, value in supplements):
            return request
        blocks = [f"REQUEST:\n{request}"]
        blocks.extend(
            f"{label}:\n{value}" for label, value in supplements if value
        )
        return "\n\n".join(blocks)

    @staticmethod
    def _semantic_source_role_ranges(
        request: str,
        context: RendererContext,
    ) -> tuple[tuple[str, int, int], ...]:
        """Map combined-source line numbers to their UI input role."""

        blocks = [
            ("request", "REQUEST", request),
            ("common_supplement", "OVERALL_SUPPLEMENT", context.overall_supplement),
            ("start_supplement", "START_IMAGE_SUPPLEMENT", context.start_frame_note),
            ("end_supplement", "END_IMAGE_SUPPLEMENT", context.end_frame_note),
        ]
        active_blocks = [blocks[0], *(block for block in blocks[1:] if block[2])]
        ranges: list[tuple[str, int, int]] = []
        block_start_line = 1
        for role, _label, value in active_blocks:
            content_start_line = block_start_line + 1
            ranges.append(
                (role, content_start_line, content_start_line + value.count("\n"))
            )
            block_start_line += 3 + value.count("\n")
        return tuple(ranges)

    @staticmethod
    def _literal_source_role(
        line_number: int,
        ranges: tuple[tuple[str, int, int], ...],
    ) -> str:
        for role, first_line, last_line in ranges:
            if first_line <= line_number <= last_line:
                return role
        return "request"

    @classmethod
    def _analyze_roles(cls, renderer: Any, request: str, context: RendererContext) -> RendererAnalysis:
        """Let Request select renderer mode while preserving literals in every role."""
        request_analysis = renderer.analyze_request(request)
        semantic_source = cls._semantic_source(request, context)
        if semantic_source == request:
            return request_analysis
        all_roles_analysis = renderer.analyze_request(semantic_source)
        role_ranges = cls._semantic_source_role_ranges(request, context)
        return RendererAnalysis(
            literals=tuple(
                replace(
                    literal,
                    source_role=cls._literal_source_role(
                        literal.line_number,
                        role_ranges,
                    ),
                )
                for literal in all_roles_analysis.literals
            ),
            input_mode=request_analysis.input_mode,
        )

    @staticmethod
    def _input_role_material(context: RendererContext) -> str:
        """Give the LLM supplements separately from the central user request."""
        blocks = [
            "INPUT ROLE POLICY:",
            "The user message is REQUEST: the central user intent. Preserve it as the primary instruction.",
            "OVERALL_SUPPLEMENT adds context or constraints to the whole request; it must not replace the request or create a different request.",
            "START_IMAGE_SUPPLEMENT applies only to the starting image/state. Never apply it as an end-state instruction.",
            "END_IMAGE_SUPPLEMENT applies only to the ending image/state. Never apply it as a start-state instruction.",
            "Integrate supplied roles naturally in the final prompt; the final prompt need not retain these section labels.",
            "Preserve Literal Content and Protected Terms from every supplied role exactly.",
            "Perform model-specific optimization only under the selected renderer's policy.",
        ]
        for label, value in (
            ("OVERALL_SUPPLEMENT", context.overall_supplement),
            ("START_IMAGE_SUPPLEMENT", context.start_frame_note),
            ("END_IMAGE_SUPPLEMENT", context.end_frame_note),
        ):
            if value:
                blocks.append(f"{label}:\n{value}")
        return "\n".join(blocks)

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
        supports_start = settings.mode in {"I2V", "I2VA", "FL2VA"}
        supports_end = settings.mode in {"FL2VA", "L2VA"}
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
            overall_supplement=settings.common_supplement.strip(),
            start_frame_note=(settings.start_frame_note.strip() if supports_start else ""),
            end_frame_note=(settings.end_frame_note.strip() if supports_end else ""),
            references=tuple(
                f"{reference.tag()} {reference.description.strip()}"
                for reference in settings.references
            ),
            auto_quality_tags=settings.auto_quality_tags,
        )
