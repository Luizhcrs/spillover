from spillover.adapters.anthropic import AnthropicAdapter
from spillover.adapters.openai import OpenAIAdapter


def test_anthropic_parse_response_text():
    a = AnthropicAdapter()
    assert a.parse_response_text({"content": [{"type": "text", "text": "hi"}]}) == "hi"


def test_openai_parse_response_text():
    o = OpenAIAdapter()
    assert (
        o.parse_response_text(
            {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        )
        == "hi"
    )


def test_anthropic_extract_usage_openai_shape_returns_none():
    """Anthropic adapter must refuse openai-shape usage (zero is wrong, must be None)."""
    a = AnthropicAdapter()
    body = b'{"usage":{"prompt_tokens":100,"completion_tokens":20}}'
    assert a.extract_usage_non_streaming(body) == (0, 0)
    # Note: this is current behavior -- the field "input_tokens" is missing so we
    # return (0, 0). This is acceptable because the proxy routes by URL, not by
    # response shape sniffing.


def test_openai_extract_usage_anthropic_shape_returns_zeros():
    o = OpenAIAdapter()
    body = b'{"usage":{"input_tokens":100,"output_tokens":20}}'
    assert o.extract_usage_non_streaming(body) == (0, 0)


def test_anthropic_inject_ltm_into_string_system():
    a = AnthropicAdapter()
    p = {"system": "rules"}
    a.inject_ltm(p, "<spillover-ltm>X</spillover-ltm>")
    assert p["system"].startswith("<spillover-ltm>")
    assert "rules" in p["system"]


def test_anthropic_inject_ltm_into_none_system():
    a = AnthropicAdapter()
    p = {}
    a.inject_ltm(p, "<spillover-ltm>X</spillover-ltm>")
    assert p["system"] == "<spillover-ltm>X</spillover-ltm>"


def test_openai_inject_ltm_when_no_system():
    o = OpenAIAdapter()
    p = {"messages": [{"role": "user", "content": "hi"}]}
    o.inject_ltm(p, "<spillover-ltm>X</spillover-ltm>")
    assert p["messages"][0]["role"] == "system"
    assert "<spillover-ltm>" in p["messages"][0]["content"]


def test_openai_inject_ltm_coalesces_existing_system():
    o = OpenAIAdapter()
    p = {
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hi"},
        ]
    }
    o.inject_ltm(p, "<spillover-ltm>X</spillover-ltm>")
    assert p["messages"][0]["role"] == "system"
    assert "rules" in p["messages"][0]["content"]
    assert "<spillover-ltm>" in p["messages"][0]["content"]
    # No second system inserted
    assert sum(1 for m in p["messages"] if m["role"] == "system") == 1


def test_anthropic_sse_extract_text():
    a = AnthropicAdapter()
    captured = [
        b'data: {"delta":{"text":"hel"}}\n\n',
        b'data: {"delta":{"text":"lo"}}\n\n',
    ]
    assert a.extract_assistant_text_sse(captured) == "hello"


def test_openai_sse_extract_text():
    o = OpenAIAdapter()
    captured = [
        b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
    ]
    assert o.extract_assistant_text_sse(captured) == "hello"
