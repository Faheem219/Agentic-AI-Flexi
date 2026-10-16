// Typed client for the Veritas FastAPI backend.

// Backend origin, e.g. https://api.example.com. Empty = same origin (local dev uses the Vite proxy).
// A trailing "/" or "/api" is tolerated, because every request path below already starts with /api.
const BASE = ((import.meta.env.VITE_API_BASE as string | undefined) ?? "").trim().replace(/\/+$/, "").replace(/\/api$/, "");

export type RunStatus =
  | "queued" | "running" | "awaiting_approval" | "completed"
  | "rejected" | "cancelled" | "failed" | "interrupted";

export interface Source {
  sid: string;
  key: string;
  type: "openalex" | "arxiv" | "wikipedia" | "kb" | "crossref";
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  url: string | null;
  doi: string | null;
  abstract: string;
  citations: number | null;
}

export interface Reference extends Source { n: number }

export interface SubQuestion {
  id: string;
  question: string;
  rationale: string;
  tools: string[];
  search_queries: string[];
}

export interface Plan {
  objective: string;
  sub_questions: SubQuestion[];
  report_outline: string[];
  available_tools?: string[];
}

export interface Critique {
  revision: number;
  scores: Record<string, number>;
  overall: number | null;
  verdict: "accept" | "revise";
  verdict_reasons?: string[];
  issues: { severity: string; issue: string; fix: string }[];
  strengths: string[];
  will_revise: boolean;
  note?: string | null;
}

export interface RunSettings { depth: string; sources: string[]; hitl: boolean; max_revisions: number }

export interface Metrics {
  duration_s?: number;
  llm_calls?: number;
  cached_llm_calls?: number;
  model_fallbacks?: number;
  tokens_total?: number;
  tool_calls?: number;
  sources_found?: number;
  sources_cited?: number;
  citation_coverage?: number;
  critic_score?: number | null;
  revisions?: number;
  words?: number;
  models_used?: Record<string, number>;
  per_agent?: Record<string, { llm_calls: number; tokens: number; tool_calls: number; latency_s: number }>;
  [k: string]: unknown;
}

export interface Run {
  id: string;
  question: string;
  settings: RunSettings;
  status: RunStatus;
  created_at: number;
  updated_at: number;
  plan?: Plan | null;
  report?: string | null;
  references?: Reference[] | null;
  sources?: Record<string, Source> | null;
  critique?: Critique[] | null;
  metrics?: Metrics | null;
  error?: string | null;
}

export interface TraceEvent {
  id: number;
  run_id: string;
  ts: number;
  type: string;
  agent: string | null;
  data: Record<string, any>;
}

export interface Health {
  status: string;
  version: string;
  models: { smart: string[]; fast: string[]; embedding: string; status: Record<string, string> };
  mcp: { mode: string; tools: string[] };
  knowledge_base_documents: number;
  access_code_required: boolean;
}

export interface KBDocument {
  id: string; title: string; filename: string; pages: number; chunks: number; chars: number;
  embedded: number; created_at: number;
}

export interface KBHit {
  chunk_id: string; doc_id: string; doc_title: string; page: number; text: string;
  rrf_score: number; bm25_rank: number | null; dense_rank: number | null; cosine: number | null;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const ACCESS_KEY = "veritas.accessCode";
export const accessCode = {
  get: () => { try { return localStorage.getItem(ACCESS_KEY) ?? ""; } catch { return ""; } },
  set: (v: string) => { try { localStorage.setItem(ACCESS_KEY, v); } catch { /* storage unavailable */ } },
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const code = accessCode.get();
  if (code) headers["X-Access-Code"] = code;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Can't reach the Veritas server. Check that the backend is running.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  tools: () => request<{ name: string; description: string; input_schema: any }[]>("/api/tools"),
  stats: () => request<Record<string, any>>("/api/stats"),
  evals: () => request<any>("/api/evals/latest"),
  listRuns: (limit = 30) => request<Run[]>(`/api/runs?limit=${limit}`),
  getRun: (id: string) => request<Run>(`/api/runs/${id}`),
  createRun: (body: { question: string } & Partial<RunSettings>) =>
    request<{ run_id: string }>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  events: (id: string, after = 0) => request<TraceEvent[]>(`/api/runs/${id}/events?after=${after}`),
  approve: (id: string, body: { action: "approve" | "reject"; plan?: Partial<Plan>; feedback?: string }) =>
    request(`/api/runs/${id}/approve`, { method: "POST", body: JSON.stringify(body) }),
  cancel: (id: string) => request(`/api/runs/${id}/cancel`, { method: "POST" }),
  deleteRun: (id: string) => request(`/api/runs/${id}`, { method: "DELETE" }),
  chat: (id: string, message: string, history: { role: "user" | "assistant"; content: string }[]) =>
    request<{ answer: string; model: string; latency_s: number }>(`/api/runs/${id}/chat`, {
      method: "POST", body: JSON.stringify({ message, history }),
    }),
  traceUrl: (id: string) => `${BASE}/api/runs/${id}/trace`,
  streamUrl: (id: string, after: number) => `${BASE}/api/runs/${id}/stream?after=${after}`,
  kbList: () => request<KBDocument[]>("/api/kb/documents"),
  kbUpload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<KBDocument>("/api/kb/documents", { method: "POST", body: fd });
  },
  kbDelete: (id: string) => request(`/api/kb/documents/${id}`, { method: "DELETE" }),
  kbSearch: (query: string, top_k = 5) =>
    request<KBHit[]>("/api/kb/search", { method: "POST", body: JSON.stringify({ query, top_k }) }),
};

export const TOOL_LABELS: Record<string, string> = {
  search_openalex: "OpenAlex",
  search_arxiv: "arXiv",
  search_wikipedia: "Wikipedia",
  search_knowledge_base: "Your library",
};

export const TERMINAL: RunStatus[] = ["completed", "rejected", "cancelled", "failed", "interrupted"];
