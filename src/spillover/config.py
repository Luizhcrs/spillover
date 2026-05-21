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

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            port=int(os.environ.get("SPILLOVER_PORT", "8787")),
            watermark=float(os.environ.get("SPILLOVER_WATERMARK", "0.85")),
            window_max=int(os.environ.get("SPILLOVER_WINDOW_MAX", "200000")),
            db_root=Path(os.environ.get("SPILLOVER_DB_ROOT", str(Path.home() / ".spillover"))),
            upstream_base_url=os.environ.get(
                "SPILLOVER_UPSTREAM_BASE_URL", "https://api.anthropic.com"
            ),
        )
