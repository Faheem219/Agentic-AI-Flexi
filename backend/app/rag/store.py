"""Knowledge base: document ingestion and hybrid retrieval (BM25 + dense embeddings + RRF).

Ingestion  : extract text -> page-aware chunking -> contextual header -> embeddings -> SQLite
Retrieval  : BM25 keyword ranking  +  cosine similarity on Gemini embeddings,
             fused with Reciprocal Rank Fusion (RRF). If embeddings are unavailable
             (e.g. quota exhausted) retrieval degrades gracefully to BM25 only.
"""

from __future__ import annotations

import io
import re
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

import numpy as np
from rank_bm25 import BM25Okapi

from .. import db
from .chunking import chunk_pages

Embedder = Callable[[list[str], str], Awaitable[list[list[float]]]]

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an the of in on for to and or with by from is are was were be been this that these those as at it its "
    "into than then there their which what who how why can could should would will do does did not no".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


def extract_pages(filename: str, data: bytes) -> list[str]:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return [(p.extract_text() or "") for p in reader.pages]
    text = data.decode("utf-8", errors="ignore")
    # treat ~3000-char blocks as "pages" for txt/markdown so citations stay specific
    paras = re.split(r"\n\s*\n", text)
    pages, buf = [], ""
    for p in paras:
        if len(buf) + len(p) > 3000 and buf:
            pages.append(buf)
            buf = ""
        buf += p + "\n\n"
    if buf.strip():
        pages.append(buf)
    return pages


def _title_from(filename: str, pages: list[str], data: bytes | None = None) -> str:
    """Prefer the document's own title (Markdown H1 / PDF metadata), else the file name."""
    first = (pages[0] if pages else "").lstrip()
    m = re.match(r"#\s+(.+)", first)
    if m:
        return m.group(1).strip()[:160]
    if filename.lower().endswith(".pdf") and data:
        try:
            from pypdf import PdfReader

            meta_title = (PdfReader(io.BytesIO(data)).metadata or {}).get("/Title")
            if meta_title and len(str(meta_title).strip()) > 3:
                return str(meta_title).strip()[:160]
        except Exception:  # noqa: BLE001
            pass
    stem = re.sub(r"\.(pdf|txt|md|markdown)$", "", filename, flags=re.I)
    title = re.sub(r"[_\-]+", " ", stem).strip() or "Untitled document"
    return title[:1].upper() + title[1:] if title.islower() else title


async def ingest_document(filename: str, data: bytes, embed: Embedder | None) -> dict:
    pages = extract_pages(filename, data)
    title = _title_from(filename, pages, data)
    pieces = chunk_pages(pages)
    if not pieces:
        raise ValueError("No extractable text found in the document (is it a scanned PDF?).")
    # Lightweight "contextual retrieval": prefix each chunk with its document title + page so
    # the chunk is self-describing for both BM25 and the embedding model.
    texts = [f"[{title} | p.{page}] {chunk}" for page, chunk in pieces]
    vectors: list[list[float]] | None = None
    if embed is not None:
        try:
            vectors = await embed(texts, "RETRIEVAL_DOCUMENT")
        except Exception:  # noqa: BLE001 - quota/network: keep BM25-only retrieval working
            vectors = None
    doc_id = uuid.uuid4().hex[:12]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO documents (id, title, filename, pages, chunks, chars, embedded, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, title, filename, len(pages), len(pieces), sum(len(t) for t in texts),
             int(vectors is not None), time.time()),
        )
        for i, ((page, _), text) in enumerate(zip(pieces, texts)):
            blob = np.asarray(vectors[i], dtype=np.float32).tobytes() if vectors else None
            conn.execute(
                "INSERT INTO chunks (id, doc_id, idx, page, text, embedding) VALUES (?, ?, ?, ?, ?, ?)",
                (f"{doc_id}-{i}", doc_id, i, page, text, blob),
            )
    db.kv_set("kb_version", str(time.time()))
    return get_document(doc_id)


def list_documents() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_document(doc_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def delete_document(doc_id: str) -> bool:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    db.kv_set("kb_version", str(time.time()))
    return cur.rowcount > 0


def document_count() -> int:
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


@dataclass
class _Index:
    version: str
    ids: list[str]
    meta: list[dict]
    bm25: BM25Okapi | None
    matrix: np.ndarray | None  # L2-normalised embeddings (rows aligned with ids where present)
    has_vec: np.ndarray        # bool mask of chunks that have embeddings


class HybridRetriever:
    """In-memory index rebuilt lazily whenever the knowledge base changes."""

    def __init__(self, embed: Embedder | None) -> None:
        self.embed = embed
        self._index: _Index | None = None

    def _load(self) -> _Index:
        version = db.kv_get("kb_version") or "0"
        if self._index and self._index.version == version:
            return self._index
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT c.id, c.doc_id, c.page, c.text, c.embedding, d.title FROM chunks c"
                " JOIN documents d ON d.id = c.doc_id ORDER BY c.doc_id, c.idx"
            ).fetchall()
        ids = [r["id"] for r in rows]
        meta = [{"chunk_id": r["id"], "doc_id": r["doc_id"], "doc_title": r["title"],
                 "page": r["page"], "text": r["text"]} for r in rows]
        bm25 = BM25Okapi([tokenize(m["text"]) or ["_"] for m in meta]) if rows else None
        vecs, mask = [], []
        dim = None
        for r in rows:
            if r["embedding"]:
                v = np.frombuffer(r["embedding"], dtype=np.float32)
                dim = dim or v.shape[0]
                vecs.append(v / (np.linalg.norm(v) or 1.0))
                mask.append(True)
            else:
                vecs.append(None)
                mask.append(False)
        matrix = None
        if dim:
            matrix = np.vstack([v if v is not None else np.zeros(dim, dtype=np.float32) for v in vecs])
        self._index = _Index(version, ids, meta, bm25, matrix, np.array(mask, dtype=bool))
        return self._index

    async def search(self, query: str, top_k: int = 5, pool: int = 20, rrf_k: int = 60) -> list[dict]:
        idx = self._load()
        if not idx.ids:
            return []
        # 1) sparse / keyword ranking
        bm25_rank: dict[int, int] = {}
        if idx.bm25 is not None:
            scores = idx.bm25.get_scores(tokenize(query))
            order = [i for i in np.argsort(-scores)[:pool] if scores[i] > 0]
            bm25_rank = {int(i): r for r, i in enumerate(order, start=1)}
        # 2) dense / semantic ranking
        dense_rank: dict[int, int] = {}
        dense_score: dict[int, float] = {}
        if idx.matrix is not None and idx.has_vec.any() and self.embed is not None:
            try:
                q = np.asarray((await self.embed([query], "RETRIEVAL_QUERY"))[0], dtype=np.float32)
                q = q / (np.linalg.norm(q) or 1.0)
                sims = idx.matrix @ q
                sims[~idx.has_vec] = -1.0
                order = np.argsort(-sims)[:pool]
                dense_rank = {int(i): r for r, i in enumerate(order, start=1)}
                dense_score = {int(i): float(sims[i]) for i in order}
            except Exception:  # noqa: BLE001 - degrade to BM25 only
                dense_rank = {}
        # 3) Reciprocal Rank Fusion
        fused: dict[int, float] = {}
        for ranks in (bm25_rank, dense_rank):
            for i, r in ranks.items():
                fused[i] = fused.get(i, 0.0) + 1.0 / (rrf_k + r)
        best = sorted(fused, key=lambda i: -fused[i])[:top_k]
        return [
            {**idx.meta[i], "rrf_score": round(fused[i], 5), "bm25_rank": bm25_rank.get(i),
             "dense_rank": dense_rank.get(i), "cosine": round(dense_score[i], 4) if i in dense_score else None}
            for i in best
        ]


def chunk_to_source(hit: dict) -> dict:
    """Normalise a retrieved chunk into the common citable *source* shape."""
    return {
        "key": f"kb:{hit['chunk_id']}",
        "type": "kb",
        "title": f"{hit['doc_title']} (uploaded document, p.{hit['page']})",
        "authors": [],
        "year": None,
        "venue": "Knowledge base",
        "url": None,
        "doi": None,
        "abstract": re.sub(r"#{1,6}\s+", "", re.sub(r"^\[[^\]]*\]\s*", "", hit["text"]))[:1200],
        "citations": None,
        "retrieval": {k: hit.get(k) for k in ("rrf_score", "bm25_rank", "dense_rank", "cosine")},
    }
