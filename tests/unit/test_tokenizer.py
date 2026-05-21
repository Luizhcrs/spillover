from spillover.eviction.tokenizer import count_tokens


def test_count_tokens_simple_string():
    n = count_tokens("hello world")
    assert isinstance(n, int)
    assert n > 0
    assert n < 20  # sanity


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_anthropic_message():
    msg = {"role": "user", "content": "What's the capital of France?"}
    n = count_tokens(msg)
    assert n > 0


def test_count_tokens_memoized():
    s = "the quick brown fox " * 50
    n1 = count_tokens(s)
    n2 = count_tokens(s)
    assert n1 == n2
