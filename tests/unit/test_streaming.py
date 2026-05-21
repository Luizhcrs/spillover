import pytest

from spillover.proxy.streaming import duplicate_stream


@pytest.mark.asyncio
async def test_duplicate_stream_yields_chunks_and_captures():
    async def source():
        for chunk in [b"a", b"bc", b"def"]:
            yield chunk

    captured: list[bytes] = []
    out: list[bytes] = []
    async for chunk in duplicate_stream(source(), captured):
        out.append(chunk)
    assert out == [b"a", b"bc", b"def"]
    assert b"".join(captured) == b"abcdef"
