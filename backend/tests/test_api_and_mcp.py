"""HTTP API (FastAPI) and MCP server integration tests."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from mcp import Client

from app.agents.graph import Deps
from app.config import get_settings
from app.main import create_app
from app.mcp_server import server
from app.rag import store
from app.toolbox import Toolbox
from tests.conftest import FakeLLM, FakeToolbox

SAMPLE = Path(__file__).resolve().parent.parent / "sample_docs" / "agentic_ai_study_notes.md"


def _client():
    deps = Deps(llm=FakeLLM(), toolbox=FakeToolbox(), settings=get_settings())
    return TestClient(create_app(deps))


def _wait(client, run_id, until=("completed", "failed", "rejected", "awaiting_approval"), timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in until:
            return run
        time.sleep(0.1)
    raise AssertionError(f"run stuck in {run['status']}")


def test_health_and_tools():
    with _client() as c:
        h = c.get("/api/health").json()
        assert h["status"] == "ok" and "search_openalex" in h["mcp"]["tools"]
        assert len(c.get("/api/tools").json()) == 4


def _wait_event(client, run_id, event_type, timeout=10):
    """The run row is marked completed just before the final trace event is written, so wait for it."""
    end = time.time() + timeout
    while time.time() < end:
        events = client.get(f"/api/runs/{run_id}/events").json()
        if events and events[-1]["type"] == event_type:
            return events
        time.sleep(0.05)
    raise AssertionError(f"no {event_type} event")


def test_run_lifecycle_over_http():
    with _client() as c:
        rid = c.post("/api/runs", json={"question": "How should retrieval augmented generation be evaluated?",
                                        "depth": "quick"}).json()["run_id"]
        run = _wait(c, rid)
        assert run["status"] == "completed" and run["references"]
        events = _wait_event(c, rid, "run_finished")
        assert events[0]["type"] == "run_created" and events[-1]["type"] == "run_finished"
        later = c.get(f"/api/runs/{rid}/events", params={"after": events[-3]["id"]}).json()
        assert len(later) == 2
        trace = c.get(f"/api/runs/{rid}/trace").json()
        assert trace["run"]["id"] == rid and len(trace["events"]) == len(events)
        chat = c.post(f"/api/runs/{rid}/chat", json={"message": "What improves faithfulness?"}).json()
        assert "[1]" in chat["answer"]
        assert any(r["id"] == rid for r in c.get("/api/runs").json())
        assert c.get("/api/stats").json()["completed"] == 1


def test_hitl_over_http():
    with _client() as c:
        rid = c.post("/api/runs", json={"question": "How should retrieval augmented generation be evaluated?",
                                        "depth": "quick", "hitl": True}).json()["run_id"]
        run = _wait(c, rid)
        assert run["status"] == "awaiting_approval" and run["plan"]["sub_questions"]
        assert c.post(f"/api/runs/{rid}/approve", json={"action": "approve"}).status_code == 200
        assert _wait(c, rid, until=("completed", "failed"))["status"] == "completed"
        # approving twice is rejected
        assert c.post(f"/api/runs/{rid}/approve", json={"action": "approve"}).status_code == 409


def test_sse_stream_replays_events_and_ends():
    with _client() as c:
        rid = c.post("/api/runs", json={"question": "How should retrieval augmented generation be evaluated?",
                                        "depth": "quick"}).json()["run_id"]
        _wait(c, rid)
        with c.stream("GET", f"/api/runs/{rid}/stream") as resp:
            body = "".join(resp.iter_text())
        assert "event: trace" in body and "event: end" in body and '"final"' in body


def test_knowledge_base_endpoints():
    with _client() as c:
        up = c.post("/api/kb/documents", files={"file": (SAMPLE.name, SAMPLE.read_bytes(), "text/markdown")})
        assert up.status_code == 200 and up.json()["chunks"] > 3
        hits = c.post("/api/kb/search", json={"query": "three layers of failure handling retries", "top_k": 3}).json()
        assert hits and "retr" in hits[0]["text"].lower()
        assert c.post("/api/kb/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 415
        assert c.delete(f"/api/kb/documents/{up.json()['id']}").status_code == 200
        assert c.get("/api/kb/documents").json() == []


def test_access_code_protects_write_endpoints(monkeypatch):
    monkeypatch.setattr(get_settings(), "access_code", "s3cret")
    with _client() as c:
        body = {"question": "How should retrieval augmented generation be evaluated?", "depth": "quick"}
        assert c.post("/api/runs", json=body).status_code == 401
        assert c.post("/api/runs", json=body, headers={"X-Access-Code": "s3cret"}).status_code == 200
        assert c.get("/api/health").json()["access_code_required"] is True


async def test_mcp_server_exposes_tools_resources_and_prompts():
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"search_openalex", "search_arxiv", "search_wikipedia", "search_knowledge_base"}
        assert tools["search_openalex"].input_schema["required"] == ["query"]
        resources = (await client.list_resources()).resources
        assert any(str(r.uri) == "kb://documents" for r in resources)
        prompts = (await client.list_prompts()).prompts
        assert any(p.name == "literature_review" for p in prompts)


async def test_mcp_knowledge_base_tool_roundtrip():
    await store.ingest_document(SAMPLE.name, SAMPLE.read_bytes(), None)
    tb = Toolbox(get_settings())  # MCP_MODE=inprocess in tests
    await tb.start()
    try:
        assert tb.mode == "mcp-inprocess"
        decls = tb.declarations(["search_knowledge_base"])
        assert decls[0].name == "search_knowledge_base" and "query" in decls[0].parameters_json_schema["properties"]
        payload = await tb.call("search_knowledge_base", {"query": "cache augmented generation", "top_k": 2})
        assert payload["results"] and payload["results"][0]["type"] == "kb"
        json.dumps(payload)
        unknown = await tb.call("delete_everything", {})
        assert "Unknown tool" in unknown["error"]
    finally:
        await tb.stop()
