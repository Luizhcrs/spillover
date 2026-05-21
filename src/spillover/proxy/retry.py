from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from spillover.logging import get_logger

log = get_logger("retry")
T = TypeVar("T")

_RETRYABLE_STATUS = {429, 502, 503, 504}
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    cap: float = 16.0,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await fn()
        except _RETRYABLE_EXC as e:
            last_exc = e
            if attempt == max_attempts:
                raise
        else:
            if isinstance(result, httpx.Response) and result.status_code in _RETRYABLE_STATUS:
                if attempt == max_attempts:
                    return result
            else:
                return result
        delay = min(cap, base_delay * (4 ** (attempt - 1)))
        jitter = random.uniform(0, delay * 0.1)
        log.warning("retry attempt=%d delay=%.2fs", attempt, delay + jitter)
        await asyncio.sleep(delay + jitter)
    if last_exc is not None:
        raise last_exc
    return result  # type: ignore[return-value]
