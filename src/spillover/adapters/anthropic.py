from __future__ import annotations

import json
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
        payload: dict = {
            "model": conversation.model,
            "max_tokens": conversation.max_tokens,
            "messages": [
                {"role": t.role, "content": t.content} for t in conversation.turns
            ],
        }
        if conversation.system is not None:
            payload["system"] = conversation.system
        payload.update(conversation.extra)
        return payload

    def _extract_tool_calls(self, content: Any) -> list[dict]:
        if not isinstance(content, list):
            return []
        return [
            {"id": b.get("id"), "name": b.get("name"), "input": b.get("input")}
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]

    def extract_usage_non_streaming(self, body: bytes) -> tuple[int, int] | None:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        usage = data.get("usage")
        if not usage:
            return None
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    def extract_usage_sse(self, captured: list[bytes]) -> tuple[int, int] | None:
        joined = b"".join(captured).decode("utf-8", errors="replace")
        input_tokens = 0
        output_tokens = 0
        found = False
        for line in joined.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage = obj.get("usage") or (obj.get("message") or {}).get("usage") or {}
            if usage:
                input_tokens = int(usage.get("input_tokens", input_tokens))
                output_tokens = int(usage.get("output_tokens", output_tokens))
                found = True
        return (input_tokens, output_tokens) if found else None

    def parse_response_text(self, resp_json: dict) -> str:
        return "".join(
            b.get("text", "")
            for b in resp_json.get("content", [])
            if isinstance(b, dict)
        )

    def extract_assistant_text_sse(self, captured: list[bytes]) -> str:
        joined = b"".join(captured).decode("utf-8", errors="replace")
        text = ""
        for line in joined.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                continue
            delta = obj.get("delta") or {}
            if "text" in delta:
                text += delta["text"]
        return text

    def inject_ltm(self, payload: dict, ltm_text: str) -> None:
        if not ltm_text:
            return
        existing = payload.get("system")
        if existing is None:
            payload["system"] = ltm_text
        elif isinstance(existing, str):
            payload["system"] = ltm_text + "\n\n" + existing
        elif isinstance(existing, list):
            payload["system"] = [{"type": "text", "text": ltm_text}, *existing]
