from spillover.facet.classifier import classify


def test_priority_marker():
    assert classify("Remember this: always use uuid7") == "priority"
    assert classify("Lembra disso: nunca commitar segredos") == "priority"


def test_procedural_by_tool_calls():
    assert classify("anything", [{"name": "Read"}]) == "procedural"


def test_procedural_by_marker():
    assert classify("First read the config, then call setup()") == "procedural"


def test_semantic_marker():
    assert classify(
        "A vector index is a kind of approximate nearest neighbor structure"
    ) == "semantic"


def test_default_episodic():
    assert classify("We tried it and it worked fine.") == "episodic"


def test_priority_wins_over_procedural():
    assert (
        classify("Remember this: how to deploy", [{"name": "Bash"}])
        == "priority"
    )
