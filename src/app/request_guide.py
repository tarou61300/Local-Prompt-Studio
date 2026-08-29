from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestGuideEntry:
    key: str
    title: str
    example: str


def request_guide_entries(
    tr: Callable[[str], str],
    *,
    profile_id: str | None = None,
) -> tuple[RequestGuideEntry, ...]:
    """Return neutral request examples; profile_id leaves room for future overrides."""

    _ = profile_id
    return tuple(
        RequestGuideEntry(
            key,
            tr(f"input.guide.{key}.title"),
            tr(f"input.guide.{key}.example"),
        )
        for key in ("time", "fixed_camera", "cut", "speech", "visible_text")
    )
