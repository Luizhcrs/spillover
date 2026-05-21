import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    return TestClient(app)


@respx.mock
def test_passthrough_non_streaming(client):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "hi back"}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        )
    )
    r = client.post(
        "/v1/messages",
        headers={
            "X-Project": "proj_test",
            "Authorization": "Bearer test-key",
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content"][0]["text"] == "hi back"


@respx.mock
def test_passthrough_streaming(client):
    sse_stop = b'{"type":"message_stop","usage":{"input_tokens":5,"output_tokens":1}}'
    sse_body = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b'event: content_block_delta\ndata: {"delta":{"text":"hi"}}\n\n'
        b"event: message_stop\ndata: " + sse_stop + b"\n\n"
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "proj_test", "Authorization": "Bearer test"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    assert b"message_stop" in r.content


@respx.mock
def test_passthrough_propagates_upstream_4xx_non_streaming(client):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "p", "Authorization": "Bearer bad"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


@respx.mock
def test_passthrough_propagates_upstream_4xx_streaming(client):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            429,
            content=b'event: error\ndata: {"type":"rate_limit"}\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "p", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 10,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 429


def test_invalid_json_returns_400(client):
    r = client.post(
        "/v1/messages",
        headers={
            "X-Project": "p",
            "Authorization": "Bearer t",
            "content-type": "application/json",
        },
        content=b"{not valid json",
    )
    assert r.status_code == 400
    assert "invalid JSON" in r.json()["error"]
