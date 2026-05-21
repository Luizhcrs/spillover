from __future__ import annotations

import time
import uuid
from typing import Any

# Known patterns that indicate the CLI is asking the model to "compact" or
# "summarize" the conversation locally. We short-circuit these so the CLI
# treats them as completed without forwarding to Anthropic.

_COMPACT_KEYWORDS = [
    "compact the conversation",
    "compact this conversation",
    "summarize the conversation so far",
    "summarize this conversation",
    "create a concise summary of the conversation",
    "resuma a conversa",
    "compacte a conversa",
]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def should_intercept_request(payload: dict) -> bool:
    """Return True if the inbound /v1/messages payload looks like the CLI is
    asking the model to compact/summarize the conversation."""
    messages = payload.get("messages") or []
    if not messages:
        return False
    last = messages[-1]
    if last.get("role") != "user":
        return False
    text = _content_to_text(last.get("content")).lower()
    return any(kw in text for kw in _COMPACT_KEYWORDS)


def make_intercept_response(payload: dict) -> dict:
    """Return a synthetic 200 response that satisfies the CLI's compact request
    without forwarding to Anthropic. The body is a no-op message hinting the
    proxy is managing memory."""
    return {
        "id": f"msg_spillover_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": payload.get("model", "claude-opus-4-7"),
        "content": [
            {
                "type": "text",
                "text": (
                    "[spillover] Conversation memory is managed by the spillover "
                    "proxy. No client-side compaction needed."
                ),
            }
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1,
            "output_tokens": 20,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "spillover_intercepted": True,
        "spillover_intercept_ts": int(time.time() * 1000),
    }
