"""Run lifecycle: start research runs in the background, pause for human approval, resume, cancel."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from . import db
from .agents.graph import ALL_TOOLS, DEPTH, RUN_CONTEXTS, Deps, RunContext, build_graph
from .events import EventBus, RunTracer, current_tracer

log = logging.getLogger("veritas.runs")


def normalize_settings(raw: dict | None) -> dict:
    raw = raw or {}
    depth = raw.get("depth") if raw.get("depth") in DEPTH else "standard"
    sources = [s for s in (raw.get("sources") or ALL_TOOLS) if s in ALL_TOOLS] or ["search_openalex"]
    try:
        max_rev = max(0, min(int(raw.get("max_revisions", 1)), 3))
    except (TypeError, ValueError):
        max_rev = 1
    return {"depth": depth, "sources": sources, "hitl": bool(raw.get("hitl", False)), "max_revisions": max_rev}


class RunManager:
    def __init__(self, deps: Deps, bus: EventBus) -> None:
        self.deps = deps
        self.bus = bus
        self.graph = build_graph(deps, checkpointer=InMemorySaver())
        self._tasks: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(deps.settings.max_concurrent_runs)

    def start(self, question: str, settings: dict, client_ip: str | None = None) -> str:
        run_id = uuid.uuid4().hex[:12]
        settings = normalize_settings(settings)
        db.create_run(run_id, question.strip(), settings, client_ip)
        tracer = RunTracer(run_id, self.bus)
        RUN_CONTEXTS[run_id] = RunContext(deps=self.deps, tracer=tracer)
        tracer.emit("run_created", None, question=question.strip(), settings=settings)
        payload = {"run_id": run_id, "question": question.strip(), "settings": settings, "revision": 0}
        self._spawn(run_id, payload)
        return run_id

    def resume(self, run_id: str, decision: dict[str, Any]) -> None:
        run = db.get_run(run_id)
        if not run or run["status"] != "awaiting_approval" or run_id not in RUN_CONTEXTS:
            raise ValueError("Run is not awaiting approval")
        RUN_CONTEXTS[run_id].tracer.emit("human_decision", "human", action=decision.get("action", "approve"),
                                         edited=bool(decision.get("plan")))
        db.update_run(run_id, status="running")
        self._spawn(run_id, Command(resume=decision))

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        run = db.get_run(run_id)
        if task and not task.done():
            task.cancel()
            return True
        if run and run["status"] == "awaiting_approval":
            db.update_run(run_id, status="cancelled", error="Cancelled by the user")
            self._emit(run_id, "run_cancelled")
            self._cleanup(run_id)
            return True
        return False

    def is_active(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        return bool(t and not t.done())

    def _spawn(self, run_id: str, payload: Any) -> None:
        task = asyncio.create_task(self._execute(run_id, payload), name=f"run-{run_id}")
        self._tasks[run_id] = task

    def _emit(self, run_id: str, type_: str, **data: Any) -> None:
        ctx = RUN_CONTEXTS.get(run_id)
        if ctx:
            ctx.tracer.emit(type_, None, **data)
        else:
            event = db.insert_event(run_id, type_, None, data)
            self.bus.publish(run_id, event)

    def _cleanup(self, run_id: str) -> None:
        RUN_CONTEXTS.pop(run_id, None)
        self._tasks.pop(run_id, None)

    async def _execute(self, run_id: str, payload: Any) -> None:
        cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": 60}
        ctx = RUN_CONTEXTS[run_id]
        current_tracer.set(ctx.tracer)
        try:
            async with self._sem:
                if not isinstance(payload, Command):
                    db.update_run(run_id, status="running")
                    ctx.tracer.emit("run_started", None)
                async for _ in self.graph.astream(payload, cfg, stream_mode="updates"):
                    pass
            snapshot = await self.graph.aget_state(cfg)
            if snapshot.interrupts:
                plan = snapshot.values.get("plan")
                db.update_run(run_id, status="awaiting_approval")
                ctx.tracer.emit("awaiting_approval", "human_review", plan=plan)
                self._tasks.pop(run_id, None)
                return  # keep RUN_CONTEXTS: the run resumes later
            status = snapshot.values.get("status", "completed")
            ctx.tracer.emit("run_finished", None, status=status, metrics=ctx.tracer.metrics())
            self._cleanup(run_id)
        except asyncio.CancelledError:
            db.update_run(run_id, status="cancelled", error="Cancelled by the user", metrics=ctx.tracer.metrics())
            ctx.tracer.emit("run_cancelled", None)
            self._cleanup(run_id)
        except Exception as e:  # noqa: BLE001
            log.exception("run %s failed", run_id)
            msg = f"{type(e).__name__}: {e}"[:800]
            db.update_run(run_id, status="failed", error=msg, metrics=ctx.tracer.metrics())
            ctx.tracer.emit("run_failed", None, error=msg)
            self._cleanup(run_id)
