import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { accessCode, api, Health, TOOL_LABELS } from "../api";

const EXAMPLES = [
  "What techniques are most effective at reducing hallucinations in retrieval-augmented generation?",
  "How does spaced repetition affect long-term retention in learning?",
  "What is the impact of remote work on employee productivity and well-being?",
  "What are the environmental costs of training large language models?",
];

const DEPTHS = [
  { id: "quick", label: "Quick", hint: "2 sub-questions, about 1 minute" },
  { id: "standard", label: "Standard", hint: "3 sub-questions, about 2 minutes" },
  { id: "deep", label: "Deep", hint: "4 sub-questions with extra searching, 3 to 4 minutes" },
];

const STEPS = [
  ["Screen", "Guardrails check the question for prompt injection and strip personal data."],
  ["Plan", "A planner splits it into sub-questions and picks the right databases for each."],
  ["Research", "Researcher agents search in parallel through MCP tools and record grounded findings."],
  ["Write", "A writer synthesises the findings, citing a source after every claim."],
  ["Verify", "Code checks every citation, then a critic scores the draft and sends it back if needed."],
];

export default function AskPage({ health, onCreated }: { health: Health | null; onCreated: () => void }) {
  const navigate = useNavigate();
  const [question, setQuestion] = useState("");
  const [depth, setDepth] = useState("standard");
  const [sources, setSources] = useState<string[]>(["search_openalex", "search_arxiv", "search_wikipedia", "search_knowledge_base"]);
  const [hitl, setHitl] = useState(false);
  const [code, setCode] = useState(accessCode.get());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const kbEmpty = (health?.knowledge_base_documents ?? 0) === 0;

  const toggle = (t: string) => setSources((s) => (s.includes(t) ? s.filter((x) => x !== t) : [...s, t]));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (question.trim().length < 12) {
      setError("Write the question as a full sentence (at least 12 characters).");
      return;
    }
    if (!sources.some((s) => s !== "search_knowledge_base" || !kbEmpty)) {
      setError("Choose at least one source to search.");
      return;
    }
    setBusy(true);
    setError(null);
    accessCode.set(code);
    try {
      const { run_id } = await api.createRun({ question: question.trim(), depth, sources, hitl, max_revisions: 1 });
      onCreated();
      navigate(`/runs/${run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="ask">
      <h1 className="ask-title">Ask the literature a question.</h1>
      <p className="ask-lede">
        A team of AI agents searches OpenAlex, arXiv, Wikipedia and your own documents, then writes a brief in which every
        claim points to a real source you can open.
      </p>

      <form className="question-box" onSubmit={submit}>
        <label htmlFor="q" className="visually-hidden">Research question</label>
        <textarea
          id="q"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="For example: does spaced repetition improve long-term retention for medical students?"
          maxLength={1200}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e as unknown as FormEvent); }}
        />
        <div className="options">
          <div className="group" role="group" aria-label="Depth">
            <span className="group-label">Depth</span>
            <div className="segmented">
              {DEPTHS.map((d) => (
                <button type="button" key={d.id} aria-pressed={depth === d.id} title={d.hint} onClick={() => setDepth(d.id)}>
                  {d.label}
                </button>
              ))}
            </div>
          </div>
          <div className="group" role="group" aria-label="Sources">
            <span className="group-label">Search</span>
            {Object.entries(TOOL_LABELS).map(([id, label]) => {
              const disabled = id === "search_knowledge_base" && kbEmpty;
              return (
                <label className="check" key={id} title={disabled ? "Upload documents in Your library first" : undefined}
                       style={disabled ? { opacity: 0.45 } : undefined}>
                  <input type="checkbox" checked={sources.includes(id) && !disabled} disabled={disabled} onChange={() => toggle(id)} />
                  {label}
                </label>
              );
            })}
          </div>
          <label className="check" title="The run pauses after planning so you can edit or approve the plan">
            <input type="checkbox" checked={hitl} onChange={(e) => setHitl(e.target.checked)} />
            Let me review the plan first
          </label>
          {health?.access_code_required && (
            <div className="group">
              <label className="group-label" htmlFor="code">Access code</label>
              <input id="code" className="input" style={{ width: 140 }} value={code} onChange={(e) => setCode(e.target.value)} />
            </div>
          )}
          <div className="submit">
            <button className="btn primary" disabled={busy} type="submit">
              {busy ? <><span className="spinner" /> Starting</> : "Start research"}
            </button>
          </div>
        </div>
      </form>
      {error && <p className="notice" style={{ marginTop: 14 }} role="alert">{error}</p>}

      <div className="examples" aria-label="Example questions">
        {EXAMPLES.map((q) => (
          <button key={q} type="button" onClick={() => setQuestion(q)}>{q}</button>
        ))}
      </div>

      <section className="how" aria-labelledby="how-title">
        <h2 id="how-title">What happens after you ask</h2>
        <ol className="steps">
          {STEPS.map(([title, text], i) => (
            <li key={title}>
              <span className="n">{i + 1}</span>
              <strong>{title}</strong>
              <span>{text}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
