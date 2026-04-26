"""Smoke test for the FastMCP server.

Uses FastMCP's in-memory `Client` (passes the FastMCP instance directly,
no subprocess, no transport) so we hit the same lifespan + ASGI client
the stdio entrypoint would.

Skips cleanly if the optional `fastmcp` extra is not installed.
"""
from __future__ import annotations

import pytest

fastmcp = pytest.importorskip("fastmcp")


@pytest.mark.asyncio
async def test_mcp_lists_tools_and_calls_pipelines() -> None:
    from fastmcp import Client

    from backend.mcp.server import build_server

    server = build_server()

    async with Client(server) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}

        # Core surface present.
        for expected in (
            "list_pipelines",
            "get_pipeline",
            "run_pipeline",
            "list_runs",
            "get_run",
            "list_journal_entries",
            "get_journal_entry_trace",
            "approve_journal_entry",
            "list_envelopes",
            "get_report",
            "list_period_reports",
            "approve_period_report",
            "list_employees",
            "list_wiki_pages",
            "simulate_swan_event",
        ):
            assert expected in names, f"missing tool: {expected}"

        # Resources + prompts wired.
        resources = await client.list_resource_templates()
        uris = {r.uriTemplate for r in resources}
        assert "agnes://run/{run_id}" in uris
        assert "agnes://entry/{entry_id}/trace" in uris

        prompts = await client.list_prompts()
        prompt_names = {p.name for p in prompts}
        assert "triage_failed_run" in prompt_names
        assert "period_close_checklist" in prompt_names

        # End-to-end call: list_pipelines should hit the FastAPI router and
        # come back with an `items` list (may be empty in a fresh DB).
        result = await client.call_tool("list_pipelines", {})
        payload = result.data if hasattr(result, "data") else result
        # FastMCP returns a CallToolResult; .data is the structured payload
        # (.content is the text rendering). Either is fine for the smoke check.
        assert payload is not None
