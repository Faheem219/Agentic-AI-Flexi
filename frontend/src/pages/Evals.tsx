import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

const pct = (x?: number | null) => (x == null ? "n/a" : `${Math.round(x * 100)}%`);
const num = (x?: number | null, d = 1) => (x == null ? "n/a" : Number(x.toFixed(d)).toLocaleString());
const avg = (o?: Record<string, number | null>) => {
  const v = Object.values(o ?? {}).filter((x): x is number => x != null);
  return v.length ? (v.reduce((a, b) => a + b, 0) / v.length).toFixed(2) : "n/a";
};

export default function EvalsPage() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.evals().then(setData).catch((e) => setError(e.message)); }, []);

  return (
    <div>
      <header className="page-head">
        <h1>Evaluation</h1>
        <p>
          Veritas against a single call to the same Gemini model, on research questions from six fields. Every reference from both
          systems is looked up in Crossref and OpenAlex by DOI, arXiv ID or title. A separate model judges both answers blind.
        </p>
      </header>
      {error && <p className="notice warn">{error}</p>}
      {!data && !error && <p className="empty"><span className="spinner" /> Loading results</p>}
      {data && <Results data={data} />}
    </div>
  );
}

function Results({ data }: { data: any }) {
  // Eval runs only exist in the database of the machine that ran the benchmark; link them when present.
  const [present, setPresent] = useState<Set<string>>(new Set());
  useEffect(() => {
    Promise.all(data.questions.map((q: any) => q.veritas.run_id
      ? api.getRun(q.veritas.run_id).then(() => q.veritas.run_id as string).catch(() => null) : Promise.resolve(null)))
      .then((ids) => setPresent(new Set(ids.filter((x): x is string => !!x))));
  }, [data]);
  const s = data.summary;
  const v = s.veritas;
  const b = s.baseline;
  const headline = [
    { label: "References that exist", v: pct(v.verified_reference_rate), b: pct(b.verified_reference_rate) },
    { label: "References not found anywhere", v: pct(v.fabricated_reference_rate), b: pct(b.fabricated_reference_rate) },
    { label: "DOIs pointing to the cited paper", v: pct(v.doi_accuracy), b: pct(b.doi_accuracy) },
    { label: "Blind judge, mean score out of 5", v: avg(v.judge), b: avg(b.judge) },
  ];
  return (
    <>
      <div className="headline-compare">
        {headline.map((h) => (
          <div key={h.label}>
            <div className="label">{h.label}</div>
            <div className="vals">
              <div className="v">{h.v}<small>Veritas</small></div>
              <div className="v" style={{ color: "var(--pencil)" }}>{h.b}<small>Single call</small></div>
            </div>
          </div>
        ))}
      </div>
      <p className="small muted" style={{ marginTop: 10 }}>
        Generated {data.generated_at} with {data.config.model_smart} and {data.config.model_fast}, {data.config.depth} depth.
        Judge preference: Veritas {s.judge_wins.veritas}, single call {s.judge_wins.baseline}, tie {s.judge_wins.tie}.
      </p>

      <section className="section">
        <h2>Cost of the extra rigour</h2>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Measure</th><th className="num">Veritas</th><th className="num">Single call</th></tr></thead>
            <tbody>
              <tr><td>References per answer</td><td className="num">{num(v.avg_references)}</td><td className="num">{num(b.avg_references)}</td></tr>
              <tr><td>Sentences carrying a citation</td><td className="num">{pct(v.avg_citation_coverage)}</td><td className="num">not measured</td></tr>
              <tr><td>Average time (s)</td><td className="num">{num(v.avg_latency_s)}</td><td className="num">{num(b.avg_latency_s)}</td></tr>
              <tr><td>Model calls</td><td className="num">{num(v.avg_llm_calls)}</td><td className="num">1</td></tr>
              <tr><td>Tool calls</td><td className="num">{num(v.avg_tool_calls)}</td><td className="num">0</td></tr>
              <tr><td>Tokens</td><td className="num">{Math.round(v.avg_tokens ?? 0).toLocaleString()}</td><td className="num">{Math.round(b.avg_tokens ?? 0).toLocaleString()}</td></tr>
              <tr><td>Critic score (out of 10)</td><td className="num">{num(v.avg_critic_score)}</td><td className="num">n/a</td></tr>
              <tr><td>Runs revised by the critic</td><td className="num">{v.runs_revised} of {s.veritas_completed}</td><td className="num">n/a</td></tr>
              {(["relevance", "depth", "evidence", "transparency"] as const).map((k) => (
                <tr key={k}><td>Judge: {k}</td><td className="num">{num(v.judge[k], 2)}</td><td className="num">{num(b.judge[k], 2)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <h2>Per question</h2>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Question</th><th className="num">Veritas refs found</th><th className="num">Single-call refs found</th><th className="num">Not found</th><th className="num">Critic</th><th className="num">Time (s)</th><th>Judge</th></tr></thead>
            <tbody>
              {data.questions.map((q: any) => (
                <tr key={q.id}>
                  <td>
                    {present.has(q.veritas.run_id) ? <Link to={`/runs/${q.veritas.run_id}`}>{q.question}</Link> : q.question}
                    <br /><span className="tiny muted">{q.domain}</span>
                  </td>
                  <td className="num">{q.veritas_verification ? `${q.veritas_verification.verified}/${q.veritas_verification.total}` : q.veritas.status}</td>
                  <td className="num">{q.baseline_verification ? `${q.baseline_verification.verified}/${q.baseline_verification.total}` : "-"}</td>
                  <td className="num">{q.baseline_verification?.not_found ?? "-"}</td>
                  <td className="num">{q.veritas.metrics?.critic_score ?? "-"}</td>
                  <td className="num">{q.veritas.latency_s ?? "-"}</td>
                  <td>{q.judge?.winner === "veritas" ? "Veritas" : q.judge?.winner === "baseline" ? "Single call" : q.judge?.winner ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <h2>Guardrails: {data.guardrails.passed} of {data.guardrails.total} cases handled correctly</h2>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Input</th><th>Case</th><th>Expected</th><th>Result</th></tr></thead>
            <tbody>
              {data.guardrails.cases.map((c: any, i: number) => (
                <tr key={i}><td>{c.input}</td><td>{c.category}</td><td>{c.expected}</td><td className={c.passed ? "pass" : "fail"}>{c.got}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {data.scope && (
        <section className="section">
          <h2>Planner scope checks: {data.scope.passed} of {data.scope.total} correct</h2>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Input</th><th>Expected</th><th>Result</th><th>Reason given</th></tr></thead>
              <tbody>
                {data.scope.cases.map((c: any, i: number) => (
                  <tr key={i}><td>{c.input}</td><td>{c.expected}</td><td className={c.passed ? "pass" : "fail"}>{c.got}</td><td className="small">{c.reason ?? ""}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
