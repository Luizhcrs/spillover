from __future__ import annotations

import json
import sqlite3

from spillover.retriever.vector import Hit


def render_ltm_block(db: sqlite3.Connection, hits: list[Hit]) -> str:
    if not hits:
        return ""
    ids = [h.episode_id for h in hits]
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT id, role, content_json, memory_type FROM episodes "
        f"WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    sections: list[str] = []
    for hit in hits:
        row = by_id.get(hit.episode_id)
        if row is None:
            continue
        content = json.loads(row["content_json"])
        if isinstance(content, list):
            text = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        else:
            text = str(content)
        sections.append(
            f'<episode id="{hit.episode_id}" type="{row["memory_type"]}" '
            f'role="{row["role"]}">\n{text}\n</episode>'
        )
    if not sections:
        return ""
    return (
        "<spillover-ltm>\n"
        "The following are relevant past episodes retrieved from long-term memory.\n"
        "They are NOT part of the active conversation.\n\n"
        + "\n\n".join(sections)
        + "\n</spillover-ltm>"
    )
