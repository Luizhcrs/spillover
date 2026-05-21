from spillover.logging import redact


def test_redact_authorization_bearer():
    out = redact({"Authorization": "Bearer sk-ant-XXXXXXXXXXXX"})
    assert "sk-ant-XXXXXXXXXXXX" not in out["Authorization"]
    assert out["Authorization"].startswith("Bearer")
    assert out["Authorization"].endswith("XXX")


def test_redact_lowercase_x_api_key():
    out = redact({"x-api-key": "abcdefghij"})
    assert out["x-api-key"] != "abcdefghij"


def test_redact_passes_other_headers_through():
    out = redact({"X-Project": "proj-1", "Content-Type": "application/json"})
    assert out["X-Project"] == "proj-1"
    assert out["Content-Type"] == "application/json"


def test_redact_handles_none_and_empty():
    assert redact(None) == {}
    assert redact({}) == {}
