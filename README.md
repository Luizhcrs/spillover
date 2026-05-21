# spillover

Transparent LLM proxy with overflow memory architecture.

**Status:** v0.1 (MVP foundation only — see `docs/superpowers/plans/` for roadmap).

## Install

```bash
pip install -e ".[dev]"
```

## Run the proxy

```bash
spillover up
```

Then point your client at `http://127.0.0.1:8787`:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude code
```

See `docs/superpowers/specs/2026-05-20-spillover-design.md` for full architecture.
