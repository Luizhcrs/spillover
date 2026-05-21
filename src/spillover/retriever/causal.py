from __future__ import annotations

import kuzu

from spillover.retriever.vector import Hit


def causality_chain(
    conn: kuzu.Connection,
    seed_episode_ids: list[str],
    depth: int = 3,
    limit: int = 30,
) -> list[Hit]:
    """For each seed episode, walk AFTER edges up to `depth` hops and return
    Episodes in the chain.

    Score = 1.0 for direct AFTER, 0.7 at hop 2, 0.5 at hop 3.
    Uses iterative 1-hop queries for Kuzu dialect compatibility.
    """
    if not seed_episode_ids:
        return []
    scores: dict[str, float] = {}
    decay_by_hop = {1: 1.0, 2: 0.7, 3: 0.5}

    current_frontier = list(seed_episode_ids)
    for hop in range(1, depth + 1):
        if not current_frontier:
            break
        decay = decay_by_hop.get(hop, 0.5)
        next_frontier: list[str] = []
        # Forward: seed -> next
        for seed_id in current_frontier:
            try:
                res = conn.execute(
                    "MATCH (s:Episode {id: $id})-[:AFTER]->(e:Episode) "
                    "RETURN e.id LIMIT $limit",
                    {"id": seed_id, "limit": limit},
                )
                while res.has_next():
                    (eid,) = res.get_next()
                    if eid not in scores or scores[eid] < decay:
                        scores[eid] = decay
                    if hop < depth:
                        next_frontier.append(eid)
            except Exception:
                continue
            # Backward: next -> seed
            try:
                res = conn.execute(
                    "MATCH (e:Episode)-[:AFTER]->(s:Episode {id: $id}) "
                    "RETURN e.id LIMIT $limit",
                    {"id": seed_id, "limit": limit},
                )
                while res.has_next():
                    (eid,) = res.get_next()
                    if eid not in scores or scores[eid] < decay:
                        scores[eid] = decay
                    if hop < depth:
                        next_frontier.append(eid)
            except Exception:
                continue
        current_frontier = list(set(next_frontier) - set(seed_episode_ids))

    # Exclude the seed IDs themselves from results
    for sid in seed_episode_ids:
        scores.pop(sid, None)

    hits = [
        Hit(
            episode_id=eid,
            score=score,
            memory_type=None,
            importance=None,
            ts=None,
            source="causal",
        )
        for eid, score in scores.items()
    ]
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]
