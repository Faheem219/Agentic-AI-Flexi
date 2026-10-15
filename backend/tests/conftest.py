"""Test fixtures: an isolated data directory, a deterministic fake LLM and a fake MCP toolbox.

No network access and no API quota are used by the unit/integration tests.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile

# Configure the environment BEFORE the app modules are imported.
_TMP = tempfile.mkdtemp(prefix="veritas-test-")
os.environ.update({
    "DATA_DIR": _TMP,
    "GEMINI_API_KEY": "",
    "LLM_CACHE": "false",
    "MCP_MODE": "inprocess",
    "ACCESS_CODE": "",
})

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app import db  # noqa: E402
from app.events import current_tracer  # noqa: E402
from app.llm import LLMResult, LLMUnavailable  # noqa: E402

db.init_db()


def contents_text(contents) -> str:
    if isinstance(contents, str):
        return contents
    out = []
    for c in contents:
        for p in c.parts or []:
            if p.text:
                out.append(p.text)
            if p.function_response:
                out.append(str(p.function_response.response))
    return "\n".join(out)


def hash_embed(text: str, dim: int = 64) -> list[float]:
    """Deterministic bag-of-words embedding: similar vocabulary -> similar vectors."""
    v = np.zeros(dim, dtype=np.float32)
    for w in re.findall(r"[a-z]{3,}", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


class FakeLLM:
    """Scripted stand-in for GeminiLLM. Handlers are keyed by agent role (planner, researcher...)."""

    def __init__(self, handlers: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.handlers = {**default_handlers(), **(handlers or {})}

    async def generate(self, *, agent, contents, system=None, tools=None, json_schema=None, tier="fast", **kw):
        role = agent.split(":")[0]
        text = contents_text(contents)
        self.calls.append({"agent": agent, "role": role, "text": text, "system": system, "tier": tier,
                           "tools": [t.name for t in tools] if tools else [], "json": json_schema is not None})
        out = self.handlers[role](agent=agent, text=text, call_no=sum(1 for c in self.calls if c["role"] == role))
        if isinstance(out, Exception):
            raise out
        result = out if isinstance(out, LLMResult) else LLMResult(text=out, model=f"fake-{tier}",
                                                                 usage={"input": len(text) // 4, "output": len(out) // 4, "thinking": 0})
        tracer = current_tracer.get()
        if tracer:
            tracer.record_llm(agent, result.model, result.usage, 0.01, False)
            tracer.emit("llm_call", agent, model=result.model, latency_s=0.01, tokens_in=result.usage["input"],
                        tokens_out=result.usage["output"], tokens_thinking=0, cached=False, fallback_from=None,
                        function_calls=[])
        return result

    async def embed(self, texts, task_type="RETRIEVAL_DOCUMENT"):
        return [hash_embed(t) for t in texts]

    def model_status(self):
        return {"fake-smart": "ready", "fake-fast": "ready"}


def default_handlers() -> dict:
    import json

    def planner(text, **_):
        n = 3 if "deep" not in text else 4
        return json.dumps({
            "in_scope": True, "scope_reason": "research question",
            "objective": "Understand the topic",
            "sub_questions": [
                {"question": f"Sub-question {i} about retrieval augmented generation evaluation", "rationale": "needed",
                 "tools": ["search_openalex"], "search_queries": [f"rag evaluation {i}", f"rag hallucination {i}"]}
                for i in range(1, n + 1)
            ],
            "report_outline": ["Background", "Evidence", "Gaps"],
        })

    def researcher(text, **_):
        ids = list(dict.fromkeys(re.findall(r"\[(S\d+)\]", text)))[:3]
        return json.dumps({
            "summary": "Retrieval augmented generation evaluation relies on faithfulness metrics.",
            "findings": [{"claim": f"Retrieval augmented generation evaluation study {sid} reports faithfulness gains",
                          "source_ids": [sid], "confidence": "high"} for sid in ids],
            "gaps": ["Few long-term studies"],
        })

    def writer(text, call_no, **_):
        ids = list(dict.fromkeys(re.findall(r"^(S\d+) \|", text, flags=re.M)))[:4] or ["S1"]
        cites = "".join(f"[{i}]" for i in ids[:2])
        if "This is revision" not in text:
            # first draft: one uncited claim sentence and one hallucinated citation ID
            return ("## Executive Summary\nRetrieval augmented generation evaluation measures faithfulness gains "
                    f"across many study settings and benchmarks {cites}. Some claims here have no support at all "
                    "in any retrieved document whatsoever today.\n\n## Key Findings\n### Evidence\nRetrieval augmented "
                    "generation evaluation study results report faithfulness gains in several settings [S99].\n")
        return ("## Executive Summary\nRetrieval augmented generation evaluation measures faithfulness gains "
                f"across many study settings and benchmarks {cites}.\n\n## Key Findings\n### Evidence\nRetrieval "
                f"augmented generation evaluation study results report faithfulness gains in several settings [{ids[-1]}].\n"
                "\n## References\n[1] should be stripped\n")

    def critic(call_no, **_):
        if call_no == 1:
            return json.dumps({"scores": {"coverage": 8, "groundedness": 5, "clarity": 8, "balance": 7},
                               "verdict": "revise",
                               "issues": [{"severity": "high", "issue": "Uncited claim", "fix": "Cite or remove"}],
                               "strengths": ["Clear"]})
        return json.dumps({"scores": {"coverage": 9, "groundedness": 9, "clarity": 9, "balance": 8},
                           "verdict": "accept", "issues": [], "strengths": ["Well grounded"]})

    def chat(**_):
        return "The report says faithfulness improves [1]."

    return {"planner": planner, "researcher": researcher, "writer": writer, "critic": critic, "chat": chat}


class FakeToolbox:
    """In-memory replacement for the MCP toolbox; returns deterministic sources per query."""

    mode = "fake"

    def __init__(self, fail_tools: set[str] | None = None) -> None:
        self.fail_tools = fail_tools or set()
        self.calls: list[tuple[str, dict]] = []
        self.tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}}
                      for n in ("search_openalex", "search_arxiv", "search_wikipedia", "search_knowledge_base")]

    @property
    def names(self):
        return [t["name"] for t in self.tools]

    def declarations(self, names=None):
        return []

    async def call(self, name, args):
        self.calls.append((name, args))
        if name in self.fail_tools:
            return {"results": [], "error": f"{name} is down", "latency_s": 0.0}
        q = args.get("query", "")
        h = hashlib.md5(f"{name}{q}".encode()).hexdigest()[:6]
        return {"results": [
            {"key": f"doi:10.1/{h}{i}", "type": "openalex", "title": f"Retrieval augmented generation evaluation paper {h}{i}",
             "authors": ["A. Author", "B. Author"], "year": 2024, "venue": "Journal of Tests", "url": f"https://doi.org/10.1/{h}{i}",
             "doi": f"10.1/{h}{i}", "abstract": "Retrieval augmented generation evaluation study reports faithfulness gains in several settings.",
             "citations": 10 * i}
            for i in range(1, 3)
        ], "latency_s": 0.0}

    async def start(self):
        pass

    async def stop(self):
        pass


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def fake_toolbox():
    return FakeToolbox()


@pytest.fixture(autouse=True)
def clean_db():
    with db.connect() as conn:
        for t in ("runs", "events", "documents", "chunks", "memories", "llm_cache", "tool_cache", "kv"):
            conn.execute(f"DELETE FROM {t}")
    yield


__all__ = ["FakeLLM", "FakeToolbox", "LLMUnavailable", "hash_embed", "contents_text"]
