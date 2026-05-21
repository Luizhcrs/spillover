import httpx
import pytest

from spillover.proxy.retry import (
    _retry_after_seconds,
    with_retry,
    with_retry_stream,
)


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
async def test_retries_on_429_until_success():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429)
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


def test_retry_after_header_parsed():
    r = httpx.Response(429, headers={"retry-after": "2.5"})
    assert _retry_after_seconds(r) == 2.5


def test_retry_after_none_when_absent():
    assert _retry_after_seconds(httpx.Response(429)) is None


@pytest.mark.asyncio
async def test_stream_retries_429_until_success():
    """with_retry_stream: 429 then 200 reconnects without leaking 429."""
    calls = 0
    transport_responses = [
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(200, content=b"data: ok\n\n"),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        resp = transport_responses[min(calls, len(transport_responses) - 1)]
        calls += 1
        return resp

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    def build():
        return client.build_request("POST", "/v1/messages", content=b"{}")

    resp = await with_retry_stream(
        client, build, max_attempts=5, base_delay=0.01, cap=0.05
    )
    assert resp.status_code == 200
    assert calls == 3
    await resp.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_returns_final_429_after_exhaustion():
    """If all attempts return 429, surface the final response open."""
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, content=b"")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    def build():
        return client.build_request("POST", "/v1/messages", content=b"{}")

    resp = await with_retry_stream(
        client, build, max_attempts=3, base_delay=0.01, cap=0.05
    )
    assert resp.status_code == 429
    assert calls == 3
    await resp.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_retries_on_connect_error():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise httpx.ConnectError("flaky")
        return httpx.Response(200, content=b"data: ok\n\n")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    def build():
        return client.build_request("POST", "/v1/messages", content=b"{}")

    resp = await with_retry_stream(
        client, build, max_attempts=3, base_delay=0.01, cap=0.05
    )
    assert resp.status_code == 200
    assert calls == 2
    await resp.aclose()
    await client.aclose()
