from __future__ import annotations


def rewrite_usage(
    usage: dict | None,
    tokens_archived_this_turn: int,
) -> dict | None:
    """Subtract tokens_archived_this_turn from input_tokens so the client
    believes its budget is healthier and does not trigger auto-compact.

    Idempotent: returns a new dict; does not mutate input. Floors at 1 to
    avoid division-by-zero in downstream client budget heuristics.
    """
    if not usage:
        return usage
    real_input = int(usage.get("input_tokens", 0))
    new_input = max(1, real_input - max(0, tokens_archived_this_turn))
    out = dict(usage)
    out["input_tokens"] = new_input
    out["spillover_real_input_tokens"] = real_input  # for audit
    return out


def rewrite_response_json(
    resp_json: dict,
    tokens_archived_this_turn: int,
) -> dict:
    """Apply rewrite to the response's `usage` field if present."""
    if "usage" not in resp_json:
        return resp_json
    out = dict(resp_json)
    out["usage"] = rewrite_usage(resp_json["usage"], tokens_archived_this_turn)
    return out
