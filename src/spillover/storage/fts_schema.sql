CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    episode_id UNINDEXED,
    body,
    tokenize="unicode61 tokenchars './-_:'"
);
