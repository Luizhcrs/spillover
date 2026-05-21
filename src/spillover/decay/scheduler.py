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
}


def _apply_decay_for_project(db_root: Path, project_id: str) -> int:
    """Recompute importance for every vec_episode in this project. Returns count."""
    db = open_project_db(db_root, project_id)
    n = 0
    try:
        rows = db.execute(
            "SELECT episode_id, memory_type, ts FROM vec_episodes "
            "WHERE memory_type IS NOT NULL"
        ).fetchall()
        now_ms = int(time.time() * 1000)
        for r in rows:
            pinned_row = db.execute(
                "SELECT pinned, hit_count FROM episodes WHERE id=?",
                (r["episode_id"],),
            ).fetchone()
            if pinned_row and pinned_row["pinned"] == 1:
                continue
            age_hours = max(0, (now_ms - int(r["ts"])) / 1000 / 3600)
            half_life = HALF_LIFE_HOURS.get(r["memory_type"], 24)
            decay = math.exp(-age_hours / half_life)
            base = {
                "priority": 1.0,
                "procedural": 0.7,
                "semantic": 0.6,
                "episodic": 0.5,
            }.get(r["memory_type"], 0.5)
            hit_count = int(pinned_row["hit_count"]) if pinned_row else 0
            new_imp = min(1.0, base * decay + min(hit_count * 0.05, 0.5))
            db.execute(
                "UPDATE vec_episodes SET importance=? WHERE episode_id=?",
                (new_imp, r["episode_id"]),
            )
            n += 1
    finally:
        db.close()
    return n


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
            except asyncio.TimeoutError:
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
            n = await loop.run_in_executor(
                None, _apply_decay_for_project, self.db_root, pid
            )
            if n > 0:
                log.info("decay project=%s updated=%d", pid, n)
