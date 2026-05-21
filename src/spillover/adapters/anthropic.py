from __future__ import annotations

from typing import Any

from spillover.adapters.base import Adapter, Conversation, ConversationTurn
from spillover.eviction.tokenizer import count_tokens

_PASSTHROUGH_KEYS = {
    "stream",
    "stop_sequences",
    "temperature",
    "top_p",
    "top_k",
    "metadata",
    "tools",
    "tool_choice",
    "thinking",
    "anthropic_version",
}


class AnthropicAdapter(Adapter):
    def parse(self, payload: dict) -> Conversation:
        system = payload.get("system")
        system_tokens = count_tokens(system) if system else 0

        turns: list[ConversationTurn] = []
        for i, msg in enumerate(payload.get("messages", [])):
            content = msg["content"]
            tool_calls = self._extract_tool_calls(content)
            tok = count_tokens(content)
            turns.append(
                ConversationTurn(
                    role=msg["role"],
                    content=content,
                    tool_calls=tool_calls,
                    token_count=tok,
                    source_index=i,
                )
            )

        extra = {k: payload[k] for k in _PASSTHROUGH_KEYS if k in payload}

        return Conversation(
            system=system,
            system_tokens=system_tokens,
            turns=turns,
            model=payload.get("model"),
            max_tokens=payload.get("max_tokens", 4096),
            extra=extra,
        )

    def build(self, conversation: Conversation) -> dict:
        raise NotImplementedError("build implemented in next task")

    def _extract_tool_calls(self, content: Any) -> list[dict]:
        if not isinstance(content, list):
            return []
        return [
            {"id": b.get("id"), "name": b.get("name"), "input": b.get("input")}
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
