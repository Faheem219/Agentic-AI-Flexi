# Veritas: a multi-agent research assistant with verifiable citations

Veritas answers a research question by running a small team of AI agents. They plan the research, search real scholarly databases in parallel, write a brief and critique it. Every claim cites a record that was actually retrieved, and the reference list is assembled from database metadata rather than written by a language model.

Built for the CA3 *Agentic AI* assignment (Symbiosis, Flexi programme).

| | Veritas | Single call to the same Gemini model |
|---|---|---|
| References that exist (checked against Crossref / OpenAlex) | **100%** | 50% |
| References not found anywhere | **0%** | 25% |
| DOIs that point to the cited paper | **98%** | 46% |
| Blind LLM judge, mean score out of 5 | **4.96** | 4.08 |
| Average time per answer | 58 s | 6 s |

The figures come from 6 research questions in 6 fields. See [`backend/evals/results/latest.md`](backend/evals/results/latest.md).

## What is agentic about it

| Pattern | Where |
|---|---|
| Multi-agent orchestration (LangGraph state machine) | `backend/app/agents/graph.py` |
| Tool use through the **Model Context Protocol** (separate MCP server over stdio) | `backend/app/mcp_server.py`, `backend/app/toolbox.py` |
| Routing: the planner picks databases per sub-question | planner node |
| Parallelisation: researchers fan out with LangGraph `Send` | `fan_out` |
| Plan-and-execute, then ReAct refinement with native function calling | researcher node |
| Reflection: writer and critic loop with a code-based citation verifier | writer / verifier / critic nodes |
| Human in the loop: interrupt and resume to review or edit the plan | human_review node |
| RAG: page-aware chunking, contextual chunk headers, hybrid BM25 + embeddings + RRF | `backend/app/rag/` |
| CAG: follow-up chat over the whole brief held in context | `POST /api/runs/{id}/chat` |
| Episodic memory of past runs (embedding recall) | `backend/app/memory.py` |
| Guardrails: injection screening, PII redaction, model scope check, untrusted-data delimiters | `backend/app/guardrails.py` |
| Reliability: model fallback chain, cool-downs, retries, caches, Crossref fallback, graceful degradation | `backend/app/llm.py`, `backend/app/tools/scholarly.py` |
| Observability: every step, model call and tool call is a trace event, streamed over SSE | `backend/app/events.py` |
| Evaluation: baseline comparison, reference verification, blind LLM judge, red-team suite | `backend/evals/run_evals.py` |

## Architecture

```
React (Vercel) --HTTPS /api--> FastAPI (VM, Docker) --> LangGraph agent workflow --> Gemini gateway --> Gemini API
                                    |                         |
                                 SQLite (WAL)            MCP client --stdio--> MCP tool server --> OpenAlex / arXiv /
                         runs, traces, library, memory                                            Wikipedia / Crossref / library
```

## Run locally

Requirements: Python 3.12+, Node 20+, and a free Gemini API key.

```bash
# backend
cd backend
cp .env.example .env            # set GEMINI_API_KEY
uv venv --python 3.12 .venv && uv pip install -r requirements-dev.txt   # or: python -m venv .venv && pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                     # http://localhost:5173 (proxies /api to :8000)
```

## Tests and evaluation

```bash
cd backend
.venv/bin/python -m pytest -v            # 48 tests, no network or API quota needed (scripted fake LLM)
.venv/bin/python -m evals.run_evals      # live benchmark; writes evals/results/latest.{json,md}
```

## Use the tools from any MCP host

The research tools are a standard MCP server, so Claude Desktop or Claude Code can use them directly:

```json
{
  "mcpServers": {
    "veritas-research-tools": {
      "command": "/path/to/backend/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/backend"
    }
  }
}
```

Or serve it over HTTP: `python -m app.mcp_server --http --port 8765`.

## Deploy

See [`deploy/DEPLOY.md`](deploy/DEPLOY.md). In short, the backend runs in Docker on a VM (`backend/docker-compose.yml`) and the frontend runs on Vercel (`frontend/vercel.json` proxies `/api` to the VM).

## Project layout

```
backend/
  app/
    agents/graph.py      LangGraph workflow: all agent nodes, routing, reflection loop, HITL
    agents/prompts.py    role prompts + JSON schemas
    llm.py               Gemini gateway (fallbacks, cool-down, cache, accounting)
    mcp_server.py        MCP tool server (tools, resource, prompt)
    toolbox.py           MCP client -> Gemini function declarations
    tools/scholarly.py   OpenAlex, arXiv, Wikipedia, Crossref clients
    rag/                 chunking, ingestion, hybrid retrieval
    guardrails.py, citations.py, memory.py, events.py, runs.py, main.py, db.py, config.py
  tests/                 48 pytest tests
  evals/                 benchmark dataset, runner, results
  sample_docs/           sample library document
frontend/                React + Vite app (catalogue-card UI), vercel.json
deploy/                  DEPLOY.md, HTTPS compose override, Caddyfile, systemd unit
report/                  CA3 report (.docx) and figures
```
