from pathlib import Path

from app.rag import store
from app.rag.chunking import chunk_pages, chunk_text
from tests.conftest import FakeLLM

SAMPLE = Path(__file__).resolve().parent.parent / "sample_docs" / "agentic_ai_study_notes.md"


def test_chunks_respect_size_and_overlap():
    text = " ".join(f"Sentence number {i} talks about retrieval and agents." for i in range(200))
    chunks = chunk_text(text, size=500, overlap=100)
    assert len(chunks) > 5
    assert all(len(c) <= 600 for c in chunks)
    # consecutive chunks share overlapping context
    assert any(chunks[i][-40:].split()[-1] in chunks[i + 1] for i in range(len(chunks) - 1))


def test_chunk_pages_keeps_page_numbers():
    pieces = chunk_pages(["Page one text " * 30, "", "Page three text " * 30], size=200, overlap=20)
    assert {p for p, _ in pieces} == {1, 3}


async def test_ingest_and_hybrid_search_ranks_relevant_section_first():
    llm = FakeLLM()
    doc = await store.ingest_document(SAMPLE.name, SAMPLE.read_bytes(), llm.embed)
    assert doc["chunks"] >= 5 and doc["embedded"] == 1
    retriever = store.HybridRetriever(llm.embed)
    hits = await retriever.search("cache-augmented generation context window prompt caching", top_k=3)
    assert hits, "expected results"
    assert "cache-augmented" in hits[0]["text"].lower() or "cag" in hits[0]["text"].lower()
    assert hits[0]["bm25_rank"] is not None and hits[0]["dense_rank"] is not None
    src = store.chunk_to_source(hits[0])
    assert src["type"] == "kb" and "p." in src["title"]


async def test_retrieval_degrades_to_bm25_without_embeddings():
    doc = await store.ingest_document(SAMPLE.name, SAMPLE.read_bytes(), None)
    assert doc["embedded"] == 0
    hits = await store.HybridRetriever(None).search("BM25 reciprocal rank fusion hybrid retrieval", top_k=2)
    assert hits and hits[0]["dense_rank"] is None
    assert "bm25" in hits[0]["text"].lower()


async def test_index_refreshes_after_delete():
    llm = FakeLLM()
    doc = await store.ingest_document(SAMPLE.name, SAMPLE.read_bytes(), llm.embed)
    retriever = store.HybridRetriever(llm.embed)
    assert await retriever.search("workflow automation n8n", top_k=1)
    store.delete_document(doc["id"])
    assert await retriever.search("workflow automation n8n", top_k=1) == []
