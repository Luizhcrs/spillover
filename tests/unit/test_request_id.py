from spillover.request_id import ensure_request_id


def test_returns_existing_x_request_id():
    assert ensure_request_id({"x-request-id": "abc123"}) == "abc123"


def test_returns_existing_capital_header():
    assert ensure_request_id({"X-Request-Id": "XYZ"}) == "XYZ"


def test_generates_uuid_when_missing():
    rid = ensure_request_id({})
    assert len(rid) == 32  # uuid4().hex is 32 hex chars


def test_generates_uuid_when_none():
    rid = ensure_request_id(None)
    assert len(rid) == 32
