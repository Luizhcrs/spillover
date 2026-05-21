from spillover.counter_compact.usage_rewrite import (
    rewrite_response_json,
    rewrite_usage,
)


def test_rewrite_subtracts_archived():
    u = rewrite_usage({"input_tokens": 1000, "output_tokens": 50}, 400)
    assert u["input_tokens"] == 600
    assert u["spillover_real_input_tokens"] == 1000
    assert u["output_tokens"] == 50


def test_rewrite_floors_at_1():
    u = rewrite_usage({"input_tokens": 100}, 200)
    assert u["input_tokens"] == 1


def test_rewrite_no_usage():
    assert rewrite_usage(None, 5) is None
    assert rewrite_usage({}, 5) == {}


def test_rewrite_response_json_passthrough_without_usage():
    body = {"id": "msg", "content": []}
    out = rewrite_response_json(body, 100)
    assert out == body


def test_rewrite_response_json_with_usage():
    body = {"usage": {"input_tokens": 800, "output_tokens": 100}}
    out = rewrite_response_json(body, 300)
    assert out["usage"]["input_tokens"] == 500
    # Original body untouched
    assert body["usage"]["input_tokens"] == 800
