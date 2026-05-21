import httpx
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


def make_client(config):
    app = create_app(config)
    return TestClient(app)


@respx.mock
def test_content_chunks_pass_through_unbuffered(config):
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
    # Big enough to trigger eviction so rewrite fires
    messages = []
    for i in range(12):
        messages.append(
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 320}
        )
    with make_client(config) as client:
        r = client.post(
            "/v1/messages",
            headers={"X-Project": "abcdef12", "Authorization": "Bearer t"},
            json={
                "model": "claude-opus-4-7",
                "max_tokens": 100,
                "stream": True,
                "messages": messages,
            },
        )
    assert r.status_code == 200
    body = r.content
    # Content chunk present
    assert b"content_block_delta" in body
    # Usage chunk rewritten
    assert b"spillover_real_input_tokens" in body
