from spillover.adapters.openai import OpenAIAdapter
from spillover.adapters.base import Conversation, ConversationTurn


def test_parse_basic():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    }
    conv = OpenAIAdapter().parse(payload)
    assert conv.model == "gpt-4o"
    assert conv.max_tokens == 100
    assert conv.system == "be brief"
    assert len(conv.turns) == 2
    assert conv.turns[0].role == "user"


def test_parse_multiple_system_messages_concatenated():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "messages": [
            {"role": "system", "content": "rule 1"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "rule 2"},
        ],
    }
    conv = OpenAIAdapter().parse(payload)
    assert conv.system == "rule 1\n\nrule 2"


def test_parse_extra_preserved():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "stream": True,
        "temperature": 0.7,
        "messages": [{"role": "user", "content": "hi"}],
    }
    conv = OpenAIAdapter().parse(payload)
    assert conv.extra.get("stream") is True
    assert conv.extra.get("temperature") == 0.7


def test_parse_tool_calls():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "messages": [
            {
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            }
        ],
    }
    conv = OpenAIAdapter().parse(payload)
    assert len(conv.turns[0].tool_calls) == 1
    assert conv.turns[0].tool_calls[0]["id"] == "tc1"


def test_build_roundtrip():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "stream": True,
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
    }
    adapter = OpenAIAdapter()
    rebuilt = adapter.build(adapter.parse(payload))
    assert rebuilt["model"] == "gpt-4o"
    assert rebuilt["max_tokens"] == 100
    assert rebuilt["stream"] is True
    assert {"role": "system", "content": "be brief"} in rebuilt["messages"]
    assert {"role": "user", "content": "hi"} in rebuilt["messages"]


def test_build_omits_system_when_none():
    conv = Conversation(
        system=None,
        turns=[ConversationTurn(role="user", content="hi", tool_calls=[], token_count=1)],
        model="gpt-4o",
        max_tokens=100,
    )
    rebuilt = OpenAIAdapter().build(conv)
    assert all(m["role"] != "system" for m in rebuilt["messages"])
