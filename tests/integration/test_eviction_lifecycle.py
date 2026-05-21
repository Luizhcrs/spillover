import hashlib

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app
from spillover.storage.sqlite import open_project_db


@pytest.fixture
def client(config):
    return TestClient(create_app(config))


def _upstream_resp(input_tokens: int, output_tokens: int, text: str = "ok"):
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    )


@respx.mock
def test_no_eviction_below_watermark(client, config):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_upstream_resp(500, 50)  # 550 of 1000 -> 0.55 < 0.85
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "p1", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "x" * 100}],
        },
    )
    assert r.status_code == 200

    # "p1" is 2 chars — middleware sha1-hashes it
    pid = hashlib.sha1(b"p1").hexdigest()
    db = open_project_db(config.db_root, pid)
    try:
        count = db.execute("SELECT COUNT(*) FROM episodes WHERE evicted=1").fetchone()[0]
        assert count == 0
    finally:
        db.close()


@respx.mock
def test_eviction_triggers_above_watermark(client, config):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_upstream_resp(900, 80)  # 980 of 1000 -> 0.98 > 0.85
    )
    # Big conversation: 12 turns, each ~80 tokens
    messages = []
    for i in range(12):
        messages.append(
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 320}
        )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "p2", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": messages,
        },
    )
    assert r.status_code == 200

    # "p2" is 2 chars — middleware sha1-hashes it
    pid = hashlib.sha1(b"p2").hexdigest()
    db = open_project_db(config.db_root, pid)
    try:
        evicted = db.execute(
            "SELECT COUNT(*) FROM episodes WHERE evicted=1"
        ).fetchone()[0]
        assert evicted > 0
        freed = db.execute(
            "SELECT SUM(token_count) FROM episodes WHERE evicted=1"
        ).fetchone()[0]
        assert freed >= 80
    finally:
        db.close()


@respx.mock
def test_eviction_uses_last_user_turn_not_last_assistant(client, config):
    """If the conversation ends with an assistant turn (malformed input), the
    eviction should still find the last user turn rather than misattributing."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_upstream_resp(900, 80)
    )
    messages = []
    for i in range(11):
        messages.append(
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 320}
        )
    # Add a trailing assistant message so the last turn is NOT role=user
    messages.append({"role": "assistant", "content": "x" * 320})
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "abc123def", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": messages,
        },
    )
    assert r.status_code == 200
    # No assertion failure inside _maybe_evict, eviction ran
    db = open_project_db(config.db_root, "abc123def")
    try:
        evicted = db.execute(
            "SELECT COUNT(*) FROM episodes WHERE evicted=1"
        ).fetchone()[0]
        assert evicted > 0
    finally:
        db.close()


@respx.mock
def test_eviction_archives_actual_content(client, config):
    """Verify the archived rows contain the original turn content, not tool_calls
    or some swapped field."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_upstream_resp(900, 80)
    )
    messages = []
    for i in range(12):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"turn-{i:02d}-" + ("y" * 300)})
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "fedcba98", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": messages,
        },
    )
    assert r.status_code == 200
    import json as _json
    db = open_project_db(config.db_root, "fedcba98")
    try:
        rows = db.execute(
            "SELECT content_json FROM episodes WHERE evicted=1 ORDER BY id"
        ).fetchall()
        assert len(rows) > 0
        for row in rows:
            content = _json.loads(row["content_json"])
            assert isinstance(content, str)
            assert content.startswith("turn-")
    finally:
        db.close()
