from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from .literal_content import LiteralContent, missing_literal_contents
from .profile_models import LengthGuidance, ProfileVariant
from .protected_terms import ProtectedTerm, missing_protected_terms


LITERAL_CONTENT_NOT_PRESERVED = "LITERAL_CONTENT_NOT_PRESERVED"
PROTECTED_TERM_NOT_PRESERVED = "PROTECTED_TERM_NOT_PRESERVED"
DANBOORU_OUTPUT_INVALID = "DANBOORU_OUTPUT_INVALID"
UNKNOWN_RENDERER = "UNKNOWN_RENDERER"

_ANIMA_SECTION_ORDER = (
    "quality_meta_year_safety",
    "subject_count",
    "character",
    "series",
    "artist",
    "general",
)
_ANIMA_ALLOWED_SECTIONS = frozenset((*_ANIMA_SECTION_ORDER, "negative"))
_SCORE_TAG = re.compile(r"^score_\d+$", re.IGNORECASE)
_WEIGHTED_TAG = re.compile(r"^\((.+):([0-9]+(?:\.[0-9]+)?)\)$")


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

    def llm_output_instruction(self, output_language: str) -> str: ...

    def request_payload_overrides(self) -> dict[str, object]: ...

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


def _plain_output_instruction(output_language: str) -> str:
    return (
        "Return only the complete prompt required by the selected profile, "
        f"using its declared output language ({output_language}). "
        "Do not add analysis, a preface such as 'Here is your prompt', or Markdown fences."
    )


def _render_plain(
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


class VideoNarrativeRenderer:
    renderer_id = "video_narrative"

    def llm_output_instruction(self, output_language: str) -> str:
        return _plain_output_instruction(output_language)

    def request_payload_overrides(self) -> dict[str, object]:
        return {}

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> RenderResult:
        return _render_plain(generated, variant, literals, protected_terms)


class NaturalLanguageRenderer:
    renderer_id = "natural_language"

    def llm_output_instruction(self, output_language: str) -> str:
        return _plain_output_instruction(output_language)

    def request_payload_overrides(self) -> dict[str, object]:
        return {}

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> RenderResult:
        return _render_plain(generated, variant, literals, protected_terms)


def _load_json_object(generated: str) -> dict[str, object]:
    text = generated.strip()
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        decoder = json.JSONDecoder()
        raw = None
        for match in re.finditer(r"\{", text):
            try:
                candidate, _end = decoder.raw_decode(text, match.start())
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(candidate, dict):
                raw = candidate
                break
        if raw is None:
            raise TransformationError(DANBOORU_OUTPUT_INVALID)
    if not isinstance(raw, dict):
        raise TransformationError(DANBOORU_OUTPUT_INVALID)
    return raw


def _parse_danbooru_sections(generated: str) -> dict[str, tuple[str, ...]]:
    raw = _load_json_object(generated)
    if not raw or set(raw) - _ANIMA_ALLOWED_SECTIONS:
        raise TransformationError(DANBOORU_OUTPUT_INVALID)
    sections: dict[str, tuple[str, ...]] = {}
    for name in _ANIMA_ALLOWED_SECTIONS:
        value = raw.get(name, [])
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            raise TransformationError(DANBOORU_OUTPUT_INVALID)
        sections[name] = tuple(item.strip() for item in value)
    return sections


def _normalization_exemptions(
    literals: tuple[LiteralContent, ...],
    protected_terms: tuple[ProtectedTerm, ...],
) -> frozenset[str]:
    return frozenset(
        (
            *(item.text for item in literals),
            *(item.text for item in protected_terms),
        )
    )


def _normalize_danbooru_tag(value: str, *, artist: bool, exemptions: frozenset[str]) -> str:
    tag = value.strip()
    if tag in exemptions:
        return tag

    weighted = _WEIGHTED_TAG.fullmatch(tag)
    if weighted is not None:
        inner = _normalize_danbooru_tag(
            weighted.group(1),
            artist=artist,
            exemptions=exemptions,
        )
        return f"({inner}:{weighted.group(2)})"

    if tag.startswith("@"):
        body = re.sub(r"\s+", " ", tag[1:].replace("_", " ").strip()).lower()
        return f"@{body}" if body else ""

    if _SCORE_TAG.fullmatch(tag):
        return tag.lower()

    normalized = re.sub(r"\s+", " ", tag.replace("_", " ").strip()).lower()
    if artist and normalized:
        return f"@{normalized}"
    return normalized


def _dedupe_tags(values: tuple[str, ...] | list[str], seen: set[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


class DanbooruTagsRenderer:
    renderer_id = "danbooru_tags"

    def llm_output_instruction(self, output_language: str) -> str:
        return (
            "Return only one valid JSON object and no Markdown fences. "
            "The allowed keys are "
            '"quality_meta_year_safety", "subject_count", "character", "series", '
            '"artist", "general", and "negative". '
            "Every value must be an array of strings. Omit nothing important from the user request. "
            "Do not include the profile's standard recommended quality or negative tags; "
            "the renderer adds those deterministically. "
            "Put only user-requested or semantically necessary quality/meta/year/safety tags in "
            '"quality_meta_year_safety". Put 1girl/1boy/1other style count tags in "subject_count". '
            'Put character names in "character", series names in "series", artist tags in "artist", '
            'all other positive visual tags in "general", and only user-requested exclusions in "negative". '
            "Literal Content and Protected Terms must appear as exact complete string values without changes."
        )

    def request_payload_overrides(self) -> dict[str, object]:
        # llama.cpp can constrain this response to a JSON object. The renderer still
        # performs its own strict key/type validation and does not trust the server
        # to enforce the semantic schema.
        return {"response_format": {"type": "json_object"}}

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> RenderResult:
        sections = _parse_danbooru_sections(generated)
        exemptions = _normalization_exemptions(literals, protected_terms)

        normalized_sections: dict[str, list[str]] = {}
        for section in _ANIMA_SECTION_ORDER:
            normalized_sections[section] = [
                _normalize_danbooru_tag(
                    item,
                    artist=section == "artist",
                    exemptions=exemptions,
                )
                for item in sections[section]
            ]

        explicit_safety = {
            item.casefold()
            for item in normalized_sections["quality_meta_year_safety"]
            if item.casefold() in {"safe", "sensitive", "nsfw", "explicit"}
        }
        fixed_positive_values = (
            *variant.required_prompt.positive_prefix,
            *variant.recommended_prompt.positive_prefix,
        )
        if explicit_safety and "safe" not in explicit_safety:
            fixed_positive_values = tuple(
                item for item in fixed_positive_values if item.casefold() != "safe"
            )
        fixed_positive = tuple(fixed_positive_values)
        fixed_positive_suffix = (
            *variant.recommended_prompt.positive_suffix,
            *variant.required_prompt.positive_suffix,
        )
        positive_seen = {item.casefold() for item in (*fixed_positive, *fixed_positive_suffix)}
        generated_positive: list[str] = []
        for section in _ANIMA_SECTION_ORDER:
            generated_positive.extend(
                _dedupe_tags(normalized_sections[section], positive_seen)
            )

        positive_parts = [
            *fixed_positive,
            *generated_positive,
            *fixed_positive_suffix,
        ]
        positive = ", ".join(part for part in positive_parts if part).strip()

        fixed_negative = (
            *variant.required_prompt.negative_prefix,
            *variant.recommended_prompt.negative_prefix,
        )
        fixed_negative_suffix = (
            *variant.recommended_prompt.negative_suffix,
            *variant.required_prompt.negative_suffix,
        )
        negative_seen = {item.casefold() for item in (*fixed_negative, *fixed_negative_suffix)}
        generated_negative = [
            _normalize_danbooru_tag(item, artist=False, exemptions=exemptions)
            for item in sections["negative"]
        ]
        negative_parts = [
            *fixed_negative,
            *_dedupe_tags(generated_negative, negative_seen),
            *fixed_negative_suffix,
        ]
        negative = ", ".join(part for part in negative_parts if part).strip() or None

        if missing_literal_contents(positive, literals):
            raise TransformationError(LITERAL_CONTENT_NOT_PRESERVED)
        if missing_protected_terms(positive, protected_terms):
            raise TransformationError(PROTECTED_TERM_NOT_PRESERVED)

        return RenderResult(
            positive=positive,
            negative=negative,
            warnings=length_warnings(positive, variant.length_guidance),
        )


class RendererRegistry:
    def __init__(self) -> None:
        renderers: tuple[Renderer, ...] = (
            VideoNarrativeRenderer(),
            NaturalLanguageRenderer(),
            DanbooruTagsRenderer(),
        )
        self._renderers: dict[str, Renderer] = {
            renderer.renderer_id: renderer for renderer in renderers
        }

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._renderers)

    def get(self, renderer_id: str) -> Renderer:
        try:
            return self._renderers[renderer_id]
        except KeyError as exc:
            raise TransformationError(UNKNOWN_RENDERER) from exc
