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
def test_openai_passthrough_non_streaming(client):
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 1,
                    "total_tokens": 6,
                },
            },
        )
    )
    r = client.post(
        "/v1/chat/completions",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer sk-test"},
        json={
            "model": "gpt-4o",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "hi"


@respx.mock
def test_openai_passthrough_4xx(client):
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    r = client.post(
        "/v1/chat/completions",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer bad"},
        json={
            "model": "gpt-4o",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 401
