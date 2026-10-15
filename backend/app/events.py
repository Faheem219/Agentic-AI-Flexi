"""Observability: every agent step, LLM call and tool call becomes a persisted trace event.

Events are written to SQLite (so a run can be replayed or exported) and pushed to live
subscribers (Server-Sent Events stream in the UI).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextvars import ContextVar
from typing import Any

from . import db


class EventBus:
    """In-process pub/sub fan-out of run events to SSE subscribers."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        self._subs[run_id].discard(q)
        if not self._subs[run_id]:
            self._subs.pop(run_id, None)

    def publish(self, run_id: str, event: dict) -> None:
        for q in list(self._subs.get(run_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer: it can recover via ?after= polling
                pass


class RunTracer:
    """Collects metrics for one run and emits its trace events."""

    def __init__(self, run_id: str, bus: EventBus | None = None) -> None:
        self.run_id = run_id
        self.bus = bus
        self.started = time.time()
        self.llm_calls = 0
        self.cached_llm_calls = 0
        self.fallbacks = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tokens_thinking = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.models_used: dict[str, int] = defaultdict(int)
        self.per_agent: dict[str, dict[str, float]] = defaultdict(
            lambda: {"llm_calls": 0, "tokens": 0, "tool_calls": 0, "latency_s": 0.0}
        )

    def emit(self, type_: str, agent: str | None = None, **data: Any) -> dict:
        event = db.insert_event(self.run_id, type_, agent, data)
        if self.bus:
            self.bus.publish(self.run_id, event)
        return event

    def record_llm(self, agent: str, model: str, usage: dict, latency: float, cached: bool) -> None:
        self.llm_calls += 1
        self.cached_llm_calls += int(cached)
        self.tokens_in += usage.get("input", 0)
        self.tokens_out += usage.get("output", 0)
        self.tokens_thinking += usage.get("thinking", 0)
        self.models_used[model] += 1
        a = self.per_agent[agent.split(":")[0]]
        a["llm_calls"] += 1
        a["tokens"] += usage.get("input", 0) + usage.get("output", 0) + usage.get("thinking", 0)
        a["latency_s"] += latency

    def record_tool(self, agent: str, error: bool) -> None:
        self.tool_calls += 1
        self.tool_errors += int(error)
        self.per_agent[agent.split(":")[0]]["tool_calls"] += 1

    def metrics(self) -> dict:
        return {
            "duration_s": round(time.time() - self.started, 2),
            "llm_calls": self.llm_calls,
            "cached_llm_calls": self.cached_llm_calls,
            "model_fallbacks": self.fallbacks,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_thinking": self.tokens_thinking,
            "tokens_total": self.tokens_in + self.tokens_out + self.tokens_thinking,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "models_used": dict(self.models_used),
            "per_agent": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in self.per_agent.items()},
        }


# The tracer of the run currently executing in this asyncio task (set by each graph node).
current_tracer: ContextVar[RunTracer | None] = ContextVar("current_tracer", default=None)


def emit(type_: str, agent: str | None = None, **data: Any) -> dict | None:
    tracer = current_tracer.get()
    return tracer.emit(type_, agent, **data) if tracer else None
