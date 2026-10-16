"""Deterministic (code-based) citation verification for drafts.

Complements the LLM critic: code checks what can be checked exactly (does every cited ID
exist? which sentences carry no citation? does the cited source plausibly support the
sentence?), the model judges what needs judgement (coverage, balance, clarity).
"""

from __future__ import annotations

import re

from .rag.store import tokenize

CITE_GROUP = re.compile(r"\[(S\d+(?:\s*[,;]\s*S\d+)*)\]")
CITE = re.compile(r"\[S(\d+)\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])")


def normalize_citations(text: str) -> str:
    """Rewrite grouped citations like [S1, S3] into [S1][S3]."""
    def repl(m: re.Match) -> str:
        ids = re.findall(r"S\d+", m.group(1))
        return "".join(f"[{i}]" for i in ids)
    return CITE_GROUP.sub(repl, text)


def _body_sentences(markdown: str) -> list[str]:
    """Substantive sentences of the report (headings, tables and short fragments excluded)."""
    sentences: list[str] = []
    for line in markdown.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("|") or s.startswith("---"):
            continue
        s = re.sub(r"^([-*+]|\d+\.)\s+", "", s)
        for sent in _SENTENCE_SPLIT.split(s):
            if len(re.findall(r"[A-Za-z]{2,}", sent)) >= 8:
                sentences.append(sent.strip())
    return sentences


def verify(draft: str, sources: dict[str, dict], findings: list[dict] | None = None) -> dict:
    draft = normalize_citations(draft)
    cited = [f"S{n}" for n in CITE.findall(draft)]
    unique_cited = list(dict.fromkeys(cited))
    invalid = [c for c in unique_cited if c not in sources]

    # remove citations to sources that do not exist (hallucinated IDs)
    cleaned = draft
    for bad in invalid:
        cleaned = cleaned.replace(f"[{bad}]", "")

    # evidence text per source = source title/abstract + researcher claims that cited it
    evidence: dict[str, set[str]] = {}
    for sid, src in sources.items():
        evidence[sid] = set(tokenize(f"{src.get('title', '')} {src.get('abstract', '')}"))
    for f in findings or []:
        for item in f.get("findings", []):
            for sid in item.get("source_ids", []):
                if sid in evidence:
                    evidence[sid] |= set(tokenize(item.get("claim", "")))

    sentences = _body_sentences(cleaned)
    cited_sentences, uncited, weak = 0, [], []
    for sent in sentences:
        ids = [f"S{n}" for n in CITE.findall(sent)]
        if not ids:
            uncited.append(sent)
            continue
        cited_sentences += 1
        words = set(tokenize(CITE.sub("", sent)))
        support = set().union(*(evidence.get(i, set()) for i in ids)) if ids else set()
        overlap = len(words & support) / max(len(words), 1)
        if overlap < 0.15:
            weak.append({"sentence": sent[:240], "cited": ids, "overlap": round(overlap, 2)})

    total = len(sentences)
    coverage = round(cited_sentences / total, 3) if total else 0.0
    valid_cited = [c for c in unique_cited if c in sources]
    return {
        "cleaned_draft": cleaned,
        "sentences": total,
        "cited_sentences": cited_sentences,
        "citation_coverage": coverage,
        "citations_total": len(cited),
        "unique_sources_cited": len(valid_cited),
        "invalid_citations": invalid,
        "weakly_supported": weak[:8],
        "uncited_sentences": [s[:240] for s in uncited[:8]],
        "unused_sources": [s for s in sources if s not in valid_cited][:20],
    }


def renumber(report: str, sources: dict[str, dict]) -> tuple[str, list[dict]]:
    """Map internal source IDs (S7, S2, ...) to reader-friendly [1], [2] in order of first use
    and build the reference list from verified source metadata only."""
    order: list[str] = []
    for n in CITE.findall(report):
        sid = f"S{n}"
        if sid in sources and sid not in order:
            order.append(sid)
    mapping = {sid: i for i, sid in enumerate(order, start=1)}
    out = CITE.sub(lambda m: f"[{mapping[f'S{m.group(1)}']}]" if f"S{m.group(1)}" in mapping else "", report)
    refs = [{"n": mapping[sid], "sid": sid, **sources[sid]} for sid in order]
    return out, refs
