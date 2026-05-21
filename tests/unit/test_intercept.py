from spillover.counter_compact.intercept import (
    make_intercept_response,
    should_intercept_request,
)


def test_intercept_detects_english_compact():
    payload = {
        "messages": [
            {"role": "user", "content": "Please compact the conversation so far"}
        ]
    }
    assert should_intercept_request(payload) is True


def test_intercept_detects_ptbr_compact():
    payload = {
        "messages": [
            {"role": "user", "content": "Resuma a conversa para liberar contexto"}
        ]
    }
    assert should_intercept_request(payload) is True


def test_intercept_ignores_normal_message():
    payload = {
        "messages": [{"role": "user", "content": "fix the bug in auth"}]
    }
    assert should_intercept_request(payload) is False


def test_intercept_ignores_assistant_last_turn():
    payload = {
        "messages": [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "compact the conversation"},
        ]
    }
    assert should_intercept_request(payload) is False


def test_intercept_empty_messages():
    assert should_intercept_request({"messages": []}) is False
    assert should_intercept_request({}) is False


def test_intercept_with_content_blocks():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "please summarize this conversation"}
                ],
            }
        ]
    }
    assert should_intercept_request(payload) is True


def test_make_intercept_response_shape():
    r = make_intercept_response({"model": "claude-opus-4-7"})
    assert r["role"] == "assistant"
    assert r["model"] == "claude-opus-4-7"
    assert r["spillover_intercepted"] is True
    assert r["content"][0]["type"] == "text"
    assert "spillover" in r["content"][0]["text"]
    assert r["stop_reason"] == "end_turn"
