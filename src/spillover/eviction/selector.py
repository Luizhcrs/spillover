from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ActiveTurn:
    index: int
    token_count: int
    role: str
    pinned: bool = False
    memory_type: str | None = None
    is_system: bool = False
    density: int = 0  # entities + decisions + tool_calls; higher = more valuable


@dataclass
class SelectionResult:
    evicted_indexes: list[int] = field(default_factory=list)
    tokens_freed: int = 0
    pass_used: int = 0
    budget_pressure: bool = False


def _weight(t: ActiveTurn) -> float:
    return t.token_count / max(1, t.density)


def _ordered_candidates(cands: list[ActiveTurn]) -> list[ActiveTurn]:
    """Sort candidates for eviction.

    If any candidate has density > 0, use weighted sort: higher weight (low
    density, high token_count) evicted first. Tiebreak by index ascending.
    When all densities are 0, preserve original (FIFO) order.
    """
    if any(t.density > 0 for t in cands):
        return sorted(cands, key=lambda t: (-_weight(t), t.index))
    return cands  # preserve original FIFO order


def _evictable_pass1(turns: list[ActiveTurn], recent_buffer: int) -> list[ActiveTurn]:
    if not turns:
        return []
    cutoff = max(0, len(turns) - recent_buffer)
    cands = [
        t
        for i, t in enumerate(turns)
        if not t.is_system
        and not t.pinned
        and t.memory_type != "priority"
        and i < cutoff
    ]
    return _ordered_candidates(cands)


def _evictable_pass2(turns: list[ActiveTurn], recent_buffer: int) -> list[ActiveTurn]:
    if not turns:
        return []
    cutoff = max(0, len(turns) - recent_buffer)
    candidates = [
        (i, t)
        for i, t in enumerate(turns)
        if not t.is_system and not t.pinned and i < cutoff
    ]
    # Non-priority first, then priority -- mirrors pass 1 preference
    non_priority = [t for _, t in candidates if t.memory_type != "priority"]
    priority = [t for _, t in candidates if t.memory_type == "priority"]
    return _ordered_candidates(non_priority) + _ordered_candidates(priority)


def select_for_eviction(
    turns: list[ActiveTurn],
    tokens_to_free: int,
    recent_buffer: int = 4,
) -> SelectionResult:
    """Pick which active turns to evict to meet a token budget.

    Implements the 3-pass policy from spec §5:
      - Pass 1: FIFO over non-priority, non-pinned, non-system, non-recent turns.
        When any turn has density>0, uses weighted sort instead of FIFO.
      - Pass 2 (fallback if Pass 1 short): drain remaining non-priority first,
        then priority turns oldest-first. Still excludes system, pinned, recent.
      - Pass 3 (fallback if Pass 2 short): return whatever Pass 2 freed and
        set ``budget_pressure=True`` so the caller can shrink the LTM injection
        budget for this turn and emit a budget-pressure event.

    Args:
        turns: active conversation turns in chronological order.
        tokens_to_free: number of tokens to evict in this call (typically equal
            to the new user+assistant tokens entering this turn for 1:1 balance).
        recent_buffer: number of most-recent turns to protect (default 4).

    Returns:
        SelectionResult. ``evicted_indexes`` are positions in ``turns``.
        ``pass_used`` is 0 if no work was attempted (``tokens_to_free <= 0``),
        1, 2, or 3 otherwise. ``budget_pressure`` is True only when Pass 3 fires.
    """
    if tokens_to_free <= 0:
        return SelectionResult()

    evicted: list[int] = []
    freed = 0

    for t in _evictable_pass1(turns, recent_buffer):
        evicted.append(t.index)
        freed += t.token_count
        if freed >= tokens_to_free:
            return SelectionResult(
                evicted_indexes=evicted, tokens_freed=freed, pass_used=1
            )

    evicted = []
    freed = 0
    for t in _evictable_pass2(turns, recent_buffer):
        evicted.append(t.index)
        freed += t.token_count
        if freed >= tokens_to_free:
            return SelectionResult(
                evicted_indexes=evicted, tokens_freed=freed, pass_used=2
            )

    # Pass 3: Pass 2 ran but did not free enough. Keep whatever it freed and
    # signal budget pressure so the caller can compensate.
    return SelectionResult(
        evicted_indexes=evicted,
        tokens_freed=freed,
        pass_used=3,
        budget_pressure=True,
    )
