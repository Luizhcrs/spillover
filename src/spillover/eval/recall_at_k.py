from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from spillover.eval.dataset import EvalPair, load_pairs
from spillover.facet.embed import embed_text
from spillover.facet.entities import extract_entities
from spillover.retriever.fusion import rrf_fuse
from spillover.retriever.graph import graph_walk
from spillover.retriever.vector import vector_topk
from spillover.storage.kuzu import open_project_kuzu
from spillover.storage.sqlite import open_project_db


@dataclass
class RecallResult:
    pair: EvalPair
    rank: int | None  # 1-indexed rank of expected_episode_id in fused hits; None if not found
    top_ids: list[str] = None  # type: ignore[assignment]


def evaluate_recall(
    db_root: Path,
    project_id: str,
    pairs: list[EvalPair],
    *,
    vector_k: int = 50,
    graph_k: int = 50,
    final_k: int = 20,
) -> list[RecallResult]:
    db = open_project_db(db_root, project_id)
    try:
        results: list[RecallResult] = []
        for pair in pairs:
            emb = embed_text(pair.query)
            v_hits = vector_topk(db, emb, k=vector_k)
            seeds = [e.name for e in extract_entities(pair.query)][:20]
            g_hits: list = []
            if seeds:
                try:
                    kuzu_conn = open_project_kuzu(db_root, project_id)
                    g_hits = graph_walk(kuzu_conn, seeds, k_hop=2, limit=graph_k)
                except Exception:
                    pass
            fused = rrf_fuse(v_hits, g_hits)[:final_k]
            top_ids = [h.episode_id for h in fused]
            rank: int | None = None
            if pair.expected_episode_id in top_ids:
                rank = top_ids.index(pair.expected_episode_id) + 1
            results.append(RecallResult(pair=pair, rank=rank, top_ids=top_ids))
        return results
    finally:
        db.close()


def recall_at_k(results: list[RecallResult], k: int) -> float:
    if not results:
        return 0.0
    hits = sum(1 for r in results if r.rank is not None and r.rank <= k)
    return hits / len(results)


def render_recall_report(results: list[RecallResult]) -> str:
    lines = ["# Recall@K report", ""]
    for k in (1, 3, 5, 10, 20):
        r = recall_at_k(results, k)
        n_hits = sum(1 for x in results if x.rank is not None and x.rank <= k)
        lines.append(f"- recall@{k}: **{r * 100:.1f}%** ({n_hits}/{len(results)})")
    lines.append("\n## misses\n")
    for r in results:
        if r.rank is None:
            lines.append(
                f"- query=`{r.pair.query}` expected=`{r.pair.expected_episode_id}`"
                f" -- not in top-{len(r.top_ids)}"
            )
    return "\n".join(lines) + "\n"


def load_and_evaluate(
    db_root: Path,
    project_id: str,
    dataset_path: Path,
) -> str:
    pairs = load_pairs(dataset_path)
    results = evaluate_recall(db_root, project_id, pairs)
    return render_recall_report(results)
