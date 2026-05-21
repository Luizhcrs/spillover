from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvalPair:
    query: str
    expected_episode_id: str
    notes: str = ""


def load_pairs(path: Path) -> list[EvalPair]:
    out: list[EvalPair] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            obj = json.loads(line)
            out.append(
                EvalPair(
                    query=obj["query"],
                    expected_episode_id=obj["expected_episode_id"],
                    notes=obj.get("notes", ""),
                )
            )
    return out
