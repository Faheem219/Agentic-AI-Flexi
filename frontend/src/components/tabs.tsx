import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, Plan, Reference, Run, Source, SubQuestion, TOOL_LABELS, TraceEvent } from "../api";
import CatalogCard from "./CatalogCard";
import { agentName } from "./team";

/* ------------------------------------------------------------------ citations */
function Cite({ n, refs, label }: { n: string; refs: Map<string, Source & { n?: number }>; label: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const hoverTimer = useRef<number | undefined>(undefined);
  const src = refs.get(n);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  return (
    <span className="cite" ref={ref}
          onMouseEnter={() => { hoverTimer.current = window.setTimeout(() => setOpen(true), 250); }}
          onMouseLeave={() => { window.clearTimeout(hoverTimer.current); setOpen(false); }}>
      <button type="button" aria-expanded={open} aria-label={`Source ${label}${src ? `: ${src.title}` : ""}`}
              onClick={() => setOpen((o) => !o)}>{label}</button>
      {open && src && (
        <span className="cite-pop" role="dialog">
          <CatalogCard source={src} call={/^\d+$/.test(label) ? `[${label}]` : label} />
        </span>
      )}
    </span>
  );
}

export function CitedMarkdown({ text, refs, idPattern = "number" }: {
  text: string; refs: Map<string, Source & { n?: number }>; idPattern?: "number" | "sid";
}) {
  const prepared = idPattern === "number"
    ? text.replace(/\[(\d{1,3})\](?!\()/g, "[$1](#cite-$1)")
    : text.replace(/\[(S\d+)\](?!\()/g, "[$1](#cite-$1)");
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => {
          if (href?.startsWith("#cite-")) {
            const key = href.slice(6);
            return <Cite n={key} refs={refs} label={key} />;
          }
          return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
        },
      }}
    >
      {prepared}
    </ReactMarkdown>
  );
}

/* ------------------------------------------------------------------ report */
export function ReportTab({ run, events }: { run: Run; events: TraceEvent[] }) {
  const refs = run.references ?? [];
  const refMap = useMemo(() => new Map(refs.map((r) => [String(r.n), r])), [refs]);
  const drafts = events.filter((e) => e.type === "draft");
  const latestDraft = drafts[drafts.length - 1];
  const draftSources = useMemo(() => {
    const m = new Map<string, Source>();
    events.filter((e) => e.type === "tool_result").forEach((e) =>
      e.data.source_ids?.forEach((sid: string, i: number) =>
        m.set(sid, { sid, key: sid, type: "openalex", title: e.data.titles[i], authors: [], year: null, venue: null, url: null, doi: null, abstract: "", citations: null })));
    return m;
  }, [events]);

  if (run.report) {
    return (
      <div>
        <article className="report"><CitedMarkdown text={run.report} refs={refMap} /></article>
        <section className="references" aria-labelledby="refs-title">
          <h2 id="refs-title">References</h2>
          <p className="tiny muted" style={{ marginBottom: 8 }}>
            Built from the metadata the search tools returned, never written by the model. Every entry is a record retrieved from OpenAlex, arXiv, Wikipedia or your library.
          </p>
          <ol>
            {refs.map((r: Reference) => (
              <li key={r.n} id={`ref-${r.n}`}>
                <span className="rn">[{r.n}]</span>
                <span>
                  {r.authors?.length ? `${r.authors.slice(0, 3).join(", ")}${r.authors.length > 3 ? " et al." : ""} ` : ""}
                  {r.year ? `(${r.year}). ` : ""}<span className="rt">{r.title}</span>{r.venue ? `. ${r.venue}` : ""}.{" "}
                  {r.url && <a href={r.url} target="_blank" rel="noreferrer">{r.doi ? `doi:${r.doi}` : "link"}</a>}
                  <span className="verified" title="Retrieved from a scholarly database during this run">retrieved</span>
                </span>
              </li>
            ))}
          </ol>
        </section>
      </div>
    );
  }
  if (latestDraft) {
    return (
      <div>
        <p className="notice warn" style={{ marginBottom: 20, maxWidth: "68ch" }}>
          Draft {latestDraft.data.revision + 1} is being checked by the verifier and critic. Citations still use internal source IDs.
        </p>
        <article className="report"><CitedMarkdown text={latestDraft.data.text} refs={draftSources} idPattern="sid" /></article>
      </div>
    );
  }
  return <p className="empty">The brief appears here once the writer has a draft. Follow the agents on the left meanwhile.</p>;
}

/* ------------------------------------------------------------------ sources */
export function SourcesTab({ run, events }: { run: Run; events: TraceEvent[] }) {
  const [filter, setFilter] = useState<"all" | "cited" | "uncited">("all");
  const refBySid = useMemo(() => new Map((run.references ?? []).map((r) => [r.sid, r.n])), [run.references]);
  const sources: Source[] = useMemo(() => {
    if (run.sources) return Object.values(run.sources);
    const m = new Map<string, Source>();
    events.filter((e) => e.type === "tool_result").forEach((e) =>
      e.data.source_ids?.forEach((sid: string, i: number) => m.set(sid, {
        sid, key: sid, type: e.data.tool === "search_arxiv" ? "arxiv" : e.data.tool === "search_wikipedia" ? "wikipedia" : e.data.tool === "search_knowledge_base" ? "kb" : "openalex",
        title: e.data.titles[i], authors: [], year: null, venue: null, url: null, doi: null, abstract: "", citations: null,
      })));
    return [...m.values()];
  }, [run.sources, events]);
  const sorted = [...sources].sort((a, b) => (refBySid.get(a.sid) ?? 999) - (refBySid.get(b.sid) ?? 999));
  const shown = sorted.filter((s) => filter === "all" || (filter === "cited") === refBySid.has(s.sid));
  if (!sources.length) return <p className="empty">Sources appear here as the researchers retrieve them.</p>;
  return (
    <div>
      <div className="filter-row">
        <div className="segmented" role="group" aria-label="Filter sources">
          {(["all", "cited", "uncited"] as const).map((f) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "all" ? `All ${sources.length}` : f === "cited" ? `Cited ${refBySid.size}` : `Not cited ${sources.length - refBySid.size}`}
            </button>
          ))}
        </div>
        <span className="small muted">Every retrieved record gets a call number (S1, S2...) so agents can cite it exactly.</span>
      </div>
      <div className="card-grid">
        {shown.map((s) => (
          <CatalogCard key={s.sid} source={s} className={refBySid.has(s.sid) || !run.references ? "" : "uncited"}
                       call={refBySid.has(s.sid) ? `[${refBySid.get(s.sid)}]` : s.sid}
                       sub={refBySid.has(s.sid) ? s.sid : undefined} />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ plan */
function PlanReadOnly({ plan }: { plan: Plan }) {
  return (
    <ol className="plan-list">
      {plan.sub_questions.map((sq) => (
        <li key={sq.id} className="plan-item">
          <span className="qid">{sq.id}</span>
          <h3>{sq.question}</h3>
          <p className="small muted" style={{ marginBottom: 8 }}>{sq.rationale}</p>
          <div className="chips">
            {sq.tools.map((t) => <span key={t} className="chip on">{TOOL_LABELS[t] ?? t}</span>)}
            {sq.search_queries.map((q) => <span key={q} className="chip">"{q}"</span>)}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function PlanTab({ run, plan, onDecision }: { run: Run; plan: Plan | null; onDecision: () => void }) {
  if (!plan) return <p className="empty">The planner hasn't produced a plan yet.</p>;
  if (run.status === "awaiting_approval") return <PlanEditor run={run} plan={plan} onDecision={onDecision} />;
  return (
    <div>
      {plan.objective && <p className="report" style={{ marginBottom: 18 }}><b>Objective.</b> {plan.objective}</p>}
      <PlanReadOnly plan={plan} />
    </div>
  );
}

function PlanEditor({ run, plan, onDecision }: { run: Run; plan: Plan; onDecision: () => void }) {
  const tools = plan.available_tools ?? Object.keys(TOOL_LABELS);
  const [subs, setSubs] = useState<SubQuestion[]>(plan.sub_questions.map((s) => ({ ...s })));
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const update = (i: number, patch: Partial<SubQuestion>) => setSubs((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  async function decide(action: "approve" | "reject", e?: FormEvent) {
    e?.preventDefault();
    if (action === "approve" && !subs.some((s) => s.question.trim())) { setError("Keep at least one sub-question."); return; }
    setBusy(true);
    setError(null);
    try {
      await api.approve(run.id, action === "approve"
        ? { action, feedback, plan: { sub_questions: subs.filter((s) => s.question.trim()) } }
        : { action, feedback });
      onDecision();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <form className="approval" onSubmit={(e) => decide("approve", e)}>
      <h2>Review the research plan</h2>
      <p className="small muted" style={{ margin: "6px 0 14px" }}>
        Nothing has been searched yet. Edit the sub-questions, change which sources each one uses, or add your own. The researchers start when you approve.
      </p>
      <div className="edit-grid">
        {subs.map((sq, i) => (
          <div key={i} className="plan-item">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="qid">q{i + 1}</span>
              <button type="button" className="btn small quiet" onClick={() => setSubs((s) => s.filter((_, j) => j !== i))}>Remove</button>
            </div>
            <label className="visually-hidden" htmlFor={`sq-${i}`}>Sub-question {i + 1}</label>
            <textarea id={`sq-${i}`} className="textarea" value={sq.question} onChange={(e) => update(i, { question: e.target.value })} />
            <div className="row" style={{ marginTop: 8 }}>
              <span className="tiny muted">Search in</span>
              {tools.map((t) => (
                <button type="button" key={t} className={`chip ${sq.tools.includes(t) ? "on" : ""}`} aria-pressed={sq.tools.includes(t)}
                        onClick={() => update(i, { tools: sq.tools.includes(t) ? sq.tools.filter((x) => x !== t) : [...sq.tools, t] })}>
                  {TOOL_LABELS[t] ?? t}
                </button>
              ))}
            </div>
            <label className="tiny muted" style={{ display: "block", marginTop: 8 }}>
              Search queries, separated by commas
              <input className="input" value={sq.search_queries.join(", ")}
                     onChange={(e) => update(i, { search_queries: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
            </label>
          </div>
        ))}
      </div>
      {subs.length < 6 && (
        <button type="button" className="btn quiet small" style={{ marginTop: 10 }}
                onClick={() => setSubs((s) => [...s, { id: `q${s.length + 1}`, question: "", rationale: "Added by the reviewer", tools: [tools[0]], search_queries: [] }])}>
          Add a sub-question
        </button>
      )}
      <label className="field-label" htmlFor="fb" style={{ marginTop: 16 }}>Guidance for the researchers (optional)</label>
      <textarea id="fb" className="textarea" value={feedback} onChange={(e) => setFeedback(e.target.value)}
                placeholder="For example: focus on randomised controlled trials published after 2018" />
      {error && <p className="notice" style={{ marginTop: 10 }}>{error}</p>}
      <div className="row" style={{ marginTop: 14 }}>
        <button className="btn primary" disabled={busy} type="submit">Approve and start research</button>
        <button className="btn quiet" disabled={busy} type="button" onClick={() => decide("reject")}>Reject plan</button>
      </div>
    </form>
  );
}

/* ------------------------------------------------------------------ review */
export function ReviewTab({ events }: { events: TraceEvent[] }) {
  const ver = events.filter((e) => e.type === "verification");
  const crit = events.filter((e) => e.type === "critique");
  if (!crit.length && !ver.length) return <p className="empty">The verifier and critic report here after the first draft.</p>;
  return (
    <div className="review">
      {ver.map((v, i) => {
        const c = crit[i]?.data;
        return (
          <section key={v.id} className={`review-round ${c ? c.verdict : ""}`}>
            <h3>Draft {i + 1}: {c ? (c.will_revise ? "sent back for revision" : c.verdict === "accept" ? "accepted" : "kept (revision limit reached)") : "under review"}</h3>
            <p className="small muted" style={{ marginTop: 4 }}>
              Code check: {Math.round(v.data.citation_coverage * 100)}% of {v.data.sentences} sentences cite a source,
              {" "}{v.data.unique_sources_cited} sources cited, {v.data.invalid_citations.length} invalid citation IDs removed.
            </p>
            {c && Object.keys(c.scores).length > 0 && (
              <div className="bars">
                {Object.entries(c.scores as Record<string, number>).map(([k, s]) => (
                  <ReviewBar key={k} label={k} value={s} />
                ))}
              </div>
            )}
            {c?.verdict_reasons?.length > 0 && <p className="small"><b>Why revise:</b> {c.verdict_reasons.join("; ")}</p>}
            {c?.issues?.length > 0 && (
              <ul className="issues">
                {c.issues.map((it: any, j: number) => (
                  <li key={j}><span className={`sev ${it.severity}`}>{it.severity}</span> {it.issue} <span className="muted">Fix: {it.fix}</span></li>
                ))}
              </ul>
            )}
            {v.data.weakly_supported?.length > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary className="small">Weakly supported sentences ({v.data.weakly_supported.length})</summary>
                <ul className="issues">{v.data.weakly_supported.map((w: any, j: number) => <li key={j}>{w.sentence} <span className="muted">({w.cited.join(", ")})</span></li>)}</ul>
              </details>
            )}
            {c?.strengths?.length > 0 && <p className="small" style={{ marginTop: 8 }}><b>Strengths:</b> {c.strengths.join(" ")}</p>}
          </section>
        );
      })}
    </div>
  );
}

function ReviewBar({ label, value }: { label: string; value: number }) {
  return (
    <>
      <span style={{ textTransform: "capitalize" }}>{label}</span>
      <span className="bar"><i className={value < 7 ? "low" : ""} style={{ width: `${value * 10}%` }} /></span>
      <b>{value}</b>
    </>
  );
}

/* ------------------------------------------------------------------ ask the report (CAG) */
export function AskTab({ run }: { run: Run }) {
  const [msgs, setMsgs] = useState<{ role: "user" | "assistant"; content: string; meta?: string }[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refMap = useMemo(() => new Map((run.references ?? []).map((r) => [String(r.n), r])), [run.references]);
  if (run.status !== "completed") return <p className="empty">You can ask follow-up questions once the brief is published.</p>;

  async function send(e: FormEvent) {
    e.preventDefault();
    const q = text.trim();
    if (!q) return;
    const history = msgs.map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: q }]);
    setText("");
    setBusy(true);
    setError(null);
    try {
      const res = await api.chat(run.id, q, history);
      setMsgs((m) => [...m, { role: "assistant", content: res.answer, meta: `${res.model}, ${res.latency_s} s` }]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      <p className="small muted">
        Answers come only from this brief and the abstracts of its references, which are loaded into the model's context in full (cache-augmented generation).
      </p>
      {msgs.map((m, i) => (
        <div key={i} className={`msg ${m.role}`}>
          {m.role === "assistant" ? <CitedMarkdown text={m.content} refs={refMap} /> : m.content}
          {m.meta && <span className="meta">{m.meta}</span>}
        </div>
      ))}
      {busy && <div className="msg assistant"><span className="spinner" /> Reading the brief</div>}
      {error && <p className="notice">{error}</p>}
      <form className="row" onSubmit={send}>
        <label htmlFor="chat" className="visually-hidden">Follow-up question</label>
        <input id="chat" className="input" style={{ flex: 1 }} value={text} onChange={(e) => setText(e.target.value)}
               placeholder="For example: which finding has the strongest evidence?" />
        <button className="btn primary" disabled={busy || !text.trim()} type="submit">Ask</button>
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------ trace */
export function TraceTab({ run, events }: { run: Run; events: TraceEvent[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const t0 = events[0]?.ts ?? 0;
  const m = run.metrics;
  return (
    <div>
      <div className="row" style={{ marginBottom: 14, justifyContent: "space-between" }}>
        <span className="small muted">{events.length} trace events. Every model call, tool call and agent step is recorded.</span>
        <a className="btn quiet small" href={api.traceUrl(run.id)} target="_blank" rel="noreferrer">Download trace (JSON)</a>
      </div>
      {m?.per_agent && (
        <div className="table-wrap" style={{ marginBottom: 22 }}>
          <table className="data">
            <thead><tr><th>Agent</th><th className="num">Model calls</th><th className="num">Tokens</th><th className="num">Tool calls</th><th className="num">Model time (s)</th></tr></thead>
            <tbody>
              {Object.entries(m.per_agent).map(([a, v]) => (
                <tr key={a}><td>{agentName(a)}</td><td className="num">{v.llm_calls}</td><td className="num">{v.tokens.toLocaleString()}</td><td className="num">{v.tool_calls}</td><td className="num">{v.latency_s}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>Time</th><th>Agent</th><th>Event</th><th>Data</th></tr></thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td className="num">{(e.ts - t0).toFixed(2)}</td>
                <td>{agentName(e.agent)}</td>
                <td>{e.type}</td>
                <td>
                  <button type="button" className="btn small quiet" onClick={() => setOpen(open === e.id ? null : e.id)}>
                    {open === e.id ? "Hide" : "Show"}
                  </button>
                  {open === e.id && <pre className="json">{JSON.stringify(e.data, null, 2)}</pre>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function TabBar({ tabs, current, onChange }: { tabs: { id: string; label: ReactNode }[]; current: string; onChange: (id: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" type="button" aria-selected={current === t.id} onClick={() => onChange(t.id)}>{t.label}</button>
      ))}
    </div>
  );
}
