from spillover.eviction.selector import (
    ActiveTurn,
    select_for_eviction,
)


def _t(idx, tokens, role="user", pinned=False, memory_type=None, is_system=False):
    return ActiveTurn(
        index=idx,
        token_count=tokens,
        role=role,
        pinned=pinned,
        memory_type=memory_type,
        is_system=is_system,
    )


def test_pass1_fifo_non_priority():
    turns = [
        _t(0, 100, is_system=True),
        _t(1, 200),
        _t(2, 300),
        _t(3, 100, memory_type="priority"),
        _t(4, 400),
        _t(5, 50),  # recent buffer
        _t(6, 50),
        _t(7, 50),
        _t(8, 50),
    ]
    result = select_for_eviction(turns, tokens_to_free=400, recent_buffer=4)
    # Recent buffer = last 4 -> indexes 5,6,7,8 excluded
    # System excluded (0)
    # Priority excluded (3) on pass 1
    # FIFO over 1,2,4 -> 1 (200) + 2 (300) = 500 >= 400, stop
    assert result.evicted_indexes == [1, 2]
    assert result.tokens_freed == 500
    assert result.pass_used == 1


def test_pass2_priority_fallback_when_pass1_short():
    turns = [
        _t(0, 100, is_system=True),
        _t(1, 100, memory_type="priority"),
        _t(2, 200, memory_type="priority"),
        _t(3, 100),  # only 100 tokens non-priority available
        _t(4, 50),
        _t(5, 50),
        _t(6, 50),
        _t(7, 50),
    ]
    # Pass 1 finds only turn 3 (100 tokens) -> not enough for 250
    # Pass 2 includes priority oldest-first: 3 (100) + 1 (100) + 2 (200) -> stop at 400
    result = select_for_eviction(turns, tokens_to_free=250, recent_buffer=4)
    assert 3 in result.evicted_indexes
    assert 1 in result.evicted_indexes
    assert result.pass_used == 2
    assert result.tokens_freed >= 250


def test_pass3_budget_pressure_when_everything_protected():
    turns = [
        _t(0, 100, is_system=True),
        _t(1, 100, pinned=True),
        _t(2, 100, pinned=True),
        _t(3, 50),  # recent
        _t(4, 50),
        _t(5, 50),
        _t(6, 50),
    ]
    result = select_for_eviction(turns, tokens_to_free=300, recent_buffer=4)
    assert result.pass_used == 3
    assert result.budget_pressure is True
    # No turn evicted because pinned + system + recent cover everything
    assert result.evicted_indexes == []


def test_no_eviction_needed_below_threshold():
    # Caller must check fill_ratio before invoking; selector still returns empty
    # when tokens_to_free is 0
    turns = [_t(0, 100), _t(1, 100)]
    result = select_for_eviction(turns, tokens_to_free=0, recent_buffer=4)
    assert result.evicted_indexes == []
    assert result.tokens_freed == 0
    assert result.pass_used == 0


def test_token_balance_invariant_over_50_turns():
    """N tokens new in -> at least N tokens out (steady-state)."""
    turns = [_t(i, 100) for i in range(50)]
    result = select_for_eviction(turns, tokens_to_free=350, recent_buffer=4)
    assert result.tokens_freed >= 350
