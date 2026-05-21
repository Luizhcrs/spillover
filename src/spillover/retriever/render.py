from __future__ import annotations

import json
import sqlite3

from spillover.retriever.vector import Hit


def render_ltm_block(db: sqlite3.Connection, hits: list[Hit]) -> str:
    """Render hits as a single <spillover-ltm> block."""
    if not hits:
        return ""
    sections: list[str] = []
    for hit in hits:
        row = db.execute(
            "SELECT role, content_json, memory_type FROM episodes WHERE id = ?",
            (hit.episode_id,),
        ).fetchone()
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
    return (
        "<spillover-ltm>\n"
        "The following are relevant past episodes retrieved from long-term memory.\n"
        "They are NOT part of the active conversation.\n\n"
        + "\n\n".join(sections)
        + "\n</spillover-ltm>"
    )
