from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetProfile:
    name: str
    system_pct: float
    working_memory_pct: float
    active_pct: float
    ltm_pct: float
    scratchpad_pct: float


CODING = BudgetProfile("coding", 0.05, 0.30, 0.40, 0.10, 0.15)
RESEARCH = BudgetProfile("research", 0.05, 0.10, 0.50, 0.30, 0.05)
CONVERSATION = BudgetProfile("conversation", 0.05, 0.10, 0.70, 0.10, 0.05)
DEFAULT = BudgetProfile("default", 0.04, 0.20, 0.50, 0.15, 0.11)


def select_profile(payload: dict, override: str = "auto") -> BudgetProfile:
    if override == "coding":
        return CODING
    if override == "research":
        return RESEARCH
    if override == "conversation":
        return CONVERSATION
    if override and override != "auto":
        return DEFAULT
    # auto-detect
    tool_count = sum(
        1
        for m in payload.get("messages", [])
        for b in (m.get("content") if isinstance(m.get("content"), list) else [])
        if isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result")
    )
    if tool_count >= 3 or payload.get("tools"):
        return CODING
    msg_count = len(payload.get("messages", []))
    if msg_count >= 10:
        return CONVERSATION
    return DEFAULT
