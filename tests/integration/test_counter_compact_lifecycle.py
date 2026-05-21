import httpx
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app
from spillover.storage.sqlite import open_project_db


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


@respx.mock
def test_intercept_short_circuits_compact_request(config):
    """A user message asking for compaction is intercepted and never forwarded."""
    app = create_app(config)
    with TestClient(app) as client:
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=_resp(10, 10)
        )
        r = client.post(
            "/v1/messages",
            headers={"X-Project": "abcdef12", "Authorization": "Bearer t"},
            json={
                "model": "claude-opus-4-7",
                "max_tokens": 100,
                "messages": [
                    {"role": "user", "content": "compact the conversation so far"}
                ],
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body.get("spillover_intercepted") is True
    assert route.call_count == 0  # never forwarded


@respx.mock
def test_usage_rewrite_subtracts_archived(config):
    """Non-streaming response usage.input_tokens is rewritten when eviction archived."""
    app = create_app(config)
    with TestClient(app) as client:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=_resp(900, 80)
        )
        pid = "abcdef12"
        messages = []
        for i in range(12):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append({"role": role, "content": "x" * 320})
        r = client.post(
            "/v1/messages",
            headers={"X-Project": pid, "Authorization": "Bearer t"},
            json={
                "model": "claude-opus-4-7",
                "max_tokens": 100,
                "messages": messages,
            },
        )
    assert r.status_code == 200
    body = r.json()
    usage = body["usage"]
    assert "spillover_real_input_tokens" in usage
    assert usage["spillover_real_input_tokens"] == 900
    assert usage["input_tokens"] < 900  # subtracted


@respx.mock
def test_compaction_detection_rescues_dropped_turns(config):
    """Two-request flow: round-trip 1 sees assistant turns; round-trip 2 sends
    a 'summary' that drops them. Proxy rescues the missing turns."""
    app = create_app(config)
    with TestClient(app) as client:
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=_resp(50, 50)
        )
        pid = "abcdef12"
        # Round-trip 1: substantial history
        r1 = client.post(
            "/v1/messages",
            headers={"X-Project": pid, "Authorization": "Bearer t"},
            json={
                "model": "claude-opus-4-7",
                "max_tokens": 100,
                "messages": [
                    {"role": "user", "content": "u1"},
                    {"role": "assistant", "content": "a1 about foo.py"},
                    {"role": "user", "content": "u2"},
                    {"role": "assistant", "content": "a2 about bar.py"},
                    {"role": "user", "content": "u3"},
                ],
            },
        )
        assert r1.status_code == 200

        # Round-trip 2: client compacts a1+a2 into a summary
        r2 = client.post(
            "/v1/messages",
            headers={"X-Project": pid, "Authorization": "Bearer t"},
            json={
                "model": "claude-opus-4-7",
                "max_tokens": 100,
                "messages": [
                    {"role": "assistant", "content": "SUMMARY: discussed foo.py and bar.py"},
                    {"role": "user", "content": "now do the thing"},
                ],
            },
        )
        assert r2.status_code == 200

    # The proxy should have archived a1 and a2 as rescued episodes
    db = open_project_db(config.db_root, pid)
    try:
        rescued_count = db.execute(
            "SELECT COUNT(*) FROM episodes WHERE compaction_rescued=1"
        ).fetchone()[0]
        assert rescued_count == 2
    finally:
        db.close()
