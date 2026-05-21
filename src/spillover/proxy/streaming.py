from __future__ import annotations

from collections.abc import AsyncIterator


async def duplicate_stream(
    source: AsyncIterator[bytes],
    sink: list[bytes],
) -> AsyncIterator[bytes]:
    async for chunk in source:
        sink.append(chunk)
        yield chunk
