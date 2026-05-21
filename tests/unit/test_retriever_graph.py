from spillover.retriever.graph import graph_walk
from spillover.storage.kuzu import open_project_kuzu


def test_graph_walk_one_hop(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    conn.execute(
        "CREATE (e:Episode {id: 'e1', ts: 0, memory_type: 'episodic', "
        "importance: 1.0})"
    )
    conn.execute("CREATE (n:Entity {name: 'foo.py', kind: 'file'})")
    conn.execute(
        "MATCH (e:Episode {id: 'e1'}), (n:Entity {name: 'foo.py'}) "
        "CREATE (e)-[:MENTIONS]->(n)"
    )
    hits = graph_walk(conn, ["foo.py"], k_hop=1, limit=10)
    assert len(hits) == 1
    assert hits[0].episode_id == "e1"
    assert hits[0].score == 1.0
    assert hits[0].source == "graph"


def test_graph_walk_empty_seeds(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    assert graph_walk(conn, [], k_hop=2, limit=10) == []


def test_graph_walk_no_match(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    conn.execute(
        "CREATE (e:Episode {id: 'e1', ts: 0, memory_type: 'episodic', "
        "importance: 1.0})"
    )
    hits = graph_walk(conn, ["missing.py"], k_hop=1, limit=10)
    assert hits == []
