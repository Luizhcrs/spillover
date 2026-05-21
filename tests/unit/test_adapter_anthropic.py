from spillover.adapters.anthropic import AnthropicAdapter


def test_parse_minimal():
    payload = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "system": "you are helpful",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    }
    adapter = AnthropicAdapter()
    conv = adapter.parse(payload)
    assert conv.model == "claude-opus-4-7"
    assert conv.max_tokens == 1024
    assert conv.system == "you are helpful"
    assert conv.system_tokens > 0
    assert len(conv.turns) == 2
    assert conv.turns[0].role == "user"
    assert conv.turns[1].role == "assistant"
    assert all(t.token_count > 0 for t in conv.turns)


def test_parse_content_blocks():
    payload = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "thinking..."},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "Read",
                        "input": {"file_path": "/x"},
                    },
                ],
            }
        ],
    }
    conv = AnthropicAdapter().parse(payload)
    assert len(conv.turns) == 1
    assert len(conv.turns[0].tool_calls) == 1
    assert conv.turns[0].tool_calls[0]["name"] == "Read"


def test_parse_extra_preserved():
    payload = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "stream": True,
        "metadata": {"user_id": "abc"},
        "messages": [{"role": "user", "content": "hi"}],
    }
    conv = AnthropicAdapter().parse(payload)
    assert conv.extra.get("stream") is True
    assert conv.extra.get("metadata") == {"user_id": "abc"}
