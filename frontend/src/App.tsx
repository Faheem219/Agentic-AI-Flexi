import { useCallback, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, Health, Run } from "./api";
import AskPage from "./pages/Ask";
import RunPage from "./pages/RunPage";
import LibraryPage from "./pages/Library";
import EvalsPage from "./pages/Evals";
import AboutPage from "./pages/About";

export function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 30 24" aria-hidden="true">
      <rect x="1" y="1" width="28" height="22" rx="1.5" fill="#fff" stroke="#18243D" strokeWidth="1.5" />
      <line x1="1" y1="6.5" x2="29" y2="6.5" stroke="#B8323A" strokeWidth="1.5" />
      <line x1="5" y1="11.5" x2="25" y2="11.5" stroke="#C5D3EA" strokeWidth="1.3" />
      <line x1="5" y1="16" x2="18" y2="16" stroke="#C5D3EA" strokeWidth="1.3" />
      <circle cx="15" cy="20" r="1.4" fill="#F2F4F7" stroke="#C5D3EA" />
    </svg>
  );
}

export default function App() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const location = useLocation();

  const loadRuns = useCallback(() => {
    api.listRuns(25).then((r) => { setRuns(r); setOffline(false); }).catch(() => setOffline(true));
  }, []);

  useEffect(() => {
    loadRuns();
    api.health().then(setHealth).catch(() => setOffline(true));
  }, [loadRuns, location.pathname]);

  useEffect(() => {
    if (!runs.some((r) => ["running", "queued"].includes(r.status))) return;
    const t = window.setInterval(loadRuns, 4000);
    return () => window.clearInterval(t);
  }, [runs, loadRuns]);

  const userRuns = runs.filter((r) => !r.id.startsWith("eval-"));

  return (
    <div className="shell">
      <aside className="rail">
        <NavLink to="/" className="brand" aria-label="Veritas home">
          <BrandMark />
          <span>
            <span className="brand-name">Veritas</span>
            <span className="brand-sub">Research agents that cite</span>
          </span>
        </NavLink>
        <nav className="nav" aria-label="Main">
          <NavLink to="/" end>New question</NavLink>
          <NavLink to="/library">Your library</NavLink>
          <NavLink to="/evals">Evaluation</NavLink>
          <NavLink to="/about">How it works</NavLink>
        </nav>
        <section className="rail-section" aria-label="Recent research">
          <h2>Recent research</h2>
          {userRuns.length === 0 && <p className="tiny muted" style={{ padding: "0 10px" }}>Your questions will appear here.</p>}
          <ul className="recent">
            {userRuns.map((r) => (
              <li key={r.id}>
                <NavLink to={`/runs/${r.id}`} title={r.question}>
                  <span className={`dot ${r.status}`} aria-label={r.status} />
                  <span className="q">{r.question}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </section>
        <div className="rail-foot">
          {offline ? (
            <span className="fail">Server unreachable</span>
          ) : health ? (
            <>
              <span className="ok">Connected</span>, tools over {health.mcp.mode === "mcp-stdio" ? "MCP (stdio)" : "MCP"}
              <br />Models: {health.models.smart[0]}, {health.models.fast[0]}
            </>
          ) : "Connecting..."}
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<AskPage health={health} onCreated={loadRuns} />} />
          <Route path="/runs/:id" element={<RunPage onChange={loadRuns} />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="*" element={<div className="empty">This page doesn't exist. <NavLink to="/">Ask a new question</NavLink>.</div>} />
        </Routes>
      </main>
    </div>
  );
}
