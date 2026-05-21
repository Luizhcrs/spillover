import json

from spillover.counter_compact.sse_rewrite import rewrite_sse_body


def test_rewrite_message_stop_usage():
    body = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop","usage":{"input_tokens":1000,"output_tokens":50}}\n\n'
    )
    out = rewrite_sse_body(body, 400)
    assert b"input_tokens" in out
    # Extract and check
    for line in out.splitlines():
        if line.startswith(b"data: ") and b"usage" in line:
            obj = json.loads(line[len(b"data: "):])
            assert obj["usage"]["input_tokens"] == 600
            assert obj["usage"]["spillover_real_input_tokens"] == 1000
            break
    else:
        raise AssertionError("usage line not found")


def test_rewrite_message_delta_nested_usage():
    body = (
        b'event: message_delta\ndata: {"type":"message_delta","message":{"usage":{"input_tokens":800}}}\n\n'
    )
    out = rewrite_sse_body(body, 300)
    obj = None
    for line in out.splitlines():
        if line.startswith(b"data:") and b"usage" in line:
            obj = json.loads(line[len(b"data: "):])
            break
    assert obj is not None
    assert obj["message"]["usage"]["input_tokens"] == 500


def test_rewrite_no_op_when_archived_zero():
    body = b'data: {"usage":{"input_tokens":100}}\n\n'
    assert rewrite_sse_body(body, 0) == body


def test_rewrite_no_op_when_no_usage():
    body = b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'
    assert rewrite_sse_body(body, 50) == body
