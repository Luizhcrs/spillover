from unittest.mock import patch

from spillover.proxy.app import _inject_ltm


def _payload(*turns):
    return {"model": "x", "max_tokens": 100, "messages": list(turns)}


@patch.dict("os.environ", {"SPILLOVER_LTM_PLACEMENT": "between"})
def test_between_inserts_before_last_user():
    payload = _payload(
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2 NEW"},
    )
    _inject_ltm(payload, "<ltm>X</ltm>")
    msgs = payload["messages"]
    # Synthetic pair inserted between a1 and u2
    assert msgs[0]["content"] == "u1"
    assert msgs[1]["content"] == "a1"
    assert msgs[2]["role"] == "user"
    assert "recall" in msgs[2]["content"]
    assert msgs[3]["role"] == "assistant"
    assert msgs[3]["content"] == "<ltm>X</ltm>"
    assert msgs[4]["content"] == "u2 NEW"


@patch.dict("os.environ", {"SPILLOVER_LTM_PLACEMENT": "between"})
def test_between_fallback_when_no_user():
    payload = _payload({"role": "assistant", "content": "a1"})
    _inject_ltm(payload, "<ltm>X</ltm>")
    assert payload.get("system") == "<ltm>X</ltm>"


@patch.dict("os.environ", {"SPILLOVER_LTM_PLACEMENT": "between"})
def test_between_single_user_turn():
    """Same shape as the bench: only one user turn, no history. Inserts before it."""
    payload = _payload({"role": "user", "content": "question"})
    _inject_ltm(payload, "<ltm>X</ltm>")
    msgs = payload["messages"]
    assert len(msgs) == 3
    assert msgs[0]["role"] == "user"
    assert "recall" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "<ltm>X</ltm>"
    assert msgs[2]["content"] == "question"
