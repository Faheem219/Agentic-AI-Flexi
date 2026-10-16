"""MCP client side: discovers the research tools and executes tool calls for the agents.

Default mode launches the MCP server as a subprocess over stdio (a real MCP client/server
boundary). If that fails, it falls back to connecting to the same server in-process, which
still speaks the MCP protocol.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import AsyncExitStack
from typing import Any

from google.genai import types
from mcp import Client, StdioServerParameters

from .config import BASE_DIR, Settings

log = logging.getLogger("veritas.toolbox")


class Toolbox:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mode = "stopped"
        self.tools: list[dict[str, Any]] = []
        self._client: Client | None = None
        self._stack: AsyncExitStack | None = None

    async def start(self) -> None:
        self._stack = AsyncExitStack()
        if self.settings.mcp_mode == "stdio":
            try:
                params = StdioServerParameters(
                    command=sys.executable, args=["-m", "app.mcp_server"],
                    cwd=str(BASE_DIR), env=dict(os.environ),
                )
                self._client = await self._stack.enter_async_context(Client(params))
                self.mode = "mcp-stdio"
            except Exception as e:  # noqa: BLE001
                log.warning("MCP stdio server failed to start (%s); using in-process MCP", e)
                await self._stack.aclose()
                self._stack = AsyncExitStack()
                self._client = None
        if self._client is None:
            from .mcp_server import server

            self._client = await self._stack.enter_async_context(Client(server))
            self.mode = "mcp-inprocess"
        listed = await self._client.list_tools()
        self.tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in listed.tools
        ]
        log.info("MCP toolbox ready (%s): %s", self.mode, [t["name"] for t in self.tools])

    async def stop(self) -> None:
        if self._stack:
            await self._stack.aclose()
        self.mode = "stopped"

    @property
    def names(self) -> list[str]:
        return [t["name"] for t in self.tools]

    def declarations(self, names: list[str] | None = None) -> list[types.FunctionDeclaration]:
        """Convert MCP tool schemas into Gemini function declarations."""
        out = []
        for t in self.tools:
            if names is not None and t["name"] not in names:
                continue
            schema = {k: v for k, v in t["input_schema"].items() if k != "title"}
            props = {
                k: {kk: vv for kk, vv in v.items() if kk != "title"}
                for k, v in (schema.get("properties") or {}).items()
            }
            schema["properties"] = props
            out.append(types.FunctionDeclaration(
                name=t["name"], description=t["description"], parameters_json_schema=schema))
        return out

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool via MCP. Always returns a dict with `results` (and maybe `error`)."""
        if self._client is None:
            raise RuntimeError("Toolbox not started")
        if name not in self.names:
            return {"results": [], "error": f"Unknown tool '{name}'. Available: {self.names}"}
        t0 = time.time()
        try:
            res = await self._client.call_tool(name, args)
        except Exception as e:  # noqa: BLE001
            return {"results": [], "error": f"MCP call failed: {type(e).__name__}: {e}"[:300],
                    "latency_s": round(time.time() - t0, 2)}
        text = "".join(getattr(c, "text", "") for c in (res.content or []))
        try:
            payload = json.loads(text) if text else {"results": []}
        except json.JSONDecodeError:
            payload = {"results": [], "error": f"Non-JSON tool output: {text[:200]}"}
        if res.is_error and "error" not in payload:
            payload["error"] = text[:300] or "tool error"
        payload["latency_s"] = round(time.time() - t0, 2)
        return payload
