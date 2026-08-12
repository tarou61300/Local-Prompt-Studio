from __future__ import annotations

import re
from dataclasses import dataclass


_LITERAL_LINE = re.compile(r"^\s*\[(speech|text):([a-zA-Z-]+)\]\s+(.+)$")
_LANGUAGE_CODE = re.compile(r"^[a-zA-Z]+(?:-[a-zA-Z]+)*$")


@dataclass(frozen=True, slots=True)
class LiteralContent:
    kind: str
    language: str
    text: str
    line_number: int


def parse_literal_content(value: str) -> tuple[LiteralContent, ...]:
    literals: list[LiteralContent] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        match = _LITERAL_LINE.fullmatch(line)
        if match is None:
            continue
        kind, language, text = match.groups()
        if _LANGUAGE_CODE.fullmatch(language) is None:
            continue
        literals.append(LiteralContent(kind, language, text, line_number))
    return tuple(literals)


def missing_literal_contents(
    output: str, literals: tuple[LiteralContent, ...]
) -> tuple[LiteralContent, ...]:
    return tuple(literal for literal in literals if literal.text not in output)
