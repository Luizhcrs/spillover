import httpx
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


def make_client(config):
    app = create_app(config)
    return TestClient(app)


@respx.mock
def test_metrics_increment_after_request(config):
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
        r = client.post(
            "/v1/messages",
            headers={"X-Project": "abcdef12", "Authorization": "Bearer t"},
            json={
                "model": "claude-opus-4-7",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert r.status_code == 200

        m = client.get("/metrics")
    assert m.status_code == 200
    body = m.text
    # The label line must appear with status=200
    assert 'project="abcdef12",provider="anthropic",status="200"' in body
    # The value must be a positive float (>= 1.0)
    import re
    pattern = r'spillover_requests_total\{[^}]*project="abcdef12"[^}]*\}\s+([\d.]+)'
    match = re.search(pattern, body)
    assert match is not None, "spillover_requests_total not found in metrics body"
    assert float(match.group(1)) >= 1.0
