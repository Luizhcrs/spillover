import json

import httpx
import pytest

from spillover.proxy.fallback import (
    attempt_fallback_stream,
    attempt_fallback_unary,
    make_sse_banner,
    rewrite_payload_model,
    should_fallback,
)


def test_should_fallback_when_429_and_different_model():
    r = httpx.Response(429)
    assert should_fallback(r, "claude-haiku-4-5-20251001", "claude-sonnet-4-6")


def test_should_not_fallback_when_no_fallback_configured():
    r = httpx.Response(429)
    assert not should_fallback(r, "", "claude-sonnet-4-6")


def test_should_not_fallback_when_same_model():
    r = httpx.Response(429)
    assert not should_fallback(r, "claude-sonnet-4-6", "claude-sonnet-4-6")


def test_should_not_fallback_on_200():
    r = httpx.Response(200)
    assert not should_fallback(r, "claude-haiku-4-5-20251001", "claude-sonnet-4-6")


def test_should_fallback_on_503_and_529():
    for code in (502, 503, 504, 529):
        assert should_fallback(
            httpx.Response(code), "claude-haiku-4-5-20251001", "claude-sonnet-4-6"
        )


def test_rewrite_payload_model_swaps_field():
    body = json.dumps({"model": "claude-sonnet-4-6", "max_tokens": 10}).encode()
    new = rewrite_payload_model(body, "claude-haiku-4-5-20251001")
    obj = json.loads(new)
    assert obj["model"] == "claude-haiku-4-5-20251001"
    assert obj["max_tokens"] == 10


def test_rewrite_payload_model_handles_invalid_json():
    body = b"not json"
    assert rewrite_payload_model(body, "haiku") == body


def test_make_sse_banner_format():
    raw = make_sse_banner("claude-sonnet-4-6", "claude-haiku-4-5-20251001")
    text = raw.decode("utf-8")
    assert text.startswith("event: spillover_fallback\n")
    assert "claude-sonnet-4-6" in text
    assert "claude-haiku-4-5-20251001" in text
    assert text.endswith("\n\n")


@pytest.mark.asyncio
async def test_attempt_fallback_unary_no_op_when_disabled():
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    client = httpx.AsyncClient(transport=transport, base_url="http://t")
    resp, used = await attempt_fallback_unary(
        client, "/x", {}, b'{"model":"a"}', "a", ""
    )
    assert not used
    assert resp is None
    await client.aclose()


@pytest.mark.asyncio
async def test_attempt_fallback_unary_swaps_and_calls():
    seen_models = []

    async def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen_models.append(body["model"])
        return httpx.Response(200, json={"ok": True, "model": body["model"]})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://t")
    body = json.dumps({"model": "claude-sonnet-4-6", "messages": []}).encode()
    resp, used = await attempt_fallback_unary(
        client, "/v1/messages", {}, body, "claude-sonnet-4-6", "claude-haiku-4-5-20251001"
    )
    assert used is True
    assert resp.status_code == 200
    assert seen_models == ["claude-haiku-4-5-20251001"]
    await client.aclose()


@pytest.mark.asyncio
async def test_attempt_fallback_stream_swaps_and_opens():
    seen_models = []

    async def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen_models.append(body["model"])
        return httpx.Response(200, content=b"data: ok\n\n")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://t")
    body = json.dumps({"model": "claude-sonnet-4-6", "messages": [], "stream": True}).encode()

    def build(new_body: bytes):
        return client.build_request("POST", "/v1/messages", content=new_body)

    resp, used = await attempt_fallback_stream(
        client, build, body, "claude-sonnet-4-6", "claude-haiku-4-5-20251001"
    )
    assert used is True
    assert resp.status_code == 200
    assert seen_models == ["claude-haiku-4-5-20251001"]
    await resp.aclose()
    await client.aclose()
