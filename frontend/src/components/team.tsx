import { useEffect, useMemo, useRef, useState } from "react";
import { Run, TOOL_LABELS, TraceEvent } from "../api";

type StageState = "pending" | "running" | "done" | "failed" | "waiting" | "skipped" | "blocked";

export function agentName(agent: string | null): string {
  if (!agent) return "System";
  if (agent.startsWith("researcher:")) return `Researcher ${agent.split(":")[1]}`;
  const map: Record<string, string> = {
    guardrail: "Guardrail", memory: "Memory", planner: "Planner", researcher: "Researchers", human_review: "Plan review", human: "You",
    writer: "Writer", verifier: "Verifier", critic: "Critic", finalize: "Publisher", finish: "System", chat: "Assistant",
  };
  return map[agent] ?? agent;
}

const pct = (x?: number | null) => (x == null ? "n/a" : `${Math.round(x * 100)}%`);
const tool = (t: string) => TOOL_LABELS[t] ?? t;

interface Stage { id: string; title: string; desc: string; state: StageState; live?: string; loops?: number }
interface Branch { id: string; question: string; state: StageState; live: string }

export function deriveTeam(events: TraceEvent[], run: Run | null) {
  const st: Record<string, StageState> = {};
  const live: Record<string, string> = {};
  const branches = new Map<string, Branch>();
  const subQs = new Map<string, string>();
  let writerRuns = 0;
  const plan = run?.plan ?? events.find((e) => e.type === "plan_created")?.data.plan;
  plan?.sub_questions?.forEach((sq: any) => subQs.set(sq.id, sq.question));
  const srcCount: Record<string, number> = {};

  for (const e of events) {
    const a = e.agent ?? "";
    const d = e.data;
    if (a.startsWith("researcher:")) {
      const id = a.split(":")[1];
      const b = branches.get(id) ?? { id, question: subQs.get(id) ?? id, state: "pending" as StageState, live: "" };
      if (e.type === "node_started") { b.state = "running"; b.live = "Searching"; }
      if (e.type === "tool_call") b.live = `Searching ${tool(d.tool)}`;
      if (e.type === "tool_result") srcCount[id] = (srcCount[id] ?? 0) + (d.count ?? 0);
      if (e.type === "llm_call") b.live = "Reading the evidence";
      if (e.type === "findings") b.live = `${d.findings?.length ?? 0} findings from ${d.sources_gathered} sources`;
      if (e.type === "node_finished") b.state = "done";
      if (e.type === "node_failed" || (e.type === "findings" && d.status === "failed")) b.state = "failed";
      b.question = subQs.get(id) ?? b.question;
      branches.set(id, b);
      continue;
    }
    if (e.type === "node_started") { st[a] = "running"; if (a === "writer") writerRuns += 1; }
    if (e.type === "node_finished" && st[a] !== "blocked" && st[a] !== "waiting") st[a] = "done";
    if (e.type === "node_failed") st[a] = "failed";
    switch (e.type) {
      case "guardrail":
        if (!d.allowed) { st.guardrail = "blocked"; live.guardrail = "Blocked the question"; }
        else live.guardrail = Object.keys(d.redactions ?? {}).length ? `Passed, personal data redacted` : "Passed all checks";
        break;
      case "memory_recalled":
        live.memory = d.items?.length ? `Recalled ${d.items.length} related past ${d.items.length === 1 ? "run" : "runs"}` : "No related past research";
        break;
      case "plan_created": live.planner = `${d.plan.sub_questions.length} sub-questions`; break;
      case "scope_rejected": st.planner = "blocked"; live.planner = "Declined: out of scope"; break;
      case "awaiting_approval": st.human_review = "waiting"; live.human_review = "Waiting for your approval"; break;
      case "plan_approved":
        st.human_review = d.auto ? "skipped" : "done";
        live.human_review = d.auto ? "Skipped, plan auto-approved" : d.edited ? "Approved with your edits" : "Approved by you";
        break;
      case "plan_rejected": st.human_review = "blocked"; live.human_review = "You rejected the plan"; break;
      case "draft": live.writer = `Draft ${d.revision + 1}, ${d.words} words`; break;
      case "verification": live.verifier = `${pct(d.citation_coverage)} of sentences cited, ${d.invalid_citations.length} invalid`; break;
      case "critique": live.critic = `Score ${d.overall ?? "n/a"}/10, ${d.will_revise ? "sent back for revision" : "accepted"}`; break;
      case "final": live.finalize = `${d.references.length} verified references`; break;
    }
  }
  const nBranches = Math.max(branches.size, plan?.sub_questions?.length ?? 0);
  plan?.sub_questions?.forEach((sq: any) => {
    if (!branches.has(sq.id)) branches.set(sq.id, { id: sq.id, question: sq.question, state: "pending", live: "" });
  });
  const bl = [...branches.values()].sort((x, y) => x.id.localeCompare(y.id));
  const researchState: StageState = bl.length === 0 ? "pending"
    : bl.every((b) => b.state === "done" || b.state === "failed") ? (bl.every((b) => b.state === "failed") ? "failed" : "done")
    : bl.some((b) => b.state !== "pending") ? "running" : "pending";
  const totalSrc = Object.values(srcCount).reduce((a, b) => a + b, 0);
  const doneCount = bl.filter((b) => b.state === "done").length;

  const stages: Stage[] = [
    { id: "guardrail", title: "Guardrail", desc: "Screens for prompt injection and personal data" },
    { id: "memory", title: "Memory", desc: "Recalls related research from earlier runs" },
    { id: "planner", title: "Planner", desc: "Breaks the question down and routes each part to sources" },
    { id: "human_review", title: "Plan review", desc: "You can edit or approve the plan" },
    { id: "research", title: "Researchers", desc: "Search in parallel and record grounded findings" },
    { id: "writer", title: "Writer", desc: "Synthesises findings into a cited brief" },
    { id: "verifier", title: "Verifier", desc: "Checks every citation in code" },
    { id: "critic", title: "Critic", desc: "Scores the draft and sends it back if needed" },
    { id: "finalize", title: "Publisher", desc: "Numbers references from verified metadata" },
  ].map((s) => ({
    ...s,
    state: s.id === "research" ? researchState : st[s.id] ?? "pending",
    live: s.id === "research" && nBranches ? `${doneCount} of ${nBranches} done, ${totalSrc} sources retrieved` : live[s.id],
    loops: s.id === "writer" && writerRuns > 1 ? writerRuns - 1 : undefined,
  }));
  if (run && ["failed", "cancelled", "interrupted"].includes(run.status)) {
    stages.forEach((s) => { if (s.state === "running") s.state = "failed"; });
  }
  return { stages, branches: bl };
}

export function AgentTeam({ events, run }: { events: TraceEvent[]; run: Run | null }) {
  const { stages, branches } = useMemo(() => deriveTeam(events, run), [events, run]);
  const icon = (s: StageState, i: number) =>
    s === "done" ? "✓" : s === "failed" || s === "blocked" ? "×" : s === "skipped" ? "–" : String(i + 1);
  return (
    <section className="panel" aria-label="Agent team">
      <div className="panel-head"><h2>Agent team</h2><span className="tiny muted">LangGraph workflow</span></div>
      <ol className="team">
        {stages.map((s, i) => (
          <li key={s.id} className={`stage ${s.state}`}>
            <span className="marker" aria-hidden="true">{icon(s.state, i)}</span>
            <div>
              <div className="stage-title">
                {s.title}
                {s.loops ? <span className="loop">revised {s.loops}x</span> : null}
                <span className="visually-hidden">({s.state})</span>
              </div>
              <div className="stage-desc">{s.desc}</div>
              {s.live && <div className="stage-live">{s.live}</div>}
              {s.id === "research" && branches.length > 0 && (
                <ul className="branches">
                  {branches.map((b) => (
                    <li key={b.id} className={`branch ${b.state}`}>
                      <span className="tick" aria-hidden="true" />
                      <span><b>{b.id}</b> {b.question.length > 90 ? `${b.question.slice(0, 90)}...` : b.question}
                        {b.live && <span className="muted"> ({b.live})</span>}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function describe(e: TraceEvent): { text: string; detail?: string; error?: boolean } | null {
  const d = e.data;
  switch (e.type) {
    case "run_created": return { text: "Run created" };
    case "run_started": return { text: "Started" };
    case "guardrail":
      return d.allowed
        ? { text: `Input passed guardrails${Object.keys(d.redactions ?? {}).length ? `; redacted ${Object.entries(d.redactions).map(([k, v]) => `${v} ${k}`).join(", ")}` : ""}` }
        : { text: `Blocked: ${d.reasons.join(" ")}`, error: true };
    case "memory_recalled":
      return { text: d.items?.length ? `Recalled ${d.items.length} related run(s)` : "No related past research",
               detail: d.items?.map((m: any) => `${m.question} (similarity ${m.similarity})`).join("\n") };
    case "plan_created": return { text: `Plan ready with ${d.plan.sub_questions.length} sub-questions`, detail: d.plan.sub_questions.map((s: any) => `${s.id}: ${s.question}`).join("\n") };
    case "scope_rejected": return { text: `Declined: ${d.reason}`, error: true };
    case "awaiting_approval": return { text: "Paused for your approval" };
    case "human_decision": return { text: d.action === "approve" ? `You approved the plan${d.edited ? " with edits" : ""}` : "You rejected the plan" };
    case "plan_approved": return d.auto ? { text: "Plan auto-approved" } : null;
    case "tool_call": return { text: `Searched ${tool(d.tool)} for "${d.args?.query ?? ""}"` };
    case "tool_result":
      return d.error ? { text: `${tool(d.tool)} failed: ${d.error}`, error: true }
        : { text: `${d.count} results from ${tool(d.tool)} in ${d.latency_s ?? "?"} s${d.fallback ? ` (${d.fallback})` : ""}`, detail: d.titles?.map((t: string, i: number) => `${d.source_ids[i]} ${t}`).join("\n") };
    case "llm_call":
      return { text: `${d.model}: ${(d.tokens_in + d.tokens_out + (d.tokens_thinking ?? 0)).toLocaleString()} tokens, ${d.latency_s} s${d.cached ? ", from cache" : ""}${d.fallback_from ? `, fell back from ${d.fallback_from}` : ""}${d.function_calls?.length ? `, requested ${d.function_calls.join(", ")}` : ""}` };
    case "llm_fallback": return { text: `${d.failed_model} unavailable (${d.reason.split(": ").pop()}); switching model`, error: true };
    case "llm_error": return { text: "All models failed", detail: d.failures?.join("\n"), error: true };
    case "llm_safety_block": return { text: `Safety filter blocked the request (${d.reason})`, error: true };
    case "thought": return { text: "Reasoning summary", detail: d.text };
    case "findings": return { text: `${d.findings?.length ?? 0} findings, ${d.sources_gathered} sources`, detail: [d.summary, ...(d.gaps ?? []).map((g: string) => `Gap: ${g}`)].join("\n") };
    case "draft": return { text: `Draft ${d.revision + 1} written (${d.words} words)` };
    case "verification": return { text: `Citation check: ${pct(d.citation_coverage)} of ${d.sentences} sentences cited, ${d.invalid_citations.length} invalid IDs removed, ${d.weakly_supported.length} weakly supported` };
    case "critique": return { text: `Score ${d.overall ?? "n/a"}/10, ${d.verdict}${d.verdict_reasons?.length ? ` (${d.verdict_reasons.join(", ")})` : ""}`, detail: d.issues?.map((i: any) => `[${i.severity}] ${i.issue}`).join("\n") };
    case "revision_requested": return { text: `Draft sent back for revision ${d.revision}` };
    case "final": return { text: `Brief published with ${d.references.length} references` };
    case "stopped": return { text: `Stopped: ${d.reason ?? d.status}`, error: d.status !== "completed" };
    case "run_failed": return { text: `Run failed: ${d.error}`, error: true };
    case "run_cancelled": return { text: "Run cancelled", error: true };
    case "run_finished": return { text: `Finished in ${d.metrics?.duration_s ?? "?"} s` };
    default: return null;
  }
}

export function ActivityLog({ events, live }: { events: TraceEvent[]; live: boolean }) {
  const ref = useRef<HTMLOListElement>(null);
  const t0 = events[0]?.ts ?? 0;
  const rows = events.map((e) => ({ e, d: describe(e) })).filter((r) => r.d);
  useEffect(() => {
    if (live && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [rows.length, live]);
  return (
    <section className="panel" aria-label="Activity">
      <div className="panel-head"><h2>Activity</h2><span className="tiny muted">{rows.length} steps</span></div>
      <ol className="log" ref={ref} aria-live="polite">
        {rows.map(({ e, d }) => (
          <li key={e.id}>
            <time>{(e.ts - t0).toFixed(1)}s</time>
            <div>
              <span className="who">{agentName(e.agent)}</span>{" "}
              <span className={d!.error ? "err" : undefined}>{d!.text}</span>
              {d!.detail && (
                <details><summary>Details</summary><p>{d!.detail}</p></details>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function useElapsed(events: TraceEvent[], running: boolean) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, [running]);
  if (!events.length) return 0;
  const end = running ? now : events[events.length - 1].ts;
  return Math.max(0, end - events[0].ts);
}

export function MetricsStrip({ events, run, running }: { events: TraceEvent[]; run: Run | null; running: boolean }) {
  const elapsed = useElapsed(events, running);
  const m = run?.metrics;
  const live = useMemo(() => {
    let llm = 0, tokens = 0, tools = 0, cov: number | undefined, score: number | undefined;
    const srcs = new Set<string>();
    for (const e of events) {
      if (e.type === "llm_call") { llm++; tokens += e.data.tokens_in + e.data.tokens_out + (e.data.tokens_thinking ?? 0); }
      if (e.type === "tool_call") tools++;
      if (e.type === "tool_result") e.data.source_ids?.forEach((s: string) => srcs.add(s));
      if (e.type === "verification") cov = e.data.citation_coverage;
      if (e.type === "critique" && e.data.overall != null) score = e.data.overall;
    }
    return { llm, tokens, tools, sources: srcs.size, cov, score };
  }, [events]);
  const done = run?.status === "completed" && m;
  const items: [string, string][] = [
    [`${Math.round(done ? m!.duration_s ?? elapsed : elapsed)} s`, "elapsed"],
    [String(done ? m!.llm_calls : live.llm), "model calls"],
    [(done ? m!.tokens_total ?? live.tokens : live.tokens).toLocaleString(), "tokens"],
    [String(done ? m!.tool_calls : live.tools), "tool calls"],
    [done ? `${m!.sources_cited} of ${m!.sources_found}` : String(live.sources), done ? "sources cited" : "sources found"],
    [pct(done ? m!.citation_coverage : live.cov), "sentences cited"],
    [(done ? m!.critic_score : live.score)?.toString() ?? "n/a", "critic score"],
  ];
  return (
    <div className="metrics" role="list" aria-label="Run metrics">
      {items.map(([v, l]) => (
        <div className="metric" role="listitem" key={l}><b>{v}</b><span>{l}</span></div>
      ))}
    </div>
  );
}
