import httpx
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


def make_client(config):
    app = create_app(config)
    return TestClient(app)


@respx.mock
def test_proxy_accepts_request_with_env_project_only(config, monkeypatch):
    monkeypatch.setenv("SPILLOVER_PROJECT_ID", "abcdef1234")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg",
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
    )
    with make_client(config) as client:
        # Note: no X-Project header
        r = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer t"},
            json={
                "model": "claude-opus-4-7",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
