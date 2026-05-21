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


@dataclass
class SelectionResult:
    evicted_indexes: list[int] = field(default_factory=list)
    tokens_freed: int = 0
    pass_used: int = 0
    budget_pressure: bool = False


def _evictable_pass1(turns: list[ActiveTurn], recent_buffer: int) -> list[ActiveTurn]:
    if not turns:
        return []
    cutoff = max(0, len(turns) - recent_buffer)
    return [
        t
        for i, t in enumerate(turns)
        if not t.is_system
        and not t.pinned
        and t.memory_type != "priority"
        and i < cutoff
    ]


def _evictable_pass2(turns: list[ActiveTurn], recent_buffer: int) -> list[ActiveTurn]:
    if not turns:
        return []
    cutoff = max(0, len(turns) - recent_buffer)
    candidates = [
        (i, t)
        for i, t in enumerate(turns)
        if not t.is_system and not t.pinned and i < cutoff
    ]
    # Non-priority first (FIFO), then priority (FIFO) — mirrors pass 1 preference
    non_priority = [t for _, t in candidates if t.memory_type != "priority"]
    priority = [t for _, t in candidates if t.memory_type == "priority"]
    return non_priority + priority


def select_for_eviction(
    turns: list[ActiveTurn],
    tokens_to_free: int,
    recent_buffer: int = 4,
) -> SelectionResult:
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

    if freed >= tokens_to_free:
        return SelectionResult(
            evicted_indexes=evicted, tokens_freed=freed, pass_used=2
        )

    return SelectionResult(
        evicted_indexes=[],
        tokens_freed=0,
        pass_used=3,
        budget_pressure=True,
    )
