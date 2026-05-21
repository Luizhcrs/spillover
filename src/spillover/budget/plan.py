from __future__ import annotations

from dataclasses import dataclass

from spillover.config import Config


@dataclass(frozen=True)
class TokenPlan:
    ceiling: int
    system_tokens: int
    working_memory_tokens: int
    active_tokens: int
    ltm_tokens: int
    scratchpad_tokens: int

    @property
    def evictable_budget(self) -> int:
        """Tokens an eviction call may legitimately leave in the active layer."""
        return self.active_tokens

    @property
    def total(self) -> int:
        return (
            self.system_tokens
            + self.working_memory_tokens
            + self.active_tokens
            + self.ltm_tokens
            + self.scratchpad_tokens
        )


def plan_from_config(config: Config) -> TokenPlan:
    c = config.operational_ceiling_tokens
    return TokenPlan(
        ceiling=c,
        system_tokens=int(c * config.system_pct),
        working_memory_tokens=int(c * config.working_memory_pct),
        active_tokens=int(c * config.active_pct),
        ltm_tokens=int(c * config.ltm_pct),
        scratchpad_tokens=int(c * config.scratchpad_pct),
    )
