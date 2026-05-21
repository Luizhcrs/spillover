CREATE TABLE IF NOT EXISTS episodes (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    role            TEXT NOT NULL,
    content_json    TEXT NOT NULL,
    tool_calls_json TEXT,
    code_refs_json  TEXT,
    token_count     INTEGER NOT NULL,
    ts              INTEGER NOT NULL,
    hash            TEXT NOT NULL,
    evicted         INTEGER NOT NULL DEFAULT 0,
    pinned          INTEGER NOT NULL DEFAULT 0,
    hit_count       INTEGER NOT NULL DEFAULT 0,
    memory_type     TEXT,
    facet_pending   INTEGER NOT NULL DEFAULT 1,
    compaction_rescued INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_episodes_evicted_ts ON episodes(evicted, ts);
CREATE INDEX IF NOT EXISTS idx_episodes_hash ON episodes(hash);

CREATE TABLE IF NOT EXISTS seen_turns (
    project_id      TEXT NOT NULL,
    turn_hash       TEXT NOT NULL,
    turn_index      INTEGER NOT NULL,
    content_json    TEXT NOT NULL,
    first_seen_ts   INTEGER NOT NULL,
    last_seen_ts    INTEGER NOT NULL,
    PRIMARY KEY (project_id, turn_hash)
);

CREATE INDEX IF NOT EXISTS idx_seen_turns_last_seen ON seen_turns(last_seen_ts);
