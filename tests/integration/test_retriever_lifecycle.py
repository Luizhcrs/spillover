import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app
from spillover.storage.sqlite import open_project_db


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _resp(input_tokens, output_tokens, text="ok"):
    return httpx.Response(
        200,
        json={
            "id": "msg",
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )


@pytest.mark.slow
@respx.mock
def test_archived_episode_becomes_retrievable(client, config):
    """End-to-end: an evicted turn in request 1 must appear as LTM in request 2."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_resp(900, 80, text="ok")
    )
    pid = "abcdef12"

    messages = []
    for i in range(12):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append(
            {"role": role, "content": f"turn {i} about config/foo.py setting watermark"}
        )
    r1 = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": messages,
        },
    )
    assert r1.status_code == 200

    # Wait up to 60s for facet worker to drain (model load + embed)
    db = open_project_db(config.db_root, pid)
    try:
        deadline = time.time() + 60
        pending = -1
        while time.time() < deadline:
            pending = db.execute(
                "SELECT COUNT(*) FROM episodes WHERE facet_pending=1"
            ).fetchone()[0]
            if pending == 0:
                break
            time.sleep(0.5)
        assert pending == 0, "facet worker did not drain in time"
        vec_count = db.execute("SELECT COUNT(*) FROM vec_episodes").fetchone()[0]
        assert vec_count > 0
    finally:
        db.close()

    # Request 2: mention foo.py to trigger LTM retrieval
    r2 = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "remind me what we did with foo.py"}
            ],
        },
    )
    assert r2.status_code == 200

    last_request = route.calls.last.request
    body = last_request.read().decode("utf-8")
    assert "<spillover-ltm>" in body
    assert "foo.py" in body
