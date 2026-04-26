"""Agnes MCP server — Model Context Protocol surface over the FastAPI app.

External AI agents (Claude Desktop, IDE assistants, custom agents) drive
Agnes through this surface: trigger pipelines, inspect runs, query the GL,
read the wiki, approve held entries / period reports.

Tools, resources, and prompts are thin wrappers over the FastAPI routers in
`backend.api.*`. Every tool dispatches via httpx ASGI in-process — no
duplicate business logic, no separate uvicorn required.

Entrypoints:

    python -m backend.mcp                # stdio transport (Claude Desktop)
    python -m backend.mcp --http :8765   # streamable-http transport
"""
from .server import build_server

__all__ = ["build_server"]
