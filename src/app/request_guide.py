from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestGuideEntry:
    key: str
    title: str
    example: str


def request_guide_entries(
    locale_id: str,
    *,
    profile_id: str | None = None,
) -> tuple[RequestGuideEntry, ...]:
    """Return neutral request examples; profile_id leaves room for future overrides."""

    _ = profile_id
    if locale_id == "ja-JP":
        return (
            RequestGuideEntry("time", "時間指定", "0〜3秒は静止し、3〜8秒でゆっくり振り向く。"),
            RequestGuideEntry("fixed_camera", "固定カメラ", "カメラは固定。構図を変えない。"),
            RequestGuideEntry(
                "cut",
                "カット変更",
                "途中でカットを切り替え、正面のクローズアップへ移る。",
            ),
            RequestGuideEntry(
                "speech",
                "発話",
                "[speech:ja]こんにちは[/speech]",
            ),
            RequestGuideEntry(
                "visible_text",
                "表示文字",
                "[text:ja]月夜珈琲[/text]",
            ),
        )
    return (
        RequestGuideEntry(
            "time",
            "Timing",
            "Remain still from 0 to 3 seconds, then turn slowly from 3 to 8 seconds.",
        ),
        RequestGuideEntry(
            "fixed_camera",
            "Fixed camera",
            "Keep the camera fixed and do not change the framing.",
        ),
        RequestGuideEntry(
            "cut",
            "Cut change",
            "Cut once to a front-facing close-up midway through the scene.",
        ),
        RequestGuideEntry(
            "speech",
            "Speech",
            "[speech:en]Hello[/speech]",
        ),
        RequestGuideEntry(
            "visible_text",
            "Visible text",
            "[text:en]OPEN[/text]",
        ),
    )
