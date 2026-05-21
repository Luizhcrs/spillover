from __future__ import annotations

import uuid


def ensure_request_id(headers: dict | None) -> str:
    if headers:
        existing = headers.get("x-request-id") or headers.get("X-Request-Id")
        if existing:
            return str(existing)
    return uuid.uuid4().hex
