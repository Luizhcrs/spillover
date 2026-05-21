import httpx
import pytest

from spillover.proxy.retry import with_retry


@pytest.mark.asyncio
async def test_returns_immediately_on_2xx():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True})

    r = await with_retry(fn)
    assert r.status_code == 200
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_on_503_until_success():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200)

    r = await with_retry(fn, base_delay=0.01, cap=0.05)
    assert r.status_code == 200
    assert calls == 3


@pytest.mark.asyncio
async def test_returns_last_5xx_after_exhaustion():
    async def fn():
        return httpx.Response(503)

    r = await with_retry(fn, max_attempts=2, base_delay=0.01, cap=0.05)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_raises_after_repeated_connect_error():
    async def fn():
        raise httpx.ConnectError("boom")

    with pytest.raises(httpx.ConnectError):
        await with_retry(fn, max_attempts=2, base_delay=0.01, cap=0.05)
