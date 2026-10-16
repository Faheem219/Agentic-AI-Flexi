import json

from app import citations
from app.jsonutil import parse_json
from app.tools.scholarly import parse_arxiv_feed, parse_openalex, reconstruct_abstract

SOURCES = {
    "S1": {"title": "Dense retrieval improves open-domain question answering", "abstract": "Dense passage retrieval outperforms BM25 on open-domain QA benchmarks."},
    "S2": {"title": "Hallucination in large language models", "abstract": "Survey of hallucination causes and mitigation in LLMs."},
}


def test_invalid_citations_are_removed_and_reported():
    draft = ("Dense passage retrieval outperforms BM25 on open-domain question answering benchmarks [S1][S7].\n"
             "Hallucination remains a major open problem for large language models in practice [S2].")
    report = citations.verify(draft, SOURCES)
    assert report["invalid_citations"] == ["S7"]
    assert "[S7]" not in report["cleaned_draft"]
    assert report["citation_coverage"] == 1.0


def test_coverage_counts_uncited_sentences():
    draft = ("## Key Findings\nDense passage retrieval outperforms BM25 on open-domain question answering benchmarks [S1]. "
             "This sentence makes a substantive claim without giving any source at all.")
    report = citations.verify(draft, SOURCES)
    assert report["sentences"] == 2 and report["cited_sentences"] == 1
    assert report["citation_coverage"] == 0.5
    assert report["uncited_sentences"]


def test_weak_support_is_flagged():
    draft = "Quantum annealing hardware dramatically accelerates protein folding simulations in modern laboratories [S1]."
    report = citations.verify(draft, SOURCES)
    assert report["weakly_supported"] and report["weakly_supported"][0]["cited"] == ["S1"]


def test_grouped_citations_are_normalised_and_renumbered():
    text = citations.normalize_citations("Claim A [S2, S1]. Claim B [S2].")
    assert text == "Claim A [S2][S1]. Claim B [S2]."
    out, refs = citations.renumber(text, SOURCES)
    assert out == "Claim A [1][2]. Claim B [1]."
    assert [r["sid"] for r in refs] == ["S2", "S1"] and refs[0]["n"] == 1


def test_parse_json_handles_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here it is: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}
    assert parse_json("not json") is None


ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2005.11401v4</id>
    <published>2020-05-22T17:56:04Z</published>
    <title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>
    <summary>  Large pre-trained language models have been shown to store factual knowledge.  </summary>
    <author><name>Patrick Lewis</name></author><author><name>Ethan Perez</name></author>
    <arxiv:primary_category term="cs.CL"/>
  </entry>
</feed>"""


def test_arxiv_parser():
    [p] = parse_arxiv_feed(ARXIV_XML)
    assert p["key"] == "arxiv:2005.11401" and p["year"] == 2020
    assert p["authors"] == ["Patrick Lewis", "Ethan Perez"]
    assert p["url"] == "https://arxiv.org/abs/2005.11401" and "cs.CL" in p["venue"]


def test_openalex_parser_rebuilds_abstract():
    data = {"results": [{
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/ABC", "display_name": "A Paper",
        "publication_year": 2023, "cited_by_count": 42,
        "authorships": [{"author": {"display_name": "Jane Doe"}}],
        "abstract_inverted_index": {"Agents": [0], "use": [1], "tools": [2]},
        "primary_location": {"source": {"display_name": "Nature"}},
    }]}
    [p] = parse_openalex(data)
    assert p["key"] == "doi:10.1/abc" and p["abstract"] == "Agents use tools"
    assert p["venue"] == "Nature" and p["citations"] == 42
    assert reconstruct_abstract(None) == ""
    json.dumps(p)  # must be serialisable for MCP transport


async def test_openalex_falls_back_to_crossref_when_rate_limited(monkeypatch):
    import httpx

    from app.tools import scholarly

    async def limited(*a, **k):
        req = httpx.Request("GET", "https://api.openalex.org/works")
        raise httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))

    async def crossref(query, n, y=None):
        return [{"key": "doi:10.1/x", "type": "crossref", "title": "Fallback paper", "authors": [], "year": 2024,
                 "venue": None, "url": "https://doi.org/10.1/x", "doi": "10.1/x", "abstract": "", "citations": 1}]

    monkeypatch.setattr(scholarly, "_search_openalex", limited)
    monkeypatch.setattr(scholarly, "search_crossref", crossref)
    [r] = await scholarly.search_openalex("anything", 3)
    assert r["type"] == "crossref" and r["fallback_from"] == "openalex"


def test_crossref_parser_strips_jats_markup():
    from app.tools.scholarly import parse_crossref
    [p] = parse_crossref([{"DOI": "10.5/ABC", "title": ["A Title"], "author": [{"given": "Ada", "family": "Lovelace"}],
                           "issued": {"date-parts": [[2021, 5]]}, "container-title": ["Journal"],
                           "abstract": "<jats:p>Hello <jats:italic>world</jats:italic></jats:p>", "is-referenced-by-count": 3}])
    assert p["abstract"] == "Hello world" and p["authors"] == ["Ada Lovelace"] and p["year"] == 2021
