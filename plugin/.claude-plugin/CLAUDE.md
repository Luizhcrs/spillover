# Spillover plugin context

This Claude Code session is routed through a local **spillover** proxy at `http://127.0.0.1:8787`.

The proxy archives old turns into a per-project SQLite + Kuzu store under
`~/.spillover/projects/<sha1(cwd)>/` once the working context crosses a watermark,
then injects the most relevant past episodes back into each new request as
an `<spillover-ltm>` block. This means:

- You may see `<spillover-ltm>` blocks injected near the top of the conversation
  with exact quotes from prior turns. Treat them as authoritative history.
- Auto-compaction is disabled for this session — context does not get summarised.
- Each working directory has its own isolated memory.

You do not need to call any tools to use this. Memory is transparent.
