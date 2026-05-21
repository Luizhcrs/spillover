from spillover.facet.entities import extract_entities


def test_extracts_file_path():
    entities = extract_entities("see src/spillover/proxy/app.py line 42")
    files = [e for e in entities if e.kind == "file"]
    assert any(e.name == "src/spillover/proxy/app.py" for e in files)


def test_extracts_url():
    entities = extract_entities("docs at https://example.com/api ok")
    urls = [e for e in entities if e.kind == "url"]
    assert any(e.name == "https://example.com/api" for e in urls)


def test_extracts_identifier_called():
    entities = extract_entities("we call processBatch() then commit()")
    idents = [e for e in entities if e.kind == "identifier"]
    names = {e.name for e in idents}
    assert "processBatch" in names
    assert "commit" in names


def test_extracts_command_in_backticks():
    entities = extract_entities("run `git status` to check")
    cmds = [e for e in entities if e.kind == "command"]
    assert any(e.name == "git status" for e in cmds)


def test_dedup_repeated_entities():
    entities = extract_entities("foo.py and foo.py again")
    files = [e for e in entities if e.kind == "file"]
    assert len(files) == 1


def test_handles_list_content():
    content = [
        {"type": "text", "text": "see /tmp/x.log"},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/y.log"}},
    ]
    entities = extract_entities(content)
    files = [e.name for e in entities if e.kind == "file"]
    assert "/tmp/x.log" in files


def test_empty_returns_empty():
    assert extract_entities("") == []
    assert extract_entities([]) == []
    assert extract_entities(None) == []
