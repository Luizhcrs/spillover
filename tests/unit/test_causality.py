from spillover.retriever.causal import causality_chain
from spillover.storage.kuzu import clear_kuzu_cache, open_project_kuzu


def test_causality_chain_forward_one_hop(tmp_path):
    clear_kuzu_cache()
    conn = open_project_kuzu(tmp_path, "p1")
    conn.execute("CREATE (a:Episode {id: 'a', ts: 1, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (b:Episode {id: 'b', ts: 2, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (c:Episode {id: 'c', ts: 3, memory_type: 'episodic', importance: 1.0})")
    conn.execute("MATCH (a:Episode {id: 'a'}), (b:Episode {id: 'b'}) CREATE (a)-[:AFTER]->(b)")
    conn.execute("MATCH (b:Episode {id: 'b'}), (c:Episode {id: 'c'}) CREATE (b)-[:AFTER]->(c)")

    hits = causality_chain(conn, ["a"], depth=1)
    ids = [h.episode_id for h in hits]
    assert "b" in ids
    assert "c" not in ids  # 2 hops away


def test_causality_chain_multi_hop(tmp_path):
    clear_kuzu_cache()
    conn = open_project_kuzu(tmp_path, "p2")
    conn.execute("CREATE (a:Episode {id: 'a', ts: 1, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (b:Episode {id: 'b', ts: 2, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (c:Episode {id: 'c', ts: 3, memory_type: 'episodic', importance: 1.0})")
    conn.execute("MATCH (a:Episode {id: 'a'}), (b:Episode {id: 'b'}) CREATE (a)-[:AFTER]->(b)")
    conn.execute("MATCH (b:Episode {id: 'b'}), (c:Episode {id: 'c'}) CREATE (b)-[:AFTER]->(c)")

    hits = causality_chain(conn, ["a"], depth=3)
    ids = [h.episode_id for h in hits]
    assert "b" in ids
    assert "c" in ids


def test_causality_chain_empty_seeds(tmp_path):
    clear_kuzu_cache()
    conn = open_project_kuzu(tmp_path, "p3")
    assert causality_chain(conn, [], depth=2) == []
