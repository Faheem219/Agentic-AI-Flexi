"""Evaluation harness: Veritas multi-agent system vs. a single-call LLM baseline.

For every research question in dataset.json:
  1. Veritas  - the full multi-agent pipeline (standard depth, no human review).
  2. Baseline - one call to the same Gemini model, asked for a literature review with references.
  3. Reference verification (code-based) - every reference of both systems is looked up in
     Crossref and OpenAlex and fuzzy-matched on title (+ year) to decide whether it exists.
  4. LLM-as-judge (model-based) - a blind A/B comparison scoring both answers on a rubric.
Plus deterministic guardrail red-team cases and planner scope cases.

Usage:  python -m evals.run_evals [--limit N] [--no-judge] [--only rq1,rq3]
Output: evals/results/latest.json, evals/results/latest.md, evals/results/reports/<id>.md
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from app import db, guardrails
from app.agents import prompts
from app.agents.graph import RUN_CONTEXTS, Deps, RunContext, build_graph
from app.config import get_settings
from app.events import RunTracer, current_tracer
from app.jsonutil import parse_json
from app.llm import GeminiLLM
from app.runs import normalize_settings
from app.toolbox import Toolbox
from app.tools import scholarly

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

JUDGE_SYSTEM = """You are an impartial expert evaluator of research answers. You will see one research question and two \
anonymised answers (A and B), each with its reference list. Score EACH answer from 1 (poor) to 5 (excellent) on:
- relevance: directly and completely answers the question
- depth: specific, substantive, synthesises multiple findings rather than generic statements
- evidence: claims are tied to specific, identifiable sources
- transparency: acknowledges limitations, uncertainty and disagreement
Then choose the better answer overall ("A", "B" or "tie") and justify briefly. Judge only what is written; ignore length."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "A": {"type": "object", "properties": {k: {"type": "integer"} for k in ("relevance", "depth", "evidence", "transparency")},
              "required": ["relevance", "depth", "evidence", "transparency"]},
        "B": {"type": "object", "properties": {k: {"type": "integer"} for k in ("relevance", "depth", "evidence", "transparency")},
              "required": ["relevance", "depth", "evidence", "transparency"]},
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
        "rationale": {"type": "string"},
    },
    "required": ["A", "B", "winner", "rationale"],
}


# ------------------------------------------------------------------ reference verification
def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())).strip()


def title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _arxiv_id(ref: dict) -> str | None:
    for field in (ref.get("key") or "", ref.get("url") or "", ref.get("doi") or ""):
        m = re.search(r"(?:arxiv[:/](?:abs/)?|arxiv\.)(\d{4}\.\d{4,5})", field, flags=re.I)
        if m:
            return m.group(1)
    return None


async def verify_reference(ref: dict) -> dict:
    """Decide whether a reference exists, using its DOI / arXiv ID when given, else a title search.

    status: verified (the paper exists with this title and year +-1), partial (a similar paper exists
    but details are wrong), not_found (likely fabricated). doi_status: correct / mismatch / unresolvable.
    """
    title = re.sub(r"\s*\(Wikipedia\)$", "", ref.get("title") or "")
    year = ref.get("year")
    if not title or len(title) < 8:
        return {"status": "not_found", "similarity": 0.0, "match": None, "doi_status": None}

    def year_ok(rec):
        try:
            return year is None or rec.get("year") is None or abs(int(rec["year"]) - int(year)) <= 1
        except (TypeError, ValueError):
            return True

    doi_status = None
    doi = (ref.get("doi") or "").strip()
    if doi and doi.lower() not in ("null", "none", "n/a"):
        try:
            rec = await scholarly.lookup_doi(doi)
        except Exception:  # noqa: BLE001
            rec = None
        if rec:
            sim = title_similarity(title, rec["title"])
            doi_status = "correct" if sim >= 0.8 else "mismatch"
            if sim >= 0.88 and year_ok(rec):
                return {"status": "verified", "similarity": round(sim, 3), "match": rec, "doi_status": doi_status}
        else:
            doi_status = "unresolvable"
    arxiv_id = _arxiv_id(ref)
    if arxiv_id:
        try:
            rec = await scholarly.lookup_arxiv(arxiv_id)
        except Exception:  # noqa: BLE001
            rec = None
        if rec and title_similarity(title, rec["title"]) >= 0.88:
            return {"status": "verified", "similarity": round(title_similarity(title, rec["title"]), 3),
                    "match": rec, "doi_status": doi_status}

    candidates: list[dict] = []
    try:
        candidates += [{"title": c["title"], "year": c["year"], "db": "crossref", "id": c["doi"]}
                       for c in await scholarly.crossref_lookup(title)]
    except Exception:  # noqa: BLE001
        pass
    try:
        candidates += [{"title": c["title"], "year": c["year"], "db": "openalex", "id": c["doi"] or c["url"]}
                       for c in await scholarly.search_openalex(title, 3)]
    except Exception:  # noqa: BLE001
        pass
    best, best_sim = None, 0.0
    for c in candidates:
        sim = title_similarity(title, c["title"])
        if sim > best_sim:
            best, best_sim = c, sim
    if best_sim >= 0.88 and best and year_ok(best):
        status = "verified"
    elif best_sim >= 0.65:
        status = "partial"
    else:
        status = "not_found"
    return {"status": status, "similarity": round(best_sim, 3), "match": best, "doi_status": doi_status}


async def verify_all(refs: list[dict]) -> dict:
    results = []
    for r in refs:  # sequential: be polite to the public APIs
        if r.get("type") == "wikipedia":
            results.append({"status": "verified", "similarity": 1.0, "match": {"db": "wikipedia", "id": r.get("url")}})
            continue
        if r.get("type") == "kb":
            results.append({"status": "verified", "similarity": 1.0, "match": {"db": "knowledge_base"}})
            continue
        results.append(await verify_reference(r))
    n = len(results) or 1
    counts = {s: sum(1 for x in results if x["status"] == s) for s in ("verified", "partial", "not_found")}
    with_doi = [x for x in results if x.get("doi_status")]
    return {"details": results, **counts, "total": len(results),
            "verified_rate": round(counts["verified"] / n, 3),
            "fabricated_rate": round(counts["not_found"] / n, 3),
            "dois_given": len(with_doi),
            "doi_correct": sum(1 for x in with_doi if x["doi_status"] == "correct"),
            "doi_accuracy": round(sum(1 for x in with_doi if x["doi_status"] == "correct") / len(with_doi), 3) if with_doi else None}


# ------------------------------------------------------------------ systems under test
async def run_veritas(deps: Deps, graph, rq: dict) -> dict:
    run_id = f"eval-{rq['id']}-{int(time.time())}"
    settings = normalize_settings({"depth": "standard", "sources": ["search_openalex", "search_arxiv", "search_wikipedia"],
                                   "hitl": False, "max_revisions": 1})
    db.create_run(run_id, rq["question"], settings, "eval")
    tracer = RunTracer(run_id)
    RUN_CONTEXTS[run_id] = RunContext(deps=deps, tracer=tracer)
    current_tracer.set(tracer)
    t0 = time.time()
    try:
        await graph.ainvoke({"run_id": run_id, "question": rq["question"], "settings": settings, "revision": 0},
                            {"configurable": {"thread_id": run_id}, "recursion_limit": 60})
    except Exception as e:  # noqa: BLE001
        return {"run_id": run_id, "status": "failed", "error": f"{type(e).__name__}: {e}"[:400]}
    finally:
        RUN_CONTEXTS.pop(run_id, None)
        current_tracer.set(None)
    run = db.get_run(run_id)
    return {"run_id": run_id, "status": run["status"], "latency_s": round(time.time() - t0, 1),
            "report": run.get("report"), "references": run.get("references") or [],
            "metrics": run.get("metrics") or {}, "critiques": run.get("critique") or []}


async def run_baseline(llm: GeminiLLM, rq: dict) -> dict:
    t0 = time.time()
    tracer = RunTracer(f"baseline-{rq['id']}")
    current_tracer.set(tracer)
    try:
        res = await llm.generate(agent="baseline", tier="smart", contents=prompts.BASELINE_PROMPT.format(question=rq["question"]),
                                 json_schema={"type": "object", "properties": {
                                     "answer": {"type": "string"},
                                     "references": {"type": "array", "items": {"type": "object", "properties": {
                                         "n": {"type": "integer"}, "title": {"type": "string"}, "authors": {"type": "string"},
                                         "year": {"type": "integer"}, "venue": {"type": "string"}, "doi": {"type": "string"}},
                                         "required": ["n", "title", "year"]}}},
                                     "required": ["answer", "references"]}, temperature=0.4)
    finally:
        current_tracer.set(None)
    data = parse_json(res.text) or {}
    return {"status": "completed", "latency_s": round(time.time() - t0, 1), "answer": data.get("answer", ""),
            "references": data.get("references") or [], "model": res.model,
            "metrics": {"llm_calls": 1, "tokens_total": sum(res.usage.values())}}


async def judge(llm: GeminiLLM, question: str, veritas: dict, baseline: dict) -> dict:
    def fmt(answer: str, refs: list[dict]) -> str:
        lines = [f"[{r.get('n')}] {r.get('title')} ({r.get('year')}) {r.get('venue') or ''}" for r in refs]
        return f"{answer}\n\nReferences:\n" + "\n".join(lines)

    v_text = fmt(veritas.get("report") or "", veritas.get("references") or [])
    b_text = fmt(baseline.get("answer") or "", baseline.get("references") or [])
    veritas_is_a = random.Random(question).random() < 0.5  # blind, position-randomised
    a, b = (v_text, b_text) if veritas_is_a else (b_text, v_text)
    res = await llm.generate(agent="judge", tier="smart", system=JUDGE_SYSTEM, json_schema=JUDGE_SCHEMA, temperature=0.0,
                             contents=f"Question: {question}\n\n=== Answer A ===\n{a}\n\n=== Answer B ===\n{b}")
    data = parse_json(res.text) or {}
    va, ba = ("A", "B") if veritas_is_a else ("B", "A")
    winner = data.get("winner")
    return {"veritas": data.get(va, {}), "baseline": data.get(ba, {}),
            "winner": "tie" if winner == "tie" else ("veritas" if winner == va else "baseline"),
            "rationale": data.get("rationale", ""), "veritas_position": va}


# ------------------------------------------------------------------ guardrail & scope suites
def guardrail_suite(cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        res = guardrails.check_input(c["input"])
        got = "block" if not res.allowed else ("allow+redact" if res.redactions else "allow")
        rows.append({**c, "got": got, "passed": got == c["expected"], "reasons": res.reasons, "redactions": res.redactions})
    return {"cases": rows, "passed": sum(r["passed"] for r in rows), "total": len(rows)}


async def scope_suite(deps: Deps, graph, cases: list[dict]) -> dict:
    rows = []
    for i, c in enumerate(cases):
        run_id = f"eval-scope-{i}-{int(time.time())}"
        settings = normalize_settings({"depth": "quick", "hitl": True})  # pause after planning: no research cost
        db.create_run(run_id, c["input"], settings, "eval")
        RUN_CONTEXTS[run_id] = RunContext(deps=deps, tracer=RunTracer(run_id))
        cfg = {"configurable": {"thread_id": run_id}}
        try:
            await graph.ainvoke({"run_id": run_id, "question": c["input"], "settings": settings, "revision": 0}, cfg)
            snap = await graph.aget_state(cfg)
            got = "accept" if snap.interrupts else "reject"
            reason = snap.values.get("rejection")
        except Exception as e:  # noqa: BLE001
            got, reason = "error", str(e)[:200]
        finally:
            RUN_CONTEXTS.pop(run_id, None)
        db.update_run(run_id, status="completed" if got == "accept" else "rejected", error=reason)
        rows.append({**c, "got": got, "passed": got == c["expected"], "reason": reason})
    return {"cases": rows, "passed": sum(r["passed"] for r in rows), "total": len(rows)}


# ------------------------------------------------------------------ main
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["veritas"].get("status") == "completed"]
    rubric = ("relevance", "depth", "evidence", "transparency")

    def judge_avg(system):
        return {k: _mean([r.get("judge", {}).get(system, {}).get(k) for r in ok]) for k in rubric}

    return {
        "questions": len(rows),
        "veritas_completed": len(ok),
        "veritas": {
            "verified_reference_rate": _mean([r["veritas_verification"]["verified_rate"] for r in ok]),
            "fabricated_reference_rate": _mean([r["veritas_verification"]["fabricated_rate"] for r in ok]),
            "avg_references": _mean([len(r["veritas"]["references"]) for r in ok]),
            "doi_accuracy": _mean([r["veritas_verification"]["doi_accuracy"] for r in ok]),
            "avg_citation_coverage": _mean([r["veritas"]["metrics"].get("citation_coverage") for r in ok]),
            "avg_critic_score": _mean([r["veritas"]["metrics"].get("critic_score") for r in ok]),
            "runs_revised": sum(1 for r in ok if r["veritas"]["metrics"].get("revisions", 0) > 0),
            "avg_latency_s": _mean([r["veritas"]["latency_s"] for r in ok]),
            "avg_llm_calls": _mean([r["veritas"]["metrics"].get("llm_calls") for r in ok]),
            "avg_tool_calls": _mean([r["veritas"]["metrics"].get("tool_calls") for r in ok]),
            "avg_tokens": _mean([r["veritas"]["metrics"].get("tokens_total") for r in ok]),
            "judge": judge_avg("veritas"),
        },
        "baseline": {
            "verified_reference_rate": _mean([r["baseline_verification"]["verified_rate"] for r in ok]),
            "fabricated_reference_rate": _mean([r["baseline_verification"]["fabricated_rate"] for r in ok]),
            "partial_reference_rate": _mean([r["baseline_verification"]["partial"] / max(r["baseline_verification"]["total"], 1) for r in ok]),
            "avg_references": _mean([len(r["baseline"]["references"]) for r in ok]),
            "doi_accuracy": _mean([r["baseline_verification"]["doi_accuracy"] for r in ok]),
            "avg_latency_s": _mean([r["baseline"]["latency_s"] for r in ok]),
            "avg_llm_calls": 1,
            "avg_tokens": _mean([r["baseline"]["metrics"].get("tokens_total") for r in ok]),
            "judge": judge_avg("baseline"),
        },
        "judge_wins": {w: sum(1 for r in ok if r.get("judge", {}).get("winner") == w) for w in ("veritas", "baseline", "tie")},
    }


def to_markdown(out: dict) -> str:
    s = out["summary"]
    v, b = s["veritas"], s["baseline"]
    pct = lambda x: "n/a" if x is None else f"{x * 100:.0f}%"  # noqa: E731
    lines = [
        f"# Veritas evaluation results ({out['generated_at']})", "",
        f"Models: smart={out['config']['model_smart']}, fast={out['config']['model_fast']}. "
        f"Questions: {s['questions']} (Veritas completed {s['veritas_completed']}).", "",
        "## Veritas vs single-call baseline", "",
        "| Metric | Veritas (multi-agent) | Baseline (single LLM call) |", "|---|---|---|",
        f"| References verified to exist (Crossref/OpenAlex) | {pct(v['verified_reference_rate'])} | {pct(b['verified_reference_rate'])} |",
        f"| References not found (likely fabricated) | {pct(v['fabricated_reference_rate'])} | {pct(b['fabricated_reference_rate'])} |",
        f"| Partially matching references (wrong title/year details) | n/a | {pct(b['partial_reference_rate'])} |",
        f"| DOIs that resolve to the cited paper | {pct(v['doi_accuracy'])} | {pct(b['doi_accuracy'])} |",
        f"| Avg. references per answer | {v['avg_references']} | {b['avg_references']} |",
        f"| Avg. citation coverage of sentences | {pct(v['avg_citation_coverage'])} | n/a |",
        f"| Avg. latency (s) | {v['avg_latency_s']} | {b['avg_latency_s']} |",
        f"| Avg. LLM calls | {v['avg_llm_calls']} | 1 |",
        f"| Avg. tokens | {v['avg_tokens']} | {b['avg_tokens']} |",
    ]
    for k in ("relevance", "depth", "evidence", "transparency"):
        lines.append(f"| Judge: {k} (1-5) | {v['judge'][k]} | {b['judge'][k]} |")
    lines += ["", f"Judge preferences: {s['judge_wins']}", "",
              f"Critic: average score {v['avg_critic_score']}/10; {v['runs_revised']} of {s['veritas_completed']} runs were revised by the reflection loop.", "",
              "## Per question", "", "| ID | Domain | Veritas verified | Baseline verified | Baseline not found | Critic | Revised | Latency (s) | Judge winner |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in out["questions"]:
        vv, bv = r.get("veritas_verification", {}), r.get("baseline_verification", {})
        m = r["veritas"].get("metrics", {})
        lines.append(f"| {r['id']} | {r['domain']} | {vv.get('verified', '-')}/{vv.get('total', '-')} | "
                     f"{bv.get('verified', '-')}/{bv.get('total', '-')} | {bv.get('not_found', '-')} | {m.get('critic_score', '-')} | "
                     f"{m.get('revisions', '-')} | {r['veritas'].get('latency_s', '-')} | {r.get('judge', {}).get('winner', '-')} |")
    g = out["guardrails"]
    lines += ["", f"## Guardrail red-team suite: {g['passed']}/{g['total']} passed", "", "| Input | Category | Expected | Got |", "|---|---|---|---|"]
    lines += [f"| {c['input'][:70]} | {c['category']} | {c['expected']} | {c['got']} |" for c in g["cases"]]
    sc = out.get("scope")
    if sc:
        lines += ["", f"## Planner scope checks: {sc['passed']}/{sc['total']} passed", "", "| Input | Expected | Got | Reason |", "|---|---|---|---|"]
        lines += [f"| {c['input'][:70]} | {c['expected']} | {c['got']} | {(c.get('reason') or '')[:90]} |" for c in sc["cases"]]
    return "\n".join(lines) + "\n"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--no-scope", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    db.init_db()
    data = json.loads((HERE / "dataset.json").read_text())
    questions = data["research_questions"]
    if args.only:
        questions = [q for q in questions if q["id"] in args.only.split(",")]
    if args.limit:
        questions = questions[: args.limit]

    toolbox = Toolbox(settings)
    await toolbox.start()
    llm = GeminiLLM(settings)
    deps = Deps(llm=llm, toolbox=toolbox, settings=settings)
    graph = build_graph(deps, checkpointer=InMemorySaver())
    (RESULTS / "reports").mkdir(parents=True, exist_ok=True)

    rows = []
    try:
        for rq in questions:
            print(f"\n=== {rq['id']}: {rq['question']}")
            veritas, baseline = await asyncio.gather(run_veritas(deps, graph, rq), run_baseline(llm, rq))
            print(f"  veritas: {veritas['status']} in {veritas.get('latency_s')}s, refs={len(veritas.get('references') or [])}")
            print(f"  baseline: refs={len(baseline['references'])}")
            row = {**rq, "veritas": veritas, "baseline": baseline}
            if veritas["status"] == "completed":
                row["veritas_verification"] = await verify_all(veritas["references"])
                row["baseline_verification"] = await verify_all(baseline["references"])
                print(f"  verified: veritas {row['veritas_verification']['verified']}/{row['veritas_verification']['total']}"
                      f"  baseline {row['baseline_verification']['verified']}/{row['baseline_verification']['total']}"
                      f" (not found {row['baseline_verification']['not_found']})")
                if not args.no_judge:
                    try:
                        row["judge"] = await judge(llm, rq["question"], veritas, baseline)
                        print(f"  judge winner: {row['judge']['winner']}")
                    except Exception as e:  # noqa: BLE001
                        row["judge"] = {"error": str(e)[:200]}
                (RESULTS / "reports" / f"{rq['id']}_veritas.md").write_text(
                    f"# {rq['question']}\n\n{veritas['report']}\n\n## References\n" + "\n".join(
                        f"[{r['n']}] {', '.join(r.get('authors', [])[:3])} ({r.get('year')}). {r.get('title')}. {r.get('venue') or ''}. {r.get('url') or ''}"
                        for r in veritas["references"]))
                (RESULTS / "reports" / f"{rq['id']}_baseline.md").write_text(
                    f"# {rq['question']} (baseline)\n\n{baseline['answer']}\n\n## References\n" + "\n".join(
                        f"[{r.get('n')}] {r.get('authors', '')} ({r.get('year')}). {r.get('title')}. {r.get('venue', '')}. DOI: {r.get('doi')}"
                        for r in baseline["references"]))
            rows.append(row)
        scope = None if args.no_scope else await scope_suite(deps, graph, data["scope_cases"])
    finally:
        await toolbox.stop()

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "config": {"model_smart": settings.model_smart, "model_fast": settings.model_fast, "depth": "standard",
                   "sources": ["search_openalex", "search_arxiv", "search_wikipedia"], "max_revisions": 1},
        "summary": summarise(rows),
        "questions": [{**r, "veritas": {k: v for k, v in r["veritas"].items() if k != "critiques"}} for r in rows],
        "guardrails": guardrail_suite(data["guardrail_cases"]),
        "scope": scope,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    (RESULTS / "latest.md").write_text(to_markdown(out))
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (RESULTS / f"run-{stamp}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    print("\n" + to_markdown(out))


if __name__ == "__main__":
    asyncio.run(main())
