from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .skill_manager import SkillManager


H3_MODES = ("T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA")
PROCESSING_MODES = ("Faithful", "Balanced", "Creative")
REFERENCE_LIMITS = {"Picture": 9, "Video": 3, "Audio": 3}
TEMPERATURES = {"Faithful": 0.35, "Balanced": 0.55, "Creative": 0.75}
DEFAULT_MAX_OUTPUT_TOKENS = 1536

SYSTEM_INSTRUCTION = """You transform a user's request into one finished English prompt for MiniMax H3.
The official MiniMax H3 skill and its reference guide take priority.
Preserve the user's intent, action order, camera directions, relationships, and timing.
Never alter dialogue. Keep dialogue in the language written by the user; do not translate it.
In Faithful mode, do not add unspecified actions or substantial details.
Do not use jailbreak instructions or attempt to circumvent the selected local model.
Return only the finished H3 prompt, with no explanation, preface, or Markdown code fence."""

PROCESSING_INSTRUCTIONS = {
    "Faithful": "Preserve every specified action and its order. Add no unspecified action or major detail.",
    "Balanced": "Preserve the request; add only minimal continuity, timing, natural motion, ambience, or camera clarity.",
    "Creative": "Preserve the central intent; you may add useful cinematic direction and natural visual detail.",
}


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
    start_frame_note: str = ""
    end_frame_note: str = ""
    references: list[H3Reference] = field(default_factory=list)


def clean_model_output(text: str) -> str:
    """Remove Qwen reasoning blocks and harmless wrappers without eating prompt text."""
    cleaned = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"^\s*```(?:text|markdown)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


class PromptEngine:
    def __init__(self, skill_manager: SkillManager) -> None:
        self.skill_manager = skill_manager

    def build_messages(self, request: str, settings: PromptSettings) -> list[dict[str, str]]:
        request = request.strip()
        if not request:
            raise ValueError("Requestを入力してください。")
        self._validate(settings)
        skill = self.skill_manager.load_skill()
        guide = self.skill_manager.reference_for_mode(settings.mode)
        ui_block = self._ui_block(settings)
        # Some embedded Jinja templates (including Qwen3.5 variants) accept a
        # system role only as the first message. Keep every instruction and the
        # Skill text unchanged, but place them in one leading system message.
        system_content = "\n\n".join(
            (
                SYSTEM_INSTRUCTION,
                f"OFFICIAL H3 SKILL:\n{skill}",
                f"REFERENCE GUIDE FOR {settings.mode}:\n{guide}",
                ui_block,
                (
                    "OUTPUT FORMAT: Return only the complete H3 prompt. Do not add analysis, "
                    "a preface such as 'Here is your prompt', or Markdown fences."
                ),
            )
        )
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": request},
        ]

    def request_payload(self, request: str, settings: PromptSettings) -> dict[str, Any]:
        return {
            "messages": self.build_messages(request, settings),
            "temperature": TEMPERATURES[settings.processing],
            "top_p": 0.9,
            "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "stream": False,
        }

    def _validate(self, settings: PromptSettings) -> None:
        if settings.mode not in H3_MODES:
            raise ValueError(f"未対応のH3モードです: {settings.mode}")
        if settings.processing not in PROCESSING_MODES:
            raise ValueError(f"未対応のPrompt Processingです: {settings.processing}")
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

    def _ui_block(self, settings: PromptSettings) -> str:
        lines = [
            "UI SETTINGS (use only where they do not contradict the user):",
            f"Mode: {settings.mode}",
            f"Duration: {settings.duration} seconds",
            f"Prompt Processing: {settings.processing}",
            f"Processing rule: {PROCESSING_INSTRUCTIONS[settings.processing]}",
            f"Camera: {settings.camera}",
            f"Shot: {settings.shot}",
            f"Motion: {settings.motion}",
            "Audio: " + ", ".join(
                name
                for name, enabled in (
                    ("Environmental / scene audio", settings.environmental_audio),
                    ("Dialogue", settings.dialogue),
                    ("Background music", settings.background_music),
                )
                if enabled
            ),
        ]
        if settings.mode in {"I2VA", "FL2VA"} and settings.start_frame_note.strip():
            lines.append(f"Start image note: {settings.start_frame_note.strip()}")
        if settings.mode in {"FL2VA", "L2VA"} and settings.end_frame_note.strip():
            lines.append(f"End image note: {settings.end_frame_note.strip()}")
        if settings.mode == "Ref2VA":
            lines.append("References (the files themselves are not analyzed or sent):")
            for reference in settings.references:
                lines.append(f"{reference.tag()} {reference.description.strip()}")
        return "\n".join(lines)
