"""Scholarly search tools: arXiv, OpenAlex and Wikipedia (all free, no API key needed).

Every tool returns a list of normalised *source* dicts so that downstream agents can
cite them uniformly:

    {key, type, title, authors, year, venue, url, doi, abstract, citations}
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

from .. import db
from ..config import get_settings

USER_AGENT = "VeritasResearchAgent/1.0 (academic project; https://github.com/)"
ABSTRACT_CHARS = 1200
_STOP = set(
    "a an the of in on for to and or with by from is are was were be been being what which who whom how why "
    "does do did can could should would will this that these those as at about into than then there their its "
    "it vs versus between among".split()
)

_client: httpx.AsyncClient | None = None
_arxiv_lock = asyncio.Lock()
_arxiv_last = 0.0


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(25.0, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    return _client


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _keywords(query: str, limit: int = 8) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", query.lower()) if w not in _STOP]
    return words[:limit]


async def _cached(tool: str, args: dict, fn) -> list[dict]:
    """Tool-result cache (a form of cache-augmented retrieval for repeated queries)."""
    key = hashlib.sha256(f"{tool}|{json.dumps(args, sort_keys=True)}".encode()).hexdigest()
    hit = db.cache_get("tool_cache", key, ttl_s=get_settings().tool_cache_ttl_s)
    if hit is not None:
        return json.loads(hit)
    result = await fn()
    if result:  # never cache empty / failed lookups
        db.cache_put("tool_cache", key, json.dumps(result))
    return result


# ------------------------------------------------------------------ arXiv
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def parse_arxiv_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall("a:entry", ATOM):
        raw_id = _clean(entry.findtext("a:id", default="", namespaces=ATOM))
        arxiv_id = re.sub(r"v\d+$", "", raw_id.rsplit("/abs/", 1)[-1])
        if not arxiv_id:
            continue
        published = entry.findtext("a:published", default="", namespaces=ATOM)
        authors = [_clean(a.findtext("a:name", default="", namespaces=ATOM)) for a in entry.findall("a:author", ATOM)]
        doi = entry.findtext("arxiv:doi", default=None, namespaces=ATOM)
        journal = entry.findtext("arxiv:journal_ref", default=None, namespaces=ATOM)
        cat = entry.find("arxiv:primary_category", ATOM)
        out.append({
            "key": f"arxiv:{arxiv_id}",
            "type": "arxiv",
            "title": _clean(entry.findtext("a:title", default="", namespaces=ATOM)),
            "authors": authors[:8],
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "venue": _clean(journal) if journal else f"arXiv ({cat.get('term') if cat is not None else 'preprint'})",
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "doi": doi,
            "abstract": _clean(entry.findtext("a:summary", default="", namespaces=ATOM))[:ABSTRACT_CHARS],
            "citations": None,
        })
    return out


async def search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    max_results = max(1, min(int(max_results), 10))

    async def run() -> list[dict]:
        global _arxiv_last
        words = _keywords(query)
        attempts = []
        if words:
            attempts.append(" AND ".join(f"all:{w}" for w in words[:6]))
            attempts.append(" AND ".join(f"all:{w}" for w in words[:3]))
        attempts.append(f'all:"{query}"')
        for q in attempts:
            async with _arxiv_lock:  # arXiv asks for at most one request every 3 seconds
                wait = 3.0 - (time.time() - _arxiv_last)
                if wait > 0:
                    await asyncio.sleep(wait)
                resp = await client().get(
                    "https://export.arxiv.org/api/query",
                    params={"search_query": q, "max_results": max_results, "sortBy": "relevance"},
                )
                _arxiv_last = time.time()
            resp.raise_for_status()
            results = parse_arxiv_feed(resp.text)
            if results:
                return results
        return []

    return await _cached("search_arxiv", {"q": query, "n": max_results}, run)


# ------------------------------------------------------------------ OpenAlex
def reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str:
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = [(i, w) for w, idxs in inverted.items() for i in idxs]
    return " ".join(w for _, w in sorted(positions))


def parse_openalex(data: dict) -> list[dict]:
    out = []
    for w in data.get("results", []):
        title = _clean(w.get("display_name") or w.get("title"))
        if not title:
            continue
        doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
        loc = w.get("primary_location") or {}
        venue = ((loc.get("source") or {}).get("display_name")) or None
        oa_url = (w.get("open_access") or {}).get("oa_url")
        authors = [
            _clean((a.get("author") or {}).get("display_name"))
            for a in (w.get("authorships") or [])[:8]
        ]
        out.append({
            "key": f"doi:{doi.lower()}" if doi else f"openalex:{w.get('id', '').rsplit('/', 1)[-1]}",
            "type": "openalex",
            "title": title,
            "authors": [a for a in authors if a],
            "year": w.get("publication_year"),
            "venue": venue,
            "url": f"https://doi.org/{doi}" if doi else (oa_url or w.get("id")),
            "doi": doi,
            "abstract": reconstruct_abstract(w.get("abstract_inverted_index"))[:ABSTRACT_CHARS],
            "citations": w.get("cited_by_count"),
        })
    return out


async def search_openalex(query: str, max_results: int = 5, from_year: int | None = None) -> list[dict]:
    """OpenAlex search with a tool-level fallback to Crossref when OpenAlex is rate-limited or down."""
    max_results = max(1, min(int(max_results), 10))
    try:
        return await _search_openalex(query, max_results, from_year)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429 or e.response.status_code >= 500:
            results = await search_crossref(query, max_results, from_year)
            for r in results:
                r["fallback_from"] = "openalex"
            return results
        raise


async def _search_openalex(query: str, max_results: int, from_year: int | None) -> list[dict]:
    async def run() -> list[dict]:
        params: dict[str, Any] = {
            "search": query,
            "per-page": max_results,
            "select": "id,doi,display_name,publication_year,cited_by_count,authorships,"
                      "abstract_inverted_index,primary_location,open_access",
        }
        filters = ["has_abstract:true"]
        if from_year:
            filters.append(f"from_publication_date:{int(from_year)}-01-01")
        params["filter"] = ",".join(filters)
        settings = get_settings()
        if settings.openalex_api_key:
            params["api_key"] = settings.openalex_api_key
        if settings.contact_email:
            params["mailto"] = settings.contact_email
        resp = await client().get("https://api.openalex.org/works", params=params)
        resp.raise_for_status()
        return parse_openalex(resp.json())

    return await _cached("search_openalex", {"q": query, "n": max_results, "y": from_year}, run)


# ------------------------------------------------------------------ Crossref (fallback)
def parse_crossref(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        title = _clean((it.get("title") or [""])[0])
        doi = it.get("DOI")
        if not title or not doi:
            continue
        year = None
        try:
            year = it["issued"]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            pass
        authors = [_clean(f"{a.get('given', '')} {a.get('family', '')}") for a in (it.get("author") or [])[:8]]
        out.append({
            "key": f"doi:{doi.lower()}",
            "type": "crossref",
            "title": title,
            "authors": [a for a in authors if a],
            "year": year,
            "venue": _clean((it.get("container-title") or [""])[0]) or None,
            "url": f"https://doi.org/{doi}",
            "doi": doi,
            "abstract": _clean(re.sub(r"<[^>]+>", " ", it.get("abstract") or ""))[:ABSTRACT_CHARS],
            "citations": it.get("is-referenced-by-count"),
        })
    return out


async def search_crossref(query: str, max_results: int = 5, from_year: int | None = None) -> list[dict]:
    async def run() -> list[dict]:
        filters = ["has-abstract:true"]
        if from_year:
            filters.append(f"from-pub-date:{int(from_year)}")
        params = {"query": query, "rows": max_results, "filter": ",".join(filters),
                  "select": "DOI,title,author,issued,container-title,abstract,is-referenced-by-count"}
        email = get_settings().contact_email
        if email:
            params["mailto"] = email
        resp = await client().get("https://api.crossref.org/works", params=params)
        resp.raise_for_status()
        return parse_crossref(resp.json().get("message", {}).get("items", []))

    return await _cached("search_crossref", {"q": query, "n": max_results, "y": from_year}, run)


# ------------------------------------------------------------------ Wikipedia
async def search_wikipedia(query: str, max_results: int = 3) -> list[dict]:
    max_results = max(1, min(int(max_results), 5))

    async def run() -> list[dict]:
        resp = await client().get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "format": "json",
                    "srlimit": max_results},
        )
        resp.raise_for_status()
        titles = [h["title"] for h in resp.json().get("query", {}).get("search", [])]

        async def summary(title: str) -> dict | None:
            r = await client().get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}"
            )
            if r.status_code != 200:
                return None
            d = r.json()
            url = ((d.get("content_urls") or {}).get("desktop") or {}).get("page") or \
                f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
            return {
                "key": f"wikipedia:{title.lower()}",
                "type": "wikipedia",
                "title": f"{d.get('title', title)} (Wikipedia)",
                "authors": ["Wikipedia contributors"],
                "year": int(d["timestamp"][:4]) if d.get("timestamp") else None,
                "venue": "Wikipedia",
                "url": url,
                "doi": None,
                "abstract": _clean(d.get("extract"))[:ABSTRACT_CHARS],
                "citations": None,
            }

        results = await asyncio.gather(*(summary(t) for t in titles), return_exceptions=True)
        return [r for r in results if isinstance(r, dict) and r.get("abstract")]

    return await _cached("search_wikipedia", {"q": query, "n": max_results}, run)


# ------------------------------------------------------------------ verification helpers (evals)
async def crossref_lookup(title: str) -> list[dict]:
    resp = await client().get(
        "https://api.crossref.org/works",
        params={"query.bibliographic": title, "rows": 3, "select": "DOI,title,issued,author"},
    )
    resp.raise_for_status()
    items = resp.json().get("message", {}).get("items", [])
    out = []
    for it in items:
        year = None
        try:
            year = it["issued"]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            pass
        out.append({"title": _clean((it.get("title") or [""])[0]), "doi": it.get("DOI"), "year": year})
    return out


async def lookup_doi(doi: str) -> dict | None:
    """Resolve a DOI to its registered title/year (OpenAlex covers Crossref + DataCite DOIs)."""
    doi = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
    r = await client().get(f"https://api.openalex.org/works/doi:{doi}",
                           params={"select": "display_name,publication_year,doi"})
    if r.status_code == 200:
        d = r.json()
        return {"title": _clean(d.get("display_name")), "year": d.get("publication_year"), "db": "openalex", "id": doi}
    r = await client().get(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
    if r.status_code == 200:
        m = r.json().get("message", {})
        year = None
        try:
            year = m["issued"]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            pass
        return {"title": _clean((m.get("title") or [""])[0]), "year": year, "db": "crossref", "id": doi}
    return None


async def lookup_arxiv(arxiv_id: str) -> dict | None:
    global _arxiv_last
    async with _arxiv_lock:
        wait = 3.0 - (time.time() - _arxiv_last)
        if wait > 0:
            await asyncio.sleep(wait)
        r = await client().get("https://export.arxiv.org/api/query", params={"id_list": arxiv_id, "max_results": 1})
        _arxiv_last = time.time()
    if r.status_code != 200:
        return None
    papers = parse_arxiv_feed(r.text)
    if not papers or not papers[0]["title"]:
        return None
    return {"title": papers[0]["title"], "year": papers[0]["year"], "db": "arxiv", "id": arxiv_id}
