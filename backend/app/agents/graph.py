"""The Veritas multi-agent workflow, built as a LangGraph state machine.

    guardrail -> memory -> planner -> human_review --(fan-out, parallel)--> researcher x N
        -> writer -> verifier -> critic --(revise)--> writer ...
                                        --(accept)--> finalize

Patterns used: input guardrails, episodic memory, plan-and-execute, routing (planner picks
tools per sub-question), parallelisation (map/fan-out of researchers), ReAct tool use over
MCP, reflection (writer <-> critic loop), human-in-the-loop (interrupt / resume) and
code-based + model-based evaluation of the output.
"""

from __future__ import annotations

import asyncio
import functools
import json
import operator
import re
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from google.genai import types
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .. import citations, db, guardrails
from .. import memory as memory_store
from ..config import Settings
from ..events import RunTracer, current_tracer
from ..jsonutil import parse_json
from ..llm import GeminiLLM, LLMSafetyBlock, LLMUnavailable, user_text
from ..rag import store
from ..toolbox import Toolbox
from . import prompts

DEPTH = {
    "quick": {"sub_questions": 2, "refine_steps": 0, "results": 4, "words": 450},
    "standard": {"sub_questions": 3, "refine_steps": 1, "results": 5, "words": 700},
    "deep": {"sub_questions": 4, "refine_steps": 2, "results": 6, "words": 1000},
}
ALL_TOOLS = ["search_openalex", "search_arxiv", "search_wikipedia", "search_knowledge_base"]
MIN_CITATION_COVERAGE = 0.75  # below this share of cited sentences the critic loop forces a revision


# ------------------------------------------------------------------ state
def merge_dicts(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class ResearchState(TypedDict, total=False):
    run_id: str
    question: str
    settings: dict
    clean_question: str
    guardrail: dict
    memories: list[dict]
    plan: dict
    plan_feedback: str
    findings: Annotated[list[dict], operator.add]
    sources: Annotated[dict, merge_dicts]
    draft: str
    verification: dict
    critique: dict
    critiques: Annotated[list[dict], operator.add]
    revision: int
    report: str
    references: list[dict]
    status: str
    rejection: str


@dataclass
class Deps:
    llm: GeminiLLM
    toolbox: Toolbox
    settings: Settings


class SourceRegistry:
    """Run-wide registry that gives every retrieved source a stable citation ID (S1, S2...),
    de-duplicated by DOI / arXiv ID / URL across parallel researchers."""

    def __init__(self) -> None:
        self.by_id: dict[str, dict] = {}
        self._by_key: dict[str, str] = {}

    @staticmethod
    def _title_key(src: dict) -> str | None:
        if src.get("type") == "kb":
            return None  # chunks of one document share a title but are distinct passages
        t = re.sub(r"[^a-z0-9]+", "", (src.get("title") or "").lower())
        return f"title:{t[:90]}" if len(t) > 15 else None

    def add(self, src: dict) -> str:
        key = src.get("key") or src.get("url") or src.get("title", "")
        tkey = self._title_key(src)  # the same paper often appears on both arXiv and OpenAlex
        for k in (key, tkey):
            if k and k in self._by_key:
                return self._by_key[k]
        sid = f"S{len(self.by_id) + 1}"
        self.by_id[sid] = {**src, "sid": sid}
        self._by_key[key] = sid
        if tkey:
            self._by_key[tkey] = sid
        return sid


@dataclass
class RunContext:
    deps: Deps
    tracer: RunTracer
    registry: SourceRegistry = field(default_factory=SourceRegistry)


RUN_CONTEXTS: dict[str, RunContext] = {}


def _ctx(state: dict) -> RunContext:
    ctx = RUN_CONTEXTS[state["run_id"]]
    current_tracer.set(ctx.tracer)
    return ctx


def traced(name: str):
    """Wrap a node so it emits node_started / node_finished / node_failed trace events."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(state: dict) -> dict:
            ctx = _ctx(state)
            label = f"researcher:{state['sub_question']['id']}" if name == "researcher" else name
            ctx.tracer.emit("node_started", label)
            t0 = time.time()
            try:
                out = await fn(state, ctx)
            except GraphBubbleUp:  # human-in-the-loop interrupt: not an error
                raise
            except Exception as e:
                ctx.tracer.emit("node_failed", label, error=f"{type(e).__name__}: {e}"[:500])
                raise
            ctx.tracer.emit("node_finished", label, duration_s=round(time.time() - t0, 2))
            return out
        return wrapper
    return deco


# ------------------------------------------------------------------ helpers
def available_tools(deps: Deps, requested: list[str] | None) -> list[str]:
    tools = [t for t in (requested or ALL_TOOLS) if t in deps.toolbox.names]
    if "search_knowledge_base" in tools and store.document_count() == 0:
        tools.remove("search_knowledge_base")
    return tools or [t for t in ["search_openalex"] if t in deps.toolbox.names]


def normalize_plan(raw: dict, max_sub: int, allowed: list[str]) -> dict:
    subs = []
    for i, sq in enumerate((raw.get("sub_questions") or [])[:max_sub], start=1):
        q = str(sq.get("question", "")).strip()[:400]
        if not q:
            continue
        tools = [t for t in (sq.get("tools") or []) if t in allowed][:2]
        if not tools:
            tools = ["search_openalex"] if "search_openalex" in allowed else allowed[:1]
        queries = [str(x).strip()[:120] for x in (sq.get("search_queries") or []) if str(x).strip()][:3]
        subs.append({
            "id": f"q{len(subs) + 1}",
            "question": q,
            "rationale": str(sq.get("rationale", "")).strip()[:400],
            "tools": tools,
            "search_queries": queries or [q[:120]],
        })
    return {
        "objective": str(raw.get("objective", "")).strip()[:500],
        "sub_questions": subs,
        "report_outline": [str(x)[:120] for x in (raw.get("report_outline") or [])][:6],
    }


def _tool_args(tool: str, query: str, n: int) -> dict:
    if tool == "search_knowledge_base":
        return {"query": query, "top_k": n}
    if tool == "search_wikipedia":
        return {"query": query, "max_results": 2}
    return {"query": query, "max_results": n}


def _format_sources(tool: str, args: dict, sids: list[str], registry: SourceRegistry, error: str | None) -> str:
    head = f"### {tool}({json.dumps(args.get('query', ''))})"
    if error:
        return f"{head}\nERROR: {error}"
    if not sids:
        return f"{head}\n(no results)"
    lines = [head]
    for sid in sids:
        s = registry.by_id[sid]
        authors = ", ".join(s.get("authors", [])[:3]) + (" et al." if len(s.get("authors", [])) > 3 else "")
        meta = " | ".join(str(x) for x in [authors, s.get("year"), s.get("venue"),
                                             f"cited by {s['citations']}" if s.get("citations") is not None else None] if x)
        lines.append(f"[{sid}] {s.get('title')}\n    {meta}\n    {(s.get('abstract') or '')[:650]}")
    return "\n".join(lines)


async def execute_tool(ctx: RunContext, agent: str, tool: str, args: dict) -> tuple[str, list[str]]:
    """Call one MCP tool, register returned sources, emit trace events, return LLM-ready text."""
    ctx.tracer.emit("tool_call", agent, tool=tool, args=args)
    payload = await ctx.deps.toolbox.call(tool, args)
    error = payload.get("error")
    sids = [ctx.registry.add(src) for src in payload.get("results", [])]
    ctx.tracer.record_tool(agent, error=bool(error))
    fallback = "served by Crossref fallback" if any(r.get("fallback_from") for r in payload.get("results", [])) else None
    ctx.tracer.emit(
        "tool_result", agent, tool=tool, query=args.get("query"), count=len(sids), source_ids=sids, fallback=fallback,
        titles=[ctx.registry.by_id[s]["title"][:140] for s in sids], error=error,
        latency_s=payload.get("latency_s"),
    )
    return _format_sources(tool, args, sids, ctx.registry, error), sids


def _clean_findings(parsed: dict | None, valid_ids: set[str]) -> dict:
    parsed = parsed if isinstance(parsed, dict) else {}
    items = []
    for f in (parsed.get("findings") or [])[:8]:
        claim = str(f.get("claim", "")).strip()
        ids = [s for s in (f.get("source_ids") or []) if s in valid_ids]
        if claim and ids:
            conf = f.get("confidence") if f.get("confidence") in ("high", "medium", "low") else "medium"
            items.append({"claim": claim[:600], "source_ids": ids[:4], "confidence": conf})
    return {
        "summary": str(parsed.get("summary", "")).strip()[:1200],
        "findings": items,
        "gaps": [str(g)[:300] for g in (parsed.get("gaps") or [])][:5],
    }


# ------------------------------------------------------------------ graph
def build_graph(deps: Deps, checkpointer=None):
    llm = deps.llm

    @traced("guardrail")
    async def guardrail_node(state: dict, ctx: RunContext) -> dict:
        res = guardrails.check_input(state["question"])
        ctx.tracer.emit("guardrail", "guardrail", allowed=res.allowed, reasons=res.reasons,
                        redactions=res.redactions, checks=res.checks)
        info = {"allowed": res.allowed, "reasons": res.reasons, "redactions": res.redactions}
        if not res.allowed:
            return {"status": "rejected", "rejection": " ".join(res.reasons), "guardrail": info}
        return {"clean_question": res.sanitized, "guardrail": info}

    @traced("memory")
    async def memory_node(state: dict, ctx: RunContext) -> dict:
        try:
            mems = await memory_store.recall(state["clean_question"], llm.embed, exclude_run=state["run_id"])
        except Exception as e:  # noqa: BLE001 - memory is optional
            ctx.tracer.emit("memory_recalled", "memory", items=[], note=f"memory unavailable: {e}"[:200])
            return {"memories": []}
        ctx.tracer.emit("memory_recalled", "memory", items=mems)
        return {"memories": mems}

    @traced("planner")
    async def planner_node(state: dict, ctx: RunContext) -> dict:
        s = state["settings"]
        depth = DEPTH[s["depth"]]
        tools = available_tools(deps, s.get("sources"))
        kb_note = ""
        if "search_knowledge_base" in tools:
            titles = [d["title"] for d in store.list_documents()[:10]]
            kb_note = f"\nUser's knowledge base documents: {titles}"
        system = prompts.PLANNER_SYSTEM.format(
            n=depth["sub_questions"], tools=", ".join(tools),
            tool_guide="\n".join(f"  * {t}: {prompts.TOOL_GUIDE[t]}" for t in tools),
        )
        mem_text = ""
        if state.get("memories"):
            mem_text = "\n\nPrior research memories (from earlier runs of this system):\n" + "\n".join(
                f"- Q: {m['question']}\n  Summary: {m['summary'][:500]} (similarity {m['similarity']})"
                for m in state["memories"])
        try:
            res = await llm.generate(
                agent="planner", tier="smart", system=system,
                contents=f"Research question: {state['clean_question']}{kb_note}{mem_text}",
                json_schema=prompts.plan_schema(tools), temperature=0.2, include_thoughts=True,
            )
        except LLMSafetyBlock as e:
            ctx.tracer.emit("scope_rejected", "planner", reason=str(e))
            return {"status": "rejected", "rejection": f"The request was declined by the safety filter. {e}"}
        raw = parse_json(res.text) or {}
        if raw.get("in_scope") is False:
            reason = raw.get("scope_reason") or "The question is outside the scope of a research assistant."
            ctx.tracer.emit("scope_rejected", "planner", reason=reason)
            return {"status": "rejected", "rejection": reason}
        plan = normalize_plan(raw, depth["sub_questions"], tools)
        if not plan["sub_questions"]:
            raise ValueError("Planner returned no usable sub-questions")
        plan["available_tools"] = tools
        ctx.tracer.emit("plan_created", "planner", plan=plan)
        db.update_run(state["run_id"], plan=plan)
        return {"plan": plan}

    @traced("human_review")
    async def human_review_node(state: dict, ctx: RunContext) -> dict:
        if not state["settings"].get("hitl"):
            ctx.tracer.emit("plan_approved", "human_review", auto=True, edited=False)
            return {}
        decision = interrupt({"plan": state["plan"]})  # pauses the graph until the user responds
        decision = decision if isinstance(decision, dict) else {}
        feedback = str(decision.get("feedback", "")).strip()[:1000]
        if decision.get("action") == "reject":
            ctx.tracer.emit("plan_rejected", "human_review", feedback=feedback)
            return {"status": "cancelled", "rejection": "The research plan was rejected by the user."
                    + (f" Feedback: {feedback}" if feedback else "")}
        plan = state["plan"]
        edited = False
        if decision.get("plan"):
            tools = plan.get("available_tools") or available_tools(deps, state["settings"].get("sources"))
            new_plan = normalize_plan({**plan, **decision["plan"]}, 6, tools)
            if new_plan["sub_questions"]:
                new_plan["available_tools"] = tools
                edited = new_plan["sub_questions"] != plan["sub_questions"]
                plan = new_plan
        ctx.tracer.emit("plan_approved", "human_review", auto=False, edited=edited, plan=plan, feedback=feedback)
        db.update_run(state["run_id"], plan=plan)
        return {"plan": plan, "plan_feedback": feedback}

    @traced("researcher")
    async def researcher_node(state: dict, ctx: RunContext) -> dict:
        sq = state["sub_question"]
        agent = f"researcher:{sq['id']}"
        depth = DEPTH[state["settings"]["depth"]]
        tools = [t for t in sq["tools"] if t in deps.toolbox.names]
        gathered: list[str] = []
        try:
            # Phase 1 - plan-and-execute: run the planner's searches in parallel, no LLM call needed.
            planned = [(t, q) for q in sq["search_queries"][:2] for t in tools][:4]
            blocks = await asyncio.gather(*(
                execute_tool(ctx, agent, t, _tool_args(t, q, depth["results"])) for t, q in planned))
            for _, sids in blocks:
                gathered += sids
            evidence = "\n\n".join(text for text, _ in blocks)
            contents: list[types.Content] = [user_text(
                f"Overall research question: {state['question']}\n"
                f"Your sub-question ({sq['id']}): {sq['question']}\nWhy it matters: {sq['rationale']}\n"
                + (f"User guidance: {state['plan_feedback']}\n" if state.get("plan_feedback") else "")
                + f"\n{prompts.UNTRUSTED_DATA_NOTICE}\n<tool_results>\n{evidence}\n</tool_results>\n\n"
                "If this evidence is sufficient, reply with the findings JSON now; otherwise call tools to refine."
            )]
            # Phase 2 - ReAct refinement: the agent may call tools again before committing to findings.
            refine = depth["refine_steps"]
            system = prompts.RESEARCHER_SYSTEM.format(
                tools=", ".join(tools), max_calls=refine * 2, untrusted=prompts.UNTRUSTED_DATA_NOTICE)
            parsed = None
            for step in range(refine + 1):
                final = step == refine
                res = await llm.generate(
                    agent=agent, tier="fast", system=system, contents=contents,
                    tools=None if final else deps.toolbox.declarations(tools),
                    json_schema=prompts.FINDINGS_SCHEMA if final else None,
                    temperature=0.2, include_thoughts=True,
                )
                if res.function_calls and not final:
                    contents.append(res.content)
                    calls = res.function_calls[:2]
                    outs = await asyncio.gather(*(
                        execute_tool(ctx, agent, fc.name, {k: v for k, v in dict(fc.args or {}).items()})
                        for fc in calls))
                    for _, sids in outs:
                        gathered += sids
                    contents.append(types.Content(role="user", parts=[
                        types.Part(function_response=types.FunctionResponse(
                            id=fc.id, name=fc.name, response={"result": f"<tool_results>\n{text}\n</tool_results>"}))
                        for fc, (text, _) in zip(calls, outs)
                    ]))
                    continue
                parsed = parse_json(res.text)
                if isinstance(parsed, dict) and parsed.get("findings"):
                    break
                if not final and res.content is not None:
                    contents.append(res.content)
                    contents.append(user_text("Reply now with ONLY the findings JSON."))
            result = _clean_findings(parsed, set(ctx.registry.by_id))
            status = "ok" if result["findings"] else "no_findings"
        except LLMUnavailable as e:  # graceful degradation: the rest of the team carries on
            result = {"summary": "", "findings": [], "gaps": [f"Research failed: {e}"[:300]]}
            status = "failed"
        gathered = list(dict.fromkeys(gathered))
        entry = {"sub_question_id": sq["id"], "question": sq["question"], "status": status,
                 "source_ids": gathered, **result}
        ctx.tracer.emit("findings", agent, sub_question_id=sq["id"], status=status, summary=result["summary"],
                        findings=result["findings"], gaps=result["gaps"], sources_gathered=len(gathered))
        return {"findings": [entry], "sources": {sid: ctx.registry.by_id[sid] for sid in gathered}}

    @traced("writer")
    async def writer_node(state: dict, ctx: RunContext) -> dict:
        findings = state.get("findings") or []
        if not any(f["findings"] for f in findings):
            raise ValueError("No grounded findings were produced by the researchers; cannot write a report.")
        sources = state.get("sources") or {}
        cited = list(dict.fromkeys(s for f in findings for item in f["findings"] for s in item["source_ids"]))
        others = [s for s in sources if s not in cited]
        catalogue = []
        for sid in (cited + others)[:45]:
            s = sources[sid]
            authors = ", ".join(s.get("authors", [])[:3])
            catalogue.append(f"{sid} | {s.get('title')} | {authors} | {s.get('year')} | {s.get('venue')} | {s.get('type')}")
        findings_text = "\n\n".join(
            f"## {f['sub_question_id']}: {f['question']}\nSummary: {f['summary'] or '(none)'}\n"
            + "\n".join(f"- {i['claim']} [{']['.join(i['source_ids'])}] (confidence: {i['confidence']})" for i in f["findings"])
            + (f"\nGaps: {'; '.join(f['gaps'])}" if f["gaps"] else "")
            for f in findings
        )
        plan = state["plan"]
        brief = (
            f"Research question: {state['clean_question']}\nObjective: {plan.get('objective')}\n"
            f"Suggested outline themes: {plan.get('report_outline')}\n\n"
            f"# Researcher findings\n{findings_text}\n\n# Source catalogue (ID | title | authors | year | venue | type)\n"
            + "\n".join(catalogue)
        )
        revision = state.get("revision", 0)
        if revision > 0 and state.get("critique"):
            v = state.get("verification") or {}
            issues = "\n".join(f"- [{i['severity']}] {i['issue']} -> FIX: {i['fix']}"
                               for i in state["critique"].get("issues", [])) or "- (none listed)"
            brief += "\n\n" + prompts.REVISION_INSTRUCTIONS.format(
                rev=revision, issues=issues, coverage=v.get("citation_coverage", 0),
                uncited=v.get("uncited_sentences", [])[:5], weak=v.get("weakly_supported", [])[:5],
                draft=state.get("draft", ""),
            )
        words = DEPTH[state["settings"]["depth"]]["words"]
        res = await llm.generate(agent="writer", tier="smart", system=prompts.WRITER_SYSTEM.format(words=words),
                                 contents=brief, temperature=0.4, thinking="medium")
        draft = re.split(r"\n#{1,3}\s*(References|Bibliography|Sources)\b", res.text, flags=re.I)[0].strip()
        draft = citations.normalize_citations(draft)
        ctx.tracer.emit("draft", "writer", revision=revision, text=draft, words=len(draft.split()))
        return {"draft": draft}

    @traced("verifier")
    async def verifier_node(state: dict, ctx: RunContext) -> dict:
        report = citations.verify(state["draft"], state.get("sources") or {}, state.get("findings"))
        cleaned = report.pop("cleaned_draft")
        ctx.tracer.emit("verification", "verifier", revision=state.get("revision", 0), **report)
        return {"draft": cleaned, "verification": report}

    @traced("critic")
    async def critic_node(state: dict, ctx: RunContext) -> dict:
        v = state["verification"]
        plan = state["plan"]
        content = (
            f"Research question: {state['clean_question']}\nSub-questions:\n"
            + "\n".join(f"- {sq['id']}: {sq['question']}" for sq in plan["sub_questions"])
            + f"\n\n# Automatic verification report\n{json.dumps({k: v[k] for k in ('citation_coverage', 'sentences', 'cited_sentences', 'invalid_citations', 'uncited_sentences', 'weakly_supported')}, indent=1)}"
            + "\n\n# Researcher findings (ground truth for groundedness)\n"
            + "\n".join(f"- {i['claim']} [{', '.join(i['source_ids'])}]" for f in state["findings"] for i in f["findings"])
            + f"\n\n# Draft to review\n{state['draft']}"
        )
        revision = state.get("revision", 0)
        try:
            res = await llm.generate(agent="critic", tier="smart", system=prompts.CRITIC_SYSTEM,
                                     contents=content, json_schema=prompts.CRITIQUE_SCHEMA, temperature=0.1)
            crit = parse_json(res.text) or {}
        except LLMUnavailable as e:
            crit = {"scores": {}, "verdict": "accept", "issues": [], "strengths": [],
                    "note": f"Critic unavailable ({e}); draft accepted without model review."[:300]}
        scores = {k: int(crit.get("scores", {}).get(k, 0) or 0) for k in ("coverage", "groundedness", "clarity", "balance")}
        overall = round(sum(scores.values()) / 4, 2) if any(scores.values()) else None
        issues = (crit.get("issues") or [])[:6]
        # The acceptance rule is applied in code (not left to the model) so it is deterministic:
        # revise if any rubric score < 7, any high-severity issue, or thin/invalid citations.
        reasons = []
        if any(0 < s < 7 for s in scores.values()):
            reasons.append("a rubric score is below 7")
        if any(i.get("severity") == "high" for i in issues):
            reasons.append("high-severity issue")
        if v["citation_coverage"] < MIN_CITATION_COVERAGE:
            reasons.append(f"citation coverage {v['citation_coverage']:.0%} < {MIN_CITATION_COVERAGE:.0%}")
        if v["invalid_citations"]:
            reasons.append("invalid citations")
        verdict = "revise" if reasons else "accept"
        max_rev = int(state["settings"].get("max_revisions", 1))
        will_revise = verdict == "revise" and revision < max_rev
        critique = {"revision": revision, "scores": scores, "overall": overall, "verdict": verdict,
                    "model_verdict": crit.get("verdict"), "verdict_reasons": reasons,
                    "issues": issues, "strengths": (crit.get("strengths") or [])[:3],
                    "will_revise": will_revise, "note": crit.get("note")}
        ctx.tracer.emit("critique", "critic", **critique)
        update: dict[str, Any] = {"critique": critique, "critiques": [critique]}
        if will_revise:
            update["revision"] = revision + 1
            ctx.tracer.emit("revision_requested", "critic", revision=revision + 1, reasons=reasons,
                            issues=[i.get("issue", "")[:160] for i in issues[:3]])
        return update

    @traced("finalize")
    async def finalize_node(state: dict, ctx: RunContext) -> dict:
        sources = state.get("sources") or {}
        report, refs = citations.renumber(state["draft"], sources)
        v = state.get("verification") or {}
        crit = state.get("critique") or {}
        metrics = {
            **ctx.tracer.metrics(),
            "sub_questions": len(state["plan"]["sub_questions"]),
            "sources_found": len(sources),
            "sources_cited": len(refs),
            "citation_coverage": v.get("citation_coverage"),
            "invalid_citations_removed": len(v.get("invalid_citations", [])),
            "critic_score": crit.get("overall"),
            "critic_scores": crit.get("scores"),
            "revisions": state.get("revision", 0),
            "words": len(report.split()),
            "researchers_failed": sum(1 for f in state.get("findings", []) if f["status"] == "failed"),
        }
        db.update_run(state["run_id"], status="completed", report=report, references_json=refs,
                      sources_json=sources, critique=state.get("critiques"), metrics=metrics)
        summary = report.split("## Key Findings")[0].replace("## Executive Summary", "").strip()[:700]
        await memory_store.save_memory(state["run_id"], state["clean_question"], summary, llm.embed)
        ctx.tracer.emit("final", "finalize", report=report, references=refs, metrics=metrics)
        return {"report": report, "references": refs, "status": "completed"}

    @traced("finish")
    async def finish_node(state: dict, ctx: RunContext) -> dict:
        status = state.get("status", "rejected")
        db.update_run(state["run_id"], status=status, error=state.get("rejection"), metrics=ctx.tracer.metrics())
        ctx.tracer.emit("stopped", "finish", status=status, reason=state.get("rejection"))
        return {}

    # ---- routing
    def after_guardrail(state: dict) -> str:
        return "finish" if state.get("status") == "rejected" else "memory"

    def after_planner(state: dict) -> str:
        return "finish" if state.get("status") == "rejected" else "human_review"

    def fan_out(state: dict):
        if state.get("status") == "cancelled":
            return "finish"
        return [Send("researcher", {"run_id": state["run_id"], "question": state["clean_question"],
                                    "settings": state["settings"], "plan_feedback": state.get("plan_feedback", ""),
                                    "sub_question": sq})
                for sq in state["plan"]["sub_questions"]]

    def after_critic(state: dict) -> str:
        return "writer" if state.get("critique", {}).get("will_revise") else "finalize"

    g = StateGraph(ResearchState)
    g.add_node("guardrail", guardrail_node)
    g.add_node("memory", memory_node)
    g.add_node("planner", planner_node)
    g.add_node("human_review", human_review_node)
    g.add_node("researcher", researcher_node)
    g.add_node("writer", writer_node)
    g.add_node("verifier", verifier_node)
    g.add_node("critic", critic_node)
    g.add_node("finalize", finalize_node)
    g.add_node("finish", finish_node)
    g.add_edge(START, "guardrail")
    g.add_conditional_edges("guardrail", after_guardrail, ["memory", "finish"])
    g.add_edge("memory", "planner")
    g.add_conditional_edges("planner", after_planner, ["human_review", "finish"])
    g.add_conditional_edges("human_review", fan_out, ["researcher", "finish"])
    g.add_edge("researcher", "writer")
    g.add_edge("writer", "verifier")
    g.add_edge("verifier", "critic")
    g.add_conditional_edges("critic", after_critic, ["writer", "finalize"])
    g.add_edge("finalize", END)
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)
