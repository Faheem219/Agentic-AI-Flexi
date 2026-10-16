import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, Plan, TERMINAL, TOOL_LABELS } from "../api";
import { useRun } from "../useRun";
import { ActivityLog, AgentTeam, MetricsStrip } from "../components/team";
import { AskTab, PlanTab, ReportTab, ReviewTab, SourcesTab, TabBar, TraceTab } from "../components/tabs";

const STATUS_TEXT: Record<string, string> = {
  queued: "Queued", running: "Researching", awaiting_approval: "Waiting for your approval", completed: "Brief published",
  rejected: "Declined", cancelled: "Cancelled", failed: "Failed", interrupted: "Interrupted",
};

export default function RunPage({ onChange }: { onChange: () => void }) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { run, events, error, refreshRun, follow } = useRun(id);
  const [tab, setTab] = useState("report");
  const status = run?.status;
  const running = !!status && !TERMINAL.includes(status) && status !== "awaiting_approval";

  const plan: Plan | null = run?.plan ?? (events.find((e) => e.type === "plan_created")?.data.plan as Plan) ?? null;
  const sourceCount = useMemo(() => {
    if (run?.sources) return Object.keys(run.sources).length;
    const s = new Set<string>();
    events.forEach((e) => e.type === "tool_result" && e.data.source_ids?.forEach((x: string) => s.add(x)));
    return s.size;
  }, [run?.sources, events]);
  const rounds = events.filter((e) => e.type === "critique").length;

  useEffect(() => { if (status === "awaiting_approval") setTab("plan"); }, [status]);
  useEffect(() => { if (status && TERMINAL.includes(status)) onChange(); }, [status, onChange]);

  if (error && !run) return <div className="notice" role="alert">{error}</div>;
  if (!run) return <p className="empty"><span className="spinner" /> Loading the run</p>;

  const tabs = [
    { id: "report", label: "Brief" },
    { id: "sources", label: <>Sources<span className="count">{sourceCount || ""}</span></> },
    { id: "plan", label: status === "awaiting_approval" ? "Plan (needs you)" : "Plan" },
    { id: "review", label: <>Review<span className="count">{rounds || ""}</span></> },
    { id: "ask", label: "Ask the brief" },
    { id: "trace", label: "Trace" },
  ];

  return (
    <div>
      <header className="run-head">
        <h1 className="run-question">{run.question}</h1>
        <div className="run-meta">
          <span className="status-pill"><span className={`dot ${run.status}`} />{STATUS_TEXT[run.status] ?? run.status}</span>
          <span>{new Date(run.created_at * 1000).toLocaleString()}</span>
          <span>{run.settings.depth} depth</span>
          <span>{run.settings.sources.map((s) => TOOL_LABELS[s] ?? s).join(", ")}</span>
          {(running || status === "awaiting_approval") && (
            <button className="btn small quiet" onClick={async () => { await api.cancel(run.id).catch(() => null); refreshRun(); }}>Cancel run</button>
          )}
          {TERMINAL.includes(run.status) && (
            <button className="btn small quiet" onClick={async () => {
              if (!window.confirm("Delete this run and its trace?")) return;
              await api.deleteRun(run.id).catch(() => null);
              onChange();
              navigate("/");
            }}>Delete</button>
          )}
        </div>
        {run.status === "rejected" && <p className="notice" style={{ marginTop: 14, maxWidth: "72ch" }}>{run.error} Rephrase it as a research question and try again.</p>}
        {run.status === "failed" && <p className="notice" style={{ marginTop: 14, maxWidth: "72ch" }}>The run stopped: {run.error}</p>}
        {run.status === "interrupted" && <p className="notice warn" style={{ marginTop: 14 }}>The server restarted while this run was active. Start the question again.</p>}
        {run.status === "awaiting_approval" && (
          <p className="notice" style={{ marginTop: 14, maxWidth: "72ch" }}>
            The planner has proposed a plan. Review it in the Plan tab; nothing is searched until you approve.
          </p>
        )}
        <MetricsStrip events={events} run={run} running={running} />
      </header>

      <div className="run-grid">
        <div className="side">
          <AgentTeam events={events} run={run} />
          <ActivityLog events={events} live={running} />
        </div>
        <section className="panel" aria-label="Results">
          <TabBar tabs={tabs} current={tab} onChange={setTab} />
          <div className="tab-body" role="tabpanel">
            {tab === "report" && <ReportTab run={run} events={events} />}
            {tab === "sources" && <SourcesTab run={run} events={events} />}
            {tab === "plan" && <PlanTab run={run} plan={plan} onDecision={() => { refreshRun(); follow(); setTab("report"); }} />}
            {tab === "review" && <ReviewTab events={events} />}
            {tab === "ask" && <AskTab run={run} />}
            {tab === "trace" && <TraceTab run={run} events={events} />}
          </div>
        </section>
      </div>
    </div>
  );
}
