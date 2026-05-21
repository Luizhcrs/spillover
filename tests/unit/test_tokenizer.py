from spillover.eviction.tokenizer import _count_for_text, count_tokens


def test_count_tokens_simple_string():
    n = count_tokens("hello world")
    assert n == 2  # 11 chars // 4 = 2


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_none_returns_zero():
    assert count_tokens(None) == 0


def test_count_tokens_anthropic_message():
    msg = {"role": "user", "content": "What's the capital of France?"}
    n = count_tokens(msg)
    assert n > 0


def test_count_tokens_ptbr_accented():
    n = count_tokens("Olá, você está aí?")
    assert n > 0


def test_count_tokens_nested_tool_call():
    payload = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I'll read the file."},
            {
                "type": "tool_use",
                "id": "tu_1",
                "name": "Read",
                "input": {"file_path": "/tmp/x.txt"},
            },
        ],
    }
    n = count_tokens(payload)
    assert n > 0


def test_count_tokens_dict_order_stable():
    a = count_tokens({"a": 1, "b": 2})
    b = count_tokens({"b": 2, "a": 1})
    assert a == b


def test_count_tokens_bytes_decoded():
    n = count_tokens(b"hello world")
    assert n == count_tokens("hello world")


def test_count_tokens_memoization_actually_hits():
    _count_for_text.cache_clear()
    s = "the quick brown fox " * 50
    count_tokens(s)
    count_tokens(s)
    info = _count_for_text.cache_info()
    assert info.hits == 1
    assert info.misses == 1
