from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

CHAT_SYSTEM_PROMPT = (
    "You are a helpful, neutral, general-purpose assistant. "
    "Answer the user's questions directly and accurately. "
    "Use the language of the user's latest message unless they ask for another language."
)
CHAT_MAX_OUTPUT_TOKENS = 1536


class ChatEngine:
    """Build ordinary chat requests without any prompt-renderer instructions."""

    def request_payload(
        self,
        conversation: Sequence[dict[str, str]],
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT}
        ]
        for message in conversation:
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
            if role not in {"user", "assistant"} or not content.strip():
                raise ValueError("CHAT_CONVERSATION_INVALID")
            messages.append({"role": role, "content": content})
        if len(messages) == 1 or messages[-1]["role"] != "user":
            raise ValueError("CHAT_USER_MESSAGE_REQUIRED")
        return {
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": CHAT_MAX_OUTPUT_TOKENS,
            "stream": False,
        }

    @staticmethod
    def finalize_response(generated: str) -> str:
        response = re.sub(
            r"<think\b[^>]*>.*?</think\s*>",
            "",
            generated,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        if not response:
            raise ValueError("CHAT_EMPTY_RESPONSE")
        return response
