from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .literal_content import LiteralContent, missing_literal_contents
from .profile_models import LengthGuidance, ProfileVariant, PromptComponents
from .protected_terms import ProtectedTerm, missing_protected_terms


LITERAL_CONTENT_NOT_PRESERVED = "LITERAL_CONTENT_NOT_PRESERVED"
PROTECTED_TERM_NOT_PRESERVED = "PROTECTED_TERM_NOT_PRESERVED"
UNKNOWN_RENDERER = "UNKNOWN_RENDERER"


class TransformationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RenderResult:
    positive: str
    negative: str | None = None
    warnings: tuple[str, ...] = ()


class Renderer(Protocol):
    renderer_id: str

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> RenderResult: ...


def _length_value(text: str, unit: str) -> int:
    if unit == "words":
        return len(text.split())
    if unit == "tags":
        return len([part for part in text.split(",") if part.strip()])
    return len(text.split())


def length_warnings(text: str, guidance: LengthGuidance) -> tuple[str, ...]:
    if guidance.unit is None:
        return ()
    value = _length_value(text, guidance.unit)
    if guidance.hard_maximum is not None and value > guidance.hard_maximum:
        return ("PROMPT_EXCEEDS_HARD_MAXIMUM",)
    if guidance.recommended_minimum is not None and value < guidance.recommended_minimum:
        return ("PROMPT_SHORTER_THAN_RECOMMENDED",)
    if guidance.recommended_maximum is not None and value > guidance.recommended_maximum:
        return ("PROMPT_LONGER_THAN_RECOMMENDED",)
    return ()


class VideoNarrativeRenderer:
    renderer_id = "video_narrative"

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> RenderResult:
        positive = " ".join(
            (
                *variant.required_prompt.positive_prefix,
                *variant.recommended_prompt.positive_prefix,
                generated,
                *variant.recommended_prompt.positive_suffix,
                *variant.required_prompt.positive_suffix,
            )
        ).strip()
        negative_parts = (
            *variant.required_prompt.negative_prefix,
            *variant.recommended_prompt.negative_prefix,
            *variant.recommended_prompt.negative_suffix,
            *variant.required_prompt.negative_suffix,
        )
        if missing_literal_contents(positive, literals):
            raise TransformationError(LITERAL_CONTENT_NOT_PRESERVED)
        if missing_protected_terms(positive, protected_terms):
            raise TransformationError(PROTECTED_TERM_NOT_PRESERVED)
        return RenderResult(
            positive=positive,
            negative=" ".join(negative_parts).strip() or None,
            warnings=length_warnings(positive, variant.length_guidance),
        )


class RendererRegistry:
    def __init__(self) -> None:
        renderer = VideoNarrativeRenderer()
        self._renderers: dict[str, Renderer] = {renderer.renderer_id: renderer}

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._renderers)

    def get(self, renderer_id: str) -> Renderer:
        try:
            return self._renderers[renderer_id]
        except KeyError as exc:
            raise TransformationError(UNKNOWN_RENDERER) from exc
