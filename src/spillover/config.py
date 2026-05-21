from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    port: int
    watermark: float
    window_max: int
    db_root: Path
    upstream_base_url: str
    openai_base_url: str
    ltm_budget_pct: float
    retriever_topk: int
    retriever_vector_k: int
    retriever_graph_k: int

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            port=int(os.environ.get("SPILLOVER_PORT", "8787")),
            watermark=float(os.environ.get("SPILLOVER_WATERMARK", "0.85")),
            window_max=int(os.environ.get("SPILLOVER_WINDOW_MAX", "200000")),
            db_root=Path(
                os.environ.get("SPILLOVER_DB_ROOT", str(Path.home() / ".spillover"))
            ),
            upstream_base_url=os.environ.get(
                "SPILLOVER_UPSTREAM_BASE_URL", "https://api.anthropic.com"
            ),
            openai_base_url=os.environ.get(
                "SPILLOVER_OPENAI_BASE_URL", "https://api.openai.com"
            ),
            ltm_budget_pct=float(os.environ.get("SPILLOVER_LTM_BUDGET_PCT", "0.15")),
            retriever_topk=int(os.environ.get("SPILLOVER_RETRIEVER_TOPK", "8")),
            retriever_vector_k=int(
                os.environ.get("SPILLOVER_RETRIEVER_VECTOR_K", "50")
            ),
            retriever_graph_k=int(
                os.environ.get("SPILLOVER_RETRIEVER_GRAPH_K", "50")
            ),
        )
