"""Model-fallback on persistent 429/overloaded responses.

Anthropic OAuth bearer cota is per-model. If Sonnet/Opus quota is drained,
Haiku quota usually stays. This module rewrites the payload's `model` field
to a configured fallback and re-issues the upstream request when the primary
model returns a hard 429 after the retry chain has exhausted.

Behavior is fully transparent to the client EXCEPT for a single response
header (`x-spillover-fallback-model: <new>`) and (for streaming) a single
SSE event prepended to the body announcing the substitution.

If the fallback model is the empty string OR equals the requested model,
no fallback is attempted and the original 429 surfaces unchanged.
"""
from __future__ import annotations

import json
from typing import Callable

import httpx

from spillover.logging import get_logger

log = get_logger("fallback")

_RETRYABLE_STATUS = {429, 502, 503, 504, 529}


def should_fallback(resp: httpx.Response, fallback_model: str, current_model: str) -> bool:
    if not fallback_model:
        return False
    if fallback_model == current_model:
        return False
    return resp.status_code in _RETRYABLE_STATUS


def rewrite_payload_model(payload_bytes: bytes, new_model: str) -> bytes:
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return payload_bytes
    payload["model"] = new_model
    return json.dumps(payload).encode("utf-8")


def make_sse_banner(original_model: str, fallback_model: str) -> bytes:
    """One SSE event announcing the substitution (non-disruptive to client parsers)."""
    msg = (
        f"event: spillover_fallback\n"
        f"data: "
        + json.dumps(
            {
                "type": "spillover_fallback",
                "original_model": original_model,
                "fallback_model": fallback_model,
                "reason": "upstream rate limit on requested model",
            }
        )
        + "\n\n"
    )
    return msg.encode("utf-8")


async def attempt_fallback_unary(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    payload_bytes: bytes,
    current_model: str,
    fallback_model: str,
) -> tuple[httpx.Response, bool]:
    """Try fallback with retry. Returns (response, used_fallback)."""
    if not fallback_model or fallback_model == current_model:
        return None, False  # type: ignore[return-value]
    from spillover.proxy.retry import with_retry
    new_body = rewrite_payload_model(payload_bytes, fallback_model)
    new_headers = {**headers}
    new_headers["content-length"] = str(len(new_body))
    log.warning(
        "model fallback %s -> %s (upstream 429 on primary)",
        current_model,
        fallback_model,
    )

    async def _post():
        return await client.post(url, headers=new_headers, content=new_body)

    resp = await with_retry(_post)
    return resp, True


async def attempt_fallback_stream(
    client: httpx.AsyncClient,
    build_request: Callable[[bytes], httpx.Request],
    payload_bytes: bytes,
    current_model: str,
    fallback_model: str,
) -> tuple[httpx.Response, bool]:
    """Streaming variant with retry. Returns (open response, used_fallback)."""
    if not fallback_model or fallback_model == current_model:
        return None, False  # type: ignore[return-value]
    from spillover.proxy.retry import with_retry_stream
    new_body = rewrite_payload_model(payload_bytes, fallback_model)
    log.warning(
        "model fallback (stream) %s -> %s",
        current_model,
        fallback_model,
    )

    def _build():
        return build_request(new_body)

    resp = await with_retry_stream(client, _build)
    return resp, True
