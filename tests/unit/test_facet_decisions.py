from spillover.facet.decisions import (
    CodeRef,
    extract_code_refs,
    extract_decisions,
)


def test_extract_decision_ptbr():
    decisions = extract_decisions(
        "decidi usar SQLite em vez de Postgres porque é local"
    )
    assert len(decisions) >= 1
    assert any("decidi" in d.summary.lower() for d in decisions)


def test_extract_decision_en():
    decisions = extract_decisions("We chose Anthropic over OpenAI for prompt caching")
    assert any("chose" in d.summary.lower() for d in decisions)


def test_extract_decision_because():
    decisions = extract_decisions(
        "Switched the watermark because the old value caused thrashing."
    )
    assert any("because" in d.summary.lower() for d in decisions)


def test_decisions_dedup_by_hash():
    text = "decidi X\ndecidi X"
    decisions = extract_decisions(text)
    assert len(decisions) == 1


def test_extract_code_refs_read():
    refs = extract_code_refs(
        [
            {"name": "Read", "input": {"file_path": "/tmp/x.txt"}},
            {"name": "Edit", "input": {"file_path": "/tmp/y.py"}},
        ]
    )
    assert CodeRef(path="/tmp/x.txt", line=None, op="read") in refs
    assert CodeRef(path="/tmp/y.py", line=None, op="edit") in refs


def test_extract_code_refs_dedup():
    refs = extract_code_refs(
        [
            {"name": "Read", "input": {"file_path": "/tmp/x.txt"}},
            {"name": "Read", "input": {"file_path": "/tmp/x.txt"}},
        ]
    )
    assert len(refs) == 1


def test_extract_code_refs_empty():
    assert extract_code_refs([]) == []
    assert extract_code_refs([{"name": "Unknown"}]) == []
