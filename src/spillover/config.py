from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    port: int
    watermark: float
    operational_ceiling_tokens: int
    provider_max_tokens: int
    db_root: Path
    upstream_base_url: str
    openai_base_url: str
    # 5-tier budget -- must sum to 1.0
    system_pct: float
    working_memory_pct: float
    active_pct: float
    ltm_pct: float
    scratchpad_pct: float
    # legacy alias for backwards compat (== ltm_pct)
    ltm_budget_pct: float
    retriever_topk: int
    retriever_vector_k: int
    retriever_graph_k: int
    retriever_bm25_k: int
    profile_default: str  # "coding" | "research" | "conversation" | "auto"
    fallback_model_anthropic: str  # empty = disabled
    fallback_model_openai: str

    @property
    def window_max(self) -> int:
        """Backwards-compatible alias -- most code reads operational ceiling."""
        return self.operational_ceiling_tokens

    @classmethod
    def from_env(cls) -> Config:
        ceiling = int(os.environ.get("SPILLOVER_OPERATIONAL_CEILING_TOKENS",
                                     os.environ.get("SPILLOVER_WINDOW_MAX", "200000")))
        provider = int(os.environ.get("SPILLOVER_PROVIDER_MAX_TOKENS", str(ceiling * 2)))
        ltm = float(os.environ.get("SPILLOVER_LTM_BUDGET_PCT", "0.15"))
        return cls(
            port=int(os.environ.get("SPILLOVER_PORT", "8787")),
            watermark=float(os.environ.get("SPILLOVER_WATERMARK", "0.85")),
            operational_ceiling_tokens=ceiling,
            provider_max_tokens=provider,
            db_root=Path(os.environ.get("SPILLOVER_DB_ROOT", str(Path.home() / ".spillover"))),
            upstream_base_url=os.environ.get("SPILLOVER_UPSTREAM_BASE_URL", "https://api.anthropic.com"),
            openai_base_url=os.environ.get("SPILLOVER_OPENAI_BASE_URL", "https://api.openai.com"),
            system_pct=float(os.environ.get("SPILLOVER_SYSTEM_PCT", "0.04")),
            working_memory_pct=float(os.environ.get("SPILLOVER_WORKING_MEMORY_PCT", "0.20")),
            active_pct=float(os.environ.get("SPILLOVER_ACTIVE_PCT", "0.50")),
            ltm_pct=ltm,
            scratchpad_pct=float(os.environ.get("SPILLOVER_SCRATCHPAD_PCT", "0.11")),
            ltm_budget_pct=ltm,
            retriever_topk=int(os.environ.get("SPILLOVER_RETRIEVER_TOPK", "5")),
            retriever_vector_k=int(os.environ.get("SPILLOVER_RETRIEVER_VECTOR_K", "50")),
            retriever_graph_k=int(os.environ.get("SPILLOVER_RETRIEVER_GRAPH_K", "50")),
            retriever_bm25_k=int(os.environ.get("SPILLOVER_RETRIEVER_BM25_K", "50")),
            profile_default=os.environ.get("SPILLOVER_PROFILE_DEFAULT", "auto"),
            fallback_model_anthropic=os.environ.get(
                "SPILLOVER_FALLBACK_MODEL_ANTHROPIC",
                os.environ.get(
                    "SPILLOVER_FALLBACK_MODEL", "claude-haiku-4-5-20251001"
                ),
            ),
            fallback_model_openai=os.environ.get(
                "SPILLOVER_FALLBACK_MODEL_OPENAI", "gpt-4o-mini"
            ),
        )
