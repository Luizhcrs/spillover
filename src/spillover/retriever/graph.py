from __future__ import annotations

import kuzu

from spillover.retriever.vector import Hit


def graph_walk(
    conn: kuzu.Connection,
    seed_entities: list[str],
    k_hop: int = 2,
    limit: int = 50,
) -> list[Hit]:
    """Episode hits reached from seeds within k_hop edges.

    Score = 1.0 at 1 hop, 0.5 at 2 hops.
    """
    if not seed_entities:
        return []
    hits: dict[str, float] = {}

    q1 = (
        "MATCH (e:Episode)-[:MENTIONS]->(n:Entity) "
        "WHERE n.name IN $names "
        "RETURN e.id "
        "LIMIT $limit"
    )
    res = conn.execute(q1, {"names": seed_entities, "limit": limit})
    while res.has_next():
        (eid,) = res.get_next()
        hits[eid] = max(hits.get(eid, 0.0), 1.0)

    if k_hop >= 2:
        q2 = (
            "MATCH (e:Episode)-[:MENTIONS]->(n2:Entity)<-[:MENTIONS]-"
            "(e2:Episode)-[:MENTIONS]->(n:Entity) "
            "WHERE n.name IN $names AND e <> e2 "
            "RETURN DISTINCT e.id "
            "LIMIT $limit"
        )
        res = conn.execute(q2, {"names": seed_entities, "limit": limit})
        while res.has_next():
            (eid,) = res.get_next()
            hits[eid] = max(hits.get(eid, 0.0), 0.5)

    out: list[Hit] = []
    for eid, score in sorted(hits.items(), key=lambda kv: -kv[1])[:limit]:
        meta = conn.execute(
            "MATCH (e:Episode {id: $id}) "
            "RETURN e.memory_type, e.importance, e.ts",
            {"id": eid},
        )
        mt = imp = ts = None
        if meta.has_next():
            mt, imp, ts = meta.get_next()
        out.append(
            Hit(
                episode_id=eid,
                score=score,
                memory_type=mt,
                importance=imp,
                ts=ts,
                source="graph",
            )
        )
    return out
