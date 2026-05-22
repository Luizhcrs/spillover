from __future__ import annotations

import json
import os
import re

from spillover.counter_compact.usage_rewrite import rewrite_usage

_DATA_LINE = re.compile(rb"^data:\s*(.*)$", re.MULTILINE)


def has_usage_marker(chunk: bytes) -> bool:
    """Cheap check: does this chunk likely contain a usage field?"""
    return b'"usage"' in chunk


def rewrite_sse_body(body: bytes, tokens_archived_this_turn: int) -> bytes:
    """Walk SSE chunks, rewrite any data: line containing 'usage'."""
    if not body:
        return body
    # Skip work only if BOTH archived count is 0 AND no reported-cap is set.
    cap_env = os.environ.get("SPILLOVER_REPORTED_INPUT_CAP", "0")
    cap_active = bool(cap_env) and cap_env != "0"
    if tokens_archived_this_turn <= 0 and not cap_active:
        return body

    def _rewrite_match(m: re.Match) -> bytes:
        raw = m.group(1).strip()
        if not raw or raw == b"[DONE]":
            return m.group(0)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return m.group(0)
        usage_path = None
        if "usage" in obj:
            usage_path = obj
        elif isinstance(obj.get("message"), dict) and "usage" in obj["message"]:
            usage_path = obj["message"]
        if usage_path is None:
            return m.group(0)
        usage_path["usage"] = rewrite_usage(
            usage_path["usage"], tokens_archived_this_turn
        )
        rebuilt = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        return b"data: " + rebuilt

    return _DATA_LINE.sub(_rewrite_match, body)
