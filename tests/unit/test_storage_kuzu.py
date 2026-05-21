from spillover.storage.kuzu import open_project_kuzu, project_kuzu_dir


def test_kuzu_dir_path(tmp_path):
    p = project_kuzu_dir(tmp_path, "p1")
    assert p == tmp_path / "projects" / "p1" / "kuzu"


def test_open_creates_dir_and_schema(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    conn.execute(
        "CREATE (e:Episode {id: 'e1', ts: 0, memory_type: 'episodic', importance: 1.0})"
    )
    result = conn.execute("MATCH (e:Episode {id: 'e1'}) RETURN e.id")
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    assert rows[0][0] == "e1"


def test_open_idempotent(tmp_path):
    open_project_kuzu(tmp_path, "p1")
    conn = open_project_kuzu(tmp_path, "p1")
    result = conn.execute("MATCH (e:Episode) RETURN count(e)")
    while result.has_next():
        result.get_next()
