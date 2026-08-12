from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProtectedTerm:
    text: str

    def __post_init__(self) -> None:
        if not self.text or "\n" in self.text or "\r" in self.text:
            raise ValueError("Protected terms must be non-empty single-line text.")


def normalize_protected_terms(values: tuple[str, ...] | list[str]) -> tuple[ProtectedTerm, ...]:
    seen: set[str] = set()
    terms: list[ProtectedTerm] = []
    for value in values:
        term = ProtectedTerm(str(value))
        if term.text not in seen:
            terms.append(term)
            seen.add(term.text)
    return tuple(terms)


def missing_protected_terms(output: str, terms: tuple[ProtectedTerm, ...]) -> tuple[ProtectedTerm, ...]:
    return tuple(term for term in terms if term.text not in output)
