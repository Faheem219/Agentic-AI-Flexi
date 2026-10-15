"""Reliability features of the Gemini gateway, tested with a stubbed network call."""

import pytest
from google.genai import errors, types

from app.config import Settings
from app.events import RunTracer, current_tracer
from app.llm import GeminiLLM, LLMUnavailable


def _response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5),
    )


def _settings(**kw) -> Settings:
    base = dict(gemini_api_key="test", model_fast="m-fast", model_smart="m-smart",
                model_fallbacks="m-backup", llm_cache=False)
    return Settings(**{**base, **kw})


class StubLLM(GeminiLLM):
    def __init__(self, settings, behaviour):
        super().__init__(settings, client=object())
        self.behaviour = behaviour  # model -> list of outcomes (exception or text)
        self.tried: list[str] = []

    async def _call_model(self, model, contents, config):
        self.tried.append(model)
        outcome = self.behaviour[model].pop(0) if self.behaviour.get(model) else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return _response(outcome)


def _rate_limited():
    return errors.APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                           "details": [{"retryDelay": "40s"}]}})


def _unavailable():
    return errors.APIError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "overloaded"}})


async def test_falls_back_to_next_model_on_rate_limit():
    tracer = RunTracer("t-fallback")
    current_tracer.set(tracer)
    llm = StubLLM(_settings(), {"m-smart": [_rate_limited()], "m-backup": ["from backup"]})
    res = await llm.generate(agent="writer", contents="hi", tier="smart")
    assert res.text == "from backup" and res.model == "m-backup"
    assert llm.tried == ["m-smart", "m-backup"]
    assert tracer.fallbacks == 1
    # the rate-limited model is now cooling down and is skipped on the next call
    res2 = await llm.generate(agent="writer", contents="hi again", tier="smart")
    assert res2.model == "m-backup" and llm.tried[-1] == "m-backup" and llm.tried.count("m-smart") == 1
    assert llm.model_status()["m-smart"] == "cooling_down"


async def test_retries_transient_server_errors_on_same_model():
    llm = StubLLM(_settings(), {"m-fast": [_unavailable(), "recovered"]})
    res = await llm.generate(agent="researcher:q1", contents="hi", tier="fast")
    assert res.text == "recovered" and llm.tried == ["m-fast", "m-fast"]


async def test_raises_when_every_model_fails():
    llm = StubLLM(_settings(), {m: [_rate_limited()] for m in ("m-fast", "m-backup", "m-smart")})
    with pytest.raises(LLMUnavailable):
        await llm.generate(agent="planner", contents="hi", tier="fast")


async def test_response_cache_avoids_second_call():
    llm = StubLLM(_settings(llm_cache=True), {"m-fast": ["cached answer"]})
    first = await llm.generate(agent="planner", contents="same prompt", tier="fast")
    second = await llm.generate(agent="planner", contents="same prompt", tier="fast")
    assert first.text == second.text == "cached answer"
    assert not first.cached and second.cached
    assert llm.tried == ["m-fast"]


def test_model_chain_has_no_duplicates():
    chain = _settings(model_fallbacks="m-backup,m-smart").model_chain("smart")
    assert chain == ["m-smart", "m-backup", "m-fast"]
