from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from .literal_content import (
    LiteralContent,
    missing_literal_contents,
    parse_literal_content,
    quoted_content_candidates,
    remove_literal_markers,
    without_explicit_literal_content,
)
from .profile_models import LengthGuidance, ProfileVariant
from .protected_terms import ProtectedTerm, missing_protected_terms


LITERAL_CONTENT_NOT_PRESERVED = "LITERAL_CONTENT_NOT_PRESERVED"
PROTECTED_TERM_NOT_PRESERVED = "PROTECTED_TERM_NOT_PRESERVED"
DANBOORU_OUTPUT_INVALID = "DANBOORU_OUTPUT_INVALID"
ANIMA_HYBRID_OUTPUT_INVALID = "ANIMA_HYBRID_OUTPUT_INVALID"
UNREQUESTED_SEMANTIC_TAG = "UNREQUESTED_SEMANTIC_TAG"
UNKNOWN_RENDERER = "UNKNOWN_RENDERER"

_ANIMA_SECTION_ORDER = (
    "quality_meta_year_safety",
    "subject_count",
    "character",
    "series",
    "artist",
    "general",
)
_ANIMA_TAG_KEYS = frozenset((*_ANIMA_SECTION_ORDER, "negative"))
_SCORE_TAG = re.compile(r"^score_\d+$", re.IGNORECASE)
_WEIGHTED_TAG = re.compile(r"^\((.+):([0-9]+(?:\.[0-9]+)?)\)$")
_LITERAL_DIRECTIVE_TAG = re.compile(
    r"^\[(?:speech|text):[a-zA-Z]+(?:-[a-zA-Z]+)*\]\s*(.*?)"
    r"(?:\[/(?:speech|text)\])?$",
    re.IGNORECASE | re.DOTALL,
)
_ANIMA_HYBRID_OUTPUT = re.compile(
    r"\A\s*ANIMA_NATURAL:\s*(.*?)\s*ANIMA_NEGATIVE:\s*(.*?)\s*\Z",
    re.IGNORECASE | re.DOTALL,
)


class TransformationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RenderResult:
    positive: str
    negative: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RendererAnalysis:
    literals: tuple[LiteralContent, ...]
    input_mode: str | None = None


@dataclass(frozen=True, slots=True)
class RendererContext:
    task: str
    processing: str
    output_language: str
    variant_id: str
    profile_instructions: str = ""
    duration: int = 10
    camera: str = "Free"
    shot: str = "Single continuous shot"
    motion: str = "Natural"
    environmental_audio: bool = True
    dialogue: bool = True
    background_music: bool = False
    start_frame_note: str = ""
    end_frame_note: str = ""
    references: tuple[str, ...] = ()
    auto_quality_tags: bool = True


class Renderer(Protocol):
    renderer_id: str

    def prompt_style_description(self, processing: str, locale_id: str) -> str: ...

    def analyze_request(self, request: str) -> RendererAnalysis: ...

    def system_instructions(
        self,
        context: RendererContext,
        analysis: RendererAnalysis,
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> str: ...

    def llm_output_instruction(self, output_language: str) -> str: ...

    def request_payload_overrides(
        self, analysis: RendererAnalysis | None = None
    ) -> dict[str, object]: ...

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        *,
        input_mode: str | None = None,
        source_request: str | None = None,
        auto_quality_tags: bool = True,
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


def _preservation_requirements(
    literals: tuple[LiteralContent, ...],
    protected_terms: tuple[ProtectedTerm, ...],
) -> str:
    lines = ["EXACT PRESERVATION REQUIREMENTS:"]
    if literals:
        lines.append("Copy each Literal Content body exactly; never copy its marker:")
        lines.extend(f"- {item.kind}:{item.language}: {item.text}" for item in literals)
    if protected_terms:
        lines.append("Copy each Protected Term exactly without splitting or normalization:")
        lines.extend(f"- {item.text}" for item in protected_terms)
    if len(lines) == 1:
        lines.append("No Literal Content or Protected Terms were detected.")
    return "\n".join(lines)


def _profile_configuration(context: RendererContext) -> str:
    if not context.profile_instructions.strip():
        return ""
    return f"PROFILE CONFIGURATION:\n{context.profile_instructions.strip()}"


def _literal_language(text: str) -> str:
    return "ja" if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text) else "und"


def _validate_preservation(
    positive: str,
    literals: tuple[LiteralContent, ...],
    protected_terms: tuple[ProtectedTerm, ...],
) -> None:
    if missing_literal_contents(positive, literals):
        raise TransformationError(LITERAL_CONTENT_NOT_PRESERVED)
    if missing_protected_terms(positive, protected_terms):
        raise TransformationError(PROTECTED_TERM_NOT_PRESERVED)


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


class MiniMaxH3Renderer:
    renderer_id = "minimax_h3"

    @staticmethod
    def _validate_no_unrequested_semantic_tags(
        output: str,
        source_request: str | None,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> None:
        requested = "\n".join(
            (
                source_request or "",
                *(item.text for item in literals),
                *(item.text for item in protected_terms),
            )
        ).casefold().replace("_", " ")
        aliases: list[str] = []
        if "安全" in requested:
            aliases.append("safe")
        if "センシティブ" in requested:
            aliases.append("sensitive")
        if "成人向け" in requested:
            aliases.extend(("nsfw", "explicit"))
        aliases.extend(
            f"{match.group(1)}s"
            for match in re.finditer(r"((?:18|19|20)\d0)年代", requested)
        )
        requested += "\n" + "\n".join(aliases)
        restricted = (
            re.compile(r"\b(?:safe|sensitive|nsfw|explicit)\b", re.IGNORECASE),
            re.compile(r"\bscore_\d+\b", re.IGNORECASE),
            re.compile(
                r"\b(?:artist|character|copyright|series)\s*:\s*[^,.;\n]+",
                re.IGNORECASE,
            ),
            re.compile(r"(?:^|,\s*)by\s+[^,.;\n]+(?=,|[.;]|$)", re.IGNORECASE),
            re.compile(
                r"\b(?:art|artwork|painting|illustration|image|style)\s+by\s+[^,.;\n]+",
                re.IGNORECASE,
            ),
            re.compile(r"(?<!\w)@[\w -]+", re.IGNORECASE),
            re.compile(r"\b(?:(?:18|19|20)\d{2}|(?:18|19|20)\d0s|retro|vintage)\b", re.IGNORECASE),
            re.compile(
                r"(?:^|,\s*)(?:1girls?|1boys?|1others?|girls?|boys?|women|woman|"
                r"men|man|female|male|child|children|teens?|teenagers?|adults?|"
                r"loli|shota|(?:young|old|elderly|teenage|adult|middle[- ]aged)\s+"
                r"(?:girl|boy|woman|man|person|people|character|subject)s?)(?=,|[.;]|$)",
                re.IGNORECASE,
            ),
        )
        for pattern in restricted:
            for match in pattern.finditer(output):
                candidate = match.group(0).strip(" ,.;").casefold().replace("_", " ")
                forms = {candidate}
                if ":" in candidate:
                    forms.add(candidate.split(":", 1)[1].strip())
                by_match = re.search(r"\bby\s+(.+)$", candidate)
                if by_match is not None:
                    forms.add(by_match.group(1).strip())
                if candidate.startswith("@"):
                    forms.add(candidate[1:].strip())
                if not any(
                    form
                    and re.search(rf"(?<!\w){re.escape(form)}(?!\w)", requested)
                    for form in forms
                ):
                    raise TransformationError(UNREQUESTED_SEMANTIC_TAG)

    @staticmethod
    def _fixed_quality_components(
        variant: ProfileVariant,
        *,
        enabled: bool = True,
        generated: str = "",
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not enabled:
            return (), ()
        quality = re.compile(
            r"^(?:masterpiece|best quality|high quality)$",
            re.IGNORECASE,
        )
        seen: set[str] = set()

        def unique_quality(values: tuple[str, ...]) -> tuple[str, ...]:
            result: list[str] = []
            for value in values:
                key = value.strip().casefold()
                if (
                    not quality.fullmatch(value.strip())
                    or key in seen
                    or re.search(rf"(?<!\w){re.escape(key)}(?!\w)", generated, re.IGNORECASE)
                ):
                    continue
                seen.add(key)
                result.append(value)
            return tuple(result)

        return (
            unique_quality(
                (*variant.required_prompt.positive_prefix, *variant.recommended_prompt.positive_prefix)
            ),
            unique_quality(
                (*variant.recommended_prompt.positive_suffix, *variant.required_prompt.positive_suffix)
            ),
        )

    def prompt_style_description(self, processing: str, locale_id: str) -> str:
        descriptions = (
            {
                "Faithful": "入力の動作順・カメラ・台詞を最優先し、未指定の演出を極力追加しません。",
                "Balanced": "入力を維持し、H3に伝わりやすい動き・時間・音・カメラの補足を適度に加えます。",
                "Creative": "入力の中心を維持し、映像演出・カメラ・動き・雰囲気を積極的に補います。",
            }
            if locale_id == "ja-JP"
            else {
                "Faithful": "Prioritizes the requested action order, camera, and speech, adding almost no unspecified direction.",
                "Balanced": "Preserves the request while adding restrained H3-friendly motion, timing, sound, and camera detail.",
                "Creative": "Preserves the core request while actively enriching cinematic direction, camera, motion, and atmosphere.",
            }
        )
        return descriptions.get(processing, "")

    def analyze_request(self, request: str) -> RendererAnalysis:
        literals = list(parse_literal_content(request))
        speech_context = re.compile(
            r"(?:言う|話す|叫ぶ|囁く|尋ねる|答える|語る|歌う|つぶやく|台詞|セリフ|会話|"
            r"\b(?:say|says|said|speak|speaks|shout|whisper|ask|reply|sing|dialogue|speech)\b)",
            re.IGNORECASE,
        )
        text_context = re.compile(
            r"(?:看板|標識|サイン|文字|書かれ|表示|ラベル|字幕|タイトル|ポスター|メニュー|店名|"
            r"\b(?:sign|written|reads|label|caption|subtitle|title|poster|menu|typography)\b)",
            re.IGNORECASE,
        )
        for candidate in quoted_content_candidates(request):
            if text_context.search(candidate.line):
                kind = "text"
            elif speech_context.search(candidate.line):
                kind = "speech"
            else:
                continue
            literals.append(
                LiteralContent(
                    kind,
                    _literal_language(candidate.text),
                    candidate.text,
                    candidate.line_number,
                    "quote",
                )
            )
        return RendererAnalysis(tuple(literals))

    def system_instructions(
        self,
        context: RendererContext,
        analysis: RendererAnalysis,
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> str:
        processing = {
            "Faithful": "Preserve every action and its order. Add no unspecified action or major detail.",
            "Balanced": "Preserve the request; add only restrained continuity, timing, natural motion, ambience, or camera clarity.",
            "Creative": "Preserve the central intent while adding useful cinematic direction and natural visual detail.",
        }[context.processing]
        quality_policy = (
            "AUTOMATIC QUALITY TAGS: ON. The renderer adds only its configured quality "
            "components after generation. Preserve user-supplied quality tags, but do not invent extras."
            if context.auto_quality_tags
            else "AUTOMATIC QUALITY TAGS: OFF. Do not add or invent quality tags. Preserve any "
            "quality tags explicitly supplied by the user."
        )
        audio = ", ".join(
            label
            for label, enabled in (
                ("environmental / scene audio", context.environmental_audio),
                ("dialogue", context.dialogue),
                ("background music", context.background_music),
            )
            if enabled
        ) or "none selected"
        controls = [
            "H3 UI SETTINGS (do not override explicit user intent):",
            f"Mode: {context.task}",
            f"Duration: {context.duration} seconds",
            f"Prompt Processing: {context.processing}",
            f"Processing rule: {processing}",
            f"Camera: {context.camera}",
            f"Shot: {context.shot}",
            f"Motion: {context.motion}",
            f"Audio: {audio}",
        ]
        if context.task in {"I2VA", "FL2VA"} and context.start_frame_note:
            controls.append(f"Start image note: {context.start_frame_note}")
        if context.task in {"FL2VA", "L2VA"} and context.end_frame_note:
            controls.append(f"End image note: {context.end_frame_note}")
        if context.task == "Ref2VA" and context.references:
            controls.append("References (files are not analyzed or sent):")
            controls.extend(context.references)
        return "\n\n".join(
            (
                """CORE TRANSFORMATION POLICY — MINIMAX H3 RENDERER POLICY (highest priority):
Transform the user request into one finished MiniMax H3 prompt. Preserve semantic intent, action order, camera directions, relationships, timing, Literal Content, and Protected Terms. The installed official MiniMax H3 Skill is authoritative for H3 model syntax. Preserve the existing T2VA, I2VA, FL2VA, L2VA, and Ref2VA task meanings. Produce a natural-language English video narrative with one Positive prompt and no Negative prompt. Preserve exact dialogue in its original language. Interpret explicitly marked speech/text before contextual quote inference; spoken quotes belong to dialogue, while signs/labels are visible text. Quality metadata must follow the AUTOMATIC QUALITY TAGS setting below. Never add unrequested rating/safety, score/ranking, artist/byline, age/demographic, copyright/character, or year/era metadata in any processing mode. Preserve those categories only when the user explicitly supplied them. Never alter meaning merely to approach a length target. Return no analysis, preface, Markdown, or marker syntax.""",
                _profile_configuration(context),
                "\n".join(controls),
                quality_policy,
                _preservation_requirements(analysis.literals, protected_terms),
                self.llm_output_instruction(context.output_language),
            )
        )

    def llm_output_instruction(self, output_language: str) -> str:
        return (
            "OUTPUT FORMAT: Return only one complete MiniMax H3 natural-language video prompt in "
            f"{output_language}. Exact Literal Content and Protected Terms are the only language "
            "exceptions. Remove [speech:*] and [text:*] markers while preserving their bodies exactly."
        )

    def request_payload_overrides(
        self, analysis: RendererAnalysis | None = None
    ) -> dict[str, object]:
        return {}

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        *,
        input_mode: str | None = None,
        source_request: str | None = None,
        auto_quality_tags: bool = True,
    ) -> RenderResult:
        body = remove_literal_markers(generated)
        fixed_prefix, fixed_suffix = self._fixed_quality_components(
            variant,
            enabled=auto_quality_tags,
            generated=body,
        )
        positive = " ".join(
            (
                *fixed_prefix,
                body,
                *fixed_suffix,
            )
        ).strip()
        self._validate_no_unrequested_semantic_tags(
            positive,
            source_request,
            literals,
            protected_terms,
        )
        _validate_preservation(positive, literals, protected_terms)
        return RenderResult(positive, None, length_warnings(positive, variant.length_guidance))


class Wan22Renderer:
    renderer_id = "wan_2_2"

    @staticmethod
    def _validate_no_unrequested_semantic_tags(
        output: str,
        source_request: str | None,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> None:
        requested = "\n".join(
            (
                source_request or "",
                *(item.text for item in literals),
                *(item.text for item in protected_terms),
            )
        ).casefold().replace("_", " ")
        aliases: list[str] = []
        if "安全" in requested:
            aliases.append("safe")
        if "センシティブ" in requested:
            aliases.append("sensitive")
        if "成人向け" in requested:
            aliases.extend(("nsfw", "explicit"))
        aliases.extend(
            f"{match.group(1)}s"
            for match in re.finditer(r"((?:18|19|20)\d0)年代", requested)
        )
        requested += "\n" + "\n".join(aliases)
        restricted = (
            re.compile(r"\b(?:safe|sensitive|nsfw|explicit)\b", re.IGNORECASE),
            re.compile(r"\bscore_\d+\b", re.IGNORECASE),
            re.compile(r"\b(?:artist|character|copyright|series)\s*:\s*[^,.;\n]+", re.IGNORECASE),
            re.compile(r"(?:^|,\s*)by\s+[^,.;\n]+(?=,|[.;]|$)", re.IGNORECASE),
            re.compile(r"\b(?:art|artwork|painting|illustration|image|style)\s+by\s+[^,.;\n]+", re.IGNORECASE),
            re.compile(r"(?<!\w)@[\w -]+", re.IGNORECASE),
            re.compile(r"\b(?:(?:18|19|20)\d{2}|(?:18|19|20)\d0s|retro|vintage)\b", re.IGNORECASE),
            re.compile(
                r"(?:^|,\s*)(?:1girls?|1boys?|1others?|girls?|boys?|women|woman|"
                r"men|man|female|male|child|children|teens?|teenagers?|adults?|"
                r"loli|shota|(?:young|old|elderly|teenage|adult|middle[- ]aged)\s+"
                r"(?:girl|boy|woman|man|person|people|character|subject)s?)(?=,|[.;]|$)",
                re.IGNORECASE,
            ),
        )
        for pattern in restricted:
            for match in pattern.finditer(output):
                candidate = match.group(0).strip(" ,.;").casefold().replace("_", " ")
                forms = {candidate}
                if ":" in candidate:
                    forms.add(candidate.split(":", 1)[1].strip())
                by_match = re.search(r"\bby\s+(.+)$", candidate)
                if by_match is not None:
                    forms.add(by_match.group(1).strip())
                if candidate.startswith("@"):
                    forms.add(candidate[1:].strip())
                if not any(
                    form
                    and re.search(rf"(?<!\w){re.escape(form)}(?!\w)", requested)
                    for form in forms
                ):
                    raise TransformationError(UNREQUESTED_SEMANTIC_TAG)

    @staticmethod
    def _fixed_quality_components(
        variant: ProfileVariant,
        *,
        enabled: bool = True,
        generated: str = "",
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not enabled:
            return (), ()
        quality = re.compile(
            r"^(?:masterpiece|best quality|high quality)$",
            re.IGNORECASE,
        )
        seen: set[str] = set()

        def unique_quality(values: tuple[str, ...]) -> tuple[str, ...]:
            result: list[str] = []
            for value in values:
                key = value.strip().casefold()
                if (
                    not quality.fullmatch(value.strip())
                    or key in seen
                    or re.search(rf"(?<!\w){re.escape(key)}(?!\w)", generated, re.IGNORECASE)
                ):
                    continue
                seen.add(key)
                result.append(value)
            return tuple(result)

        return (
            unique_quality(
                (*variant.required_prompt.positive_prefix, *variant.recommended_prompt.positive_prefix)
            ),
            unique_quality(
                (*variant.recommended_prompt.positive_suffix, *variant.required_prompt.positive_suffix)
            ),
        )

    def prompt_style_description(self, processing: str, locale_id: str) -> str:
        descriptions = (
            {
                "Faithful": "入力の被写体・動作・順序・カメラを優先し、Wan向けの未指定要素を追加しません。",
                "Balanced": "入力を維持し、Wan向けの動き・構図・照明・環境描写を適度に補います。",
                "Creative": "入力を維持しつつ、Wan向けの映像表現・動き・照明・雰囲気を積極的に補います。",
            }
            if locale_id == "ja-JP"
            else {
                "Faithful": "Prioritizes the requested subject, action, order, and camera without adding unspecified Wan scene elements.",
                "Balanced": "Preserves the request while adding restrained Wan motion, framing, lighting, and environment detail.",
                "Creative": "Preserves the request while actively enriching Wan cinematography, motion, lighting, and atmosphere.",
            }
        )
        return descriptions.get(processing, "")

    def analyze_request(self, request: str) -> RendererAnalysis:
        literals = list(parse_literal_content(request))
        speech_context = re.compile(
            r"(?:言う|話す|叫ぶ|囁く|尋ねる|答える|歌う|台詞|セリフ|会話|"
            r"\b(?:say|says|said|speak|shout|whisper|ask|reply|sing|dialogue|speech)\b)",
            re.IGNORECASE,
        )
        text_context = re.compile(
            r"(?:看板|標識|サイン|文字|書かれ|表示|ラベル|字幕|タイトル|ポスター|メニュー|店名|"
            r"\b(?:sign|written|reads|label|caption|subtitle|title|poster|menu|typography)\b)",
            re.IGNORECASE,
        )
        for candidate in quoted_content_candidates(request):
            if text_context.search(candidate.line):
                kind = "text"
            elif speech_context.search(candidate.line):
                kind = "speech"
            else:
                continue
            literals.append(
                LiteralContent(kind, _literal_language(candidate.text), candidate.text, candidate.line_number, "quote")
            )
        return RendererAnalysis(tuple(literals))

    def system_instructions(
        self,
        context: RendererContext,
        analysis: RendererAnalysis,
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> str:
        processing = {
            "Faithful": "Reorganize only; invent no subject traits, actions, style, camera motion, or major scene details.",
            "Balanced": "Add only restrained motion, continuity, framing, lighting, or environmental detail that supports the request.",
            "Creative": "Add useful visual and cinematic detail without replacing or contradicting any explicit content.",
        }[context.processing]
        quality_policy = (
            "AUTOMATIC QUALITY TAGS: ON. The renderer adds only its configured quality "
            "components after generation. Preserve user-supplied quality tags, but do not invent extras."
            if context.auto_quality_tags
            else "AUTOMATIC QUALITY TAGS: OFF. Do not add or invent quality tags. Preserve any "
            "quality tags explicitly supplied by the user."
        )
        task = (
            "T2V: prioritize subject and observable action; 60-200 English words is soft official extension guidance only."
            if context.task == "T2V"
            else "I2V: focus on change after the source image, motion, expression, objects, and camera; do not claim unseen image facts; 100 words or fewer is soft guidance only."
        )
        return "\n\n".join(
            (
                """WAN 2.2 RENDERER POLICY (highest priority):
Produce one clean natural-language English video prompt for A14B T2V/I2V. Preserve subjects, actions, order, constraints, camera, style, Literal Content, and Protected Terms. Use concrete observable description and never force a photographic style over an explicit medium. Wan A14B is treated as video-only: do not invent audio and do not promise audible speech. Explicit speech/text markers override contextual quote inference; distinguish spoken quotes from visible signs/labels. Keep the surrounding prompt English and exact literal bodies in their original language. Quality metadata must follow the AUTOMATIC QUALITY TAGS setting below. Never add unrequested rating/safety, score/ranking, artist/byline, age/demographic, copyright/character, or year/era metadata in any processing mode. Preserve those categories only when explicitly supplied by the user. Never pad, shorten, or rewrite meaning to meet a length suggestion. One Positive prompt only; no Negative prompt, headings, JSON, or Markdown.""",
                _profile_configuration(context),
                f"Task: {task}\nPrompt Processing: {context.processing}\nProcessing rule: {processing}\nVariant: {context.variant_id} (A14B rules)",
                quality_policy,
                _preservation_requirements(analysis.literals, protected_terms),
                self.llm_output_instruction(context.output_language),
            )
        )

    def llm_output_instruction(self, output_language: str) -> str:
        return (
            "OUTPUT FORMAT: Return only one natural-language paragraph in "
            f"{output_language}. Every non-literal concept must use that language. Remove directive "
            "markers and copy only their exact bodies."
        )

    def request_payload_overrides(
        self, analysis: RendererAnalysis | None = None
    ) -> dict[str, object]:
        return {}

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        *,
        input_mode: str | None = None,
        source_request: str | None = None,
        auto_quality_tags: bool = True,
    ) -> RenderResult:
        body = remove_literal_markers(generated)
        fixed_prefix, fixed_suffix = self._fixed_quality_components(
            variant,
            enabled=auto_quality_tags,
            generated=body,
        )
        positive = " ".join(
            (
                *fixed_prefix,
                body,
                *fixed_suffix,
            )
        ).strip()
        self._validate_no_unrequested_semantic_tags(
            positive,
            source_request,
            literals,
            protected_terms,
        )
        _validate_preservation(positive, literals, protected_terms)
        return RenderResult(positive, None, length_warnings(positive, variant.length_guidance))


class LTX23Renderer:
    renderer_id = "ltx_2_3"

    @staticmethod
    def _validate_no_unrequested_semantic_tags(
        output: str,
        source_request: str | None,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> None:
        requested = "\n".join(
            (
                source_request or "",
                *(item.text for item in literals),
                *(item.text for item in protected_terms),
            )
        ).casefold().replace("_", " ")
        aliases: list[str] = []
        if "安全" in requested:
            aliases.append("safe")
        if "センシティブ" in requested:
            aliases.append("sensitive")
        if "成人向け" in requested:
            aliases.extend(("nsfw", "explicit"))
        aliases.extend(
            f"{match.group(1)}s"
            for match in re.finditer(r"((?:18|19|20)\d0)年代", requested)
        )
        requested += "\n" + "\n".join(aliases)
        restricted = (
            re.compile(r"\b(?:safe|sensitive|nsfw|explicit)\b", re.IGNORECASE),
            re.compile(r"\bscore_\d+\b", re.IGNORECASE),
            re.compile(r"\b(?:artist|character|copyright|series)\s*:\s*[^,.;\n]+", re.IGNORECASE),
            re.compile(r"(?:^|,\s*)by\s+[^,.;\n]+(?=,|[.;]|$)", re.IGNORECASE),
            re.compile(r"\b(?:art|artwork|painting|illustration|image|style)\s+by\s+[^,.;\n]+", re.IGNORECASE),
            re.compile(r"(?<!\w)@[\w -]+", re.IGNORECASE),
            re.compile(r"\b(?:(?:18|19|20)\d{2}|(?:18|19|20)\d0s|retro|vintage)\b", re.IGNORECASE),
            re.compile(
                r"(?:^|,\s*)(?:1girls?|1boys?|1others?|girls?|boys?|women|woman|"
                r"men|man|female|male|child|children|teens?|teenagers?|adults?|"
                r"loli|shota|(?:young|old|elderly|teenage|adult|middle[- ]aged)\s+"
                r"(?:girl|boy|woman|man|person|people|character|subject)s?)(?=,|[.;]|$)",
                re.IGNORECASE,
            ),
        )
        for pattern in restricted:
            for match in pattern.finditer(output):
                candidate = match.group(0).strip(" ,.;").casefold().replace("_", " ")
                forms = {candidate}
                if ":" in candidate:
                    forms.add(candidate.split(":", 1)[1].strip())
                by_match = re.search(r"\bby\s+(.+)$", candidate)
                if by_match is not None:
                    forms.add(by_match.group(1).strip())
                if candidate.startswith("@"):
                    forms.add(candidate[1:].strip())
                if not any(
                    form
                    and re.search(rf"(?<!\w){re.escape(form)}(?!\w)", requested)
                    for form in forms
                ):
                    raise TransformationError(UNREQUESTED_SEMANTIC_TAG)

    @staticmethod
    def _fixed_quality_components(
        variant: ProfileVariant,
        *,
        enabled: bool = True,
        generated: str = "",
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not enabled:
            return (), ()
        quality = re.compile(
            r"^(?:masterpiece|best quality|high quality)$",
            re.IGNORECASE,
        )
        seen: set[str] = set()

        def unique_quality(values: tuple[str, ...]) -> tuple[str, ...]:
            result: list[str] = []
            for value in values:
                key = value.strip().casefold()
                if (
                    not quality.fullmatch(value.strip())
                    or key in seen
                    or re.search(rf"(?<!\w){re.escape(key)}(?!\w)", generated, re.IGNORECASE)
                ):
                    continue
                seen.add(key)
                result.append(value)
            return tuple(result)

        return (
            unique_quality(
                (*variant.required_prompt.positive_prefix, *variant.recommended_prompt.positive_prefix)
            ),
            unique_quality(
                (*variant.recommended_prompt.positive_suffix, *variant.required_prompt.positive_suffix)
            ),
        )

    def prompt_style_description(self, processing: str, locale_id: str) -> str:
        descriptions = (
            {
                "Faithful": "入力の時系列・動作・カメラ・音声を優先し、未指定の意味要素を追加しません。",
                "Balanced": "入力を維持し、LTX向けの連続性・照明・カメラ・同期音声を適度に補います。",
                "Creative": "入力を維持しつつ、LTX向けの環境・動き・カメラ・音響を積極的に補います。",
            }
            if locale_id == "ja-JP"
            else {
                "Faithful": "Prioritizes the requested chronology, action, camera, and audio without adding missing semantic detail.",
                "Balanced": "Preserves the request while adding restrained LTX continuity, lighting, camera, and synchronized audio detail.",
                "Creative": "Preserves the request while actively enriching LTX environment, motion, camera, and sound direction.",
            }
        )
        return descriptions.get(processing, "")

    def analyze_request(self, request: str) -> RendererAnalysis:
        literals = list(parse_literal_content(request))
        speech_context = re.compile(
            r"(?:言う|話す|叫ぶ|囁く|尋ねる|答える|語る|歌う|つぶやく|台詞|セリフ|会話|"
            r"\b(?:say|says|said|speak|shout|whisper|ask|reply|sing|dialogue|speech)\b)",
            re.IGNORECASE,
        )
        text_context = re.compile(
            r"(?:看板|標識|サイン|文字|書かれ|表示|ラベル|字幕|タイトル|ポスター|メニュー|店名|"
            r"\b(?:sign|written|reads|label|caption|subtitle|title|poster|menu|typography)\b)",
            re.IGNORECASE,
        )
        for candidate in quoted_content_candidates(request):
            if text_context.search(candidate.line):
                kind = "text"
            elif speech_context.search(candidate.line):
                kind = "speech"
            else:
                continue
            literals.append(
                LiteralContent(kind, _literal_language(candidate.text), candidate.text, candidate.line_number, "quote")
            )
        return RendererAnalysis(tuple(literals))

    def system_instructions(
        self,
        context: RendererContext,
        analysis: RendererAnalysis,
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> str:
        processing = {
            "Faithful": "Reorganize and clarify only; invent no missing semantic detail.",
            "Balanced": "Add restrained visual, motion, continuity, lighting, or audio detail that directly supports the scene.",
            "Creative": "Add useful environment, lighting, motion, camera, or sound detail without replacing explicit intent.",
        }[context.processing]
        quality_policy = (
            "AUTOMATIC QUALITY TAGS: ON. The renderer adds only its configured quality "
            "components after generation. Preserve user-supplied quality tags, but do not invent extras."
            if context.auto_quality_tags
            else "AUTOMATIC QUALITY TAGS: OFF. Do not add or invent quality tags. Preserve any "
            "quality tags explicitly supplied by the user."
        )
        task = (
            "T2V: begin with the main action/setup and proceed chronologically."
            if context.task == "T2V"
            else "I2V: treat the source image as the first frame, describe only subsequent changes, and never invent unseen image facts."
        )
        return "\n\n".join(
            (
                """LTX-2.3 RENDERER POLICY (highest priority):
Produce one detailed natural-language English joint audio-video prompt for LTX-2.3. Keep a chronological flow and describe observable action, camera, environment, lighting/colors, and synchronized audio/dialogue where requested. Do not invent dialogue, camera movement, timestamps, cuts, or non-visual/non-auditory sensations. Preserve exact dialogue and state its spoken language when useful. Explicit speech/text markers override contextual quote inference; distinguish speech from signs/labels. Preserve Literal Content and Protected Terms exactly and remove marker syntax. Quality metadata must follow the AUTOMATIC QUALITY TAGS setting below. Never add unrequested rating/safety, score/ranking, artist/byline, age/demographic, copyright/character, or year/era metadata in any processing mode. Preserve those categories only when explicitly supplied by the user. The official 200-word guidance is soft only: never alter, omit, or invent meaning to fit it. One Positive prompt only; no Negative prompt, heading, list, or Markdown. Dev and Distilled variants share prompt semantics; inference settings are not emitted into the prompt.""",
                _profile_configuration(context),
                f"Task: {task}\nPrompt Processing: {context.processing}\nProcessing rule: {processing}\nVariant: {context.variant_id}",
                quality_policy,
                _preservation_requirements(analysis.literals, protected_terms),
                self.llm_output_instruction(context.output_language),
            )
        )

    def llm_output_instruction(self, output_language: str) -> str:
        return (
            "OUTPUT FORMAT: Return only one continuous detailed paragraph in "
            f"{output_language}. Keep exact Literal Content/Protected Terms as the only language "
            "exceptions and remove all directive markers."
        )

    def request_payload_overrides(
        self, analysis: RendererAnalysis | None = None
    ) -> dict[str, object]:
        return {}

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        *,
        input_mode: str | None = None,
        source_request: str | None = None,
        auto_quality_tags: bool = True,
    ) -> RenderResult:
        body = remove_literal_markers(generated)
        fixed_prefix, fixed_suffix = self._fixed_quality_components(
            variant,
            enabled=auto_quality_tags,
            generated=body,
        )
        positive = " ".join(
            (
                *fixed_prefix,
                body,
                *fixed_suffix,
            )
        ).strip()
        self._validate_no_unrequested_semantic_tags(
            positive,
            source_request,
            literals,
            protected_terms,
        )
        _validate_preservation(positive, literals, protected_terms)
        return RenderResult(positive, None, length_warnings(positive, variant.length_guidance))


class Krea2Renderer:
    renderer_id = "krea_2"

    @staticmethod
    def _validate_no_unrequested_semantic_tags(
        output: str,
        source_request: str | None,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> None:
        requested = "\n".join(
            (
                source_request or "",
                *(item.text for item in literals),
                *(item.text for item in protected_terms),
            )
        ).casefold().replace("_", " ")
        aliases: list[str] = []
        if "安全" in requested:
            aliases.append("safe")
        if "センシティブ" in requested:
            aliases.append("sensitive")
        if "成人向け" in requested:
            aliases.extend(("nsfw", "explicit"))
        aliases.extend(
            f"{match.group(1)}s"
            for match in re.finditer(r"((?:18|19|20)\d0)年代", requested)
        )
        requested += "\n" + "\n".join(aliases)
        restricted = (
            re.compile(r"\b(?:safe|sensitive|nsfw|explicit)\b", re.IGNORECASE),
            re.compile(r"\bscore_\d+\b", re.IGNORECASE),
            re.compile(r"\b(?:artist|character|copyright|series)\s*:\s*[^,.;\n]+", re.IGNORECASE),
            re.compile(r"(?:^|,\s*)by\s+[^,.;\n]+(?=,|[.;]|$)", re.IGNORECASE),
            re.compile(r"\b(?:art|artwork|painting|illustration|image|style)\s+by\s+[^,.;\n]+", re.IGNORECASE),
            re.compile(r"(?<!\w)@[\w -]+", re.IGNORECASE),
            re.compile(r"\b(?:(?:18|19|20)\d{2}|(?:18|19|20)\d0s|retro|vintage)\b", re.IGNORECASE),
            re.compile(
                r"(?:^|,\s*)(?:1girls?|1boys?|1others?|girls?|boys?|women|woman|"
                r"men|man|female|male|child|children|teens?|teenagers?|adults?|"
                r"loli|shota|(?:young|old|elderly|teenage|adult|middle[- ]aged)\s+"
                r"(?:girl|boy|woman|man|person|people|character|subject)s?)(?=,|[.;]|$)",
                re.IGNORECASE,
            ),
        )
        for pattern in restricted:
            for match in pattern.finditer(output):
                candidate = match.group(0).strip(" ,.;").casefold().replace("_", " ")
                forms = {candidate}
                if ":" in candidate:
                    forms.add(candidate.split(":", 1)[1].strip())
                by_match = re.search(r"\bby\s+(.+)$", candidate)
                if by_match is not None:
                    forms.add(by_match.group(1).strip())
                if candidate.startswith("@"):
                    forms.add(candidate[1:].strip())
                if not any(
                    form
                    and re.search(rf"(?<!\w){re.escape(form)}(?!\w)", requested)
                    for form in forms
                ):
                    raise TransformationError(UNREQUESTED_SEMANTIC_TAG)

    @staticmethod
    def _fixed_quality_components(
        variant: ProfileVariant,
        *,
        enabled: bool = True,
        generated: str = "",
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not enabled:
            return (), ()
        quality = re.compile(
            r"^(?:masterpiece|best quality|high quality)$",
            re.IGNORECASE,
        )
        seen: set[str] = set()

        def unique_quality(values: tuple[str, ...]) -> tuple[str, ...]:
            result: list[str] = []
            for value in values:
                key = value.strip().casefold()
                if (
                    not quality.fullmatch(value.strip())
                    or key in seen
                    or re.search(rf"(?<!\w){re.escape(key)}(?!\w)", generated, re.IGNORECASE)
                ):
                    continue
                seen.add(key)
                result.append(value)
            return tuple(result)

        return (
            unique_quality(
                (*variant.required_prompt.positive_prefix, *variant.recommended_prompt.positive_prefix)
            ),
            unique_quality(
                (*variant.recommended_prompt.positive_suffix, *variant.required_prompt.positive_suffix)
            ),
        )

    def prompt_style_description(self, processing: str, locale_id: str) -> str:
        descriptions = (
            {
                "Faithful": "入力の自然言語を軽く整えるだけで、未指定の物・特徴・設定を追加しません。",
                "Balanced": "入力を維持し、Krea向けの構図・画角・照明・質感を適度に補います。",
                "Creative": "入力を維持しつつ、Krea向けの構図・照明・雰囲気・表現を積極的に補います。",
            }
            if locale_id == "ja-JP"
            else {
                "Faithful": "Lightly polishes the natural-language request without adding unspecified objects, traits, or setting facts.",
                "Balanced": "Preserves the request while adding restrained Krea composition, framing, lighting, and texture detail.",
                "Creative": "Preserves the request while actively enriching Krea composition, lighting, atmosphere, and presentation.",
            }
        )
        return descriptions.get(processing, "")

    def analyze_request(self, request: str) -> RendererAnalysis:
        literals = list(parse_literal_content(request))
        speech_context = re.compile(
            r"(?:言う|話す|叫ぶ|囁く|尋ねる|答える|台詞|セリフ|会話|吹き出し|"
            r"\b(?:say|says|said|speak|shout|whisper|ask|reply|dialogue|speech|speech bubble)\b)",
            re.IGNORECASE,
        )
        text_context = re.compile(
            r"(?:看板|標識|サイン|文字|書かれ|表示|ラベル|字幕|タイトル|ポスター|メニュー|店名|"
            r"\b(?:sign|written|reads|label|caption|subtitle|title|poster|menu|typography)\b)",
            re.IGNORECASE,
        )
        for candidate in quoted_content_candidates(request):
            if text_context.search(candidate.line):
                kind = "text"
            elif speech_context.search(candidate.line):
                kind = "speech"
            else:
                continue
            literals.append(
                LiteralContent(kind, _literal_language(candidate.text), candidate.text, candidate.line_number, "quote")
            )
        return RendererAnalysis(tuple(literals), "natural")

    def system_instructions(
        self,
        context: RendererContext,
        analysis: RendererAnalysis,
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> str:
        processing = {
            "Faithful": "Lightly polish and reorganize only; do not add objects, props, traits, colors, materials, locations, or story facts.",
            "Balanced": "Add a small amount of composition, framing, lighting, texture, or atmosphere without changing the scene.",
            "Creative": "Enrich composition, lighting, atmosphere, texture, and presentation while preserving every subject, action, relationship, medium, and constraint.",
        }[context.processing]
        quality_policy = (
            "AUTOMATIC QUALITY TAGS: ON. The renderer adds only its configured quality "
            "components after generation. Preserve user-supplied quality tags, but do not invent extras."
            if context.auto_quality_tags
            else "AUTOMATIC QUALITY TAGS: OFF. Do not add or invent quality tags. Preserve any "
            "quality tags explicitly supplied by the user."
        )
        return "\n\n".join(
            (
                """KREA 2 RENDERER POLICY (highest priority):
Produce one cohesive English natural-language image prompt, never Danbooru tags, quality-tag lists, JSON, or a Negative prompt. Preserve subjects, actions, colors, spatial relationships, medium, clothing categories, and constraints. Group each subject with its attributes/action. Japanese ワンピース used as clothing means a dress unless swimwear is explicit; a beach alone never changes it. Explicit speech/text markers override quote-context inference. Distinguish spoken words from visible signs/labels; place requested visible text in quotation marks and preserve its exact body. Preserve Protected Terms exactly. Quality metadata must follow the AUTOMATIC QUALITY TAGS setting below. Never add unrequested rating/safety, score/ranking, artist/byline, age/demographic, copyright/character, or year/era metadata in any processing mode. Preserve those categories only when explicitly supplied by the user. Never add, delete, compress, or rewrite meaning to reach a length. Krea Raw and Turbo use the same natural-language prompt contract; never emit inference parameters. Return no analysis, alternatives, headings, or Markdown.""",
                _profile_configuration(context),
                f"Task: T2I\nPrompt Processing: {context.processing}\nProcessing rule: {processing}\nVariant: {context.variant_id}",
                quality_policy,
                _preservation_requirements(analysis.literals, protected_terms),
                self.llm_output_instruction(context.output_language),
            )
        )

    def llm_output_instruction(self, output_language: str) -> str:
        return (
            "OUTPUT FORMAT: Return only one natural-language image paragraph in "
            f"{output_language}. Never emit a tag list or Negative prompt. Remove literal directive "
            "markers while copying exact bodies and Protected Terms unchanged."
        )

    def request_payload_overrides(
        self, analysis: RendererAnalysis | None = None
    ) -> dict[str, object]:
        return {}

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        *,
        input_mode: str | None = None,
        source_request: str | None = None,
        auto_quality_tags: bool = True,
    ) -> RenderResult:
        body = remove_literal_markers(generated)
        fixed_prefix, fixed_suffix = self._fixed_quality_components(
            variant,
            enabled=auto_quality_tags,
            generated=body,
        )
        positive = " ".join(
            (
                *fixed_prefix,
                body,
                *fixed_suffix,
            )
        ).strip()
        self._validate_no_unrequested_semantic_tags(
            positive,
            source_request,
            literals,
            protected_terms,
        )
        _validate_preservation(positive, literals, protected_terms)
        return RenderResult(positive, None, length_warnings(positive, variant.length_guidance))


def _normalization_exemptions(
    literals: tuple[LiteralContent, ...],
    protected_terms: tuple[ProtectedTerm, ...],
) -> frozenset[str]:
    return frozenset((*(item.text for item in literals), *(item.text for item in protected_terms)))


def _normalize_anima_tag(value: str, *, artist: bool, exemptions: frozenset[str]) -> str:
    tag = value.strip()
    directive = _LITERAL_DIRECTIVE_TAG.fullmatch(tag)
    if directive is not None and directive.group(1) in exemptions:
        tag = directive.group(1)
    if tag in exemptions:
        return tag
    weighted = _WEIGHTED_TAG.fullmatch(tag)
    if weighted is not None:
        inner = _normalize_anima_tag(weighted.group(1), artist=artist, exemptions=exemptions)
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


class AnimaRenderer:
    renderer_id = "anima"

    @staticmethod
    def _semantic_reference_text(
        source_request: str | None,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> str:
        requested = "\n".join(
            (
                source_request or "",
                *(item.text for item in literals),
                *(item.text for item in protected_terms),
            )
        ).casefold().replace("_", " ")
        aliases: list[str] = []
        if "安全" in requested:
            aliases.append("safe")
        if "センシティブ" in requested:
            aliases.append("sensitive")
        if "成人向け" in requested:
            aliases.extend(("nsfw", "explicit", "adult"))
        if re.search(r"(?:女性|女の人)", requested):
            aliases.extend(("woman", "female"))
        if re.search(r"(?:少女|女の子)", requested):
            aliases.append("girl")
        if re.search(r"(?:男性|男の人)", requested):
            aliases.extend(("man", "male"))
        if re.search(r"(?:少年|男の子)", requested):
            aliases.append("boy")
        if re.search(r"(?:子供|子ども)", requested):
            aliases.extend(("child", "children"))
        if "若い" in requested:
            aliases.append("young")
        if re.search(r"(?:高齢|老人)", requested):
            aliases.extend(("old", "elderly"))
        if "若い" in requested and re.search(r"(?:女性|女の人)", requested):
            aliases.append("young woman")
        if "若い" in requested and re.search(r"(?:男性|男の人)", requested):
            aliases.append("young man")
        aliases.extend(
            f"{match.group(1)}s"
            for match in re.finditer(r"((?:18|19|20)\d0)年代", requested)
        )
        return requested + "\n" + "\n".join(aliases)

    @staticmethod
    def _semantic_term_was_requested(candidate: str, requested: str) -> bool:
        normalized = candidate.strip(" ,.;").casefold().replace("_", " ")
        forms = {normalized}
        if ":" in normalized:
            forms.add(normalized.split(":", 1)[1].strip())
        by_match = re.search(r"\bby\s+(.+)$", normalized)
        if by_match is not None:
            forms.add(by_match.group(1).strip())
        if normalized.startswith("@"):
            forms.add(normalized[1:].strip())
        return any(
            form
            and re.search(rf"(?<!\w){re.escape(form)}(?!\w)", requested)
            for form in forms
        )

    @classmethod
    def _validate_no_unrequested_semantic_tags(
        cls,
        output: str,
        source_request: str | None,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> None:
        requested = cls._semantic_reference_text(
            source_request,
            literals,
            protected_terms,
        )
        restricted = (
            re.compile(r"\b(?:safe|sensitive|nsfw|explicit)\b", re.IGNORECASE),
            re.compile(r"\bscore_\d+\b", re.IGNORECASE),
            re.compile(r"\b(?:artist|character|copyright|series)\s*:\s*[^,.;\n]+", re.IGNORECASE),
            re.compile(r"(?:^|,\s*)by\s+[^,.;\n]+(?=,|[.;]|$)", re.IGNORECASE),
            re.compile(r"\b(?:art|artwork|painting|illustration|image|style)\s+by\s+[^,.;\n]+", re.IGNORECASE),
            re.compile(r"(?<!\w)@[\w -]+", re.IGNORECASE),
            re.compile(r"\b(?:(?:18|19|20)\d{2}|(?:18|19|20)\d0s|retro|vintage)\b", re.IGNORECASE),
            re.compile(
                r"(?:^|,\s*)(?:1girls?|1boys?|1others?|girls?|boys?|women|woman|"
                r"men|man|female|male|child|children|teens?|teenagers?|adults?|"
                r"loli|shota|(?:young|old|elderly|teenage|adult|middle[- ]aged)\s+"
                r"(?:girl|boy|woman|man|person|people|character|subject)s?)(?=,|[.;]|$)",
                re.IGNORECASE,
            ),
        )
        for pattern in restricted:
            for match in pattern.finditer(output):
                if not cls._semantic_term_was_requested(match.group(0), requested):
                    raise TransformationError(UNREQUESTED_SEMANTIC_TAG)

    @classmethod
    def _validate_structured_semantic_sections(
        cls,
        sections: dict[str, tuple[str, ...]],
        source_request: str | None,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> None:
        requested = cls._semantic_reference_text(
            source_request,
            literals,
            protected_terms,
        )
        for section in ("subject_count", "character", "series", "artist"):
            for value in sections[section]:
                if not cls._semantic_term_was_requested(value, requested):
                    raise TransformationError(UNREQUESTED_SEMANTIC_TAG)

    def prompt_style_description(self, processing: str, locale_id: str) -> str:
        descriptions = (
            {
                "Faithful": "Natural/Tag/Hybrid形式と指定内容を優先し、最小限の解釈でAnima Promptを整えます。",
                "Balanced": "入力形式と指定内容を維持し、Anima向けの構図・見せ方を適度に補います。",
                "Creative": "入力形式と指定内容を維持しつつ、Anima向けの照明・雰囲気・背景・画風を積極的に補います。",
            }
            if locale_id == "ja-JP"
            else {
                "Faithful": "Prioritizes the Natural, Tag, or Hybrid form and explicit constraints with minimal Anima interpretation.",
                "Balanced": "Preserves the input form and constraints while adding restrained Anima composition and presentation detail.",
                "Creative": "Preserves the input form and constraints while actively enriching Anima lighting, atmosphere, background, and style.",
            }
        )
        return descriptions.get(processing, "")

    @staticmethod
    def input_mode(request: str) -> str:
        value = without_explicit_literal_content(request).strip()
        tag_marker = bool(
            re.search(
                r"(?:\b(?:[1-9](?:girls?|boys?|others?)|score_\d+|masterpiece|best quality|"
                r"safe|sensitive|nsfw|explicit)\b|@[\w -]+|\([^)]+:\d+(?:\.\d+)?\)|\b\w+_\w+\b)",
                value,
                re.IGNORECASE,
            )
        )
        comma_parts = [part.strip() for part in value.split(",") if part.strip()]
        compact_tag_list = len(comma_parts) >= 3 and all(
            len(part.split()) <= 5 and not re.search(r"[.!?。！？]", part)
            for part in comma_parts
        )
        first_boundary = re.search(r"[.!?。！？](?:\s+|$)", value)
        leading_tag_prefix = False
        if first_boundary is not None:
            leading_parts = [
                part.strip()
                for part in value[: first_boundary.start()].split(",")
                if part.strip()
            ]
            leading_tag_prefix = len(leading_parts) >= 2 and all(
                len(part.split()) <= 5 for part in leading_parts
            )
        natural = bool(
            re.search(r"[.!?。！？]", value)
            or re.search(
                r"\b(?:is|are|wears?|stands?|sits?|walks?|looks?|with|while|under|behind|in front of)\b",
                value,
                re.IGNORECASE,
            )
            or re.search(r"(?:が|は|を|に|で|する|いる|ある|描|立|座|歩)", value)
        )
        tags = tag_marker or compact_tag_list or leading_tag_prefix
        if tags and natural:
            return "hybrid"
        if tags:
            return "tag"
        return "natural"

    def analyze_request(self, request: str) -> RendererAnalysis:
        literals = list(parse_literal_content(request))
        speech_context = re.compile(
            r"(?:言う|話す|叫ぶ|囁く|尋ねる|答える|台詞|セリフ|会話|吹き出し|"
            r"\b(?:say|says|said|speak|shout|whisper|ask|reply|dialogue|speech|speech bubble)\b)",
            re.IGNORECASE,
        )
        text_context = re.compile(
            r"(?:看板|標識|サイン|文字|書かれ|表示|ラベル|字幕|タイトル|ポスター|メニュー|店名|"
            r"\b(?:sign|written|reads|label|caption|subtitle|title|poster|menu|typography)\b)",
            re.IGNORECASE,
        )
        for candidate in quoted_content_candidates(request):
            if text_context.search(candidate.line):
                kind = "text"
            elif speech_context.search(candidate.line):
                kind = "speech"
            else:
                continue
            literals.append(
                LiteralContent(kind, _literal_language(candidate.text), candidate.text, candidate.line_number, "quote")
            )
        return RendererAnalysis(tuple(literals), self.input_mode(request))

    def system_instructions(
        self,
        context: RendererContext,
        analysis: RendererAnalysis,
        protected_terms: tuple[ProtectedTerm, ...],
    ) -> str:
        mode = analysis.input_mode or "tag"
        processing = {
            "Faithful": "Preserve named identities, styles, colors, clothing, poses, composition, relationships, and exclusions with minimal interpretation.",
            "Balanced": "Add only a small number of strongly implied composition or presentation details.",
            "Creative": "Enrich composition, lighting, atmosphere, background, and style without replacing explicit content.",
        }[context.processing]
        quality_policy = (
            "AUTOMATIC QUALITY TAGS: ON. The renderer adds only this variant's configured "
            "quality components after generation. Preserve user-supplied quality tags, but do not invent extras."
            if context.auto_quality_tags
            else "AUTOMATIC QUALITY TAGS: OFF. Do not add or invent quality tags. Preserve any "
            "quality tags explicitly supplied by the user."
        )
        mode_contract = {
            "natural": (
                'Return one valid JSON object with exactly "positive" and "negative" string fields. Write the positive as '
                "English natural-language image prose (prefer at least two useful sentences when the input supports it). "
                "Do not turn it into a tag list. Put only requested exclusions in negative."
            ),
            "tag": (
                "Return one valid JSON object using only tag-section keys quality_meta_year_safety, subject_count, character, "
                "series, artist, general, and negative arrays of strings. Preserve and organize "
                "Danbooru/Gelbooru-style input; ordinary tags are lowercase English with spaces, except score_* tags."
            ),
            "hybrid": (
                "Do not return JSON and do not repeat or rewrite the source tag prefix; the renderer preserves "
                "that prefix deterministically. Return exactly two plain-text sections: ANIMA_NATURAL: followed "
                "by the transformed English natural-language portion, then ANIMA_NEGATIVE: followed by only "
                "user-requested negative tags or an empty value. Do not flatten the natural portion into tags."
            ),
        }[mode]
        return "\n\n".join(
            (
                """ANIMA RENDERER POLICY (highest priority):
Adapt to the detected input form instead of forcing one format: Natural remains English natural language, Tag remains organized Danbooru/Gelbooru-style tags, and Mixed remains Hybrid. Produce separate Positive and Negative prompts. Preserve all explicit constraints, relationships, Literal Content, and Protected Terms. Explicit speech/text markers override contextual quote inference; distinguish spoken content from visible signs/labels. Ordinary tag concepts are English; exact exceptions retain their Unicode and case. Quality metadata must follow the AUTOMATIC QUALITY TAGS setting below. Never add unrequested rating/safety tags, score/ranking tags, artist/byline tags, age/demographic tags, copyright/character tags, or year/era/style-era tags in any mode or processing style. Preserve those categories only when explicitly supplied by the user. Never invent or remove meaning for prompt length. Tag dropout means exhaustive tagging is unnecessary. Follow the selected mode contract exactly and return no Markdown.""",
                _profile_configuration(context),
                f"Detected input mode: {mode.upper()}\nMode contract: {mode_contract}\nPrompt Processing: {context.processing}\nProcessing rule: {processing}\nVariant: {context.variant_id}",
                quality_policy,
                _preservation_requirements(analysis.literals, protected_terms),
                self._mode_output_instruction(
                    context.output_language,
                    mode,
                    context.auto_quality_tags,
                ),
            )
        )

    def llm_output_instruction(self, output_language: str) -> str:
        return self._mode_output_instruction(output_language, "tag")

    @staticmethod
    def _mode_output_instruction(
        output_language: str,
        mode: str,
        auto_quality_tags: bool = True,
    ) -> str:
        if mode == "hybrid":
            quality_assembly = (
                "The renderer adds the unchanged source tag prefix and its configured quality components. "
                if auto_quality_tags
                else "The renderer adds the unchanged source tag prefix without automatic quality components. "
            )
            return (
                "OUTPUT VALIDATION: Return plain text, not JSON, in exactly this form:\n"
                "ANIMA_NATURAL:\n<English natural-language portion>\n"
                "ANIMA_NEGATIVE:\n<comma-separated user exclusions, or empty>\n"
                + quality_assembly
                + "Never output ANIMA_TAGS, Markdown fences, or [speech:*]/[text:*] marker syntax."
            )
        return (
            "OUTPUT VALIDATION: Return only the JSON contract selected above. Translate ordinary "
            f"concepts into {output_language}; an exact Literal Content or Protected Term is the only "
            "language exception. Never copy [speech:*] or [text:*] marker syntax."
        )

    def request_payload_overrides(
        self, analysis: RendererAnalysis | None = None
    ) -> dict[str, object]:
        if analysis is not None and analysis.input_mode == "hybrid":
            return {}
        return {"response_format": {"type": "json_object"}}

    @staticmethod
    def _tag_sections(raw: dict[str, object], allowed: frozenset[str]) -> dict[str, tuple[str, ...]]:
        if not raw or set(raw) - allowed:
            raise TransformationError(DANBOORU_OUTPUT_INVALID)
        sections: dict[str, tuple[str, ...]] = {}
        for name in _ANIMA_TAG_KEYS:
            value = raw.get(name, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise TransformationError(DANBOORU_OUTPUT_INVALID)
            sections[name] = tuple(item.strip() for item in value)
        return sections

    @staticmethod
    def _fixed_components(
        variant: ProfileVariant,
        auto_quality_tags: bool = True,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        if not auto_quality_tags:
            return (), (), (), ()
        quality_component = re.compile(
            r"^(?:masterpiece|best quality|high quality|worst quality|low quality|"
            r"blurry|jpeg artifacts|chromatic aberration)$",
            re.IGNORECASE,
        )

        def quality_only(values: tuple[str, ...], seen: set[str]) -> tuple[str, ...]:
            result: list[str] = []
            for value in values:
                key = value.strip().casefold()
                if not quality_component.fullmatch(value.strip()) or key in seen:
                    continue
                seen.add(key)
                result.append(value)
            return tuple(result)

        positive_seen: set[str] = set()
        negative_seen: set[str] = set()

        return (
            quality_only(
                (
                    *variant.required_prompt.positive_prefix,
                    *variant.recommended_prompt.positive_prefix,
                ),
                positive_seen,
            ),
            quality_only(
                (
                *variant.recommended_prompt.positive_suffix,
                *variant.required_prompt.positive_suffix,
                ),
                positive_seen,
            ),
            quality_only(
                (
                *variant.required_prompt.negative_prefix,
                *variant.recommended_prompt.negative_prefix,
                ),
                negative_seen,
            ),
            quality_only(
                (
                *variant.recommended_prompt.negative_suffix,
                *variant.required_prompt.negative_suffix,
                ),
                negative_seen,
            ),
        )

    @staticmethod
    def _quality_components_not_in_text(
        values: tuple[str, ...],
        text: str,
    ) -> tuple[str, ...]:
        return tuple(
            value
            for value in values
            if re.search(
                rf"(?<!\w){re.escape(value.strip())}(?!\w)",
                text,
                re.IGNORECASE,
            )
            is None
        )

    def _render_natural(
        self,
        raw: dict[str, object],
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        source_request: str | None,
        auto_quality_tags: bool,
    ) -> RenderResult:
        if set(raw) != {"positive", "negative"}:
            raise TransformationError(DANBOORU_OUTPUT_INVALID)
        generated_positive = raw["positive"]
        generated_negative = raw["negative"]
        if not isinstance(generated_positive, str) or not generated_positive.strip():
            raise TransformationError(DANBOORU_OUTPUT_INVALID)
        if not isinstance(generated_negative, str):
            raise TransformationError(DANBOORU_OUTPUT_INVALID)
        generated_positive = remove_literal_markers(generated_positive.strip())
        generated_negative = remove_literal_markers(generated_negative.strip())
        fixed_positive, fixed_positive_suffix, fixed_negative, fixed_negative_suffix = self._fixed_components(
            variant,
            auto_quality_tags,
        )
        fixed_positive = self._quality_components_not_in_text(
            fixed_positive,
            generated_positive,
        )
        fixed_positive_suffix = self._quality_components_not_in_text(
            fixed_positive_suffix,
            generated_positive,
        )
        fixed_negative = self._quality_components_not_in_text(
            fixed_negative,
            generated_negative,
        )
        fixed_negative_suffix = self._quality_components_not_in_text(
            fixed_negative_suffix,
            generated_negative,
        )
        positive_prefix = ", ".join(fixed_positive)
        positive = " ".join(
            part
            for part in (
                f"{positive_prefix}." if positive_prefix else "",
                generated_positive,
                ", ".join(fixed_positive_suffix),
            )
            if part
        ).strip()
        negative = ", ".join(
            part
            for part in (*fixed_negative, generated_negative, *fixed_negative_suffix)
            if part
        ).strip() or None
        self._validate_no_unrequested_semantic_tags(
            "\n".join((positive, negative or "")),
            source_request,
            literals,
            protected_terms,
        )
        _validate_preservation(positive, literals, protected_terms)
        return RenderResult(positive, negative, length_warnings(positive, variant.length_guidance))

    def _render_tag(
        self,
        raw: dict[str, object],
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        source_request: str | None,
        auto_quality_tags: bool,
    ) -> RenderResult:
        sections = self._tag_sections(raw, _ANIMA_TAG_KEYS)
        self._validate_structured_semantic_sections(
            sections,
            source_request,
            literals,
            protected_terms,
        )
        exemptions = _normalization_exemptions(literals, protected_terms)
        normalized_sections: dict[str, list[str]] = {}
        for section in _ANIMA_SECTION_ORDER:
            normalized_sections[section] = [
                _normalize_anima_tag(item, artist=section == "artist", exemptions=exemptions)
                for item in sections[section]
            ]
        fixed_positive, fixed_positive_suffix, fixed_negative, fixed_negative_suffix = self._fixed_components(
            variant,
            auto_quality_tags,
        )
        positive_seen = {item.casefold() for item in (*fixed_positive, *fixed_positive_suffix)}
        generated_positive: list[str] = []
        for section in _ANIMA_SECTION_ORDER:
            generated_positive.extend(_dedupe_tags(normalized_sections[section], positive_seen))
        assembled = [*fixed_positive, *generated_positive]
        for value in (*(item.text for item in literals), *(item.text for item in protected_terms)):
            if value not in assembled:
                generated_positive.append(value)
                assembled.append(value)
        positive_tag_text = ", ".join((*fixed_positive, *generated_positive, *fixed_positive_suffix))
        positive = positive_tag_text
        negative_seen = {item.casefold() for item in (*fixed_negative, *fixed_negative_suffix)}
        generated_negative = [
            _normalize_anima_tag(item, artist=False, exemptions=exemptions)
            for item in sections["negative"]
        ]
        negative = ", ".join(
            (*fixed_negative, *_dedupe_tags(generated_negative, negative_seen), *fixed_negative_suffix)
        ).strip() or None
        self._validate_no_unrequested_semantic_tags(
            "\n".join((positive, negative or "")),
            source_request,
            literals,
            protected_terms,
        )
        _validate_preservation(positive, literals, protected_terms)
        return RenderResult(positive, negative, length_warnings(positive, variant.length_guidance))

    @staticmethod
    def _hybrid_source_tags(source_request: str) -> tuple[str, ...]:
        source = remove_literal_markers(source_request).strip()
        boundary = re.search(r"[.。!?！？](?:\s+|$)", source)
        if boundary is None:
            raise TransformationError(ANIMA_HYBRID_OUTPUT_INVALID)
        tag_source = source[: boundary.start()].strip()
        natural_source = source[boundary.end() :].strip()
        if not tag_source or not natural_source:
            raise TransformationError(ANIMA_HYBRID_OUTPUT_INVALID)
        tags = tuple(part.strip() for part in tag_source.split(",") if part.strip())
        if not tags:
            raise TransformationError(ANIMA_HYBRID_OUTPUT_INVALID)
        return tags

    def _render_hybrid(
        self,
        generated: str,
        source_request: str | None,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        auto_quality_tags: bool,
    ) -> RenderResult:
        if source_request is None:
            raise TransformationError(ANIMA_HYBRID_OUTPUT_INVALID)
        match = _ANIMA_HYBRID_OUTPUT.fullmatch(generated)
        if match is None:
            raise TransformationError(ANIMA_HYBRID_OUTPUT_INVALID)
        natural = remove_literal_markers(match.group(1).strip())
        generated_negative = remove_literal_markers(match.group(2).strip())
        if not natural:
            raise TransformationError(ANIMA_HYBRID_OUTPUT_INVALID)

        source_tags = self._hybrid_source_tags(source_request)
        fixed_positive, fixed_positive_suffix, fixed_negative, fixed_negative_suffix = self._fixed_components(
            variant,
            auto_quality_tags,
        )
        fixed_positive = self._quality_components_not_in_text(fixed_positive, natural)
        fixed_positive_suffix = self._quality_components_not_in_text(
            fixed_positive_suffix,
            natural,
        )
        fixed_negative = self._quality_components_not_in_text(
            fixed_negative,
            generated_negative,
        )
        fixed_negative_suffix = self._quality_components_not_in_text(
            fixed_negative_suffix,
            generated_negative,
        )
        positive_seen = {item.casefold() for item in (*fixed_positive, *fixed_positive_suffix)}
        preserved_tags = _dedupe_tags(list(source_tags), positive_seen)
        for value in (*(item.text for item in literals), *(item.text for item in protected_terms)):
            if value not in (*fixed_positive, *preserved_tags, *fixed_positive_suffix) and value not in natural:
                preserved_tags.append(value)

        positive_tags = ", ".join((*fixed_positive, *preserved_tags, *fixed_positive_suffix))
        positive = f"{positive_tags}. {natural}" if positive_tags else natural

        negative_seen = {item.casefold() for item in (*fixed_negative, *fixed_negative_suffix)}
        negative_values = tuple(
            part.strip() for part in generated_negative.split(",") if part.strip()
        )
        exemptions = _normalization_exemptions(literals, protected_terms)
        normalized_negative = [
            _normalize_anima_tag(item, artist=False, exemptions=exemptions)
            for item in negative_values
        ]
        negative = ", ".join(
            (*fixed_negative, *_dedupe_tags(normalized_negative, negative_seen), *fixed_negative_suffix)
        ).strip() or None
        self._validate_no_unrequested_semantic_tags(
            "\n".join((positive, negative or "")),
            source_request,
            literals,
            protected_terms,
        )
        _validate_preservation(positive, literals, protected_terms)
        return RenderResult(positive, negative, length_warnings(positive, variant.length_guidance))

    def render(
        self,
        generated: str,
        variant: ProfileVariant,
        literals: tuple[LiteralContent, ...],
        protected_terms: tuple[ProtectedTerm, ...],
        *,
        input_mode: str | None = None,
        source_request: str | None = None,
        auto_quality_tags: bool = True,
    ) -> RenderResult:
        mode = input_mode or "tag"
        if mode == "hybrid":
            return self._render_hybrid(
                generated,
                source_request,
                variant,
                literals,
                protected_terms,
                auto_quality_tags,
            )
        raw = _load_json_object(generated)
        if mode == "natural":
            return self._render_natural(
                raw,
                variant,
                literals,
                protected_terms,
                source_request,
                auto_quality_tags,
            )
        if mode == "tag":
            return self._render_tag(
                raw,
                variant,
                literals,
                protected_terms,
                source_request,
                auto_quality_tags,
            )
        raise TransformationError(DANBOORU_OUTPUT_INVALID)


class RendererRegistry:
    def __init__(self) -> None:
        renderers: tuple[Renderer, ...] = (
            MiniMaxH3Renderer(),
            Wan22Renderer(),
            LTX23Renderer(),
            Krea2Renderer(),
            AnimaRenderer(),
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
