CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(
    episode_id TEXT PRIMARY KEY,
    embedding FLOAT[768],
    memory_type TEXT,
    importance FLOAT,
    ts INTEGER
);
