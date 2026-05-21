# Evaluation

This directory holds the reproducible recall benchmark for spillover's hybrid retriever.

## Build your own dataset

1. Run `spillover stats <project_id>` to find a project DB with > 100 episodes.
2. Run `spillover query <project_id> "<sample query>"` and pick a known-correct hit.
3. Record its `episode_id` and the query that should retrieve it.
4. Repeat for 50+ queries covering coding, decisions, bug fixes, conversational facts.
5. Save lines as `dataset.jsonl` with shape:

```json
{"query": "...", "expected_episode_id": "<uuid>", "notes": "..."}
```

## Run

```bash
python -c "from spillover.eval.recall_at_k import load_and_evaluate; from pathlib import Path; print(load_and_evaluate(Path('~/.spillover').expanduser(), '<project_id>', Path('docs/eval/dataset.jsonl')))"
```

## Targets

- recall@1: > 60%
- recall@5: > 90%  (spec acceptance gate)
- recall@10: > 95%
