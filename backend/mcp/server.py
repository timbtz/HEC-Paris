"""FastMCP server exposing the Fingent agentic surface.

Design:

- The MCP server runs in-process with the existing FastAPI app. The
  lifespan context manager opens both the FastAPI lifespan (which
  populates `app.state.store`) and an httpx.AsyncClient bound to the
  app via `ASGITransport`. Tools then call `await client.get(...)` /
  `client.post(...)` against the real routers — zero duplicated logic.

- Tools cover the agent-facing operations: pipelines, runs, ledger,
  reports, period reports, employees, documents, wiki, demo events.

- Resources expose single-entity reads via `fingent://...` URIs, so an
  MCP client (Claude Desktop, etc.) can pin a run/entry/page into the
  conversation context without invoking a tool.

- Prompts are reusable instruction templates the host UI can surface to
  end-users (e.g. "Triage failed run #123").

Transport defaults to stdio. Use `transport="streamable-http"` to expose
over HTTP for remote agents (FastMCP serves this on :8000 by default;
override with `host=` / `port=` on `mcp.run`).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Literal

import httpx
from fastmcp import Context, FastMCP
from pydantic import Field

from ..api.main import app as fastapi_app, lifespan as fastapi_lifespan


# --------------------------------------------------------------------------- #
# Lifespan: open FastAPI app + ASGI httpx client
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def _lifespan(_: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Bring up the FastAPI lifespan (DBs, event-bus reaper) and an
    in-process httpx client bound to the same app."""
    async with fastapi_lifespan(fastapi_app):
        transport = httpx.ASGITransport(app=fastapi_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://fingent",
            timeout=httpx.Timeout(60.0, read=120.0),
        ) as client:
            yield {"http": client}


def _client(ctx: Context) -> httpx.AsyncClient:
    return ctx.request_context.lifespan_context["http"]


async def _get(ctx: Context, path: str, **params: Any) -> Any:
    qp = {k: v for k, v in params.items() if v is not None}
    r = await _client(ctx).get(path, params=qp)
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return {"content_type": r.headers.get("content-type"), "body": r.text}


async def _post(ctx: Context, path: str, json_body: dict[str, Any] | None = None) -> Any:
    r = await _client(ctx).post(path, json=json_body or {})
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- #
# Server instance
# --------------------------------------------------------------------------- #


def build_server() -> FastMCP:
    mcp = FastMCP(
        name="fingent",
        instructions=(
            "Fingent is a YAML-DAG executor over three SQLite databases "
            "(accounting / orchestration / audit). Use the tools to: "
            "trigger pipelines, inspect runs and journal entries, query "
            "the general ledger, read the wiki of accounting policies, "
            "and approve held entries / period reports. Money is integer "
            "cents. Most reads are paginated; pass `limit` and `offset` "
            "to page. Pipeline triggers are async — they return a run_id; "
            "poll get_run to observe progress."
        ),
        lifespan=_lifespan,
    )

    # ----------------------------------------------------------------- #
    # Pipelines & runs
    # ----------------------------------------------------------------- #

    @mcp.tool
    async def list_pipelines(ctx: Context) -> dict:
        """Catalog of every pipeline on disk, with trigger source and node count."""
        return await _get(ctx, "/pipelines")

    @mcp.tool
    async def get_pipeline(
        ctx: Context,
        name: Annotated[str, Field(description="Pipeline name, e.g. 'transaction_booked'.")],
    ) -> dict:
        """Full DAG for one pipeline: nodes, edges, runner, layer index."""
        return await _get(ctx, f"/pipelines/{name}")

    @mcp.tool
    async def run_pipeline(
        ctx: Context,
        name: Annotated[str, Field(description="Pipeline name to trigger.")],
        trigger_payload: Annotated[
            dict[str, Any],
            Field(description="The event payload the pipeline expects (shape varies per pipeline)."),
        ],
        employee_id: Annotated[
            int | None,
            Field(default=None, description="Employee whose budget envelopes / cost ledger this run is charged to."),
        ] = None,
    ) -> dict:
        """Trigger a pipeline. Returns {run_id, stream_url}. Synchronous: blocks until the run terminates."""
        body: dict[str, Any] = {"trigger_payload": trigger_payload}
        if employee_id is not None:
            body["employee_id"] = employee_id
        return await _post(ctx, f"/pipelines/run/{name}", body)

    @mcp.tool
    async def list_runs(
        ctx: Context,
        pipeline_name: str | None = None,
        status: Annotated[
            Literal["running", "completed", "failed"] | None,
            Field(default=None, description="Filter by run status."),
        ] = None,
        from_date: Annotated[str | None, Field(default=None, description="ISO date lower bound on started_at.")] = None,
        to_date: Annotated[str | None, Field(default=None, description="ISO date upper bound on started_at.")] = None,
        limit: Annotated[int, Field(default=50, ge=1, le=200)] = 50,
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
    ) -> dict:
        """Paginated run list. Each item carries cost (micro-USD) and open review count."""
        return await _get(
            ctx, "/runs",
            pipeline_name=pipeline_name,
            status=status,
            **{"from": from_date, "to": to_date},
            limit=limit, offset=offset,
        )

    @mcp.tool
    async def get_run(
        ctx: Context,
        run_id: Annotated[int, Field(description="Pipeline run id.")],
    ) -> dict:
        """Full reconstruction: run row + ordered events + agent decisions."""
        return await _get(ctx, f"/runs/{run_id}")

    # ----------------------------------------------------------------- #
    # Ledger
    # ----------------------------------------------------------------- #

    @mcp.tool
    async def list_journal_entries(
        ctx: Context,
        status: Annotated[
            Literal["draft", "review", "posted", "reversed"] | None,
            Field(default=None, description="Filter by entry status."),
        ] = None,
        limit: Annotated[int, Field(default=50, ge=1, le=200)] = 50,
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
    ) -> dict:
        """Paginated journal entries (newest first), each with line count + total cents."""
        return await _get(ctx, "/journal_entries", status=status, limit=limit, offset=offset)

    @mcp.tool
    async def get_journal_entry_trace(
        ctx: Context,
        entry_id: Annotated[int, Field(description="Journal entry id.")],
    ) -> dict:
        """Full audit drilldown: entry, lines, decision_traces, agent decisions + costs, source run, swan tx, documents."""
        return await _get(ctx, f"/journal_entries/{entry_id}/trace")

    @mcp.tool
    async def approve_journal_entry(
        ctx: Context,
        entry_id: Annotated[int, Field(description="Journal entry id to approve.")],
        approver_id: Annotated[int, Field(description="Employee id of the human approver.")],
    ) -> dict:
        """Flip a held entry from 'review' → 'posted' and stamp approver_id/approved_at on its decision traces. Idempotent."""
        return await _post(ctx, f"/review/{entry_id}/approve", {"approver_id": approver_id})

    @mcp.tool
    async def list_envelopes(
        ctx: Context,
        employee_id: int | None = None,
        period: Annotated[str | None, Field(default=None, description="YYYY-MM period filter.")] = None,
        scope_kind: Annotated[Literal["employee", "team", "company"] | None, Field(default=None)] = None,
    ) -> dict:
        """Budget envelopes (cap, used, remaining) for the employees/company dashboard rings."""
        return await _get(ctx, "/envelopes", employee_id=employee_id, period=period, scope_kind=scope_kind)

    # ----------------------------------------------------------------- #
    # Reports — SQL-only Phase 3 endpoints, dispatched on report_type
    # ----------------------------------------------------------------- #

    @mcp.tool
    async def get_report(
        ctx: Context,
        report_type: Annotated[
            Literal[
                "trial_balance",
                "balance_sheet",
                "income_statement",
                "cashflow",
                "budget_vs_actuals",
                "vat_return",
            ],
            Field(description="Which report to compute."),
        ],
        as_of: Annotated[
            str | None,
            Field(
                default=None,
                description="YYYY-MM-DD. Required for trial_balance, balance_sheet.",
            ),
        ] = None,
        from_date: Annotated[
            str | None,
            Field(default=None, description="YYYY-MM-DD start. Required for income_statement, cashflow."),
        ] = None,
        to_date: Annotated[
            str | None,
            Field(default=None, description="YYYY-MM-DD end. Required for income_statement, cashflow."),
        ] = None,
        period: Annotated[
            str | None,
            Field(default=None, description="YYYY-MM. Required for budget_vs_actuals, vat_return."),
        ] = None,
        basis: Annotated[Literal["cash", "accrual"], Field(default="accrual")] = "accrual",
        employee_id: Annotated[int | None, Field(default=None, description="budget_vs_actuals filter.")] = None,
        category: Annotated[str | None, Field(default=None, description="budget_vs_actuals filter.")] = None,
    ) -> dict:
        """Compute a GL report. Picks the right query params per report_type."""
        params: dict[str, Any] = {}
        if report_type in ("trial_balance", "balance_sheet"):
            if not as_of:
                raise ValueError(f"{report_type} requires as_of (YYYY-MM-DD)")
            params = {"as_of": as_of, "basis": basis}
        elif report_type == "income_statement":
            if not (from_date and to_date):
                raise ValueError("income_statement requires from_date and to_date (YYYY-MM-DD)")
            params = {"from": from_date, "to": to_date, "basis": basis}
        elif report_type == "cashflow":
            if not (from_date and to_date):
                raise ValueError("cashflow requires from_date and to_date (YYYY-MM-DD)")
            params = {"from": from_date, "to": to_date}
        elif report_type == "budget_vs_actuals":
            if not period:
                raise ValueError("budget_vs_actuals requires period (YYYY-MM)")
            params = {"period": period, "employee_id": employee_id, "category": category}
        elif report_type == "vat_return":
            if not period:
                raise ValueError("vat_return requires period (YYYY-MM)")
            params = {"period": period}
        return await _get(ctx, f"/reports/{report_type}", **params)

    # ----------------------------------------------------------------- #
    # Period reports
    # ----------------------------------------------------------------- #

    @mcp.tool
    async def list_period_reports(
        ctx: Context,
        period_code: Annotated[str | None, Field(default=None, description="YYYY-MM period filter.")] = None,
        report_type: Annotated[str | None, Field(default=None, description="e.g. period_close, vat_return, year_end_close.")] = None,
        status: Annotated[Literal["draft", "final", "flagged"] | None, Field(default=None)] = None,
        limit: Annotated[int, Field(default=50, ge=1, le=200)] = 50,
        offset: Annotated[int, Field(default=0, ge=0)] = 0,
    ) -> dict:
        """List period_reports rows. Use approve_period_report to flip 'draft|flagged' → 'final'."""
        return await _get(
            ctx, "/period_reports",
            period_code=period_code,
            type=report_type,
            status=status,
            limit=limit, offset=offset,
        )

    @mcp.tool
    async def get_period_report(
        ctx: Context,
        report_id: Annotated[int, Field(description="period_reports row id.")],
    ) -> dict:
        """Single period_report row including parsed payload_json."""
        return await _get(ctx, f"/period_reports/{report_id}")

    @mcp.tool
    async def get_period_report_artifact(
        ctx: Context,
        report_id: Annotated[int, Field(description="period_reports row id.")],
        format: Annotated[Literal["md", "pdf", "csv"], Field(default="md")] = "md",
    ) -> dict:
        """Fetch the rendered artifact (markdown today, pdf/csv 'coming soon')."""
        return await _get(ctx, f"/period_reports/{report_id}/artifact", format=format)

    @mcp.tool
    async def approve_period_report(
        ctx: Context,
        report_id: Annotated[int, Field(description="period_reports row id to finalize.")],
        approver_id: Annotated[int, Field(description="Employee id of the human approver.")],
    ) -> dict:
        """Flip a draft/flagged period report to 'final'. Idempotent."""
        return await _post(ctx, f"/period_reports/{report_id}/approve", {"approver_id": approver_id})

    # ----------------------------------------------------------------- #
    # Employees & documents
    # ----------------------------------------------------------------- #

    @mcp.tool
    async def list_employees(
        ctx: Context,
        active: Annotated[bool | None, Field(default=None, description="Filter by active flag.")] = None,
    ) -> dict:
        """Employee directory."""
        return await _get(ctx, "/employees", active=active)

    @mcp.tool
    async def get_employee(
        ctx: Context,
        employee_id: Annotated[int, Field(description="Employee id.")],
    ) -> dict:
        """Employee detail + current-month envelopes + 30-day AI spend (micro-USD + call count)."""
        return await _get(ctx, f"/employees/{employee_id}")

    @mcp.tool
    async def get_document(
        ctx: Context,
        document_id: Annotated[int, Field(description="Document id.")],
    ) -> dict:
        """Document row + extracted line items. Use the `fingent://document/{id}` resource for the URI form."""
        return await _get(ctx, f"/documents/{document_id}")

    # ----------------------------------------------------------------- #
    # Wiki — accounting-policy knowledge base
    # ----------------------------------------------------------------- #

    @mcp.tool
    async def list_wiki_pages(ctx: Context) -> dict:
        """List wiki pages with head-revision metadata. Path-ordered (matches what agents see at prompt time)."""
        return await _get(ctx, "/wiki/pages")

    @mcp.tool
    async def get_wiki_page(
        ctx: Context,
        page_id: Annotated[int, Field(description="Wiki page id.")],
        revision_id: Annotated[
            int | None,
            Field(default=None, description="Optional revision id; omit for head."),
        ] = None,
    ) -> dict:
        """Fetch one wiki page (head or pinned revision) with body_md + frontmatter."""
        if revision_id is not None:
            return await _get(ctx, f"/wiki/pages/{page_id}/revisions/{revision_id}")
        return await _get(ctx, f"/wiki/pages/{page_id}")

    @mcp.tool
    async def list_wiki_revisions(
        ctx: Context,
        page_id: Annotated[int, Field(description="Wiki page id.")],
    ) -> dict:
        """Revision history for a page (newest first)."""
        return await _get(ctx, f"/wiki/pages/{page_id}/revisions")

    # ----------------------------------------------------------------- #
    # Demo / replay (Swan seed) — useful for agent walkthroughs
    # ----------------------------------------------------------------- #

    @mcp.tool
    async def list_demo_scenarios(ctx: Context) -> dict:
        """Curated Swan demo scenarios — each carries the next un-fired matching transaction."""
        return await _get(ctx, "/demo/swan/scenarios")

    @mcp.tool
    async def simulate_swan_event(
        ctx: Context,
        tx_id: Annotated[
            int | None,
            Field(default=None, description="Specific swan_transactions.id to fire; omit to advance to the next un-fired."),
        ] = None,
    ) -> dict:
        """Fire a Swan webhook event from the local seed (sets FINGENT_SWAN_LOCAL_REPLAY=1)."""
        body: dict[str, Any] = {}
        if tx_id is not None:
            body["tx_id"] = tx_id
        return await _post(ctx, "/demo/swan/simulate", body)

    # ----------------------------------------------------------------- #
    # Resources — `fingent://...` URIs an MCP client can pin into context
    # ----------------------------------------------------------------- #

    @mcp.resource("fingent://run/{run_id}")
    async def res_run(run_id: int, ctx: Context) -> dict:
        """Pinnable run reconstruction (events + agent decisions)."""
        return await _get(ctx, f"/runs/{run_id}")

    @mcp.resource("fingent://entry/{entry_id}/trace")
    async def res_entry_trace(entry_id: int, ctx: Context) -> dict:
        """Pinnable journal-entry audit trace."""
        return await _get(ctx, f"/journal_entries/{entry_id}/trace")

    @mcp.resource("fingent://employee/{employee_id}")
    async def res_employee(employee_id: int, ctx: Context) -> dict:
        """Pinnable employee detail (envelopes + 30d spend)."""
        return await _get(ctx, f"/employees/{employee_id}")

    @mcp.resource("fingent://document/{document_id}")
    async def res_document(document_id: int, ctx: Context) -> dict:
        """Pinnable document row + line items."""
        return await _get(ctx, f"/documents/{document_id}")

    @mcp.resource("fingent://wiki/{page_id}")
    async def res_wiki(page_id: int, ctx: Context) -> dict:
        """Pinnable wiki page (head revision)."""
        return await _get(ctx, f"/wiki/pages/{page_id}")

    @mcp.resource("fingent://period_report/{report_id}")
    async def res_period_report(report_id: int, ctx: Context) -> dict:
        """Pinnable period_report row + parsed payload."""
        return await _get(ctx, f"/period_reports/{report_id}")

    # ----------------------------------------------------------------- #
    # Prompts — instruction templates the host UI can offer end-users
    # ----------------------------------------------------------------- #

    @mcp.prompt
    def triage_failed_run(run_id: int) -> str:
        """Walk through investigating why a pipeline run failed."""
        return (
            f"A pipeline run #{run_id} has failed. Triage it:\n"
            f"1. Call get_run({run_id}) and read the events list — find the "
            f"first node_failed event and its data.error.\n"
            f"2. If the failure is at an agent node, look up the agent_decisions "
            f"entry for that run and node — confidence + finish_reason often "
            f"explain it.\n"
            f"3. If it is at a tool node, inspect the input snapshot in the "
            f"event payload — usually a missing referent (counterparty, "
            f"document, swan_transaction).\n"
            f"4. Decide: re-run with a corrected payload, queue for human "
            f"review, or surface a wiki gap.\n"
            f"Money is integer cents; do not introduce floats. The fix may "
            f"belong in a tool, an agent prompt, or a wiki page (policy)."
        )

    @mcp.prompt
    def period_close_checklist(period_code: str) -> str:
        """Checklist to run a clean period close for the given YYYY-MM period."""
        return (
            f"Closing period {period_code}. Steps:\n"
            f"1. get_report('trial_balance', as_of=<period end>) — confirm "
            f"balanced.\n"
            f"2. list_journal_entries(status='review') — make sure no held "
            f"entries remain that belong to this period.\n"
            f"3. run_pipeline('period_close', trigger_payload={{'period_code': "
            f"'{period_code}'}}) — produces a draft period_report with "
            f"anomaly flags.\n"
            f"4. get_period_report(<id>) — review payload + anomalies. If "
            f"any flagged anomaly is genuine, resolve it (post correcting "
            f"entry / amend wiki) before approving.\n"
            f"5. approve_period_report(<id>, approver_id=<your id>) — flips "
            f"to 'final'. Period-lock is enforced by gl_poster after this."
        )

    @mcp.prompt
    def expense_coding_guidance() -> str:
        """How an agent should think about coding a new transaction in Fingent."""
        return (
            "Coding a new transaction in Fingent:\n"
            "- Counterparty resolution comes first — deterministic rules "
            "(IBAN/VAT/merchant) win; the AI fallback only fires when no "
            "rule matches and the result writeback creates a future rule.\n"
            "- GL account classification is closed-list against the chart "
            "of accounts; never invent codes. Cite the wiki policy that "
            "supports your pick.\n"
            "- Money is always integer cents in EUR. If you see a float, "
            "stop — you are looking at a parsing bug.\n"
            "- Confidence below the configured floor routes to "
            "review_queue. Don't paper over with overclaimed confidence.\n"
            "- Posts go through gl_poster:post — never write journal_entries "
            "directly. gl_poster blocks posts to closed accounting_periods."
        )

    return mcp


# Module-level instance for `python -m backend.mcp` and importers.
mcp = build_server()


if __name__ == "__main__":  # pragma: no cover
    transport = os.environ.get("FINGENT_MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
