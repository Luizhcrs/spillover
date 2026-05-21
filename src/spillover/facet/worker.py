from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from spillover.facet.classifier import classify
from spillover.facet.decisions import extract_code_refs, extract_decisions
from spillover.facet.embed import embed_text
from spillover.facet.entities import extract_entities
from spillover.logging import get_logger
from spillover.storage.kuzu import open_project_kuzu
from spillover.storage.sqlite import open_project_db

log = get_logger("facet")


@dataclass
class FacetEvent:
    project_id: str
    episode_id: str
    db_root: Path


def _floats_to_bytes(v: list[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def _base_importance(memory_type: str, tool_call_count: int) -> float:
    base = {
        "priority": 1.0,
        "procedural": 0.7,
        "semantic": 0.6,
        "episodic": 0.5,
    }[memory_type]
    return min(1.0, base + 0.05 * tool_call_count)


def _process_one(event: FacetEvent) -> None:
    db = open_project_db(event.db_root, event.project_id)
    try:
        row = db.execute(
            "SELECT role, content_json, tool_calls_json, ts "
            "FROM episodes WHERE id = ?",
            (event.episode_id,),
        ).fetchone()
        if row is None:
            log.warning(
                "facet: episode missing project=%s id=%s",
                event.project_id,
                event.episode_id,
            )
            return

        content = json.loads(row["content_json"])
        tool_calls = json.loads(row["tool_calls_json"] or "[]")
        ts = int(row["ts"])

        text = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False)
        )
        vec = embed_text(text)
        memory_type = classify(content, tool_calls)
        importance = _base_importance(memory_type, len(tool_calls))

        db.execute(
            "DELETE FROM vec_episodes WHERE episode_id = ?",
            (event.episode_id,),
        )
        db.execute(
            "INSERT INTO vec_episodes"
            "(episode_id, embedding, memory_type, importance, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event.episode_id,
                _floats_to_bytes(vec),
                memory_type,
                importance,
                ts,
            ),
        )
        db.execute(
            "UPDATE episodes SET memory_type=?, facet_pending=0 WHERE id=?",
            (memory_type, event.episode_id),
        )
    finally:
        db.close()

    kuzu_conn = open_project_kuzu(event.db_root, event.project_id)
    kuzu_conn.execute(
        "MERGE (e:Episode {id: $id}) "
        "SET e.ts = $ts, e.memory_type = $mt, e.importance = $imp",
        {
            "id": event.episode_id,
            "ts": ts,
            "mt": memory_type,
            "imp": importance,
        },
    )
    for ent in extract_entities(content):
        kuzu_conn.execute(
            "MERGE (n:Entity {name: $name}) SET n.kind = $kind",
            {"name": ent.name, "kind": ent.kind},
        )
        kuzu_conn.execute(
            "MATCH (e:Episode {id: $eid}), (n:Entity {name: $name}) "
            "MERGE (e)-[:MENTIONS]->(n)",
            {"eid": event.episode_id, "name": ent.name},
        )
    for ref in extract_code_refs(tool_calls):
        ext = ref.path.rsplit(".", 1)[-1] if "." in ref.path else ""
        kuzu_conn.execute(
            "MERGE (f:File {path: $path}) SET f.ext = $ext",
            {"path": ref.path, "ext": ext},
        )
        kuzu_conn.execute(
            "MATCH (e:Episode {id: $eid}), (f:File {path: $path}) "
            "MERGE (e)-[:TOUCHED]->(f)",
            {"eid": event.episode_id, "path": ref.path},
        )
    for dec in extract_decisions(content):
        kuzu_conn.execute(
            "MERGE (d:Decision {hash: $h}) SET d.summary = $s",
            {"h": dec.hash, "s": dec.summary},
        )
        kuzu_conn.execute(
            "MATCH (e:Episode {id: $eid}), (d:Decision {hash: $h}) "
            "MERGE (e)-[:IMPLEMENTS]->(d)",
            {"eid": event.episode_id, "h": dec.hash},
        )

    log.info(
        "facet: processed project=%s id=%s type=%s",
        event.project_id,
        event.episode_id,
        memory_type,
    )


class FacetWorker:
    """Consumes FacetEvent from an asyncio.Queue; runs CPU-bound work in a thread."""

    def __init__(self, queue: asyncio.Queue, *, name: str = "facet-worker"):
        self.queue = queue
        self.name = name
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        from spillover.metrics.registry import facet_queue_depth

        loop = asyncio.get_running_loop()
        while True:
            event = await self.queue.get()
            facet_queue_depth.set(self.queue.qsize())
            try:
                await loop.run_in_executor(None, _process_one, event)
            except Exception:
                log.exception(
                    "facet worker error project=%s id=%s",
                    event.project_id,
                    event.episode_id,
                )
            finally:
                self.queue.task_done()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=self.name)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
