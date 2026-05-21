from __future__ import annotations

from spillover.retriever.vector import Hit

DEFAULT_TYPE_WEIGHTS = {
    "task": 1.4,
    "priority": 1.5,
    "procedural": 1.2,
    "episodic": 1.0,
    "semantic": 1.0,
}

RRF_K = 60


def rrf_fuse(
    *rankings: list[Hit],
    type_weights: dict[str, float] | None = None,
) -> list[Hit]:
    """Reciprocal Rank Fusion with optional per-type weights."""
    weights = type_weights or DEFAULT_TYPE_WEIGHTS
    scores: dict[str, float] = {}
    meta: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            type_w = weights.get(hit.memory_type or "episodic", 1.0)
            contrib = type_w / (RRF_K + rank)
            scores[hit.episode_id] = scores.get(hit.episode_id, 0.0) + contrib
            if hit.episode_id not in meta:
                meta[hit.episode_id] = hit
    fused: list[Hit] = []
    for eid in sorted(scores, key=lambda k: -scores[k]):
        h = meta[eid]
        fused.append(
            Hit(
                episode_id=eid,
                score=scores[eid],
                memory_type=h.memory_type,
                importance=h.importance,
                ts=h.ts,
                source="fusion",
            )
        )
    return fused
