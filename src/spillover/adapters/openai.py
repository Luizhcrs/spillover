from __future__ import annotations

from typing import Any

from spillover.adapters.base import Adapter, Conversation, ConversationTurn
from spillover.eviction.tokenizer import count_tokens

_PASSTHROUGH_KEYS = {
    "stream",
    "stop",
    "temperature",
    "top_p",
    "n",
    "logprobs",
    "top_logprobs",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "tools",
    "tool_choice",
    "response_format",
    "user",
}


class OpenAIAdapter(Adapter):
    def parse(self, payload: dict) -> Conversation:
        system_parts: list[str] = []
        turns: list[ConversationTurn] = []

        for i, msg in enumerate(payload.get("messages", [])):
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            tool_calls = self._extract_tool_calls(msg)
            tok = count_tokens(content)
            turns.append(
                ConversationTurn(
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                    token_count=tok,
                    source_index=i,
                )
            )

        system = "\n\n".join(system_parts) if system_parts else None
        system_tokens = count_tokens(system) if system else 0
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
        messages: list[dict] = []
        if conversation.system:
            sys = conversation.system if isinstance(conversation.system, str) else (
                "\n\n".join(
                    b.get("text", "") for b in conversation.system
                    if isinstance(b, dict)
                )
            )
            messages.append({"role": "system", "content": sys})
        for t in conversation.turns:
            entry: dict[str, Any] = {"role": t.role, "content": t.content}
            if t.tool_calls:
                entry["tool_calls"] = t.tool_calls
            messages.append(entry)
        payload: dict = {
            "model": conversation.model,
            "max_tokens": conversation.max_tokens,
            "messages": messages,
        }
        payload.update(conversation.extra)
        return payload

    def _extract_tool_calls(self, msg: dict) -> list[dict]:
        return list(msg.get("tool_calls") or [])
