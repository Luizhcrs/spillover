from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path

from spillover.logging import get_logger
from spillover.storage.sqlite import open_project_db

log = get_logger("decay")

HALF_LIFE_HOURS = {
    "priority": 60 * 24,
    "procedural": 30 * 24,
    "semantic": 14 * 24,
    "episodic": 7 * 24,
    "task": 90 * 24,
}


def _apply_decay_for_project(db_root: Path, project_id: str) -> int:
    db = open_project_db(db_root, project_id)
    n = 0
    try:
        rows = db.execute(
            "SELECT ve.episode_id, ve.memory_type, ve.ts, "
            "       COALESCE(e.pinned, 0) AS pinned, "
            "       COALESCE(e.hit_count, 0) AS hit_count "
            "FROM vec_episodes ve "
            "LEFT JOIN episodes e ON e.id = ve.episode_id "
            "WHERE ve.memory_type IS NOT NULL"
        ).fetchall()
        now_ms = int(time.time() * 1000)
        updates: list[tuple[float, str]] = []
        for r in rows:
            if int(r["pinned"]) == 1:
                continue
            age_hours = max(0, (now_ms - int(r["ts"])) / 1000 / 3600)
            half_life = HALF_LIFE_HOURS.get(r["memory_type"], 24)
            decay = math.exp(-age_hours / half_life)
            base = {
                "task": 0.95,
                "priority": 1.0,
                "procedural": 0.7,
                "semantic": 0.6,
                "episodic": 0.5,
            }.get(r["memory_type"], 0.5)
            hit_count = int(r["hit_count"])
            new_imp = min(1.0, base * decay + min(hit_count * 0.05, 0.5))
            updates.append((new_imp, r["episode_id"]))
            n += 1
        if updates:
            db.executemany(
                "UPDATE vec_episodes SET importance=? WHERE episode_id=?",
                updates,
            )
    finally:
        db.close()
    return n


def _prune_seen_turns_for_project(db_root: Path, project_id: str) -> int:
    from spillover.counter_compact.detection import prune_old_seen_turns
    db = open_project_db(db_root, project_id)
    try:
        return prune_old_seen_turns(db, project_id, ttl_hours=72)
    finally:
        db.close()


class DecayScheduler:
    def __init__(self, db_root: Path, interval_seconds: int = 6 * 3600):
        self.db_root = db_root
        self.interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="decay-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("decay tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                continue

    async def _tick(self) -> None:
        projects_dir = self.db_root / "projects"
        if not projects_dir.exists():
            return
        loop = asyncio.get_running_loop()
        for pdir in projects_dir.iterdir():
            if not pdir.is_dir():
                continue
            pid = pdir.name
            n_decayed = await loop.run_in_executor(
                None, _apply_decay_for_project, self.db_root, pid
            )
            n_pruned = await loop.run_in_executor(
                None, _prune_seen_turns_for_project, self.db_root, pid
            )
            if n_decayed > 0 or n_pruned > 0:
                log.info(
                    "decay project=%s decayed=%d pruned=%d",
                    pid, n_decayed, n_pruned,
                )
