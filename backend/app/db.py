"""SQLite persistence (runs, trace events, knowledge base, memory, caches).

SQLite in WAL mode is shared safely between the API process and the MCP tool-server
subprocess. Every helper opens a short-lived connection, so it is safe to call from any
thread or task.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    settings TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    plan TEXT,
    report TEXT,
    references_json TEXT,
    sources_json TEXT,
    critique TEXT,
    metrics TEXT,
    error TEXT,
    client_ip TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    agent TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    pages INTEGER NOT NULL,
    chunks INTEGER NOT NULL,
    chars INTEGER NOT NULL,
    embedded INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS memories (
    run_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    summary TEXT NOT NULL,
    embedding BLOB,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tool_cache (key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

TERMINAL_STATUSES = {"completed", "rejected", "cancelled", "failed", "interrupted"}


def db_path() -> Path:
    return get_settings().db_path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def _dumps(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


def _loads(v: str | None) -> Any:
    return None if v is None else json.loads(v)


# ---------------------------------------------------------------- runs
def create_run(run_id: str, question: str, settings: dict, client_ip: str | None) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, question, settings, status, created_at, updated_at, client_ip)"
            " VALUES (?, ?, ?, 'queued', ?, ?, ?)",
            (run_id, question, _dumps(settings), now, now, client_ip),
        )


def update_run(run_id: str, **fields: Any) -> None:
    json_cols = {"plan", "references_json", "sources_json", "critique", "metrics", "settings"}
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k} = ?")
        vals.append(_dumps(v) if k in json_cols else v)
    cols.append("updated_at = ?")
    vals.append(time.time())
    vals.append(run_id)
    with connect() as conn:
        conn.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", vals)


def _run_row(row: sqlite3.Row, full: bool = True) -> dict:
    d = dict(row)
    for k in ("settings", "plan", "references_json", "sources_json", "critique", "metrics"):
        if k in d:
            d[k] = _loads(d[k])
    d["references"] = d.pop("references_json", None)
    d["sources"] = d.pop("sources_json", None)
    d.pop("client_ip", None)
    if not full:
        for k in ("report", "references", "sources", "plan", "critique"):
            d.pop(k, None)
    return d


def get_run(run_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _run_row(row) if row else None


def list_runs(limit: int = 30) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, question, settings, status, created_at, updated_at, metrics, error"
            " FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_run_row(r, full=False) for r in rows]


def delete_run(run_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM memories WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))


def mark_stale_runs() -> int:
    """Runs that were in flight when the server stopped cannot be resumed (in-memory checkpoints)."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE runs SET status = 'interrupted', error = 'Server restarted while the run was active',"
            " updated_at = ? WHERE status IN ('queued', 'running', 'awaiting_approval')",
            (time.time(),),
        )
        return cur.rowcount


def run_stats() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT status, metrics FROM runs").fetchall()
    by_status: dict[str, int] = {}
    agg = {"llm_calls": 0, "tokens_total": 0, "tool_calls": 0, "duration_s": 0.0}
    critic, coverage, completed = [], [], 0
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        m = _loads(r["metrics"]) or {}
        if r["status"] == "completed":
            completed += 1
            for k in agg:
                agg[k] += m.get(k, 0) or 0
            if m.get("critic_score") is not None:
                critic.append(m["critic_score"])
            if m.get("citation_coverage") is not None:
                coverage.append(m["citation_coverage"])
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    return {
        "total_runs": len(rows),
        "by_status": by_status,
        "completed": completed,
        "avg_llm_calls": round(agg["llm_calls"] / completed, 1) if completed else None,
        "avg_duration_s": round(agg["duration_s"] / completed, 1) if completed else None,
        "total_tokens": agg["tokens_total"],
        "total_tool_calls": agg["tool_calls"],
        "avg_critic_score": avg(critic),
        "avg_citation_coverage": avg(coverage),
    }


# ---------------------------------------------------------------- events
def insert_event(run_id: str, type_: str, agent: str | None, data: dict) -> dict:
    ts = time.time()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO events (run_id, ts, type, agent, data) VALUES (?, ?, ?, ?, ?)",
            (run_id, ts, type_, agent, _dumps(data)),
        )
        eid = cur.lastrowid
    return {"id": eid, "run_id": run_id, "ts": ts, "type": type_, "agent": agent, "data": data}


def list_events(run_id: str, after: int = 0, limit: int = 2000) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
            (run_id, after, limit),
        ).fetchall()
    return [
        {"id": r["id"], "run_id": r["run_id"], "ts": r["ts"], "type": r["type"],
         "agent": r["agent"], "data": _loads(r["data"])}
        for r in rows
    ]


# ---------------------------------------------------------------- caches
def cache_get(table: str, key: str, ttl_s: float | None = None) -> str | None:
    assert table in ("llm_cache", "tool_cache")
    col = "response" if table == "llm_cache" else "result"
    with connect() as conn:
        row = conn.execute(
            f"SELECT {col} AS value, created_at FROM {table} WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    if ttl_s is not None and time.time() - row["created_at"] > ttl_s:
        return None
    return row["value"]


def cache_put(table: str, key: str, value: str) -> None:
    assert table in ("llm_cache", "tool_cache")
    col = "response" if table == "llm_cache" else "result"
    with connect() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO {table} (key, {col}, created_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )


def kv_get(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
