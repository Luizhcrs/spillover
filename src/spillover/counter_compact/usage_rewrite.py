from __future__ import annotations

import os


def rewrite_usage(
    usage: dict | None,
    tokens_archived_this_turn: int,
) -> dict | None:
    """Subtract tokens_archived_this_turn from input_tokens so the client
    believes its budget is healthier and does not trigger auto-compact.

    `SPILLOVER_REPORTED_INPUT_CAP` (env, default 0 = disabled): hard cap on
    the input_tokens we report back to the client. When set to e.g. 5000,
    every response shows at most 5k input tokens regardless of how big the
    real input was. Lets Claude Code's local "context used" counter grow
    much slower than the real conversation, pushing back its hard
    "Context limit reached" wall.

    Idempotent: returns a new dict; does not mutate input. Floors at 1 to
    avoid division-by-zero in downstream client budget heuristics.
    """
    if not usage:
        return usage
    real_input = int(usage.get("input_tokens", 0))
    new_input = max(1, real_input - max(0, tokens_archived_this_turn))
    try:
        reported_cap = int(os.environ.get("SPILLOVER_REPORTED_INPUT_CAP", "0"))
    except ValueError:
        reported_cap = 0
    if reported_cap > 0:
        new_input = min(new_input, reported_cap)
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
