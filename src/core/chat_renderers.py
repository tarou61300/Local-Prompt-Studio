from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

from .chat_engine import CHAT_MAX_OUTPUT_TOKENS, ChatEngine


REFERENCE_IMAGE_SYSTEM_PROMPT = """You convert an attached image into model-independent reference information for a later prompt-writing LLM. Report only directly observable details. Organize useful non-empty sections such as SUBJECT, APPEARANCE, CLOTHING, POSE_AND_ACTION, ENVIRONMENT, COMPOSITION, VIEWPOINT, LIGHTING, COLOR, STYLE, READABLE_TEXT, and UNCERTAIN. Put uncertain observations only under UNCERTAIN or omit them. Do not greet, praise the image, write poetry or stories, or infer personality, relationships, nationality, exact age, or season. Do not optimize for any image/video model. Do not add model-specific tags, quality tags, rating/safety tags, score tags, or artist tags. Return the sectioned reference text directly, without Markdown fences or special wrapper markers."""

REFERENCE_IMAGE_USER_INSTRUCTION = """Analyze the attached image as reusable, model-independent visual reference information. Preserve readable text exactly. Use concise bullet points under relevant uppercase section headings. Return the sectioned reference text directly without wrapper markers."""

PROMPT_TRANSFER_SYSTEM_PROMPT = """You prepare model-independent transfer content for a later prompt-writing LLM. Remove greetings, acknowledgements, preambles, and closing offers such as 'let me know' or 'I can also help'. Keep all concrete prompt-useful information without over-summarizing. Preserve numbers, colors, proper nouns, Literal Content, Protected Terms, spelling, case, and language exactly. Do not translate, infer, add new information, optimize for a model, or add quality or semantic tags. Return only one [TRANSFER_CONTENT]...[/TRANSFER_CONTENT] block."""


def _enclosed_content(text: str, tag: str) -> str | None:
    match = re.search(
        rf"\[{tag}\](.*?)\[/{tag}\]",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None or not match.group(1).strip():
        return None
    return match.group(1).strip()


class ReferenceImageRenderer(ChatEngine):
    """Render one attached image as reusable reference data, never a final prompt."""

    analysis_type = "reference_image"
    transfer_ready = True

    def request_payload(
        self,
        conversation: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        transformed = [dict(message) for message in conversation]
        for message in reversed(transformed):
            if message.get("role") == "user" and message.get("image") is not None:
                message["content"] = REFERENCE_IMAGE_USER_INSTRUCTION
                break
        else:
            raise ValueError("REFERENCE_IMAGE_REQUIRED")
        payload = super().request_payload(transformed)
        payload["messages"][0]["content"] = REFERENCE_IMAGE_SYSTEM_PROMPT
        payload["temperature"] = 0.2
        return payload

    @staticmethod
    def finalize_response(generated: str) -> str:
        response = ChatEngine.finalize_response(generated)
        # Accept and strip the obsolete envelope if a model echoes it despite
        # the current instruction, but never expose or transfer that marker.
        content = _enclosed_content(response, "REFERENCE_IMAGE")
        if content is None:
            content = response.strip().strip("`").strip()
            content = re.sub(
                r"^\s*\[/?REFERENCE_IMAGE\]\s*|\s*\[/?REFERENCE_IMAGE\]\s*$",
                "",
                content,
                flags=re.IGNORECASE,
            ).strip()
        if not content:
            raise ValueError("REFERENCE_IMAGE_OUTPUT_INVALID")
        return content


class PromptTransferRenderer(ChatEngine):
    """Remove chat boilerplate without changing concrete user-relevant facts."""

    analysis_type = "prompt_transfer"

    def request_payload(
        self,
        conversation: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        if len(conversation) != 1:
            raise ValueError("TRANSFER_SOURCE_REQUIRED")
        source = str(conversation[0].get("content", "")).strip()
        if not source:
            raise ValueError("TRANSFER_SOURCE_REQUIRED")
        return {
            "messages": [
                {"role": "system", "content": PROMPT_TRANSFER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Prepare the following assistant response for transfer.\n"
                        "<SOURCE_CONTENT>\n"
                        f"{source}\n"
                        "</SOURCE_CONTENT>"
                    ),
                },
            ],
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": CHAT_MAX_OUTPUT_TOKENS,
            "stream": False,
        }

    @staticmethod
    def finalize_response(generated: str) -> str:
        response = ChatEngine.finalize_response(generated)
        content = _enclosed_content(response, "TRANSFER_CONTENT") or response
        content = PromptTransferRenderer._remove_boilerplate(content)
        if not content:
            raise ValueError("TRANSFER_OUTPUT_INVALID")
        return content

    @staticmethod
    def _remove_boilerplate(text: str) -> str:
        content = text.strip().strip("`").strip()
        content = re.sub(
            r"^(?:はい[、,]?(?:わかりました|承知しました)[。.!]?\s*|"
            r"もちろんです[。.!]?\s*|Sure[,.!]?\s*|Of course[,.!]?\s*)",
            "",
            content,
            flags=re.IGNORECASE,
        )
        lines = content.splitlines()
        closing = re.compile(
            r"^(?:必要(?:なら|であれば).*(?:できます|ください)[。.!]?|"
            r"(?:Let me know|If you(?:'d| would) like).*)$",
            flags=re.IGNORECASE,
        )
        while lines and closing.match(lines[-1].strip()):
            lines.pop()
        return "\n".join(lines).strip()
