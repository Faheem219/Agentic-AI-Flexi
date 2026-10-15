"""Gemini LLM gateway with the reliability features an agentic system needs on a free tier.

* Model fallback chain: when one model is rate-limited (429) or overloaded (5xx), try the next.
* Per-model cool-down that honours the server's retry delay.
* Bounded concurrency (semaphore) so parallel agents do not trip rate limits.
* Response cache keyed on the full request, for reproducible evals and lower quota use.
* Token, latency and model accounting for every call, emitted as trace events.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import errors, types

from . import db
from .config import Settings
from .events import current_tracer


class LLMUnavailable(RuntimeError):
    """Raised when every model in the fallback chain failed."""


class LLMSafetyBlock(RuntimeError):
    """Raised when the provider's safety filters block the prompt or the response."""


_BLOCK_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


def _safety_block(resp: types.GenerateContentResponse) -> str | None:
    fb = getattr(resp, "prompt_feedback", None)
    if fb is not None and getattr(fb, "block_reason", None):
        return str(fb.block_reason)
    cand = resp.candidates[0] if resp.candidates else None
    reason = getattr(cand, "finish_reason", None) if cand else None
    name = getattr(reason, "name", str(reason)) if reason else ""
    has_text = bool(cand and cand.content and cand.content.parts)
    return name if name in _BLOCK_FINISH and not has_text else None


@dataclass
class LLMResult:
    text: str
    function_calls: list[types.FunctionCall] = field(default_factory=list)
    content: types.Content | None = None  # the model turn, to append to multi-turn history
    thoughts: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    latency: float = 0.0
    cached: bool = False


def user_text(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def _result_from_response(resp: types.GenerateContentResponse, model: str, latency: float, cached: bool) -> LLMResult:
    cand = resp.candidates[0] if resp.candidates else None
    parts = (cand.content.parts or []) if cand and cand.content else []
    text = "".join(p.text for p in parts if p.text and not p.thought)
    thoughts = "\n".join(p.text for p in parts if p.text and p.thought)
    calls = [p.function_call for p in parts if p.function_call]
    um = resp.usage_metadata
    usage = {
        "input": (um.prompt_token_count or 0) if um else 0,
        "output": (um.candidates_token_count or 0) if um else 0,
        "thinking": (um.thoughts_token_count or 0) if um else 0,
    }
    return LLMResult(
        text=text.strip(), function_calls=calls, content=cand.content if cand else None,
        thoughts=thoughts.strip(), model=model, usage=usage, latency=latency, cached=cached,
    )


def _retry_delay_s(err: errors.APIError) -> float:
    blob = json.dumps(err.details, default=str) if err.details else str(err)
    if "PerDay" in blob or "per day" in blob.lower():
        return 3600.0  # daily quota exhausted: park this model for an hour
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', blob)
    return min(max(float(m.group(1)) if m else 30.0, 5.0), 300.0)


class GeminiLLM:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self.client = client or genai.Client(api_key=settings.gemini_api_key)
        self._sem = asyncio.Semaphore(settings.max_concurrent_llm_calls)
        self._cooldown: dict[str, float] = {}

    # Separate method so tests can stub the network call.
    async def _call_model(self, model: str, contents: list[types.Content], config: types.GenerateContentConfig):
        return await asyncio.wait_for(
            self.client.aio.models.generate_content(model=model, contents=contents, config=config),
            timeout=self.settings.llm_timeout_s,
        )

    def model_status(self) -> dict[str, Any]:
        now = time.time()
        return {m: ("cooling_down" if self._cooldown.get(m, 0) > now else "ready")
                for m in self.settings.model_chain("smart") + self.settings.model_chain("fast")}

    async def generate(
        self,
        *,
        agent: str,
        contents: list[types.Content] | str,
        system: str | None = None,
        tools: list[types.FunctionDeclaration] | None = None,
        json_schema: dict | None = None,
        tier: str = "fast",
        temperature: float = 0.3,
        thinking: str = "low",
        include_thoughts: bool = False,
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        if isinstance(contents, str):
            contents = [user_text(contents)]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=thinking, include_thoughts=include_thoughts),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if tools:
            config.tools = [types.Tool(function_declarations=tools)]
        if json_schema:
            config.response_mime_type = "application/json"
            config.response_json_schema = json_schema

        tracer = current_tracer.get()
        key = self._cache_key(tier, config, contents)
        if self.settings.llm_cache:
            hit = db.cache_get("llm_cache", key)
            if hit:
                payload = json.loads(hit)
                resp = types.GenerateContentResponse.model_validate(payload["response"])
                result = _result_from_response(resp, payload["model"], 0.0, cached=True)
                self._record(tracer, agent, result, fallback_from=None)
                return result

        chain = self.settings.model_chain(tier)
        failures: list[str] = []
        for model in chain:
            if self._cooldown.get(model, 0) > time.time():
                failures.append(f"{model}: cooling down")
                continue
            for attempt in range(2):
                t0 = time.time()
                try:
                    async with self._sem:
                        resp = await self._call_model(model, contents, config)
                    blocked = _safety_block(resp)
                    if blocked:
                        if tracer:
                            tracer.emit("llm_safety_block", agent, model=model, reason=blocked)
                        raise LLMSafetyBlock(f"Blocked by the model's safety filters ({blocked}).")
                    result = _result_from_response(resp, model, time.time() - t0, cached=False)
                    if not result.text and not result.function_calls:
                        reason = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
                        raise ValueError(f"empty response (block_reason={reason})")
                    if self.settings.llm_cache:
                        db.cache_put("llm_cache", key, json.dumps(
                            {"model": model, "response": resp.model_dump(mode="json", exclude_none=True)}))
                    self._record(tracer, agent, result, fallback_from=chain[0] if model != chain[0] else None)
                    return result
                except errors.APIError as e:
                    failures.append(f"{model}: HTTP {e.code} {e.status}")
                    if e.code == 429:
                        self._cooldown[model] = time.time() + _retry_delay_s(e)
                        break
                    if e.code in (500, 502, 503, 504) and attempt == 0:
                        await asyncio.sleep(1.5 + random.random() * 2)
                        continue
                    if e.code == 404:
                        self._cooldown[model] = time.time() + 6 * 3600
                    break  # next model
                except (asyncio.TimeoutError, ValueError) as e:
                    failures.append(f"{model}: {type(e).__name__} {e}")
                    if attempt == 0:
                        continue
                    break
            if tracer:
                tracer.emit("llm_fallback", agent, failed_model=model, reason=failures[-1] if failures else "")
        if tracer:
            tracer.emit("llm_error", agent, failures=failures)
        raise LLMUnavailable("All Gemini models failed: " + "; ".join(failures[-6:]))

    def _record(self, tracer, agent: str, result: LLMResult, fallback_from: str | None) -> None:
        if not tracer:
            return
        tracer.record_llm(agent, result.model, result.usage, result.latency, result.cached)
        if fallback_from:
            tracer.fallbacks += 1
        tracer.emit(
            "llm_call", agent, model=result.model, latency_s=round(result.latency, 2),
            tokens_in=result.usage.get("input", 0), tokens_out=result.usage.get("output", 0),
            tokens_thinking=result.usage.get("thinking", 0), cached=result.cached,
            fallback_from=fallback_from,
            function_calls=[fc.name for fc in result.function_calls],
        )
        if result.thoughts:
            tracer.emit("thought", agent, text=result.thoughts[:1200])

    @staticmethod
    def _cache_key(tier: str, config: types.GenerateContentConfig, contents: list[types.Content]) -> str:
        blob = json.dumps(
            {
                "tier": tier,
                "config": config.model_dump(mode="json", exclude_none=True),
                "contents": [c.model_dump(mode="json", exclude_none=True) for c in contents],
            },
            sort_keys=True, default=str,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    # ------------------------------------------------------------ embeddings
    async def embed(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        """Embed texts in batches (Gemini embedding model). Single-text calls are cached."""
        if not texts:
            return []
        cache_key = None
        if len(texts) == 1:
            cache_key = "emb:" + hashlib.sha256(
                f"{self.settings.embed_model}|{self.settings.embed_dim}|{task_type}|{texts[0]}".encode()
            ).hexdigest()
            hit = db.cache_get("llm_cache", cache_key)
            if hit:
                return [json.loads(hit)]
        out: list[list[float]] = []
        cfg = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.settings.embed_dim)
        for i in range(0, len(texts), 50):
            batch = texts[i : i + 50]
            for attempt in range(3):
                try:
                    resp = await asyncio.wait_for(
                        self.client.aio.models.embed_content(
                            model=self.settings.embed_model, contents=batch, config=cfg),
                        timeout=60,
                    )
                    out.extend(list(e.values) for e in resp.embeddings)
                    break
                except errors.APIError as e:
                    if attempt == 2 or e.code not in (429, 500, 503):
                        raise
                    await asyncio.sleep(min(_retry_delay_s(e), 20) if e.code == 429 else 2)
        if cache_key:
            db.cache_put("llm_cache", cache_key, json.dumps(out[0]))
        return out
