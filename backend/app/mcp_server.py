"""Veritas research tools exposed as a Model Context Protocol (MCP) server.

The agents never call search APIs directly: they discover and invoke these tools through
an MCP client, so the same tool server can also be plugged into any MCP host
(Claude Desktop, Claude Code, IDEs...).

Run standalone:
    python -m app.mcp_server                      # stdio transport (used by the backend)
    python -m app.mcp_server --http --port 8765   # streamable HTTP for external MCP hosts
"""

from __future__ import annotations

import argparse
import json

import httpx

from mcp.server.mcpserver import MCPServer

from . import db
from .config import get_settings
from .rag import store
from .tools import scholarly

server = MCPServer(
    "veritas-research-tools",
    instructions=(
        "Scholarly research tools. Use search_openalex for peer-reviewed literature across all fields, "
        "search_arxiv for recent CS/physics/maths preprints, search_wikipedia for background definitions "
        "and search_knowledge_base for the user's own uploaded documents."
    ),
)

_retriever: store.HybridRetriever | None = None


def _get_retriever() -> store.HybridRetriever:
    global _retriever
    if _retriever is None:
        embed = None
        if get_settings().gemini_api_key:
            from .llm import GeminiLLM

            llm = GeminiLLM(get_settings())
            embed = llm.embed
        _retriever = store.HybridRetriever(embed)
    return _retriever


def _ok(results: list[dict]) -> str:
    return json.dumps({"results": results}, ensure_ascii=False)


def _err(tool: str, e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        reason = "rate limit or daily quota reached" if code == 429 else e.response.reason_phrase
        msg = f"{tool} failed: HTTP {code} ({reason}). Try another tool or a different query."
    elif isinstance(e, httpx.TimeoutException):
        msg = f"{tool} failed: the service timed out. Try another tool."
    else:
        msg = f"{tool} failed: {type(e).__name__}: {e}"[:300]
    return json.dumps({"results": [], "error": msg})


@server.tool(description=(
    "Search OpenAlex (250M+ scholarly works, all disciplines) for peer-reviewed papers. "
    "Returns title, authors, year, venue, DOI, citation count and abstract. Falls back to Crossref "
    "automatically if OpenAlex is unavailable. "
    "Optionally restrict to works published from `from_year` onwards."
))
async def search_openalex(query: str, max_results: int = 5, from_year: int | None = None) -> str:
    try:
        return _ok(await scholarly.search_openalex(query, max_results, from_year))
    except Exception as e:  # noqa: BLE001 - surface errors to the agent as data, not crashes
        return _err("search_openalex", e)


@server.tool(description=(
    "Search arXiv preprints (computer science, AI, physics, maths, statistics, quantitative biology/finance). "
    "Best for recent and cutting-edge technical work. Returns title, authors, year and abstract."
))
async def search_arxiv(query: str, max_results: int = 5) -> str:
    try:
        return _ok(await scholarly.search_arxiv(query, max_results))
    except Exception as e:  # noqa: BLE001
        return _err("search_arxiv", e)


@server.tool(description=(
    "Search Wikipedia for background knowledge, definitions and overviews of a concept. "
    "Use for context, not as primary evidence."
))
async def search_wikipedia(query: str, max_results: int = 3) -> str:
    try:
        return _ok(await scholarly.search_wikipedia(query, max_results))
    except Exception as e:  # noqa: BLE001
        return _err("search_wikipedia", e)


@server.tool(description=(
    "Semantic + keyword (hybrid RAG) search over documents the user uploaded to their private "
    "knowledge base. Returns the most relevant passages with document title and page number."
))
async def search_knowledge_base(query: str, top_k: int = 5) -> str:
    try:
        hits = await _get_retriever().search(query, top_k=max(1, min(int(top_k), 10)))
        return _ok([store.chunk_to_source(h) for h in hits])
    except Exception as e:  # noqa: BLE001
        return _err("search_knowledge_base", e)


@server.resource("kb://documents", description="List of documents in the user's knowledge base.",
                 mime_type="application/json")
def kb_documents() -> str:
    return json.dumps(store.list_documents(), default=str)


@server.prompt(description="Prompt template for a structured, citation-grounded literature review.")
def literature_review(topic: str) -> str:
    return (
        f"Write a structured literature review on: {topic}.\n"
        "1. Search OpenAlex and arXiv for the most relevant and most cited work.\n"
        "2. Group findings into themes; note agreements, disagreements and open problems.\n"
        "3. Cite every claim with the source it came from. Never cite a paper you did not retrieve."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Veritas MCP tool server")
    parser.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    db.init_db()
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
