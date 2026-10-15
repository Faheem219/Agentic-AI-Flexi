import { useEffect, useState } from "react";
import { api } from "../api";

const AGENTS = [
  ["Guardrail", "Code", "Rejects prompt injection and jailbreak patterns and redacts e-mails, phone numbers and IDs before any model sees the question.", "Deterministic rules are cheap, instant and auditable; no model call is spent on a malicious input."],
  ["Memory", "Embeddings", "Finds earlier runs on similar questions and hands their summaries to the planner.", "Lets the system build on its own past work instead of starting cold every time."],
  ["Planner", "Gemini (smart tier)", "Checks scope, splits the question into 2 to 4 sub-questions, writes search queries and routes each one to the best databases.", "Planning is the step where quality matters most, so it uses the stronger model with a JSON schema."],
  ["Plan review", "You", "Optional pause where you edit, add, remove or reject sub-questions before anything is searched.", "Human oversight at the cheapest point: before any tool or model budget is spent."],
  ["Researchers", "Gemini (fast tier) + MCP tools", "One agent per sub-question, all running in parallel. Each first runs the planned searches, then may call tools again (ReAct) before reporting findings tied to source IDs.", "Parallelism cuts wall-clock time; plan-and-execute first, ReAct second keeps model calls low on a free tier."],
  ["Writer", "Gemini (smart tier)", "Synthesises all findings into a structured brief, citing a source ID after every claim.", "A single writer gives one consistent voice and structure across sub-questions."],
  ["Verifier", "Code", "Removes citations to sources that were never retrieved, measures how many sentences are cited and flags sentences their sources do not appear to support.", "What can be checked exactly is checked by code, not by another model."],
  ["Critic", "Gemini (smart tier)", "Scores the draft on coverage, groundedness, clarity and balance, and lists fixes. A score below 7, a serious issue or citation coverage below 75% sends the draft back to the writer.", "Reflection loop: the writer gets specific feedback, bounded by a revision limit."],
  ["Publisher", "Code", "Renumbers citations and builds the reference list from retrieved metadata, then stores the run in memory.", "References can never be invented, because the model never writes them."],
];

const PATTERNS = [
  ["Tool use", "Researchers call scholarly search tools through native Gemini function calling."],
  ["Model Context Protocol", "Tools live in a separate MCP server (stdio) and are discovered at start-up; any MCP host can reuse them."],
  ["Routing", "The planner chooses which databases each sub-question should use."],
  ["Parallelisation", "Researchers fan out with LangGraph's Send API and run concurrently."],
  ["Plan-and-execute, then ReAct", "Planned searches run first without a model call; the agent then decides whether more searching is needed."],
  ["Reflection", "Writer and critic loop with code-based and model-based checks."],
  ["Human in the loop", "LangGraph interrupt and resume around the plan."],
  ["RAG", "Your library: page-aware chunking, title-prefixed (contextual) chunks, hybrid BM25 plus embedding search fused by reciprocal rank."],
  ["CAG", "Follow-up questions load the whole brief and its abstracts into context instead of retrieving."],
  ["Memory", "Episodic memory of past runs, recalled by embedding similarity."],
  ["Guardrails", "Input rules, a model scope check, untrusted-data delimiters around tool output, and output verification."],
  ["Reliability", "Model fallback chain, per-model cool-down after rate limits, retries with back-off, response and tool caching, graceful degradation when one researcher fails."],
  ["Observability", "Every step, model call and tool call is a trace event, streamed live and exportable."],
  ["Evaluation", "Benchmark against a single-call baseline with reference verification and a blind model judge."],
];

function Architecture() {
  const box = (x: number, y: number, w: number, h: number, title: string, sub: string, accent = false) => (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={4} fill="#fff" stroke={accent ? "#B8323A" : "#18243D"} strokeWidth={accent ? 1.6 : 1.2} />
      <text x={x + 12} y={y + 22} fontFamily="Hanken Grotesk, sans-serif" fontWeight={700} fontSize={13} fill="#18243D">{title}</text>
      {sub.split("\n").map((line, i) => (
        <text key={i} x={x + 12} y={y + 40 + i * 15} fontFamily="Hanken Grotesk, sans-serif" fontSize={11} fill="#5B6577">{line}</text>
      ))}
    </g>
  );
  const arrow = (x1: number, y1: number, x2: number, y2: number, label?: string) => (
    <g>
      <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#5B6577" strokeWidth={1.2} markerEnd="url(#ah)" />
      {label && <text x={(x1 + x2) / 2 + 4} y={(y1 + y2) / 2 - 4} fontFamily="Hanken Grotesk, sans-serif" fontSize={10.5} fill="#5B6577">{label}</text>}
    </g>
  );
  return (
    <svg viewBox="0 0 1060 400" role="img" aria-label="System architecture">
      <defs>
        <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="#5B6577" />
        </marker>
      </defs>
      <rect x={250} y={18} width={560} height={364} rx={6} fill="#F2F4F7" stroke="#C5D3EA" strokeDasharray="4 3" />
      <text x={262} y={38} fontFamily="Hanken Grotesk, sans-serif" fontSize={11} fill="#5B6577">Backend on a VM (Docker): FastAPI + LangGraph</text>
      {box(20, 150, 190, 92, "Web app (Vercel)", "React, live SSE trace,\nplan editor, catalogue cards")}
      {arrow(210, 196, 268, 196, "HTTPS /api")}
      {box(270, 120, 170, 150, "FastAPI", "REST + Server-Sent Events\nrun manager, rate limit,\naccess code, CORS")}
      {box(470, 56, 320, 168, "LangGraph agent workflow", "guardrail, memory, planner,\nplan review (interrupt),\nresearchers in parallel (Send),\nwriter, verifier, critic loop,\npublisher", true)}
      {arrow(440, 160, 468, 140)}
      {box(470, 250, 150, 110, "Gemini gateway", "fallback chain, cool-down,\nretries, cache, token\naccounting")}
      {box(640, 250, 150, 110, "SQLite (WAL)", "runs, trace events,\nlibrary chunks + vectors,\nmemory, caches")}
      {arrow(560, 224, 548, 248)}
      {arrow(700, 224, 712, 248)}
      {box(850, 56, 190, 168, "MCP tool server", "stdio subprocess\nsearch_openalex\nsearch_arxiv\nsearch_wikipedia\nsearch_knowledge_base", true)}
      {arrow(790, 140, 848, 140, "MCP")}
      {box(850, 260, 190, 100, "External services", "Gemini API (free tier)\nOpenAlex, arXiv,\nWikipedia, Crossref")}
      {arrow(945, 224, 945, 258)}
      {arrow(620, 305, 848, 305)}
    </svg>
  );
}

export default function AboutPage() {
  const [tools, setTools] = useState<{ name: string; description: string; input_schema: any }[]>([]);
  useEffect(() => { api.tools().then(setTools).catch(() => setTools([])); }, []);
  return (
    <div>
      <header className="page-head">
        <h1>How Veritas works</h1>
        <p>
          General-purpose chatbots can invent references that look real. Veritas splits research into roles. Agents may only cite
          records they actually retrieved, code checks every citation, and the reference list is assembled from database metadata rather than written by a model.
        </p>
      </header>
      <section className="arch" aria-label="Architecture diagram"><Architecture /></section>

      <section className="section">
        <h2>The agent team</h2>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Role</th><th>Runs on</th><th>What it does</th><th>Why it is a separate role</th></tr></thead>
            <tbody>{AGENTS.map(([r, on, what, why]) => <tr key={r}><td><b>{r}</b></td><td className="small">{on}</td><td className="small">{what}</td><td className="small muted">{why}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <h2>Agentic patterns in this system</h2>
        <div className="table-wrap">
          <table className="data">
            <tbody>{PATTERNS.map(([p, how]) => <tr key={p}><td style={{ width: 220 }}><b>{p}</b></td><td className="small">{how}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <h2>Tools discovered over MCP</h2>
        {tools.length === 0 ? <p className="empty">Connect to the backend to list the tools.</p> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Tool</th><th>Description</th><th>Parameters</th></tr></thead>
              <tbody>
                {tools.map((t) => (
                  <tr key={t.name}>
                    <td><b>{t.name}</b></td>
                    <td className="small">{t.description}</td>
                    <td className="small">{Object.keys(t.input_schema?.properties ?? {}).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
