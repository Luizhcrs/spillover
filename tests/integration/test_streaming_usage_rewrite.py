import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as c:
        yield c


@respx.mock
def test_streaming_usage_rewrite_applied(client, config):
    sse = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"text":"ok"}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop","usage":{"input_tokens":900,"output_tokens":50}}\n\n'
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=sse,
            headers={"content-type": "text/event-stream"},
        )
    )
    pid = "abcdef12"
    messages = []
    for i in range(12):
        messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 320})
    r = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "stream": True,
            "messages": messages,
        },
    )
    assert r.status_code == 200
    assert b"spillover_real_input_tokens" in r.content


@respx.mock
def test_streaming_rewrite_disabled_via_env(client, monkeypatch):
    monkeypatch.setenv("SPILLOVER_STREAM_REWRITE", "0")
    sse = (
        b'event: message_stop\ndata: {"type":"message_stop","usage":{"input_tokens":900,"output_tokens":50}}\n\n'
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}),
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    # No rewrite -> real_input_tokens absent
    assert b"spillover_real_input_tokens" not in r.content
