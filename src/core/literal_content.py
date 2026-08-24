from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_LANGUAGE = r"[a-zA-Z]+(?:-[a-zA-Z]+)*"
_PAIRED_LITERAL = re.compile(
    rf"\[(speech|text):({_LANGUAGE})\](.*?)\[/\1\]",
    re.IGNORECASE | re.DOTALL,
)
_LEGACY_LITERAL_LINE = re.compile(
    rf"^\s*\[(speech|text):({_LANGUAGE})\]\s*(\S.*)$",
    re.IGNORECASE,
)
_LITERAL_MARKER = re.compile(
    rf"\[/?(?:speech|text)(?::{_LANGUAGE})?\]",
    re.IGNORECASE,
)
_UNPAIRED_START_MARKER = re.compile(
    rf"\[(?:speech|text):{_LANGUAGE}\]\s*",
    re.IGNORECASE,
)
_QUOTED_PATTERNS = (
    ("「", "」", re.compile(r"「([^」\r\n]+)」")),
    ("“", "”", re.compile(r"“([^”\r\n]+)”")),
    ("«", "»", re.compile(r"«([^»\r\n]+)»")),
    ('"', '"', re.compile(r'"([^"\r\n]+)"')),
)


@dataclass(frozen=True, slots=True)
class LiteralContent:
    kind: str
    language: str
    text: str
    line_number: int
    source: str = "explicit"
    source_role: str = "request"


@dataclass(frozen=True, slots=True)
class LiteralDiagnosticItem:
    source_role: str
    detection_type: str
    character_count: int
    short_hash: str


@dataclass(frozen=True, slots=True)
class LiteralValidationDiagnostics:
    detected_count: int
    missing: tuple[LiteralDiagnosticItem, ...]

    @property
    def missing_count(self) -> int:
        return len(self.missing)


@dataclass(frozen=True, slots=True)
class QuotedContentCandidate:
    text: str
    line_number: int
    line: str
    opening_quote: str
    closing_quote: str


def _line_number(value: str, offset: int) -> int:
    return value.count("\n", 0, offset) + 1


def _paired_matches(value: str) -> tuple[re.Match[str], ...]:
    return tuple(_PAIRED_LITERAL.finditer(value))


def _inside(offset: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= offset < end for start, end in spans)


def parse_literal_content(value: str) -> tuple[LiteralContent, ...]:
    """Parse explicit paired blocks and legacy line-start literal directives.

    Paired blocks have priority. A legacy directive without a closing tag owns
    the remainder of its line, preserving the v2.0 input contract.
    """

    literals: list[LiteralContent] = []
    paired = _paired_matches(value)
    paired_spans = tuple(match.span() for match in paired)
    for match in paired:
        kind, language, text = match.groups()
        literals.append(
            LiteralContent(
                kind.lower(),
                language,
                text,
                _line_number(value, match.start()),
                "paired",
            )
        )

    offset = 0
    for line_number, line_with_ending in enumerate(value.splitlines(keepends=True), start=1):
        line = line_with_ending.rstrip("\r\n")
        line_end = offset + len(line_with_ending)
        overlaps_paired = any(
            start < line_end and end > offset for start, end in paired_spans
        )
        if not overlaps_paired:
            match = _LEGACY_LITERAL_LINE.fullmatch(line)
            if match is not None:
                kind, language, text = match.groups()
                literals.append(
                    LiteralContent(kind.lower(), language, text, line_number, "legacy")
                )
        offset += len(line_with_ending)

    return tuple(sorted(literals, key=lambda item: (item.line_number, item.source != "paired")))


def quoted_content_candidates(value: str) -> tuple[QuotedContentCandidate, ...]:
    """Return quote syntax candidates outside explicit literal directives.

    This function deliberately does not decide whether a quote is speech or
    visible text. That semantic decision belongs to each model renderer.
    """

    paired = _paired_matches(value)
    occupied = tuple(match.span() for match in paired)
    candidates: list[tuple[int, QuotedContentCandidate]] = []
    for opening, closing, pattern in _QUOTED_PATTERNS:
        for match in pattern.finditer(value):
            if _inside(match.start(), occupied):
                continue
            line_start = value.rfind("\n", 0, match.start()) + 1
            line_end = value.find("\n", match.end())
            if line_end < 0:
                line_end = len(value)
            line = value[line_start:line_end].rstrip("\r")
            paired_on_line = any(
                start < line_end and end > line_start for start, end in occupied
            )
            if _LEGACY_LITERAL_LINE.fullmatch(line) is not None and not paired_on_line:
                continue
            candidates.append(
                (
                    match.start(),
                    QuotedContentCandidate(
                        match.group(1),
                        _line_number(value, match.start()),
                        line,
                        opening,
                        closing,
                    ),
                )
            )
    return tuple(candidate for _offset, candidate in sorted(candidates, key=lambda item: item[0]))


def remove_literal_markers(value: str) -> str:
    """Remove recognized directive syntax while preserving each body exactly."""

    def paired_replacement(match: re.Match[str]) -> str:
        return match.group(3)

    value = _PAIRED_LITERAL.sub(paired_replacement, value)
    value = _UNPAIRED_START_MARKER.sub("", value)
    return _LITERAL_MARKER.sub("", value)


def without_explicit_literal_content(value: str) -> str:
    """Remove explicit literal directives and their bodies for format detection."""

    value = _PAIRED_LITERAL.sub("", value)
    retained: list[str] = []
    for line in value.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if _LEGACY_LITERAL_LINE.fullmatch(body) is None:
            retained.append(line)
    return "".join(retained)


def missing_literal_contents(
    output: str, literals: tuple[LiteralContent, ...]
) -> tuple[LiteralContent, ...]:
    return tuple(literal for literal in literals if literal.text not in output)


def build_literal_validation_diagnostics(
    literals: tuple[LiteralContent, ...],
    missing: tuple[LiteralContent, ...],
) -> LiteralValidationDiagnostics:
    """Build non-sensitive failure metadata without retaining Literal bodies."""

    return LiteralValidationDiagnostics(
        detected_count=len(literals),
        missing=tuple(
            LiteralDiagnosticItem(
                source_role=literal.source_role,
                detection_type=literal.source,
                character_count=len(literal.text),
                short_hash=hashlib.sha256(literal.text.encode("utf-8")).hexdigest()[:8],
            )
            for literal in missing
        ),
    )
