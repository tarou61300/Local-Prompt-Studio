from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from .localization import language_definition, locale_definition
from .protected_terms import ProtectedTerm


SOURCE_TO_UI_LOCALE = "source_to_ui_locale"
UI_LOCALE_TO_SOURCE = "ui_locale_to_source"
# Compatibility aliases for callers migrating from the Japanese-only editor.
ORIGINAL_TO_JAPANESE = SOURCE_TO_UI_LOCALE
JAPANESE_TO_ORIGINAL = UI_LOCALE_TO_SOURCE
TRANSLATION_STRUCTURE_NOT_PRESERVED = "TRANSLATION_STRUCTURE_NOT_PRESERVED"
TRANSLATION_EMPTY_RESPONSE = "TRANSLATION_EMPTY_RESPONSE"
TRANSLATION_MAX_OUTPUT_TOKENS = 2048

_KNOWN_FIELD_NAMES = (
    "integrated_multimodal_description",
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_SKILL_LOCKED_ALIGNMENT_PATTERNS = (
    re.compile(
        r"(?im)^For the target video, at 0\.00 seconds into the target video, "
        r"<Picture 1> \(from \[Shot 1\]\) is fully referenced\.[ \t]*$"
    ),
    re.compile(
        r"(?im)^How the reference pictures align with the target video — "
        r"Picture 1 \(from Shot 1\) aligns with the 0\.00-second mark of the target video; "
        r"Picture 2 \(from Shot (?:N|\d+)\) aligns with the "
        r"(?:S\.SS|\d+(?:\.\d{2})?)-second mark of the target video\.[ \t]*$"
    ),
    re.compile(
        r"(?im)^How the reference pictures align with the target video — "
        r"<Picture 1> \(from \[Shot (?:N|\d+)\]\) aligns with the "
        r"(?:S\.SS|\d+(?:\.\d{2})?)-second mark of the target video\.[ \t]*$"
    ),
)
_LITERAL_BLOCK = re.compile(
    r"\[(speech|text):[a-zA-Z]+(?:-[a-zA-Z]+)*\].*?\[/\1\]",
    re.IGNORECASE | re.DOTALL,
)
_LITERAL_LINE = re.compile(
    r"\[(?:speech|text):[a-zA-Z]+(?:-[a-zA-Z]+)*\][^\r\n]*",
    re.IGNORECASE,
)
_STRUCTURAL_PATTERNS = (
    re.compile(r"<(?:Subject|Picture)\s+\d+>", re.IGNORECASE),
    re.compile(r"\[Shot\s+\d+\]", re.IGNORECASE),
    re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?(?!\d)"),
    re.compile(
        rf"(?im)^(?:{'|'.join(re.escape(name) for name in _KNOWN_FIELD_NAMES)}):"
    ),
)
_PLACEHOLDER_PATTERN = re.compile(r"__LPS_STRUCTURE_\d{4}__")


@dataclass(frozen=True, slots=True)
class ProtectedSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class TranslationRequest:
    payload: dict[str, Any] = field(repr=False)
    placeholders: tuple[tuple[str, str], ...] = field(repr=False)
    source_structure_tokens: tuple[str, ...] = field(repr=False)
    structure_protection: bool
    direction: str
    source_language_name: str
    target_language_name: str


def _term_texts(values: Iterable[str | ProtectedTerm]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = value.text if isinstance(value, ProtectedTerm) else str(value)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def protected_spans(
    text: str,
    protected_terms: Iterable[str | ProtectedTerm] = (),
) -> tuple[ProtectedSpan, ...]:
    """Return non-overlapping structural regions in source order."""

    candidates: list[tuple[int, int, int]] = []
    priority = 0
    for pattern in (
        *_SKILL_LOCKED_ALIGNMENT_PATTERNS,
        _LITERAL_BLOCK,
        _LITERAL_LINE,
        *_STRUCTURAL_PATTERNS,
    ):
        for match in pattern.finditer(text):
            candidates.append((match.start(), match.end(), priority))
        priority += 1
    for term in _term_texts(protected_terms):
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            candidates.append((index, index + len(term), priority))
            start = index + len(term)
        priority += 1

    selected: list[ProtectedSpan] = []
    for start, end, _priority in sorted(
        candidates,
        key=lambda item: (item[0], item[2], -(item[1] - item[0])),
    ):
        if any(start < existing.end and end > existing.start for existing in selected):
            continue
        selected.append(ProtectedSpan(start, end, text[start:end]))
    return tuple(sorted(selected, key=lambda item: item.start))


def structure_tokens(
    text: str,
    protected_terms: Iterable[str | ProtectedTerm] = (),
) -> tuple[str, ...]:
    return tuple(item.text for item in protected_spans(text, protected_terms))


class PromptTranslationService:
    """Build faithful local-LLM translation requests without using renderers."""

    def request_payload(
        self,
        source_text: str,
        direction: str,
        *,
        source_language_code: str = "en",
        ui_locale_id: str = "ja-JP",
        protected_terms: Iterable[str | ProtectedTerm] = (),
        structure_protection: bool = True,
    ) -> TranslationRequest:
        if direction not in {SOURCE_TO_UI_LOCALE, UI_LOCALE_TO_SOURCE}:
            raise ValueError("TRANSLATION_DIRECTION_INVALID")
        if not source_text.strip():
            raise ValueError(TRANSLATION_EMPTY_RESPONSE)

        source_language = language_definition(source_language_code)
        if source_language is None:
            raise ValueError("TRANSLATION_SOURCE_LANGUAGE_INVALID")
        ui_language = locale_definition(ui_locale_id)
        if direction == SOURCE_TO_UI_LOCALE:
            source_language_name = source_language.llm_language_name
            target_language_name = ui_language.llm_language_name
        else:
            source_language_name = ui_language.llm_language_name
            target_language_name = source_language.llm_language_name

        term_values = _term_texts(protected_terms)
        source_tokens = structure_tokens(source_text, term_values)
        placeholders: list[tuple[str, str]] = []
        translation_source = source_text
        if structure_protection:
            spans = protected_spans(source_text, term_values)
            chunks: list[str] = []
            offset = 0
            for index, span in enumerate(spans):
                placeholder = f"__LPS_STRUCTURE_{index:04d}__"
                chunks.append(source_text[offset : span.start])
                chunks.append(placeholder)
                placeholders.append((placeholder, span.text))
                offset = span.end
            chunks.append(source_text[offset:])
            translation_source = "".join(chunks)

        protection_rule = (
            "Tokens matching __LPS_STRUCTURE_0000__ and similar placeholders are "
            "immutable. Copy every placeholder exactly once, in the same order."
            if structure_protection
            else "No structural masking is enabled, but the same faithful translation rules apply."
        )
        system = (
            "You are a faithful prompt translator, not a prompt writer or renderer. "
            f"Translate only from {source_language_name} into {target_language_name}. "
            "Do not add, remove, summarize, improve, "
            "reorder, or infer any content. Do not add camera, style, quality, rating, "
            "score, artist, demographic, identity, era, or safety details. Preserve "
            "line breaks, headings, field order, punctuation, tags, identifiers, numbers, "
            "timecodes, and prompt structure. "
            + protection_rule
            + " Return only the translated text with no analysis, preface, Markdown fence, "
            "or alternative."
        )
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": translation_source},
            ],
            "temperature": 0.1,
            "top_p": 0.8,
            "max_tokens": TRANSLATION_MAX_OUTPUT_TOKENS,
            "stream": False,
        }
        return TranslationRequest(
            payload=payload,
            placeholders=tuple(placeholders),
            source_structure_tokens=source_tokens,
            structure_protection=structure_protection,
            direction=direction,
            source_language_name=source_language_name,
            target_language_name=target_language_name,
        )

    def finalize_response(
        self,
        generated: str,
        request: TranslationRequest,
        *,
        protected_terms: Iterable[str | ProtectedTerm] = (),
    ) -> str:
        translated = re.sub(
            r"<think\b[^>]*>.*?</think\s*>",
            "",
            generated,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        fence = re.fullmatch(
            r"\x60\x60\x60(?:[^\r\n]*)?\r?\n(.*?)\r?\n\x60\x60\x60",
            translated,
            flags=re.DOTALL,
        )
        if fence is not None:
            translated = fence.group(1).strip()
        if not translated:
            raise ValueError(TRANSLATION_EMPTY_RESPONSE)

        if request.structure_protection:
            expected_names = {name for name, _value in request.placeholders}
            actual_names = _PLACEHOLDER_PATTERN.findall(translated)
            if (
                len(actual_names) != len(expected_names)
                or set(actual_names) != expected_names
                or any(translated.count(name) != 1 for name in expected_names)
            ):
                raise ValueError(TRANSLATION_STRUCTURE_NOT_PRESERVED)
            for placeholder, original in request.placeholders:
                translated = translated.replace(placeholder, original)
            if structure_tokens(translated, protected_terms) != request.source_structure_tokens:
                raise ValueError(TRANSLATION_STRUCTURE_NOT_PRESERVED)
        elif (
            structure_tokens(translated, protected_terms)
            != request.source_structure_tokens
        ):
            raise ValueError(TRANSLATION_STRUCTURE_NOT_PRESERVED)
        return translated
