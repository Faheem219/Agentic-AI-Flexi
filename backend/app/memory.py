"""Long-term (episodic) memory: past research runs, recalled by semantic similarity.

When a new question arrives, the most similar previous runs are recalled and shown to the
planner, so the agent can build on prior work instead of starting from zero.
"""

from __future__ import annotations

import time

import numpy as np

from . import db


async def save_memory(run_id: str, question: str, summary: str, embed) -> None:
    vec = None
    try:
        vec = (await embed([f"{question}\n{summary}"], "RETRIEVAL_DOCUMENT"))[0]
    except Exception:  # noqa: BLE001 - memory is best-effort
        pass
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO memories (run_id, question, summary, embedding, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, question, summary,
             np.asarray(vec, dtype=np.float32).tobytes() if vec is not None else None, time.time()),
        )


async def recall(question: str, embed, k: int = 2, min_similarity: float = 0.70,
                 exclude_run: str | None = None) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT run_id, question, summary, embedding, created_at FROM memories WHERE embedding IS NOT NULL"
        ).fetchall()
    rows = [r for r in rows if r["run_id"] != exclude_run]
    if not rows:
        return []
    q = np.asarray((await embed([question], "RETRIEVAL_QUERY"))[0], dtype=np.float32)
    q /= np.linalg.norm(q) or 1.0
    scored = []
    for r in rows:
        v = np.frombuffer(r["embedding"], dtype=np.float32)
        sim = float(v @ q / (np.linalg.norm(v) or 1.0))
        if sim >= min_similarity:
            scored.append({"run_id": r["run_id"], "question": r["question"], "summary": r["summary"],
                           "similarity": round(sim, 3), "created_at": r["created_at"]})
    scored.sort(key=lambda m: -m["similarity"])
    return scored[:k]
