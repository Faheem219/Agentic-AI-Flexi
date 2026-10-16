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

## Course status
- **Done:** "AI Agents & AI Automation – The Practical Agentic AI Guide" (Udemy, accessed through Infosys Springboard). 8 h 12 min, 91 lectures. It covers n8n workflow automation, Airtable/Gmail/PDF workflows, AI agents vs LLMs, prompting (role / constraints / tools / instructions / I-O), OpenRouter structured outputs, API integration, error handling (retries, 3 layers of failure handling), a Google Calendar agent, RAG (chunking, embeddings, retrieval, contextual retrieval, updating data) and CAG. Its certificate PDF is in the repo root.
- Infosys Springboard's Udemy licence expired, so about 37 hours must come from elsewhere. The user wants courses whose **hours are stated on the official course page or the certificate**. The plan agreed on 2026-10-11 is about 39.5–41 hours:
  - Claude Academy (academy.claude.com): Building with the Claude API 9h, Intro to MCP 1h, MCP Advanced 1.5h, agent skills 1h, subagents 0.75h, Claude Code in action 1h, human-agent teams 0.75h, plus optional Claude Code 101 1.5h
  - Simplilearn SkillUp: AI Agents for Beginners 1h, Intro to Agentic AI & LLM Architectures 1h, AI Agent Frameworks 1h, LangGraph 2h, CrewAI 2h, Reflection Agents 1h, Agentic Workflows (Bedrock) 2h, n8n 3h, Reliable Agentic AI 1h
  - IBM SkillsBuild: Make Agentic AI Work for You 4h, Intro by Design: Build an AI Agent 4h, Unleashing the Power of AI Agents 1h
  - Databricks AI Agent Fundamentals: 1.5h, plus the accreditation quiz
- **Pending:** the user will paste each completed course's contents and certificates. Then:
  1. update report Section 3 (one numbered entry per course: title, provider, hours, evidence) and the cover-page duration;
  2. enrich Section 1 (key concepts) and Section 2 (understanding) with the new course material, mapped to where Veritas uses each concept;
  3. add certificate images to Appendix A.

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

## Report status (`report/CA3_Agentic_AI_Report.docx`)
- Generated from the template by `report/build_report.py`, which edits the template's XML and keeps its styles, footer and numbering. Re-run it after changing content.
- Figures are in `report/figures/` (UI screenshots via Playwright plus diagrams).
- Placeholders are highlighted yellow: Faculty, Division, PRN, the Vercel URL, and the remaining courses in Section 3 and Appendix A.
- The audience is the faculty evaluators. Write formally, in first person for the reflection.
