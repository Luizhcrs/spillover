from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from spillover.logging import get_logger

log = get_logger("retry")
T = TypeVar("T")

_RETRYABLE_STATUS = {429, 502, 503, 504, 529}
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _default_max_attempts() -> int:
    return _env_int("SPILLOVER_RETRY_MAX_ATTEMPTS", 5)


def _default_base_delay() -> float:
    return _env_float("SPILLOVER_RETRY_BASE_DELAY", 0.5)


def _default_cap() -> float:
    return _env_float("SPILLOVER_RETRY_CAP", 60.0)


def _retry_after_seconds(resp: httpx.Response | None) -> float | None:
    if resp is None:
        return None
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    reset = resp.headers.get("anthropic-ratelimit-tokens-reset") or resp.headers.get(
        "anthropic-ratelimit-requests-reset"
    )
    if reset:
        try:
            import datetime as _dt
            target = _dt.datetime.fromisoformat(reset.replace("Z", "+00:00"))
            now = _dt.datetime.now(_dt.timezone.utc)
            return max(0.0, (target - now).total_seconds())
        except Exception:
            pass
    return None


def _backoff(attempt: int, base: float, cap: float, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(cap, max(retry_after, base))
    delay = min(cap, base * (4 ** (attempt - 1)))
    return delay + random.uniform(0, delay * 0.1)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int | None = None,
    base_delay: float | None = None,
    cap: float | None = None,
) -> T:
    ma = max_attempts if max_attempts is not None else _default_max_attempts()
    bd = base_delay if base_delay is not None else _default_base_delay()
    cp = cap if cap is not None else _default_cap()

    last_exc: Exception | None = None
    for attempt in range(1, ma + 1):
        retry_after: float | None = None
        try:
            result = await fn()
        except _RETRYABLE_EXC as e:
            last_exc = e
            if attempt == ma:
                raise
        else:
            if isinstance(result, httpx.Response) and result.status_code in _RETRYABLE_STATUS:
                retry_after = _retry_after_seconds(result)
                if attempt == ma:
                    return result
            else:
                return result
        delay = _backoff(attempt, bd, cp, retry_after)
        log.warning("retry attempt=%d delay=%.2fs", attempt, delay)
        await asyncio.sleep(delay)
    if last_exc is not None:
        raise last_exc
    return result  # type: ignore[return-value]


async def with_retry_stream(
    client: httpx.AsyncClient,
    build_request: Callable[[], httpx.Request],
    *,
    max_attempts: int | None = None,
    base_delay: float | None = None,
    cap: float | None = None,
) -> httpx.Response:
    """Send a streaming request with retry on retryable status / exceptions.

    Returns an *open* `httpx.Response` (stream=True). Caller is responsible
    for closing it. If all attempts return a retryable status, the final
    response is returned open so the upstream error reaches the client.
    """
    ma = max_attempts if max_attempts is not None else _default_max_attempts()
    bd = base_delay if base_delay is not None else _default_base_delay()
    cp = cap if cap is not None else _default_cap()

    for attempt in range(1, ma + 1):
        retry_after: float | None = None
        resp: httpx.Response | None = None
        try:
            resp = await client.send(build_request(), stream=True)
        except _RETRYABLE_EXC as e:
            if attempt == ma:
                raise
            delay = _backoff(attempt, bd, cp, None)
            log.warning(
                "retry(stream) exc=%s attempt=%d delay=%.2fs",
                type(e).__name__,
                attempt,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        if resp.status_code in _RETRYABLE_STATUS:
            retry_after = _retry_after_seconds(resp)
            if attempt == ma:
                return resp
            await resp.aclose()
            delay = _backoff(attempt, bd, cp, retry_after)
            log.warning(
                "retry(stream) status=%d attempt=%d delay=%.2fs",
                resp.status_code,
                attempt,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        return resp

    raise RuntimeError("with_retry_stream: unreachable")
