from __future__ import annotations

import json
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

    def extract_usage_non_streaming(self, body: bytes) -> tuple[int, int] | None:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        usage = data.get("usage")
        if not usage:
            return None
        return (
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )

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
            usage = obj.get("usage") or {}
            if usage:
                input_tokens = int(usage.get("prompt_tokens", input_tokens))
                output_tokens = int(usage.get("completion_tokens", output_tokens))
                found = True
        return (input_tokens, output_tokens) if found else None

    def parse_response_text(self, resp_json: dict) -> str:
        choices = resp_json.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict)
            )
        return ""

    def extract_assistant_text_sse(self, captured: list[bytes]) -> str:
        joined = b"".join(captured).decode("utf-8", errors="replace")
        text = ""
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
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                if "content" in delta and isinstance(delta["content"], str):
                    text += delta["content"]
        return text

    def inject_ltm(self, payload: dict, ltm_text: str) -> None:
        if not ltm_text:
            return
        messages = payload.setdefault("messages", [])
        # Insert as the first system message; coalesce with an existing one if present
        if messages and messages[0].get("role") == "system":
            existing = messages[0].get("content", "")
            messages[0]["content"] = ltm_text + "\n\n" + (existing or "")
        else:
            messages.insert(0, {"role": "system", "content": ltm_text})
