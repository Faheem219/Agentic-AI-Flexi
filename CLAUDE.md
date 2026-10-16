# CLAUDE.md: CA3 Agentic AI project (Symbiosis Flexi)

## What this folder is
The student's CA3 submission for the **Agentic AI** Flexi course. CA3 is worth 45 marks and has three parts:
1. **MOOCs:** at least 45 hours of free agentic-AI courses with certificates.
2. **Implementation:** a small agentic-AI project that is built, tested and **deployed**.
3. **Report:** written in the college's Word template (`CA3_Agentic_AI_Blank_Template_v2.docx`). **Never edit the original template.** The working copy is `report/CA3_Agentic_AI_Report.docx`.

The other assessments are CA1 (MCQ, 10 marks) and CA2 (certificates, 20 marks).

### Report rubric (45 marks)
| Criterion | Marks | Full-marks descriptor |
|---|---|---|
| Problem Statement | 5 | Specific and justified |
| Agent Workflow | 8 | Logical workflow with justified agent/tool roles |
| Implementation | 10 | Working, relevant and verifiable |
| Testing and Evidence | 7 | Multiple relevant tests with outputs and interpretation |
| Individual Reflection | 5 | Accurate, project-specific explanations |
| Report Quality | 10 | Complete and well-organised |

The template has no slots for Agent Workflow, Testing and Evidence, or Individual Reflection. They were added as Heading 2 subsections inside the existing numbered sections (5, 7 and 8) so the template's numbering is unchanged.

## Course status (final, 2026-10-16)
All four courses were taken on **Infosys Springboard**, with Udemy content. Total hours used in the report: **48 h 25 min**.

1. **AI Agents & AI Automation – The Practical Agentic AI Guide.** 8 h 12 min, complete. Springboard certificate dated 11 Sept 2026 (repo-root PDF). Covers n8n automation, agents vs LLMs, prompting, the Calendar agent, RAG/CAG and error handling.
2. **AI Engineer Agentic Track: The Complete Agent & MCP Course.** 20 h 52 min, complete. Certificate dated 21 Sept 2026. Covers agent loops and tool calling, design patterns, guardrails and evals, LLM-as-judge, the OpenAI Agents SDK (multi-agent orchestration, deep research agent), CrewAI, LangGraph (checkpointers, human-in-the-loop, deep agents), ADK/A2A and other frameworks, and MCP (build servers, agentic RAG, capstone).
3. **Agentic AI – Private Agentic RAG with LangGraph and Ollama.** About 80% done (progress PNG in the repo root).
   - Springboard lists 17 h 37 min, so the report counts 14 h 6 min. The Udemy content the user pasted totals 21 h 47 min.
   - The first 80% of the content covers Ollama and local model selection, LangGraph state machines, a MySQL SQL agent, PageRAG ingestion (Docling, ChromaDB, SHA-256 de-duplication), metadata-filtered retrieval with BM25 re-ranking, agentic RAG, CRAG and Reflexion RAG (the cut-off falls inside Self-RAG).
4. **Agentic AI for Beginners: Build AI Agents with LangGraph.** About 90% done (progress PNG in the repo root).
   - Springboard lists 5 h 51 min, so the report counts 5 h 16 min. The Udemy content totals 10 h 5 min.
   - The first 90% covers agent fundamentals, multi-agent systems, LangGraph agents (state, nodes, graphs), knowledge bases/RAG, tools, testing, multi-tool workflows, and AWS Bedrock agents with knowledge bases and action groups.

The report deliberately uses the Springboard-listed durations, because they match the attached progress screenshots.

## The implementation: "Veritas" (multi-agent research assistant with verifiable citations)
- **Problem:** LLM chatbots invent or garble references. In our own benchmark, a single Gemini call gave only 50% of references that exist, and its DOIs pointed to the cited paper only 46% of the time.
- **Workflow (LangGraph)** in `backend/app/agents/graph.py`:
  `guardrail → memory → planner → human_review (interrupt) → researcher×N (Send fan-out) → writer → verifier (code) → critic → (revise → writer | finalize)`.
- **Tools** are served by a separate **MCP server** (`backend/app/mcp_server.py`, mcp SDK 2.x `MCPServer`, stdio) and consumed by `backend/app/toolbox.py` (mcp `Client`). The tools are `search_openalex` (falls back to Crossref on HTTP 429/5xx), `search_arxiv`, `search_wikipedia` and `search_knowledge_base` (hybrid RAG).
- **LLM:** Google Gemini free tier through `google-genai` 2.x (`backend/app/llm.py`).
  - Smart tier: `gemini-3-flash-preview`. Fast tier: `gemini-3.5-flash-lite`. Fallbacks: `gemini-3.1-flash-lite`, `gemini-3.8-flash`, `gemini-flash-lite-latest`.
  - Embeddings: `gemini-embedding-001` (768 dimensions).
  - The 2.5 models are NOT available to this key.
- **Storage:** SQLite in WAL mode (`backend/data/veritas.db`, git-ignored). It holds runs, events, library chunks and vectors, memories, and the LLM and tool caches.
- **Frontend:** React 19 + Vite 7 + TypeScript in `frontend/`.
  - Design concept: library catalogue cards. Fonts: Newsreader / Hanken Grotesk / Courier Prime. Colours: paper #F2F4F7, ink #18243D, red #B8323A, highlighter #F2DD5C.
  - Pages: Ask, Run (agent team + live log + tabs), Library, Evaluation, How it works.
- **Secrets:** the Gemini key lives only in `backend/.env` (git-ignored). Never write it into the report, CLAUDE.md, memory or any committed file.

### Commands
```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000      # API (venv: uv, Python 3.12)
cd frontend && npm run dev                                       # UI on :5173, proxies /api
cd backend && .venv/bin/python -m pytest -q                      # 48 tests, offline (FakeLLM / FakeToolbox in tests/conftest.py)
cd backend && .venv/bin/python -m evals.run_evals                # live benchmark -> evals/results/latest.{json,md}, reports/
```
Use `zsh`-safe shell commands: `echo "----"`, not `echo ====`. Never use `rm -rf *` after `cd`.

### Deployment (user's decisions, 2026-10-16; the user will use only this)
- **Backend:** on the user's VM. They `git pull`, then run `backend/run.sh` inside **tmux** on `127.0.0.1:8000`. Their existing **Nginx + SSL domain** proxies to it. `run.sh` creates the venv, installs requirements and starts a single uvicorn worker.
  - `backend/.env` needs `GEMINI_API_KEY` and `CORS_ORIGINS=https://<project>.vercel.app`.
  - Optional: `CORS_ORIGIN_REGEX` (preview deployments), `ACCESS_CODE`, `OPENALEX_API_KEY`.
- **Frontend:** on Vercel with Root Directory `frontend`. Env var `VITE_API_BASE=https://<backend-domain>`, with no `/api` and no trailing slash; `api.ts` strips either if present. The value is baked in at build time, so redeploy after changing it. `vercel.json` only has the SPA fallback rewrite. The browser calls the backend directly, so CORS must include the Vercel URL.
- **Nginx notes:** consider `client_max_body_size 20m` for PDF uploads. SSE already sends `X-Accel-Buffering: no`.
- No CI/CD. Guide: `deploy/DEPLOY.md`.
- Docker, Caddy and systemd files remain as unused alternatives. The Docker image was smoke-tested on 2026-10-15.
- The cross-origin set-up was verified locally on 2026-10-16: run.sh on another port, a production build with VITE_API_BASE ending in "/api/", CORS allow/deny, SSE headers, and the Playwright UI.

### Key results (`backend/evals/results/latest.md`, 2026-10-15, 6 questions in 6 domains)
| Measure | Veritas | Single call |
|---|---|---|
| References that exist | 100% | 50% |
| References not found | 0% | 25% (plus 25% partial) |
| DOI accuracy | 98% | 46% |
| Blind judge (mean of 4 criteria, out of 5) | 4.96 | 4.08 |
| Judge wins | 6/6 | 0 |
| Average latency | 58 s | 6 s |
| Model calls | 8.3 | 1 |
| Tokens | 31k | 1.7k |
| Citation coverage | 87% | not measured |
| Critic score | 8.9/10 | n/a |
| Runs revised by the critic | 6 of 6 | n/a |

Guardrail red-team cases: 12/12. Planner scope checks: 3/3.

### Behaviours seen during development (useful for the report's reflection)
- A 503 on `gemini-3-flash-preview` triggered an automatic fallback to `gemini-3.1-flash-lite`, and the run still completed.
- Reflection loop on the first live run: citation coverage went from 50% to 95%, and the critic score from 8.25 to 9.75.
- The critic model once said "revise" while every score was 8 or above. The accept/revise rule was moved into code (any score < 7, a high-severity issue, or coverage < 75%), which made the decision deterministic.
- The anonymous OpenAlex budget ($0.10/day, about 100 searches) ran out during the benchmark. A Crossref fallback was added, plus optional `OPENALEX_API_KEY` support (a free key gives 10x the budget). Researchers degraded gracefully to arXiv and the library.
- The same paper appeared on both arXiv and OpenAlex, so the source registry also de-duplicates by normalised title.
- Free-tier quota matters: plan-and-execute runs the searches with no model call, then ReAct refines. That keeps a standard run at about 7–10 model calls.

## Report status (`report/CA3_Agentic_AI_Report.docx`): complete except two links
- Generated from the template by `report/build_report.py`. It edits the template's XML and keeps its styles, footer and numbering. Re-run it after any change: `python3 report/build_report.py`.
- **The user's constraints:**
  - **Under 1000 words, excluding figure captions.** The builder prints the count; Word's own statistic was 976 words of text plus 232 caption words.
  - **Black text only.** The builder recolours every colour in the template's styles and footer to black; table shading is grey.
  - **No highlights.**
- Cover fields: Division CSE C, Name Faheemuddin Sayyed, PRN 23070122196.
- Left blank for the user: **Faculty** (name not given), **Prototype/Application link** and **GitHub repository link** (the user will paste them).
- Figures are in `report/figures/`:
  - diagrams rendered from `report/figures/src/*.html`;
  - UI screenshots, with crops in `crops/`;
  - `fig_tests.png`, generated from `report/evidence/pytest_output.txt`;
  - certificates `cert_course1.png` / `cert_course2.png` and progress crops `progress_course3.jpg` / `progress_course4.jpg`.
- 12 pages. Audience: the faculty evaluators.
- Keep any further edits concise: the word limit is tight (about 25 words of headroom).
