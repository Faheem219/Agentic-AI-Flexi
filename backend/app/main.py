"""FastAPI application: REST + Server-Sent Events API for the Veritas research agents."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from google.genai import types
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import db
from .agents import prompts
from .agents.graph import ALL_TOOLS, DEPTH, Deps
from .config import BASE_DIR, get_settings
from .events import EventBus
from .llm import GeminiLLM, LLMUnavailable, user_text
from .rag import store
from .runs import RunManager
from .toolbox import Toolbox

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("veritas")
VERSION = "1.0.0"
EVAL_RESULTS = BASE_DIR / "evals" / "results" / "latest.json"


# ------------------------------------------------------------------ request models
class RunCreate(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    depth: str = "standard"
    sources: list[str] = Field(default_factory=lambda: list(ALL_TOOLS))
    hitl: bool = False
    max_revisions: int = 1


class Decision(BaseModel):
    action: str = Field("approve", pattern="^(approve|reject)$")
    plan: dict | None = None
    feedback: str = ""


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list)


class KBSearch(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)
    top_k: int = 5


# ------------------------------------------------------------------ app factory
def create_app(deps: Deps | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db()
        stale = db.mark_stale_runs()
        if stale:
            log.info("Marked %d interrupted runs from a previous process", stale)
        own_toolbox = deps is None
        d = deps
        if d is None:
            toolbox = Toolbox(settings)
            await toolbox.start()
            d = Deps(llm=GeminiLLM(settings), toolbox=toolbox, settings=settings)
        app.state.deps = d
        app.state.bus = EventBus()
        app.state.runs = RunManager(d, app.state.bus)
        app.state.retriever = store.HybridRetriever(d.llm.embed)
        app.state.started = time.time()
        yield
        if own_toolbox:
            await d.toolbox.stop()

    app = FastAPI(title="Veritas Research Agents API", version=VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )

    rate: dict[str, deque] = defaultdict(deque)

    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")

    def require_access(request: Request) -> None:
        if settings.access_code and request.headers.get("x-access-code") != settings.access_code:
            raise HTTPException(401, "A valid access code is required for this action.")

    def get_run_or_404(run_id: str) -> dict:
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        return run

    # -------------------------------------------------------------- meta
    @app.get("/api/health")
    async def health(request: Request) -> dict:
        d: Deps = request.app.state.deps
        return {
            "status": "ok",
            "version": VERSION,
            "uptime_s": round(time.time() - request.app.state.started, 1),
            "models": {"smart": settings.model_chain("smart"), "fast": settings.model_chain("fast"),
                       "embedding": settings.embed_model, "status": d.llm.model_status()},
            "mcp": {"mode": d.toolbox.mode, "tools": d.toolbox.names},
            "knowledge_base_documents": store.document_count(),
            "access_code_required": bool(settings.access_code),
            "depths": DEPTH,
        }

    @app.get("/api/tools")
    async def tools(request: Request) -> list[dict]:
        return request.app.state.deps.toolbox.tools

    @app.get("/api/stats")
    async def stats() -> dict:
        return db.run_stats()

    @app.get("/api/evals/latest")
    async def evals_latest() -> dict:
        if not EVAL_RESULTS.exists():
            raise HTTPException(404, "No evaluation results yet. Run: python -m evals.run_evals")
        return json.loads(EVAL_RESULTS.read_text())

    # -------------------------------------------------------------- runs
    @app.post("/api/runs", dependencies=[Depends(require_access)])
    async def create_run(body: RunCreate, request: Request) -> dict:
        ip = client_ip(request)
        window = rate[ip]
        now = time.time()
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= settings.runs_per_hour_per_ip:
            raise HTTPException(429, "Hourly run limit reached for this client. Please try again later.")
        window.append(now)
        run_id = request.app.state.runs.start(body.question, body.model_dump(exclude={"question"}), ip)
        return {"run_id": run_id}

    @app.get("/api/runs")
    async def list_runs(limit: int = 30) -> list[dict]:
        return db.list_runs(min(max(limit, 1), 100))

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        return get_run_or_404(run_id)

    @app.delete("/api/runs/{run_id}", dependencies=[Depends(require_access)])
    async def delete_run(run_id: str, request: Request) -> dict:
        get_run_or_404(run_id)
        if request.app.state.runs.is_active(run_id):
            raise HTTPException(409, "Cancel the run before deleting it.")
        db.delete_run(run_id)
        return {"deleted": run_id}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, after: int = 0) -> list[dict]:
        get_run_or_404(run_id)
        return db.list_events(run_id, after)

    @app.get("/api/runs/{run_id}/stream")
    async def run_stream(run_id: str, request: Request, after: int = 0):
        get_run_or_404(run_id)
        last_id = request.headers.get("last-event-id")
        cursor = int(last_id) if last_id and last_id.isdigit() else after
        bus: EventBus = request.app.state.bus

        async def gen():
            nonlocal cursor
            queue = bus.subscribe(run_id)  # subscribe first, then replay history: no gap
            try:
                for ev in db.list_events(run_id, cursor):
                    cursor = ev["id"]
                    yield {"id": str(ev["id"]), "event": "trace", "data": json.dumps(ev)}
                while True:
                    run = db.get_run(run_id)
                    if run and run["status"] in db.TERMINAL_STATUSES | {"awaiting_approval"} \
                            and queue.empty() and not request.app.state.runs.is_active(run_id):
                        for ev in db.list_events(run_id, cursor):  # flush anything left
                            cursor = ev["id"]
                            yield {"id": str(ev["id"]), "event": "trace", "data": json.dumps(ev)}
                        yield {"event": "end", "data": json.dumps({"status": run["status"]})}
                        return
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    if ev["id"] <= cursor:
                        continue
                    cursor = ev["id"]
                    yield {"id": str(ev["id"]), "event": "trace", "data": json.dumps(ev)}
            finally:
                bus.unsubscribe(run_id, queue)

        return EventSourceResponse(gen(), ping=15, headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.post("/api/runs/{run_id}/approve", dependencies=[Depends(require_access)])
    async def approve(run_id: str, body: Decision, request: Request) -> dict:
        get_run_or_404(run_id)
        try:
            request.app.state.runs.resume(run_id, body.model_dump())
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"run_id": run_id, "status": "running"}

    @app.post("/api/runs/{run_id}/cancel", dependencies=[Depends(require_access)])
    async def cancel(run_id: str, request: Request) -> dict:
        get_run_or_404(run_id)
        return {"cancelled": request.app.state.runs.cancel(run_id)}

    @app.get("/api/runs/{run_id}/trace")
    async def trace(run_id: str) -> dict:
        run = get_run_or_404(run_id)
        return {"run": run, "events": db.list_events(run_id, 0, limit=10000)}

    @app.post("/api/runs/{run_id}/chat", dependencies=[Depends(require_access)])
    async def chat(run_id: str, body: ChatRequest, request: Request) -> dict:
        run = get_run_or_404(run_id)
        if run["status"] != "completed" or not run.get("report"):
            raise HTTPException(409, "The report is not ready yet.")
        refs = "\n".join(
            f"[{r['n']}] {r.get('title')} ({r.get('year')}). {r.get('venue') or ''}\n    Abstract: {(r.get('abstract') or '')[:900]}"
            for r in run.get("references") or [])
        system = (prompts.CHAT_SYSTEM.format(untrusted=prompts.UNTRUSTED_DATA_NOTICE)
                  + f"\n\n# Research question\n{run['question']}\n\n# Report\n{run['report']}"
                  + f"\n\n# References\n<tool_results>\n{refs}\n</tool_results>")
        contents = [
            user_text(m.content) if m.role == "user"
            else types.Content(role="model", parts=[types.Part(text=m.content)])
            for m in body.history[-10:]
        ] + [user_text(body.message)]
        try:
            res = await request.app.state.deps.llm.generate(
                agent="chat", tier="smart", system=system, contents=contents, temperature=0.3)
        except LLMUnavailable as e:
            raise HTTPException(503, f"The language model is temporarily unavailable: {e}") from e
        return {"answer": res.text, "model": res.model, "latency_s": round(res.latency, 2),
                "tokens": sum(res.usage.values()), "cached": res.cached}

    # -------------------------------------------------------------- knowledge base
    @app.get("/api/kb/documents")
    async def kb_list() -> list[dict]:
        return store.list_documents()

    @app.post("/api/kb/documents", dependencies=[Depends(require_access)])
    async def kb_upload(request: Request, file: UploadFile = File(...)) -> dict:
        name = file.filename or "document.txt"
        if not name.lower().endswith((".pdf", ".txt", ".md", ".markdown")):
            raise HTTPException(415, "Supported formats: PDF, TXT, Markdown.")
        data = await file.read()
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(413, "File too large (max 15 MB).")
        try:
            doc = await store.ingest_document(name, data, request.app.state.deps.llm.embed)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return doc

    @app.delete("/api/kb/documents/{doc_id}", dependencies=[Depends(require_access)])
    async def kb_delete(doc_id: str) -> dict:
        if not store.delete_document(doc_id):
            raise HTTPException(404, "Document not found")
        return {"deleted": doc_id}

    @app.post("/api/kb/search")
    async def kb_search(body: KBSearch, request: Request) -> list[dict]:
        return await request.app.state.retriever.search(body.query, top_k=min(max(body.top_k, 1), 10))

    return app


app = create_app()
