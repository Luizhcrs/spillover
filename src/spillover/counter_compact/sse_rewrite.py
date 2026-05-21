from __future__ import annotations

import json
import re

from spillover.counter_compact.usage_rewrite import rewrite_usage

_DATA_LINE = re.compile(rb"^data:\s*(.*)$", re.MULTILINE)


def rewrite_sse_body(body: bytes, tokens_archived_this_turn: int) -> bytes:
    """Walk SSE chunks, rewrite any data: line containing 'usage'."""
    if tokens_archived_this_turn <= 0 or not body:
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
