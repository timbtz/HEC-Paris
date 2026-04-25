# Feature: Phase 2 List Endpoints + Phase F Frontend (Vite/React Dashboard)

The following plan should be complete, but its important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils types and models. Import from the right files etc.

## Feature Description

Two backend additions plus the Phase F frontend (`RealMetaPRD §12 Phase F`):

1. **Two reconciliation GET endpoints** in `backend/api/runs.py`:
   - `GET /journal_entries?limit=50&offset=0` — paginated ledger list, newest first.
   - `GET /envelopes?employee_id=<int|null>&period=<YYYY-MM>` — current envelope state for the dashboard rings, with `used_cents` rolled up from `budget_allocations`.
   Both return `{items: [...], total, limit, offset}` envelopes (or just the relevant fields for envelopes), money in integer cents, no auth (internal demo surface).

2. **`CORSMiddleware` install on the FastAPI app** — Vite dev runs at `:5173` and the current `main.py` has no CORS middleware, so without it every browser fetch fails. Permissive in dev, list-of-origins style ready for prod.

3. **Vite + React 18 + TypeScript + Tailwind v4 + Zustand frontend** under a new top-level `frontend/` directory, scaffolded per `RealMetaPRD §12 Phase F` and `Orchestration/Plans/phase-2-swan-document-frontend.md §2.K`. Six components on three tabs (Dashboard | Review | Infra), driven by the existing `/dashboard/stream` SSE plus the two new GETs for hydration.

The front-end is the only remaining Phase 2 deliverable; the two GETs unblock it (otherwise the `Ledger` and `EnvelopeRings` components would have to wait for live SSE traffic to render anything, which is a poor demo experience and drops anything that fired before page-load).

## User Story

As an engineer demoing Agnes to hackathon judges
I want a live dashboard that shows three employees' envelope rings, the live ledger, the trace drawer, and the review queue
So that the wedge query ("Marie spent €X on AI tokens this month, here's why") is *visible* on stage in under five seconds, not just runnable as SQL.

## Problem Statement

The Phase 2 backend ships every API the demo needs **except**:

- Initial-load reads for the live ledger and the envelope rings. The dashboard SSE bus only emits *deltas* — without a `GET /journal_entries?limit=50` and `GET /envelopes?employee_id=...`, opening the page on stage shows nothing until a webhook fires.
- A frontend at all. There is no `frontend/` directory, no Vite config, and `main.py` has no `CORSMiddleware`, so a browser at `http://localhost:5173` cannot call any backend endpoint.

The two are coupled: the GET endpoints are useless without the frontend, and the frontend cannot hydrate without them.

## Solution Statement

Add the two GETs first (~30 min), then add `CORSMiddleware` to `main.py` (~5 min), then build the frontend with the existing patterns in `runs.py` and the existing event payloads from `event_bus.py`, `gl_poster.py`, `budget_envelope.py`, and `review_queue.py`. Treat the frontend as a thin presentation layer over: three REST GETs (`/journal_entries`, `/envelopes`, `/review`) for hydration + one SSE stream (`/dashboard/stream`) for deltas + one POST (`/documents/upload`) and one POST (`/review/{id}/approve`) for write actions + one per-run SSE (`/runs/{id}/stream`) for the upload progress overlay.

## Feature Metadata

**Feature Type**: New Capability (frontend) + small Enhancement (two reconciliation GETs + CORS)
**Estimated Complexity**: Medium (~30 min backend + ~5h frontend per `RealMetaPRD §12 Phase F`)
**Primary Systems Affected**:
- Backend API: `backend/api/runs.py` (two new GETs), `backend/api/main.py` (CORS), one new test file.
- Frontend: new top-level `frontend/` with Vite + React 18 + TS + Tailwind v4 + Zustand + Motion (rebranded Framer Motion).
**Dependencies**:
- Backend: no new pip deps. The two GETs reuse aiosqlite, Pydantic v2, and the existing `_row_to_dict`/`_rows_to_dicts` helpers.
- Frontend: `react@18`, `react-dom@18`, `typescript`, `vite`, `@vitejs/plugin-react`, `tailwindcss@4`, `@tailwindcss/vite`, `zustand@5`, `motion` (rebranded `framer-motion`), `vite-tsconfig-paths` (dev). No router (single-page tabbed layout, `useState` is enough). No fetch/data-lib (raw `fetch` + Zustand).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE FILES BEFORE IMPLEMENTING!

Read before touching the backend:

- **`backend/api/runs.py`** (full file, ~330 lines)
  - `get_run` (lines 86–116) — canonical aiosqlite read pattern: `store.<db>.execute(sql, params)`, `await cur.fetchall()`, `await cur.close()`, `_rows_to_dicts(rows)`. **Mirror this exactly for the two new GETs.**
  - `get_entry_trace` (lines 166–268) — multi-DB read with in-Python merge; shows that money columns stay as `int` cents (no float coercion).
  - `_row_to_dict` / `_rows_to_dicts` (lines 38–45) — already exported helpers; reuse, don't reimplement.
  - `approve_entry` (lines 275–326) — write pattern via `write_tx`; **the new GETs do NOT write, so they don't need this**, but if a question comes up about the lock pattern, this is the reference.

- **`backend/api/main.py`** (full file, ~70 lines)
  - Lifespan + router mounts (lines 54–60). The two new GETs go on the existing `runs_router` (no prefix mount). `CORSMiddleware` install goes between `app = FastAPI(...)` and the `app.include_router(...)` calls.

- **`backend/api/dashboard.py`** (full file, ~65 lines)
  - The dashboard SSE generator. Heartbeat is **15s** (`_HEARTBEAT_INTERVAL = 15.0` — confirm exact constant name before mirroring), framing is hand-rolled `data: {json}\n\n` with no `id:` or `event:` lines. The frontend's EventSource consumes this format via `onmessage` (not `addEventListener('event_type', …)`).

- **`backend/orchestration/event_bus.py`** (full file)
  - `subscribe_dashboard()` / `publish_event_dashboard()` — already wired. The new GETs do not interact with it.

- **`backend/orchestration/store/schema/accounting.sql`** — reference column lists, especially `journal_entries`, `journal_lines`, `budget_envelopes`, `budget_allocations`, `documents`. Read alongside `backend/orchestration/store/migrations/accounting/0007_budget_envelopes.py` (envelope seed: 60 rows = 4 scopes × 5 categories × 3 periods).

- **`backend/orchestration/tools/gl_poster.py`** lines 142–154 — exact dashboard event payload for `ledger.entry_posted`. The frontend store applies this verbatim:
  ```python
  {"event_type": "ledger.entry_posted", "ts": "...", "data": {"entry_id", "basis", "entry_date", "total_cents", "lines", "run_id", "employee_id"}}
  ```

- **`backend/orchestration/tools/budget_envelope.py`** lines 120–196 — three event payloads:
  - `envelope.decremented` (the happy path; carries `envelope_id, employee_id, category, period, used_cents, cap_cents, soft_threshold_pct, ledger_entry_id`)
  - `envelope.skipped` (uncategorized counterparty)
  - `envelope.no_envelope` (no matching envelope row — the dashboard treats this as a soft warning).

- **`backend/orchestration/tools/review_queue.py`** lines 77–87 — `review.enqueued` payload.

- **`backend/api/runs.py`** lines 317–324 — `approve_entry` publishes a *second-style* `ledger.entry_posted` with shape `{event_type, entry_id, approver_id, approved_at}` (note: this one is *not* nested under a `data` key — it's a flatter shape than the gl_poster one). The frontend ledger reducer must handle both.

- **`backend/tests/test_runs_api.py`** — fixture/seed pattern for new GET tests. Especially `_seed_entry_with_traces` (lines 46–124) showing how to fabricate `chart_of_accounts`, `journal_entries`, `journal_lines`, `decision_traces` rows for tests.

- **`backend/tests/conftest.py`** — `store` and `fake_anthropic` fixtures.

Read before touching the frontend:

- **`Orchestration/PRDs/RealMetaPRD.md`** §12 Phase F (lines 1657–1670) — the seven frontend deliverables. Treat this as the spec.
- **`Orchestration/Plans/phase-2-swan-document-frontend.md`** §2.K (lines 404–417) — the per-component checklist already authored.
- **`Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md`** — helpful for understanding what events mean (run lifecycle), so the per-run progress overlay shows readable node names.

### New Files to Create

**Backend (test only — both GETs go into existing `backend/api/runs.py`):**
- `backend/tests/test_list_endpoints.py` — tests for the two new GETs.

**Frontend (entire tree):**
```
frontend/
  package.json
  vite.config.ts
  tsconfig.json
  tsconfig.app.json
  tsconfig.node.json
  index.html
  .env.development                      # VITE_API_BASE= (empty → use Vite proxy)
  .env.production                       # VITE_API_BASE=https://...   (placeholder)
  src/
    main.tsx
    App.tsx
    index.css                           # one line: `@import "tailwindcss";`
    types/
      index.ts                          # JournalEntry, Envelope, ReviewItem, Run, DashboardEvent (discriminated union)
    api.ts                              # typed fetch wrappers for all backend routes
    store/
      dashboard.ts                      # Zustand store: ledger, envelopes, review, connected
      runProgress.ts                    # Zustand store for the per-run upload overlay
    hooks/
      useSSE.ts                         # generic EventSource hook with StrictMode-safe cleanup
    components/
      Tabs.tsx                          # 3-tab layout (Dashboard | Review | Infra)
      Ledger.tsx                        # animated ledger rows (motion AnimatePresence)
      EnvelopeRings.tsx                 # SVG stroke-dasharray rings, employee × category grid
      EnvelopeRing.tsx                  # single ring component
      UploadZone.tsx                    # drag-drop PDF + per-run progress overlay
      RunProgressOverlay.tsx            # node-by-node SSE progress modal
      TraceDrawer.tsx                   # opens on ledger row click; calls /journal_entries/{id}/trace
      ReviewQueue.tsx                   # list + approve button
      InfraTab.tsx                      # recent runs, recent events, three DB sizes
      Skeleton.tsx                      # loading placeholder
      formatters.ts                     # cents → euros, ISO date → "DD MMM"
```

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING!

#### Reference docs in this repo (provided by user; partially inaccurate)

- **`Dev orchestration/tech framework/REF-FASTAPI-BACKEND.md`** — written for the HappyRobot project. **USE:** lifespan pattern (lines 31–82), Pydantic v2 `@field_validator`+`@classmethod` shape (276–360), CORS gotcha (line 84). **IGNORE:** `compile_worker` / `pipeline_scheduler` / wiki / drafts / approval / `state.json` / systemd / `subprocess.run` (lines 88–271, 406–800) — irrelevant to Agnes. Agnes uses `app.state.store` (multi-DB), not `app.state.db`.

- **`Dev orchestration/tech framework/REF-SSE-STREAMING-FASTAPI.md`** — also HappyRobot, **but the bus design (Section 2) is what `event_bus.py` already implements**. **USE:** failure-modes table (Section 7), CORS-for-SSE notes (Section 6 — `Last-Event-ID` only matters if reconnect dedup is added; we are not adding it). **IGNORE:** Section 3 (`write_event` hook — Agnes already publishes), Section 4 endpoint code (Agnes uses **15s heartbeats not 30s**, **no `id:` lines, no `event:` lines, no replay, no Last-Event-ID**), Section 5 Alpine.js (we use React), Section 8 HappyRobot agent names. **DO NOT** retrofit the doc's `id:`/`event:` framing — Agnes's wire format is locked in by `test_dashboard_sse.py`.

- **`Dev orchestration/_exports_for_b2b_accounting/`** — six markdown files; relevant ones:
  - `01_ORCHESTRATION_REFERENCE.md` §1c — executor event sequence (`pipeline_started → node_started → node_completed → ... → pipeline_completed`). The per-run progress overlay maps `node_id` → human label using the codebase node ids (`fetch_transaction`, `resolve_counterparty`, `classify_gl`, `build_entry`, `post_entry`, `decrement_envelope`, `gate_confidence`, `enqueue_review`, etc.). Read the actual pipeline YAML files in `backend/orchestration/pipelines/` to copy real node ids — don't make them up.
  - `04_AGENT_PATTERNS.md` §3 — confidence floor / decision-trace pattern. Useful when rendering the trace drawer.

#### External documentation (verified, late 2025 / early 2026)

**FastAPI / Python backend:**
- [FastAPI — Server Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/) — official tutorial. Confirms hand-rolled `StreamingResponse` is still recommended; FastAPI 0.135+ ships `EventSourceResponse` but for an existing project it's a no-op refactor.
  - Why: confirms current best practice; we keep the hand-rolled framing.
- [FastAPI — Query Parameters](https://fastapi.tiangolo.com/tutorial/query-params-str-validations/) — `Annotated[int, Query(ge=1, le=200)]` is the canonical 2026 idiom for bounded query params.
  - Why: pagination params on `GET /journal_entries`.
- [aiosqlite — Threading and Connection Sharing](https://github.com/omnilib/aiosqlite#threadsafety) — confirms a single long-lived connection per DB shared across requests is safe; aiosqlite serializes via a dedicated worker thread.
  - Why: justifies the existing `app.state.store.<db>` pattern; the new GETs use it without locks (reads only).

**Frontend stack:**
- [Vite — `react-ts` template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) — current `npm create vite@latest -- --template react-ts` ships React 18.3 + TS 5.6+ in early 2026.
  - Why: scaffold command.
- [Tailwind CSS — Installing with Vite](https://tailwindcss.com/docs/installation/using-vite) — Tailwind v4 setup with `@tailwindcss/vite` plugin. **No `tailwind.config.js`, no `postcss.config.js`, no `@tailwind base/components/utilities` directives.** One CSS line: `@import "tailwindcss";`.
  - Why: Tailwind v4 is current; v3 is now legacy.
- [Tailwind v4.0 release notes](https://tailwindcss.com/blog/tailwindcss-v4) — content auto-detection, no config file.
  - Gotcha: dynamic class strings (`bg-[${color}]`) break HMR cache invalidation (tailwindlabs/tailwindcss#17260) — **use static class lookups**.
- [Vite — Server Options (proxy)](https://vite.dev/config/server-options) — `server.proxy` config, exact object shape.
  - Why: the frontend dev server must proxy every backend route; the relevant subset is listed in Task 8 below.
- [MDN — EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource) — auto-reconnect, `onmessage` vs `addEventListener`. Agnes's wire format (no `event:` line) means **all messages dispatch as type `"message"` and use `onmessage`**.
- [MDN — AbortSignal.timeout()](https://developer.mozilla.org/en-US/docs/Web/API/AbortSignal/timeout_static) — supported in modern browsers; used for upload timeout.
- [Zustand v5 docs](https://github.com/pmndrs/zustand) — v5 dropped `equalityFn` arg; for shallow-equal, import `useShallow` from `zustand/react/shallow`.
  - Why: store API change vs older blog posts.
- [Motion (formerly Framer Motion)](https://motion.dev) — v12+ rebranded as `motion`, package import `motion/react`. Still the de-facto choice in 2026.
- [React 19 Upgrade Guide](https://react.dev/blog/2024/04/25/react-19-upgrade-guide) — context only; **we pick React 18.3 for stability**, since Zustand 5's `useSyncExternalStore` shim has friction with React 19's renamed internals (facebook/react#29854).

### Patterns to Follow

**Backend: aiosqlite read pattern** (mirror `runs.py:87–116`):
```python
@router.get("/journal_entries")
async def list_journal_entries(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.accounting.execute(
        "SELECT je.id, je.basis, je.entry_date, je.description, je.status, "
        "       je.source_pipeline, je.source_run_id, je.created_at, "
        "       COALESCE(SUM(jl.debit_cents), 0) AS total_cents, "
        "       COUNT(jl.id) AS line_count "
        "FROM journal_entries je "
        "LEFT JOIN journal_lines jl ON jl.entry_id = je.id "
        "GROUP BY je.id "
        "ORDER BY je.id DESC "
        "LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cur.fetchall()
    await cur.close()
    cur = await store.accounting.execute("SELECT COUNT(*) FROM journal_entries")
    total = (await cur.fetchone())[0]
    await cur.close()
    return {"items": _rows_to_dicts(rows), "total": total, "limit": limit, "offset": offset}
```

**Backend: money in integer cents** (no float coercion). Confirm by reading the `get_run` and `get_entry_trace` response shapes — every cents column stays as `int`.

**Backend: response envelope** — `{items, total, limit, offset}` for paginated lists; bare `{items}` for non-paginated lists like envelopes (because we always return all envelopes for one employee×period combo, ≤ 5 rings).

**Frontend: store reducer** (Zustand-canonical, mirrors the discriminated-union pattern):
```ts
apply: (ev) => set((s) => {
  switch (ev.event_type) {
    case 'ledger.entry_posted':
      // gl_poster.py shape: { event_type, ts, data: { entry_id, basis, entry_date, total_cents, lines, run_id, employee_id } }
      // approve_entry shape: { event_type, entry_id, approver_id, approved_at } (flat — no `data` key)
      const e = 'data' in ev ? ev.data : ev
      if (s.ledger.some(x => x.id === e.entry_id)) return s
      return { ledger: [normalize(e), ...s.ledger].slice(0, 500) }
    case 'envelope.decremented':
      return { envelopes: { ...s.envelopes, [key(ev.data)]: ev.data } }
    case 'envelope.skipped': /* show toast, no state change */ return s
    case 'envelope.no_envelope': /* show toast */ return s
    case 'review.enqueued': return { reviewIds: [...s.reviewIds, ev.data.entry_id] }
    default: return s
  }
})
```
**Note on event names**: Agnes uses **dotted** event names (`ledger.entry_posted`, `envelope.decremented`), not underscored. Match exactly — TypeScript discriminated unions are case-sensitive.

**Frontend: EventSource hook with StrictMode-safe cleanup**:
```ts
export function useSSE<T>(url: string, onEvent: (e: T) => void, onStatus?: (open: boolean) => void) {
  const onEventRef = useRef(onEvent); onEventRef.current = onEvent
  const onStatusRef = useRef(onStatus); onStatusRef.current = onStatus
  useEffect(() => {
    const es = new EventSource(url)
    es.onopen = () => onStatusRef.current?.(true)
    es.onmessage = (m) => { try { onEventRef.current(JSON.parse(m.data) as T) } catch { /* ignore */ } }
    es.onerror = () => onStatusRef.current?.(false)
    return () => { es.close() }   // critical: StrictMode double-mounts effects in dev
  }, [url])
}
```

**Frontend: Tailwind static class lookups** (avoid `bg-[${color}]` — breaks v4 HMR):
```ts
const tone = pct > 1 ? 'text-rose-500' : pct >= 0.8 ? 'text-amber-500' : 'text-emerald-500'
```

---

## IMPLEMENTATION PLAN

### Phase 1: Backend Foundations (~30 min)

Add the two GET endpoints, install CORS middleware, write tests.

**Tasks:**
- Add `GET /journal_entries` and `GET /envelopes` to `backend/api/runs.py`.
- Install `CORSMiddleware` in `backend/api/main.py` with origin `http://localhost:5173`.
- Write `backend/tests/test_list_endpoints.py` with seeded data.

### Phase 2: Frontend Scaffold (~45 min)

Bootstrap Vite project, install deps, configure Tailwind v4, configure Vite proxy, set up TypeScript paths, write `types/index.ts` and `api.ts`.

**Tasks:**
- `npm create vite@latest frontend -- --template react-ts` (then `cd frontend && npm install`).
- Install runtime deps: `npm install zustand motion`.
- Install Tailwind: `npm install tailwindcss @tailwindcss/vite`.
- Install dev deps: `npm install -D vite-tsconfig-paths`.
- Edit `vite.config.ts` to register `tailwindcss()`, `tsconfigPaths()`, and the proxy table.
- Replace `src/index.css` with `@import "tailwindcss";` (one line).
- Add `@/*` path alias to `tsconfig.app.json`.
- Write `src/types/index.ts` with TS interfaces for every API response shape (read the backend response shapes from the AUDIT report below).
- Write `src/api.ts` with typed fetch wrappers.

### Phase 3: Core Components (~3h)

Build the six Phase F components plus the SSE hook and the two Zustand stores.

**Tasks:**
- `useSSE.ts` (~30 lines, generic).
- `store/dashboard.ts` (Zustand: ledger, envelopes, review, connected, hydrate, apply).
- `store/runProgress.ts` (per-run node states for upload overlay).
- `Ledger.tsx` (motion `AnimatePresence` rows, hydrates from `api.getJournalEntries()`, applies live deltas).
- `EnvelopeRings.tsx` + `EnvelopeRing.tsx` (SVG stroke-dasharray, employee×category grid, hydrates from `api.getEnvelopes()`).
- `UploadZone.tsx` (HTML5 drag-drop + `RunProgressOverlay.tsx`).
- `TraceDrawer.tsx` (calls `api.getEntryTrace(id)`, renders chain).
- `ReviewQueue.tsx` (list + approve button calling `api.approveEntry(id, approverId)`).
- `InfraTab.tsx` (lists recent runs from `api.listRuns()` if implemented, else just three DB sizes from a future `/healthz` extension — for hackathon, hardcode the section labels and pull what's available).
- `Tabs.tsx` (`useState<'dashboard'|'review'|'infra'>`).
- `App.tsx` mounts the tabs and the global SSE subscription.
- `Skeleton.tsx` (loading placeholder for first render).
- `formatters.ts` (`centsToEuros`, `formatDate`).

### Phase 4: Wiring + Manual Rehearsal (~1.5h)

Run the dev stack end-to-end against real fixtures from `tests/`. Verify the demo path twice in a row. Tune visuals for 3m readability.

**Tasks:**
- Boot backend (`uvicorn --workers 1 --port 8000`) and frontend (`npm run dev` on `:5173`).
- Walk RealMetaPRD §11 demo script in the browser.
- Drop a Swan webhook fixture via `curl`, confirm row appears in <5s.
- Click ledger row, confirm trace drawer opens with full chain.
- Drag-drop a PDF (e.g. `tests/fixtures/anthropic_invoice.pdf` if it exists, else any PDF), confirm progress overlay shows node-by-node, accrual entry posts within ~10s.
- Drop a `Transaction.Released` for the same id; confirm reversal animates in.
- Bump ring/font sizes if 3m read fails.

### Phase 5: Validation

See [Validation Commands](#validation-commands) below. Backend tests after Phase 1; frontend typecheck after each phase; manual rehearsal twice after Phase 4.

---

## STEP-BY-STEP TASKS

Execute every task in order, top to bottom. Each task is atomic and independently testable.

### Task Format Guidelines

Use information-dense keywords: **CREATE** / **UPDATE** / **ADD** / **REMOVE** / **REFACTOR** / **MIRROR**.

---

### BACKEND TRACK

### Task 1 — UPDATE `backend/api/runs.py` to add `GET /journal_entries`

- **IMPLEMENT**:
  - Add this route handler at the end of the file (before `__all__` if any):
    ```python
    from fastapi import Query
    from typing import Annotated

    @router.get("/journal_entries")
    async def list_journal_entries(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        status_filter: Annotated[str | None, Query(alias="status")] = None,
    ) -> dict[str, Any]:
        store = request.app.state.store
        where = "WHERE je.status = ?" if status_filter else ""
        params: tuple = (status_filter,) if status_filter else ()
        cur = await store.accounting.execute(
            f"SELECT je.id, je.basis, je.entry_date, je.description, je.status, "
            f"       je.source_pipeline, je.source_run_id, je.accrual_link_id, je.reversal_of_id, je.created_at, "
            f"       COALESCE(SUM(jl.debit_cents), 0) AS total_cents, "
            f"       COUNT(jl.id) AS line_count "
            f"FROM journal_entries je "
            f"LEFT JOIN journal_lines jl ON jl.entry_id = je.id "
            f"{where} "
            f"GROUP BY je.id "
            f"ORDER BY je.id DESC "
            f"LIMIT ? OFFSET ?",
            params + (limit, offset),
        )
        rows = await cur.fetchall()
        await cur.close()
        cur = await store.accounting.execute(
            f"SELECT COUNT(*) FROM journal_entries je {where}", params,
        )
        total_row = await cur.fetchone()
        await cur.close()
        total = int(total_row[0]) if total_row else 0
        return {"items": _rows_to_dicts(rows), "total": total, "limit": limit, "offset": offset}
    ```
  - Each `items[i]` carries: `id, basis, entry_date, description, status, source_pipeline, source_run_id, accrual_link_id, reversal_of_id, created_at, total_cents, line_count`. The `total_cents` is the sum of debit lines (the natural "entry total" since debits = credits per invariant 1).
- **PATTERN**: `runs.py:87–116` (`get_run`); `runs.py:38–45` (`_rows_to_dicts`).
- **IMPORTS**: Add `from typing import Annotated` and `from fastapi import Query` at the top of `runs.py` (check if already present; do not duplicate).
- **GOTCHA**: `LEFT JOIN ... GROUP BY je.id` works because SQLite is permissive about non-aggregated columns when there's no ambiguity (every `je.*` column is functionally dependent on `je.id`). If this triggers a strict-SQL warning later, switch to a subquery: `(SELECT SUM(debit_cents) FROM journal_lines WHERE entry_id = je.id) AS total_cents`. Don't switch to multiple round-trips per row; the JOIN is correct.
- **GOTCHA**: Money stays as `int`. Don't divide by 100 here. Frontend formats.
- **VALIDATE**: Add a test in Task 3; run `python -m pytest backend/tests/test_list_endpoints.py::test_list_journal_entries -v`.

### Task 2 — UPDATE `backend/api/runs.py` to add `GET /envelopes`

- **IMPLEMENT**:
  - Add this route handler after the previous:
    ```python
    @router.get("/envelopes")
    async def list_envelopes(
        request: Request,
        employee_id: Annotated[int | None, Query()] = None,
        period: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
        scope_kind: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        store = request.app.state.store
        clauses: list[str] = []
        params: list[Any] = []
        if employee_id is not None:
            clauses.append("(be.scope_kind = 'employee' AND be.scope_id = ?)")
            params.append(employee_id)
        elif scope_kind == "company":
            clauses.append("be.scope_kind = 'company'")
        if period is not None:
            clauses.append("be.period = ?")
            params.append(period)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = await store.accounting.execute(
            f"SELECT be.id, be.scope_kind, be.scope_id, be.category, be.period, "
            f"       be.cap_cents, be.soft_threshold_pct, "
            f"       COALESCE(SUM(ba.amount_cents), 0) AS used_cents, "
            f"       COUNT(ba.id) AS allocation_count "
            f"FROM budget_envelopes be "
            f"LEFT JOIN budget_allocations ba ON ba.envelope_id = be.id "
            f"{where} "
            f"GROUP BY be.id "
            f"ORDER BY be.scope_kind, be.scope_id, be.category",
            tuple(params),
        )
        rows = await cur.fetchall()
        await cur.close()
        return {"items": _rows_to_dicts(rows)}
    ```
  - Each `items[i]` carries: `id, scope_kind, scope_id, category, period, cap_cents, soft_threshold_pct, used_cents, allocation_count`. `used_cents` is the rolled-up sum of `budget_allocations.amount_cents` (which can be negative for reversals — natural net usage).
- **PATTERN**: Same as Task 1. The `scope_kind = 'employee' AND scope_id = ?` is the canonical envelope-lookup pattern from `tools/budget_envelope.py`.
- **IMPORTS**: Already added in Task 1.
- **GOTCHA**: Period validation via Pydantic regex — passing `period=2026-99` returns 422 (good). Frontend must format periods as `YYYY-MM`.
- **GOTCHA**: When `employee_id` is given, we filter to `scope_kind='employee'` only. To also pull the `company` envelope alongside, frontend issues a second call with `scope_kind=company`. Don't try to UNION here — keeps the endpoint single-purpose.
- **GOTCHA**: `budget_allocations.amount_cents` may be negative (compensation pipeline writes negative allocations). `SUM` returns the net — exactly what the dashboard ring needs.
- **VALIDATE**: Test in Task 3.

### Task 3 — CREATE `backend/tests/test_list_endpoints.py`

- **IMPLEMENT**:
  - Mirror the fixture style from `backend/tests/test_runs_api.py`:
    ```python
    import pytest
    import pytest_asyncio
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport

    from backend.api.runs import router as runs_router
    from backend.orchestration.store.writes import write_tx


    @pytest_asyncio.fixture
    async def app(store):
        a = FastAPI()
        a.state.store = store
        a.include_router(runs_router)
        return a


    @pytest_asyncio.fixture
    async def client(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


    async def _seed_basic_entry(store, *, entry_date="2026-04-15", status="posted",
                                  total_debits=(("626100", 50000),), total_credits=(("401", 50000),)):
        async with write_tx(store.accounting, store.accounting_lock) as conn:
            cur = await conn.execute(
                "INSERT INTO journal_entries (basis, entry_date, source_pipeline, source_run_id, status) "
                "VALUES ('cash', ?, 'transaction_booked', 1, ?)",
                (entry_date, status),
            )
            entry_id = cur.lastrowid
            for code, cents in total_debits:
                await conn.execute(
                    "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
                    "VALUES (?, ?, ?, 0)", (entry_id, code, cents))
            for code, cents in total_credits:
                await conn.execute(
                    "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
                    "VALUES (?, ?, 0, ?)", (entry_id, code, cents))
        return entry_id


    @pytest.mark.asyncio
    async def test_list_journal_entries_default_pagination(store, client):
        for i in range(60):
            await _seed_basic_entry(store, entry_date=f"2026-04-{(i % 28) + 1:02d}")
        resp = await client.get("/journal_entries")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert body["total"] == 60
        assert len(body["items"]) == 50
        # newest-first: ids descend
        ids = [item["id"] for item in body["items"]]
        assert ids == sorted(ids, reverse=True)
        # money is integer cents
        for item in body["items"]:
            assert isinstance(item["total_cents"], int)
            assert item["total_cents"] == 50000
            assert item["line_count"] == 2


    @pytest.mark.asyncio
    async def test_list_journal_entries_offset_paging(store, client):
        for _ in range(15):
            await _seed_basic_entry(store)
        resp1 = await client.get("/journal_entries?limit=10&offset=0")
        resp2 = await client.get("/journal_entries?limit=10&offset=10")
        assert resp1.status_code == resp2.status_code == 200
        ids1 = [i["id"] for i in resp1.json()["items"]]
        ids2 = [i["id"] for i in resp2.json()["items"]]
        assert set(ids1).isdisjoint(set(ids2))
        assert resp2.json()["total"] == 15


    @pytest.mark.asyncio
    async def test_list_journal_entries_status_filter(store, client):
        await _seed_basic_entry(store, status="posted")
        await _seed_basic_entry(store, status="review")
        resp = await client.get("/journal_entries?status=review")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert all(i["status"] == "review" for i in body["items"])


    @pytest.mark.asyncio
    async def test_list_journal_entries_invalid_limit(client):
        resp = await client.get("/journal_entries?limit=999")
        assert resp.status_code == 422


    @pytest.mark.asyncio
    async def test_list_envelopes_employee_filter(store, client):
        # The seed migration 0007 already inserts envelopes for employee_id=1,2,3 + company,
        # for periods 2026-02, 2026-03, 2026-04, across 5 categories.
        resp = await client.get("/envelopes?employee_id=1&period=2026-04")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 5  # 5 categories
        for item in items:
            assert item["scope_kind"] == "employee"
            assert item["scope_id"] == 1
            assert item["period"] == "2026-04"
            assert isinstance(item["cap_cents"], int)
            assert isinstance(item["used_cents"], int)
            assert item["used_cents"] == 0  # no allocations seeded


    @pytest.mark.asyncio
    async def test_list_envelopes_used_cents_rolled_up(store, client):
        # Pick the ai_tokens envelope for employee 1, period 2026-04, then insert a journal entry
        # + a budget_allocations row.
        async with write_tx(store.accounting, store.accounting_lock) as conn:
            cur = await conn.execute(
                "SELECT id FROM budget_envelopes WHERE scope_kind='employee' AND scope_id=1 "
                "AND category='ai_tokens' AND period='2026-04'")
            envelope_id = (await cur.fetchone())[0]
            cur = await conn.execute(
                "INSERT INTO journal_entries (basis, entry_date, source_pipeline, source_run_id, status) "
                "VALUES ('cash','2026-04-10','transaction_booked',1,'posted')")
            entry_id = cur.lastrowid
            cur = await conn.execute(
                "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
                "VALUES (?, '626100', 5000, 0)", (entry_id,))
            line_id = cur.lastrowid
            await conn.execute(
                "INSERT INTO budget_allocations (envelope_id, line_id, amount_cents) VALUES (?, ?, 5000)",
                (envelope_id, line_id))
        resp = await client.get("/envelopes?employee_id=1&period=2026-04")
        items = {i["category"]: i for i in resp.json()["items"]}
        assert items["ai_tokens"]["used_cents"] == 5000


    @pytest.mark.asyncio
    async def test_list_envelopes_handles_negative_allocation_for_reversal(store, client):
        # Net usage = 0 after a reversal allocation
        async with write_tx(store.accounting, store.accounting_lock) as conn:
            cur = await conn.execute(
                "SELECT id FROM budget_envelopes WHERE scope_kind='employee' AND scope_id=2 "
                "AND category='food' AND period='2026-04'")
            envelope_id = (await cur.fetchone())[0]
            cur = await conn.execute(
                "INSERT INTO journal_entries (basis, entry_date, source_pipeline, source_run_id, status) "
                "VALUES ('cash','2026-04-10','transaction_booked',1,'posted')")
            e1 = cur.lastrowid
            cur = await conn.execute(
                "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
                "VALUES (?, '6257', 1500, 0)", (e1,))
            l1 = cur.lastrowid
            await conn.execute(
                "INSERT INTO budget_allocations (envelope_id, line_id, amount_cents) VALUES (?, ?, 1500)",
                (envelope_id, l1))
            # reversal: positive line + negative allocation
            cur = await conn.execute(
                "INSERT INTO journal_entries (basis, entry_date, source_pipeline, source_run_id, status, reversal_of_id) "
                "VALUES ('cash','2026-04-11','transaction_released',2,'posted',?)", (e1,))
            e2 = cur.lastrowid
            cur = await conn.execute(
                "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
                "VALUES (?, '6257', 0, 1500)", (e2,))
            l2 = cur.lastrowid
            await conn.execute(
                "INSERT INTO budget_allocations (envelope_id, line_id, amount_cents) VALUES (?, ?, -1500)",
                (envelope_id, l2))
        resp = await client.get("/envelopes?employee_id=2&period=2026-04")
        items = {i["category"]: i for i in resp.json()["items"]}
        assert items["food"]["used_cents"] == 0
    ```
- **PATTERN**: `backend/tests/test_runs_api.py`, especially the `app` and `client` fixtures.
- **IMPORTS**: `pytest`, `pytest_asyncio`, `FastAPI`, `AsyncClient`, `ASGITransport`, `runs_router`, `write_tx`. The `store` fixture comes from `conftest.py`.
- **GOTCHA**: `pytest.ini` has `asyncio_mode = auto` and a 15s `timeout`. These tests are <1s each. Don't add `@pytest.mark.asyncio` if `auto` mode is in effect — but `test_runs_api.py` uses it explicitly, so do too.
- **GOTCHA**: The `store` fixture rebuilds the schema with all migrations applied (per CLAUDE.md), so the 60 envelope rows + 200 swan transactions are already there when the fixture yields. Do not seed envelopes by hand.
- **VALIDATE**: `python -m pytest backend/tests/test_list_endpoints.py -v` — all 7 tests pass.

### Task 4 — UPDATE `backend/api/main.py` to install `CORSMiddleware`

- **IMPLEMENT**:
  - Add this between `app = FastAPI(...)` and the first `app.include_router(...)` call:
    ```python
    from fastapi.middleware.cors import CORSMiddleware

    _ALLOWED_ORIGINS = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Accept"],
    )
    ```
- **PATTERN**: Reference `Dev orchestration/tech framework/REF-FASTAPI-BACKEND.md` lines 68–82 (CORS setup), but trim to a tight, single-origin list. Production hardening is out of scope.
- **IMPORTS**: `from fastapi.middleware.cors import CORSMiddleware`.
- **GOTCHA**: `allow_origins=["*"]` plus `allow_credentials=True` would crash. Do not set both. We list origins explicitly.
- **GOTCHA**: `CORSMiddleware` must be added **before** `app.include_router(...)` for the middleware to wrap all routes.
- **GOTCHA**: `EventSource` does not preflight, so the response to `GET /dashboard/stream` itself must carry `Access-Control-Allow-Origin`. Adding the middleware here covers it.
- **VALIDATE**:
  ```bash
  uvicorn backend.api.main:app --workers 1 --port 8000 &
  curl -i -H 'Origin: http://localhost:5173' http://localhost:8000/healthz | grep -i 'access-control-allow-origin'
  # Expected: access-control-allow-origin: http://localhost:5173
  kill %1
  ```

### Task 5 — UPDATE `backend/tests/test_runs_api.py` (if applicable) — confirm CORS doesn't regress

- **IMPLEMENT**: Run the existing test suite once after Task 4. CORSMiddleware should have zero impact on integration tests because they use `ASGITransport` without an `Origin` header. If a test fails, the middleware was wired wrong.
- **VALIDATE**: `python -m pytest backend/tests/ -q` returns clean.

---

### FRONTEND TRACK

### Task 6 — CREATE `frontend/` via Vite scaffold

- **IMPLEMENT**:
  ```bash
  cd "/home/developer/Projects/HEC Paris"
  npm create vite@latest frontend -- --template react-ts
  cd frontend
  npm install
  ```
  Expect this tree (from `create-vite` v5+/v6+ as of 2026):
  ```
  frontend/
    .gitignore
    eslint.config.js
    index.html
    package.json
    public/vite.svg
    README.md
    src/
      App.css      ← will be deleted in Task 9
      App.tsx
      assets/react.svg
      index.css    ← will be replaced in Task 9
      main.tsx
      vite-env.d.ts
    tsconfig.app.json
    tsconfig.json
    tsconfig.node.json
    vite.config.ts
  ```
- **GOTCHA**: If the scaffold installs React 19 (Vite 6+ default), downgrade to React 18.3 to avoid the Zustand v5 + React 19 internals friction (facebook/react#29854):
  ```bash
  npm install react@^18.3.0 react-dom@^18.3.0
  npm install -D @types/react@^18.3.0 @types/react-dom@^18.3.0
  ```
- **GOTCHA**: Update `frontend/.gitignore` to ensure `node_modules/`, `dist/`, `.env*.local` are listed (the Vite default does this, but verify after scaffolding).
- **VALIDATE**: `cd frontend && npm run dev` boots on `:5173`; `curl http://localhost:5173` returns the default HTML.

### Task 7 — UPDATE `frontend/package.json` — install runtime + dev deps

- **IMPLEMENT**:
  ```bash
  cd frontend
  npm install zustand motion tailwindcss @tailwindcss/vite
  npm install -D vite-tsconfig-paths
  ```
  Confirm `package.json` `dependencies` contains `react`, `react-dom`, `zustand`, `motion`, `tailwindcss`, `@tailwindcss/vite`, and `devDependencies` contains `vite`, `@vitejs/plugin-react`, `typescript`, `@types/react`, `@types/react-dom`, `vite-tsconfig-paths`.
- **GOTCHA**: `motion` (not `framer-motion`). Both work, but `motion` is the canonical post-rebrand name and the import is `motion/react`.
- **GOTCHA**: Tailwind v4 requires `@tailwindcss/vite`. Do **not** install `postcss`, `autoprefixer`, or `@tailwindcss/postcss` — those are v3 patterns and not needed here.
- **VALIDATE**: `npm ls zustand motion tailwindcss @tailwindcss/vite vite-tsconfig-paths` lists all five with versions.

### Task 8 — UPDATE `frontend/vite.config.ts` — register plugins, configure proxy

- **IMPLEMENT** (full file replacement):
  ```ts
  import { defineConfig } from 'vite'
  import react from '@vitejs/plugin-react'
  import tailwindcss from '@tailwindcss/vite'
  import tsconfigPaths from 'vite-tsconfig-paths'

  // Backend mounted prefixes — must match `backend/api/main.py` router mounts:
  // /healthz, /swan, /external, /documents, /pipelines, /runs, /journal_entries,
  // /envelopes, /review, /dashboard
  const BACKEND = 'http://localhost:8000'

  export default defineConfig({
    plugins: [react(), tailwindcss(), tsconfigPaths()],
    server: {
      port: 5173,
      proxy: {
        '/healthz':         BACKEND,
        '/swan':            { target: BACKEND, changeOrigin: true },
        '/external':        { target: BACKEND, changeOrigin: true },
        '/documents':       { target: BACKEND, changeOrigin: true },
        '/pipelines':       { target: BACKEND, changeOrigin: true },
        '/runs':            { target: BACKEND, changeOrigin: true },
        '/journal_entries': { target: BACKEND, changeOrigin: true },
        '/envelopes':       { target: BACKEND, changeOrigin: true },
        '/review':          { target: BACKEND, changeOrigin: true },
        '/dashboard':       { target: BACKEND, changeOrigin: true },
      },
    },
  })
  ```
- **PATTERN**: [Vite — Server Options (proxy)](https://vite.dev/config/server-options).
- **GOTCHA**: SSE through Vite proxy works in Vite 5/6 (vitejs/vite#13522 closed); in Vite 7 some users report buffered streams — if the dashboard SSE never fires in dev, add a `configure` callback that forces `X-Accel-Buffering: no` on the proxied response (see Pitfalls section in this plan).
- **GOTCHA**: Do **not** set `ws: true` in proxy options — that's WebSockets only; SSE is plain HTTP/1.1.
- **GOTCHA**: Do not proxy paths that don't exist (e.g. don't add `/api`); the backend has no such prefix and the resulting 404s clutter logs.
- **VALIDATE**:
  ```bash
  # backend on :8000, frontend dev on :5173
  curl http://localhost:5173/healthz
  # Expected: {"status":"ok"}
  ```

### Task 9 — UPDATE `frontend/src/index.css`, `frontend/src/main.tsx`, delete `App.css`

- **IMPLEMENT**:
  - Replace `frontend/src/index.css` with a single line:
    ```css
    @import "tailwindcss";
    ```
  - In `frontend/src/main.tsx`, ensure `import './index.css'` is present and remove `import './App.css'` if present.
  - Delete `frontend/src/App.css` (`rm frontend/src/App.css`).
- **PATTERN**: Tailwind v4 install docs.
- **GOTCHA**: Do NOT use the v3 directives (`@tailwind base; @tailwind components; @tailwind utilities;`). They will be silently ignored under v4 and you'll have unstyled output.
- **GOTCHA**: Do not write a `tailwind.config.js`. v4 auto-detects content from imports. Adding a config silently degrades behavior.
- **VALIDATE**: After Task 12, `npm run dev` shows Tailwind utility classes applied.

### Task 10 — UPDATE `frontend/tsconfig.app.json` — add path alias

- **IMPLEMENT**: Add `baseUrl` and `paths` under `compilerOptions`:
  ```jsonc
  {
    "compilerOptions": {
      // ... existing settings ...
      "baseUrl": ".",
      "paths": { "@/*": ["src/*"] }
    },
    "include": ["src"]
  }
  ```
- **PATTERN**: `vite-tsconfig-paths` reads from this file and configures the Vite resolver automatically.
- **VALIDATE**: After Task 11, an import like `import { useDashboard } from '@/store/dashboard'` resolves at typecheck and runtime.

### Task 11 — CREATE `frontend/src/types/index.ts`

- **IMPLEMENT**: Hand-written types matching the backend response shapes — these are the single source of truth for the frontend. Anchor every interface in a comment that names the producing endpoint or event source.
  ```ts
  // ===== REST response shapes =====

  // GET /journal_entries  →  { items: JournalEntryListItem[], total, limit, offset }
  export interface JournalEntryListItem {
    id: number
    basis: 'cash' | 'accrual'
    entry_date: string                  // ISO date YYYY-MM-DD
    description: string | null
    status: 'draft' | 'posted' | 'review' | 'reversed'
    source_pipeline: string
    source_run_id: number
    accrual_link_id: number | null
    reversal_of_id: number | null
    created_at: string                  // ISO timestamp
    total_cents: number
    line_count: number
  }
  export interface JournalEntryListResponse {
    items: JournalEntryListItem[]
    total: number
    limit: number
    offset: number
  }

  // GET /envelopes  →  { items: EnvelopeRow[] }
  export interface EnvelopeRow {
    id: number
    scope_kind: 'employee' | 'team' | 'company'
    scope_id: number | null
    category: 'food' | 'travel' | 'saas' | 'ai_tokens' | 'leasing'
    period: string                       // YYYY-MM
    cap_cents: number
    soft_threshold_pct: number
    used_cents: number
    allocation_count: number
  }
  export interface EnvelopeListResponse { items: EnvelopeRow[] }

  // GET /journal_entries/{id}/trace
  export interface TraceLine {
    id: number; entry_id: number; account_code: string
    debit_cents: number; credit_cents: number
    counterparty_id: number | null; swan_transaction_id: string | null
    document_id: number | null; description: string | null
  }
  export interface TraceDecision {
    id: number; run_id_logical: number; node_id: string; source: string
    runner: string; model: string | null; confidence: number | null
    line_id_logical: string | null; latency_ms: number | null
    finish_reason: string | null; alternatives_json: string | null
    started_at: string; completed_at: string | null
  }
  export interface TraceResponse {
    entry: JournalEntryListItem & { /* extra fields from get_entry_trace */ }
    lines: TraceLine[]
    traces: Array<{ id: number; line_id: number; source: string; rule_id: string | null; confidence: number | null; agent_decision_id_logical: string | null; parent_event_id: string | null; approver_id: number | null; approved_at: string | null; created_at: string }>
    agent_decisions: TraceDecision[]
    agent_costs: Array<{ decision_id: number; employee_id: number | null; provider: string; model: string; input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number; reasoning_tokens: number; cost_micro_usd: number; created_at: string }>
    source_run: any | null
    swan_transactions: any[]
    documents: any[]
  }

  // ===== Dashboard SSE event shapes (from backend/orchestration/tools/*.py) =====

  // gl_poster.py:142–154
  export type LedgerEntryPostedEvent = {
    event_type: 'ledger.entry_posted'
    ts: string
    data: {
      entry_id: number; basis: 'cash' | 'accrual'; entry_date: string
      total_cents: number; lines: number; run_id: number
      employee_id: number | null
    }
  }

  // runs.py:317–324  — APPROVED variant; flat shape, no `data` key
  export type LedgerEntryApprovedEvent = {
    event_type: 'ledger.entry_posted'
    entry_id: number
    approver_id: number
    approved_at: string
  }

  // budget_envelope.py:183–196
  export type EnvelopeDecrementedEvent = {
    event_type: 'envelope.decremented'
    ts: string
    data: {
      envelope_id: number
      employee_id: number | null
      category: EnvelopeRow['category']
      period: string
      used_cents: number
      cap_cents: number
      soft_threshold_pct: number
      ledger_entry_id: number
    }
  }
  // budget_envelope.py:120–128
  export type EnvelopeSkippedEvent = {
    event_type: 'envelope.skipped'
    ts: string
    data: { entry_id: number; reason: 'uncategorized'; employee_id: number | null }
  }
  // budget_envelope.py:138–147
  export type EnvelopeNoEnvelopeEvent = {
    event_type: 'envelope.no_envelope'
    ts: string
    data: { entry_id: number; category: string; period: string; employee_id: number | null }
  }
  // review_queue.py:77–87
  export type ReviewEnqueuedEvent = {
    event_type: 'review.enqueued'
    ts: string
    data: { review_id: number; entry_id: number | null; kind: string; confidence: number | null; reason: string }
  }

  export type DashboardEvent =
    | LedgerEntryPostedEvent
    | LedgerEntryApprovedEvent
    | EnvelopeDecrementedEvent
    | EnvelopeSkippedEvent
    | EnvelopeNoEnvelopeEvent
    | ReviewEnqueuedEvent

  // ===== Per-run SSE event shape (from runs.py:127–159) =====

  export interface RunEvent {
    run_id: number
    event_type: 'pipeline_started' | 'pipeline_completed' | 'pipeline_failed'
                | 'node_started' | 'node_completed' | 'node_skipped' | 'node_failed' | 'cache_hit'
    node_id: string | null
    data: Record<string, unknown>
    ts: string
  }

  // ===== Run + employees =====

  export interface RunSummary {
    id: number; pipeline_name: string; pipeline_version: number
    trigger_source: string; trigger_payload: string
    employee_id_logical: string | null; status: 'running' | 'completed' | 'failed'
    error: string | null; started_at: string; completed_at: string | null
  }

  export interface Employee { id: number; full_name: string; email: string }
  ```
- **PATTERN**: Backend audit report (this plan's CONTEXT REFERENCES section).
- **GOTCHA**: The two `ledger.entry_posted` shapes — gl_poster's nested-`data` form and runs.py's flat-fields form — are **both** real. The reducer must handle both: `'data' in ev ? ev.data.entry_id : ev.entry_id`.
- **VALIDATE**: After Task 16, `npx tsc --noEmit` returns clean.

### Task 12 — CREATE `frontend/src/api.ts`

- **IMPLEMENT**:
  ```ts
  import type {
    JournalEntryListResponse, EnvelopeListResponse, TraceResponse, RunSummary,
  } from '@/types'

  const BASE = import.meta.env.VITE_API_BASE ?? ''

  async function j<T>(path: string, init?: RequestInit): Promise<T> {
    const r = await fetch(BASE + path, {
      ...init,
      headers: { 'Accept': 'application/json', ...(init?.headers ?? {}) },
      signal: init?.signal ?? AbortSignal.timeout(15_000),
    })
    if (!r.ok) {
      const text = await r.text().catch(() => '')
      throw new Error(`${r.status} ${r.statusText} on ${path}: ${text}`)
    }
    return r.json() as Promise<T>
  }

  export const api = {
    healthz: () => j<{status: string}>('/healthz'),

    // List endpoints (Tasks 1 & 2)
    listJournalEntries: (params: { limit?: number; offset?: number; status?: string } = {}) => {
      const qs = new URLSearchParams()
      if (params.limit !== undefined) qs.set('limit', String(params.limit))
      if (params.offset !== undefined) qs.set('offset', String(params.offset))
      if (params.status !== undefined) qs.set('status', params.status)
      return j<JournalEntryListResponse>(`/journal_entries${qs.size ? '?' + qs : ''}`)
    },
    listEnvelopes: (params: { employee_id?: number; period?: string; scope_kind?: string } = {}) => {
      const qs = new URLSearchParams()
      if (params.employee_id !== undefined) qs.set('employee_id', String(params.employee_id))
      if (params.period !== undefined) qs.set('period', params.period)
      if (params.scope_kind !== undefined) qs.set('scope_kind', params.scope_kind)
      return j<EnvelopeListResponse>(`/envelopes${qs.size ? '?' + qs : ''}`)
    },

    // Existing endpoints
    getRun: (runId: number) => j<{run: RunSummary; events: any[]; agent_decisions: any[]}>(`/runs/${runId}`),
    getEntryTrace: (entryId: number) => j<TraceResponse>(`/journal_entries/${entryId}/trace`),
    approveEntry: (entryId: number, approverId: number) =>
      j<{entry_id: number; approver_id: number; status: string}>(
        `/review/${entryId}/approve`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approver_id: approverId }),
        },
      ),
    uploadDocument: (file: File, employeeId?: number) => {
      const fd = new FormData()
      fd.append('file', file)
      if (employeeId !== undefined) fd.append('employee_id', String(employeeId))
      return j<{document_id: number; sha256: string; run_id: number; stream_url: string}>(
        '/documents/upload',
        { method: 'POST', body: fd, signal: AbortSignal.timeout(60_000) },
      )
    },
  }
  ```
- **PATTERN**: Backend route map.
- **GOTCHA**: `BASE = ''` in dev so calls go through Vite proxy (Task 8). In prod, set `VITE_API_BASE=https://...` in `.env.production`.
- **GOTCHA**: `AbortSignal.timeout(15_000)` is supported in all 2024+ evergreen browsers. Document upload uses 60s.
- **VALIDATE**: `npx tsc --noEmit` clean after Task 16.

### Task 13 — CREATE `frontend/src/hooks/useSSE.ts`

- **IMPLEMENT** (full file, ~30 lines):
  ```ts
  import { useEffect, useRef } from 'react'

  export function useSSE<T>(
    url: string,
    onEvent: (event: T) => void,
    onStatus?: (open: boolean) => void,
  ): void {
    const onEventRef = useRef(onEvent); onEventRef.current = onEvent
    const onStatusRef = useRef(onStatus); onStatusRef.current = onStatus

    useEffect(() => {
      const es = new EventSource(url)
      es.onopen = () => onStatusRef.current?.(true)
      es.onmessage = (m) => {
        try { onEventRef.current(JSON.parse(m.data) as T) }
        catch (err) { console.warn('[useSSE] malformed payload', err) }
      }
      es.onerror = () => onStatusRef.current?.(false)
      return () => { es.close() }
    }, [url])
  }
  ```
- **PATTERN**: External research; uses `useRef` to pin handlers so re-renders don't reopen the socket.
- **GOTCHA**: React 18 StrictMode double-mounts effects in dev. The cleanup function (`es.close()`) is what makes this safe — don't disable StrictMode.
- **GOTCHA**: Do not use `addEventListener('eventType', ...)`. Backend wire format has no `event:` line, so all SSE messages dispatch as type `"message"`. Use `onmessage` and route on `event_type` from the JSON payload.
- **GOTCHA**: EventSource auto-reconnects on transient errors (`onerror` is fired, then it reconnects). `onerror` does NOT mean permanent failure unless `es.readyState === EventSource.CLOSED` (value 2).
- **VALIDATE**: Manual test — `useSSE('/dashboard/stream', console.log)` in App.tsx, watch console while triggering a webhook.

### Task 14 — CREATE `frontend/src/store/dashboard.ts`

- **IMPLEMENT** (Zustand store):
  ```ts
  import { create } from 'zustand'
  import { useShallow } from 'zustand/react/shallow'
  import type {
    JournalEntryListItem, EnvelopeRow, DashboardEvent,
  } from '@/types'

  type LedgerRow = JournalEntryListItem & { _new?: boolean }

  const envelopeKey = (e: { scope_kind: string; scope_id: number | null; category: string; period: string }) =>
    `${e.scope_kind}|${e.scope_id ?? 'null'}|${e.category}|${e.period}`

  interface DashState {
    ledger: LedgerRow[]                          // newest first, capped at 200
    envelopes: Record<string, EnvelopeRow>
    reviewIds: Set<number>                        // ids of entries pending review
    connected: boolean
    hydrate: (p: { ledger: JournalEntryListItem[]; envelopes: EnvelopeRow[] }) => void
    apply: (ev: DashboardEvent) => void
    setConnected: (b: boolean) => void
  }

  export const useDashboard = create<DashState>()((set, get) => ({
    ledger: [],
    envelopes: {},
    reviewIds: new Set(),
    connected: false,
    hydrate: ({ ledger, envelopes }) => set({
      ledger,
      envelopes: Object.fromEntries(envelopes.map(e => [envelopeKey(e), e])),
    }),
    setConnected: (connected) => set({ connected }),
    apply: (ev) => {
      switch (ev.event_type) {
        case 'ledger.entry_posted': {
          // Two shapes: nested `data: {...}` from gl_poster, or flat from approve_entry.
          const isNested = 'data' in ev
          const entryId = isNested ? ev.data.entry_id : ev.entry_id
          const s = get()
          if (s.ledger.some(r => r.id === entryId)) {
            // Already in list (possibly in review) — promote status to 'posted'
            return set({
              ledger: s.ledger.map(r => r.id === entryId ? { ...r, status: 'posted' as const, _new: true } : r),
              reviewIds: new Set([...s.reviewIds].filter(id => id !== entryId)),
            })
          }
          if (!isNested) {
            // Flat shape from approve — fetch full row would be ideal, but for stage we just insert a stub.
            // The reload-from-REST on tab switch will fix it.
            return
          }
          const stub: LedgerRow = {
            id: ev.data.entry_id,
            basis: ev.data.basis,
            entry_date: ev.data.entry_date,
            description: null,
            status: 'posted',
            source_pipeline: '?',
            source_run_id: ev.data.run_id,
            accrual_link_id: null,
            reversal_of_id: null,
            created_at: ev.ts,
            total_cents: ev.data.total_cents,
            line_count: ev.data.lines,
            _new: true,
          }
          set({ ledger: [stub, ...s.ledger].slice(0, 200) })
          return
        }
        case 'envelope.decremented': {
          const e = ev.data
          const fakeKey = envelopeKey({
            scope_kind: e.employee_id != null ? 'employee' : 'company',
            scope_id: e.employee_id,
            category: e.category,
            period: e.period,
          })
          const existing = get().envelopes[fakeKey]
          set({
            envelopes: {
              ...get().envelopes,
              [fakeKey]: {
                ...(existing ?? {
                  id: e.envelope_id,
                  scope_kind: e.employee_id != null ? 'employee' : 'company',
                  scope_id: e.employee_id,
                  category: e.category,
                  period: e.period,
                  cap_cents: e.cap_cents,
                  soft_threshold_pct: e.soft_threshold_pct,
                  used_cents: 0,
                  allocation_count: 0,
                }),
                used_cents: e.used_cents,
                cap_cents: e.cap_cents,
                soft_threshold_pct: e.soft_threshold_pct,
              },
            },
          })
          return
        }
        case 'envelope.skipped':
        case 'envelope.no_envelope':
          // Toast in UI; no state change.
          console.info('[envelope]', ev.event_type, ev.data)
          return
        case 'review.enqueued':
          if (ev.data.entry_id != null) {
            const s = get()
            const next = new Set(s.reviewIds); next.add(ev.data.entry_id)
            set({ reviewIds: next })
          }
          return
      }
    },
  }))

  export const useLedger = () => useDashboard(s => s.ledger)
  export const useEnvelopes = () => useDashboard(useShallow(s => Object.values(s.envelopes)))
  export const useConnected = () => useDashboard(s => s.connected)
  ```
- **PATTERN**: Zustand v5 idiom; `useShallow` for collection selectors.
- **GOTCHA**: `Set` mutations require returning a *new* Set object for React to re-render. Don't `state.reviewIds.add(x)`; clone first.
- **GOTCHA**: The `_new: true` flag is for the row-insert animation. Set on insert; clear after the animation duration via a `setTimeout` in the component.
- **VALIDATE**: After Task 17, drop a Swan webhook fixture; the ledger array grows by 1 in React DevTools.

### Task 15 — CREATE `frontend/src/store/runProgress.ts`

- **IMPLEMENT**:
  ```ts
  import { create } from 'zustand'
  import type { RunEvent } from '@/types'

  export type NodeStatus = 'pending' | 'running' | 'completed' | 'skipped' | 'failed'

  interface RunProgressState {
    activeRunId: number | null
    nodes: Record<string, { status: NodeStatus; elapsed_ms?: number; error?: string }>
    pipelineStatus: 'running' | 'completed' | 'failed' | null
    setActiveRun: (id: number | null) => void
    apply: (ev: RunEvent) => void
    reset: () => void
  }

  export const useRunProgress = create<RunProgressState>()((set, get) => ({
    activeRunId: null,
    nodes: {},
    pipelineStatus: null,
    setActiveRun: (id) => set({ activeRunId: id, nodes: {}, pipelineStatus: id ? 'running' : null }),
    apply: (ev) => {
      switch (ev.event_type) {
        case 'pipeline_started': set({ pipelineStatus: 'running' }); return
        case 'pipeline_completed': set({ pipelineStatus: 'completed' }); return
        case 'pipeline_failed': set({ pipelineStatus: 'failed' }); return
        case 'node_started':
          if (ev.node_id) set({ nodes: { ...get().nodes, [ev.node_id]: { status: 'running' } } })
          return
        case 'node_completed':
          if (ev.node_id) set({
            nodes: { ...get().nodes, [ev.node_id]: { status: 'completed', elapsed_ms: (ev.data as any)?.elapsed_ms } }
          })
          return
        case 'node_skipped':
          if (ev.node_id) set({ nodes: { ...get().nodes, [ev.node_id]: { status: 'skipped' } } })
          return
        case 'node_failed':
          if (ev.node_id) set({ nodes: { ...get().nodes, [ev.node_id]: { status: 'failed', error: String((ev.data as any)?.error ?? '') } } })
          return
      }
    },
    reset: () => set({ activeRunId: null, nodes: {}, pipelineStatus: null }),
  }))
  ```
- **PATTERN**: Same as `dashboard.ts`.
- **VALIDATE**: After Task 18, drag-drop a PDF; the overlay populates with the document_ingested pipeline's nodes in order.

### Task 16 — CREATE `frontend/src/components/formatters.ts`

- **IMPLEMENT**:
  ```ts
  export const centsToEuros = (cents: number, opts: { signed?: boolean } = {}) => {
    const sign = cents < 0 ? '-' : opts.signed && cents > 0 ? '+' : ''
    const abs = Math.abs(cents) / 100
    return `${sign}€${abs.toFixed(2)}`
  }

  export const shortDate = (iso: string) => {
    // '2026-04-15' → '15 Apr'
    try {
      const d = new Date(iso)
      return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
    } catch { return iso }
  }

  export const categoryLabel: Record<string, string> = {
    food: 'Food', travel: 'Travel', saas: 'SaaS', ai_tokens: 'AI', leasing: 'Leasing',
  }
  ```
- **VALIDATE**: Used by Ledger, EnvelopeRing.

### Task 17 — CREATE `frontend/src/components/EnvelopeRing.tsx` and `EnvelopeRings.tsx`

- **IMPLEMENT** (`EnvelopeRing.tsx`):
  ```tsx
  import { centsToEuros, categoryLabel } from './formatters'

  export function EnvelopeRing({ used, cap, category }: { used: number; cap: number; category: string }) {
    const pct = cap > 0 ? Math.min(used / cap, 1.5) : 0
    const r = 36, c = 2 * Math.PI * r
    const tone =
      pct > 1 ? 'text-rose-500' :
      pct >= 0.8 ? 'text-amber-500' :
      'text-emerald-500'
    return (
      <div className="flex flex-col items-center gap-1">
        <svg width="96" height="96" viewBox="0 0 96 96" className={tone}>
          <circle cx="48" cy="48" r={r} fill="none" stroke="currentColor"
                  className="opacity-15" strokeWidth="10"/>
          <circle cx="48" cy="48" r={r} fill="none" stroke="currentColor"
                  strokeWidth="10" strokeLinecap="round"
                  strokeDasharray={c}
                  strokeDashoffset={c * (1 - Math.min(pct, 1))}
                  transform="rotate(-90 48 48)"
                  style={{ transition: 'stroke-dashoffset 400ms ease-out' }}/>
          <text x="48" y="54" textAnchor="middle" className="fill-current text-base font-semibold">
            {Math.round(pct * 100)}%
          </text>
        </svg>
        <div className="text-sm font-medium text-zinc-700">{categoryLabel[category] ?? category}</div>
        <div className="text-xs text-zinc-500 tabular-nums">
          {centsToEuros(used)} / {centsToEuros(cap)}
        </div>
      </div>
    )
  }
  ```

- **IMPLEMENT** (`EnvelopeRings.tsx`):
  ```tsx
  import { useEffect } from 'react'
  import { useEnvelopes, useDashboard } from '@/store/dashboard'
  import { api } from '@/api'
  import { EnvelopeRing } from './EnvelopeRing'

  // Hardcode 3 employees for demo; real list could come from /employees if implemented.
  const EMPLOYEES = [
    { id: 1, name: 'Tim' }, { id: 2, name: 'Marie' }, { id: 3, name: 'Paul' },
  ]
  const PERIOD = new Date().toISOString().slice(0, 7) // 'YYYY-MM'

  export function EnvelopeRings() {
    const envelopes = useEnvelopes()
    const hydrate = useDashboard(s => s.hydrate)

    useEffect(() => {
      // hydrate envelopes for all 3 employees
      Promise.all(EMPLOYEES.map(e => api.listEnvelopes({ employee_id: e.id, period: PERIOD })))
        .then(responses => {
          const all = responses.flatMap(r => r.items)
          hydrate({ ledger: useDashboard.getState().ledger, envelopes: all })
        })
        .catch(err => console.error('[EnvelopeRings] hydrate', err))
    }, [hydrate])

    return (
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 p-6">
        {EMPLOYEES.map(emp => {
          const empRings = envelopes.filter(e => e.scope_kind === 'employee' && e.scope_id === emp.id && e.period === PERIOD)
          return (
            <div key={emp.id} className="rounded-xl border border-zinc-200 p-4 bg-white">
              <div className="text-lg font-semibold text-zinc-900 mb-3">{emp.name}</div>
              <div className="grid grid-cols-5 gap-2">
                {empRings.map(env =>
                  <EnvelopeRing key={env.id} used={env.used_cents} cap={env.cap_cents} category={env.category}/>
                )}
              </div>
            </div>
          )
        })}
      </div>
    )
  }
  ```
- **PATTERN**: External research; SVG stroke-dasharray.
- **GOTCHA**: Don't use `bg-[${var}]` arbitrary values — Tailwind v4 HMR breaks. Static lookup table for `tone`.
- **GOTCHA**: `PERIOD` is computed once at module load. For a multi-day demo, this is fine. Don't bother with date pickers for hackathon.
- **VALIDATE**: Page renders 3 employee cards × 5 rings each; rings update color when an `envelope.decremented` event fires.

### Task 18 — CREATE `frontend/src/components/Ledger.tsx`

- **IMPLEMENT**:
  ```tsx
  import { useEffect, useState } from 'react'
  import { motion, AnimatePresence } from 'motion/react'
  import { useLedger, useDashboard } from '@/store/dashboard'
  import { api } from '@/api'
  import { centsToEuros, shortDate } from './formatters'

  export function Ledger({ onRowClick }: { onRowClick: (entryId: number) => void }) {
    const ledger = useLedger()
    const hydrate = useDashboard(s => s.hydrate)

    useEffect(() => {
      api.listJournalEntries({ limit: 50 })
        .then(r => hydrate({ ledger: r.items, envelopes: Object.values(useDashboard.getState().envelopes) }))
        .catch(err => console.error('[Ledger] hydrate', err))
    }, [hydrate])

    return (
      <div className="rounded-xl border border-zinc-200 bg-white overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-50 text-zinc-600">
            <tr>
              <th className="text-left p-3 font-medium">Date</th>
              <th className="text-left p-3 font-medium">Source</th>
              <th className="text-left p-3 font-medium">Status</th>
              <th className="text-right p-3 font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            <AnimatePresence initial={false}>
              {ledger.slice(0, 50).map(e => (
                <motion.tr key={e.id}
                  layout
                  initial={e._new ? { opacity: 0, y: -8, backgroundColor: 'rgb(236 253 245)' } : false}
                  animate={{ opacity: 1, y: 0, backgroundColor: 'rgb(255 255 255)' }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.25 }}
                  className="border-t border-zinc-100 cursor-pointer hover:bg-zinc-50"
                  onClick={() => onRowClick(e.id)}
                >
                  <td className="p-3 text-zinc-700">{shortDate(e.entry_date)}</td>
                  <td className="p-3 text-zinc-600">{e.source_pipeline}</td>
                  <td className="p-3">
                    <StatusPill status={e.status}/>
                  </td>
                  <td className="p-3 text-right tabular-nums font-medium">{centsToEuros(e.total_cents)}</td>
                </motion.tr>
              ))}
            </AnimatePresence>
          </tbody>
        </table>
      </div>
    )
  }

  function StatusPill({ status }: { status: string }) {
    const cls = {
      posted: 'bg-emerald-100 text-emerald-700',
      review: 'bg-amber-100 text-amber-700',
      reversed: 'bg-zinc-200 text-zinc-700',
      draft: 'bg-zinc-100 text-zinc-600',
    }[status] ?? 'bg-zinc-100 text-zinc-600'
    return <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{status}</span>
  }
  ```
- **GOTCHA**: `motion` import is `motion/react`, not `framer-motion`.
- **GOTCHA**: `tabular-nums` on amount cells prevents digits jittering during animations.
- **GOTCHA**: `onClick` opens the trace drawer; the parent (`App`) holds the selected entry id.
- **VALIDATE**: Hydrates with 50 rows; new rows animate in on Swan webhook.

### Task 19 — CREATE `frontend/src/components/UploadZone.tsx` and `RunProgressOverlay.tsx`

- **IMPLEMENT** (`UploadZone.tsx`):
  ```tsx
  import { useState } from 'react'
  import { api } from '@/api'
  import { useRunProgress } from '@/store/runProgress'
  import { RunProgressOverlay } from './RunProgressOverlay'

  export function UploadZone() {
    const [hover, setHover] = useState(false)
    const [employeeId, setEmployeeId] = useState<number | undefined>(1)
    const setActiveRun = useRunProgress(s => s.setActiveRun)

    const submit = async (file: File) => {
      try {
        const res = await api.uploadDocument(file, employeeId)
        setActiveRun(res.run_id)
      } catch (err) {
        console.error('[UploadZone]', err)
        alert(`Upload failed: ${(err as Error).message}`)
      }
    }
    return (
      <div className="p-6">
        <div className="flex gap-2 items-center mb-3 text-sm">
          <span className="text-zinc-600">Bill to:</span>
          <select value={employeeId ?? ''} onChange={e => setEmployeeId(e.target.value ? Number(e.target.value) : undefined)}
                  className="border border-zinc-300 rounded px-2 py-1">
            <option value="">Company</option>
            <option value="1">Tim</option>
            <option value="2">Marie</option>
            <option value="3">Paul</option>
          </select>
        </div>
        <div
          onDragOver={(e) => { e.preventDefault(); setHover(true) }}
          onDragLeave={() => setHover(false)}
          onDrop={(e) => {
            e.preventDefault(); setHover(false)
            const f = e.dataTransfer.files?.[0]
            if (f) submit(f)
          }}
          className={`border-2 border-dashed rounded-xl p-12 text-center transition cursor-pointer
            ${hover ? 'border-emerald-500 bg-emerald-50' : 'border-zinc-300'}`}
        >
          <p className="text-zinc-600">Drop a PDF invoice here</p>
          <input type="file" accept="application/pdf" className="mt-3 mx-auto block text-xs"
                 onChange={(e) => e.target.files?.[0] && submit(e.target.files[0])}/>
        </div>
        <RunProgressOverlay/>
      </div>
    )
  }
  ```

- **IMPLEMENT** (`RunProgressOverlay.tsx`):
  ```tsx
  import { useEffect } from 'react'
  import { useRunProgress } from '@/store/runProgress'
  import { useSSE } from '@/hooks/useSSE'
  import type { RunEvent } from '@/types'

  export function RunProgressOverlay() {
    const activeRunId = useRunProgress(s => s.activeRunId)
    const apply = useRunProgress(s => s.apply)
    const status = useRunProgress(s => s.pipelineStatus)
    const reset = useRunProgress(s => s.reset)
    const nodes = useRunProgress(s => s.nodes)

    // Subscribe only when there is an active run.
    const url = activeRunId != null ? `/runs/${activeRunId}/stream` : ''
    useSSE<RunEvent>(url || 'about:blank', apply)

    // Auto-close 4s after terminal.
    useEffect(() => {
      if (status === 'completed' || status === 'failed') {
        const t = setTimeout(reset, 4000)
        return () => clearTimeout(t)
      }
    }, [status, reset])

    if (activeRunId == null) return null
    const nodeIds = Object.keys(nodes)
    return (
      <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
        <div className="bg-white rounded-xl p-6 w-[480px] shadow-xl">
          <div className="flex items-center justify-between mb-3">
            <div className="font-semibold">Processing run #{activeRunId}</div>
            <span className={`text-xs px-2 py-0.5 rounded-full
              ${status === 'completed' ? 'bg-emerald-100 text-emerald-700'
              : status === 'failed' ? 'bg-rose-100 text-rose-700'
              : 'bg-blue-100 text-blue-700'}`}>{status ?? 'running'}</span>
          </div>
          <ul className="space-y-1 text-sm">
            {nodeIds.length === 0 && <li className="text-zinc-500">Waiting for first event…</li>}
            {nodeIds.map(id => {
              const n = nodes[id]
              const dot = n.status === 'completed' ? '🟢'
                : n.status === 'failed' ? '🔴'
                : n.status === 'skipped' ? '⚪'
                : n.status === 'running' ? '🔵' : '⚫'
              return <li key={id} className="flex items-center gap-2">
                <span>{dot}</span>
                <span className="font-mono text-xs">{id}</span>
                <span className="text-zinc-500 text-xs">
                  {n.elapsed_ms != null ? `${n.elapsed_ms}ms` : ''}
                  {n.error ? ` ${n.error}` : ''}
                </span>
              </li>
            })}
          </ul>
          <button onClick={reset} className="mt-4 text-sm text-zinc-500 hover:text-zinc-700">Close</button>
        </div>
      </div>
    )
  }
  ```
- **GOTCHA**: `useSSE` hook with `url=''` would still try to open a connection. The `'about:blank'` fallback is a no-op URL the browser refuses. Cleaner alternative: extract the `useEffect` body and gate on `if (!activeRunId) return`.
- **GOTCHA**: Emoji per status is acceptable for hackathon density. If your project bans emoji in source per CLAUDE.md, swap for SVG dots — verify by reading `CLAUDE.md` rules. (Project's CLAUDE.md does not appear to ban emoji explicitly; user requested emoji only when asked. Replace these with `●` glyphs styled by Tailwind classes if more conservative.)
- **VALIDATE**: Drop a PDF; overlay shows nodes appearing one by one within 1–2s each.

### Task 20 — CREATE `frontend/src/components/TraceDrawer.tsx`

- **IMPLEMENT**:
  ```tsx
  import { useEffect, useState } from 'react'
  import { motion, AnimatePresence } from 'motion/react'
  import { api } from '@/api'
  import type { TraceResponse } from '@/types'
  import { centsToEuros } from './formatters'

  export function TraceDrawer({ entryId, onClose }: { entryId: number | null; onClose: () => void }) {
    const [data, setData] = useState<TraceResponse | null>(null)
    useEffect(() => {
      if (entryId == null) { setData(null); return }
      api.getEntryTrace(entryId).then(setData).catch(err => console.error('[TraceDrawer]', err))
    }, [entryId])
    return (
      <AnimatePresence>
        {entryId != null && (
          <motion.div className="fixed inset-y-0 right-0 w-[480px] bg-white border-l border-zinc-200 shadow-2xl z-40 overflow-y-auto"
            initial={{ x: 480 }} animate={{ x: 0 }} exit={{ x: 480 }}
            transition={{ type: 'tween', duration: 0.2 }}>
            <div className="p-4 flex items-center justify-between border-b border-zinc-200">
              <div className="font-semibold">Entry #{entryId}</div>
              <button onClick={onClose} className="text-zinc-500 hover:text-zinc-800">✕</button>
            </div>
            {data ? <TraceContent data={data}/> : <div className="p-4 text-zinc-500">Loading…</div>}
          </motion.div>
        )}
      </AnimatePresence>
    )
  }

  function TraceContent({ data }: { data: TraceResponse }) {
    return (
      <div className="p-4 space-y-4 text-sm">
        <Section title="Lines">
          <table className="w-full">
            <tbody>
              {data.lines.map(l => (
                <tr key={l.id} className="border-b border-zinc-100">
                  <td className="py-1 font-mono text-xs">{l.account_code}</td>
                  <td className="py-1 text-right tabular-nums">{l.debit_cents > 0 ? centsToEuros(l.debit_cents) : ''}</td>
                  <td className="py-1 text-right tabular-nums">{l.credit_cents > 0 ? centsToEuros(l.credit_cents) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
        <Section title="Decision traces">
          {data.traces.map(t => (
            <div key={t.id} className="border-l-2 border-zinc-200 pl-3 mb-2">
              <div className="text-xs text-zinc-500">{t.source}{t.confidence != null ? ` · conf ${(t.confidence*100).toFixed(0)}%` : ''}</div>
              <div className="text-zinc-700">{t.rule_id ?? t.parent_event_id ?? '—'}</div>
            </div>
          ))}
        </Section>
        <Section title="Agent decisions">
          {data.agent_decisions.map(d => (
            <div key={d.id} className="text-xs border-l-2 border-zinc-200 pl-3 mb-2">
              <div className="font-mono">{d.node_id}</div>
              <div className="text-zinc-500">{d.runner}/{d.model ?? '?'} · {d.latency_ms ?? '?'}ms{d.confidence != null ? ` · conf ${(d.confidence*100).toFixed(0)}%` : ''}</div>
            </div>
          ))}
        </Section>
        <Section title="Cost">
          {data.agent_costs.map(c => (
            <div key={c.decision_id} className="text-xs">
              {c.provider}/{c.model} · in {c.input_tokens} / out {c.output_tokens} · ${(c.cost_micro_usd / 1_000_000).toFixed(6)}
            </div>
          ))}
        </Section>
      </div>
    )
  }

  function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return <div><div className="font-semibold text-zinc-800 mb-2">{title}</div>{children}</div>
  }
  ```
- **VALIDATE**: Click any ledger row; drawer slides in with lines/traces/decisions/cost.

### Task 21 — CREATE `frontend/src/components/ReviewQueue.tsx`

- **IMPLEMENT**:
  ```tsx
  import { useEffect, useState } from 'react'
  import { useDashboard } from '@/store/dashboard'
  import { api } from '@/api'
  import type { JournalEntryListItem } from '@/types'
  import { centsToEuros, shortDate } from './formatters'

  export function ReviewQueue() {
    const [items, setItems] = useState<JournalEntryListItem[]>([])
    const reviewIds = useDashboard(s => s.reviewIds)

    const refresh = () => api.listJournalEntries({ status: 'review', limit: 50 })
      .then(r => setItems(r.items)).catch(err => console.error('[ReviewQueue]', err))

    useEffect(() => { refresh() }, [reviewIds.size])

    const approve = async (id: number) => {
      try {
        await api.approveEntry(id, 1) // demo: Tim approves
        await refresh()
      } catch (err) { alert(`Approve failed: ${(err as Error).message}`) }
    }

    return (
      <div className="p-6">
        <div className="rounded-xl border border-zinc-200 bg-white overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 text-zinc-600">
              <tr>
                <th className="text-left p-3 font-medium">Date</th>
                <th className="text-left p-3 font-medium">Source</th>
                <th className="text-right p-3 font-medium">Amount</th>
                <th className="text-right p-3 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {items.map(e => (
                <tr key={e.id} className="border-t border-zinc-100">
                  <td className="p-3">{shortDate(e.entry_date)}</td>
                  <td className="p-3 text-zinc-600">{e.source_pipeline}</td>
                  <td className="p-3 text-right tabular-nums font-medium">{centsToEuros(e.total_cents)}</td>
                  <td className="p-3 text-right">
                    <button onClick={() => approve(e.id)}
                      className="bg-emerald-600 text-white px-3 py-1 rounded text-xs font-medium hover:bg-emerald-700">
                      Approve
                    </button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr><td colSpan={4} className="p-6 text-center text-zinc-500">No items in review.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    )
  }
  ```
- **VALIDATE**: List populates from `GET /journal_entries?status=review`; approve calls `POST /review/{id}/approve` with `approver_id=1`; row disappears after approve.

### Task 22 — CREATE `frontend/src/components/InfraTab.tsx`

- **IMPLEMENT**:
  ```tsx
  import { useEffect, useState } from 'react'
  import { useDashboard } from '@/store/dashboard'

  export function InfraTab() {
    const [healthz, setHealthz] = useState<{status: string} | null>(null)
    const connected = useDashboard(s => s.connected)
    useEffect(() => {
      fetch('/healthz').then(r => r.json()).then(setHealthz).catch(() => setHealthz(null))
    }, [])
    return (
      <div className="p-6 space-y-4">
        <div className="rounded-xl border border-zinc-200 bg-white p-4">
          <div className="font-semibold mb-2">Backend</div>
          <div className="text-sm text-zinc-700">
            <div>healthz: {healthz?.status ?? '—'}</div>
            <div>dashboard SSE: {connected ? '🟢 connected' : '🔴 disconnected'}</div>
          </div>
        </div>
        <div className="rounded-xl border border-zinc-200 bg-white p-4">
          <div className="font-semibold mb-2">Storage</div>
          <div className="text-sm text-zinc-600">
            Three SQLite databases: <code>accounting.db</code>, <code>orchestration.db</code>, <code>audit.db</code>.
          </div>
        </div>
        <div className="rounded-xl border border-zinc-200 bg-white p-4">
          <div className="font-semibold mb-2">Recent runs</div>
          <div className="text-sm text-zinc-500">
            (Listing endpoint not yet wired — open a per-run trace from the Dashboard tab.)
          </div>
        </div>
      </div>
    )
  }
  ```
- **GOTCHA**: A `GET /runs?limit=20` listing endpoint is not in scope for this plan. The Infra tab is the credibility surface; minimal content is fine.
- **VALIDATE**: Tab renders with green "connected" indicator after SSE opens.

### Task 23 — CREATE `frontend/src/components/Tabs.tsx` and `Skeleton.tsx`

- **IMPLEMENT** (`Tabs.tsx`):
  ```tsx
  type TabId = 'dashboard' | 'review' | 'infra'
  export function Tabs({ value, onChange }: { value: TabId; onChange: (t: TabId) => void }) {
    const tabs: { id: TabId; label: string }[] = [
      { id: 'dashboard', label: 'Dashboard' },
      { id: 'review', label: 'Review' },
      { id: 'infra', label: 'Infra' },
    ]
    return (
      <nav className="flex gap-1 border-b border-zinc-200 px-6 bg-white">
        {tabs.map(t => (
          <button key={t.id} onClick={() => onChange(t.id)}
            className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px transition
              ${value === t.id ? 'border-zinc-900 text-zinc-900' : 'border-transparent text-zinc-500 hover:text-zinc-800'}`}>
            {t.label}
          </button>
        ))}
      </nav>
    )
  }
  ```

- **IMPLEMENT** (`Skeleton.tsx`):
  ```tsx
  export function Skeleton() {
    return (
      <div className="p-6 space-y-4 animate-pulse">
        <div className="grid grid-cols-3 gap-6">
          {[1,2,3].map(i => <div key={i} className="h-40 bg-zinc-100 rounded-xl"/>)}
        </div>
        <div className="h-64 bg-zinc-100 rounded-xl"/>
      </div>
    )
  }
  ```

### Task 24 — UPDATE `frontend/src/App.tsx` and `frontend/src/main.tsx`

- **IMPLEMENT** (`App.tsx`):
  ```tsx
  import { useState, useEffect } from 'react'
  import { Tabs } from '@/components/Tabs'
  import { Ledger } from '@/components/Ledger'
  import { EnvelopeRings } from '@/components/EnvelopeRings'
  import { UploadZone } from '@/components/UploadZone'
  import { TraceDrawer } from '@/components/TraceDrawer'
  import { ReviewQueue } from '@/components/ReviewQueue'
  import { InfraTab } from '@/components/InfraTab'
  import { useSSE } from '@/hooks/useSSE'
  import { useDashboard } from '@/store/dashboard'
  import type { DashboardEvent } from '@/types'

  export default function App() {
    const [tab, setTab] = useState<'dashboard' | 'review' | 'infra'>('dashboard')
    const [traceEntry, setTraceEntry] = useState<number | null>(null)
    const apply = useDashboard(s => s.apply)
    const setConnected = useDashboard(s => s.setConnected)

    // Single global subscription to /dashboard/stream
    useSSE<DashboardEvent>('/dashboard/stream', apply, setConnected)

    return (
      <div className="min-h-screen bg-zinc-50 text-zinc-900">
        <header className="bg-white border-b border-zinc-200 px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="font-bold text-xl">Agnes</div>
            <div className="text-sm text-zinc-500">YAML-driven DAG executor · live demo</div>
          </div>
        </header>
        <Tabs value={tab} onChange={setTab}/>
        {tab === 'dashboard' && (
          <div className="space-y-6">
            <EnvelopeRings/>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 px-6 pb-6">
              <Ledger onRowClick={setTraceEntry}/>
              <UploadZone/>
            </div>
          </div>
        )}
        {tab === 'review' && <ReviewQueue/>}
        {tab === 'infra' && <InfraTab/>}
        <TraceDrawer entryId={traceEntry} onClose={() => setTraceEntry(null)}/>
      </div>
    )
  }
  ```
- **GOTCHA**: A single global SSE subscription on App. Don't subscribe in each component — that creates 5+ EventSource connections per page.
- **VALIDATE**: Page boots; tabs switch; SSE connection visible in browser DevTools → Network → EventStream tab.

### Task 25 — CREATE `frontend/.env.development` and `frontend/.env.production`

- **IMPLEMENT**:
  - `.env.development` (one line): `VITE_API_BASE=`
  - `.env.production` (one line): `VITE_API_BASE=`  (placeholder; the user fills if deploying)
- **GOTCHA**: An empty `VITE_API_BASE` makes the api wrapper use relative URLs. In dev that hits the Vite proxy. In prod served from same origin as backend, that also works.
- **VALIDATE**: `cat frontend/.env.development` returns the expected contents.

### Task 26 — UPDATE `frontend/index.html` — set the title

- **IMPLEMENT**: Replace the default `<title>Vite + React + TS</title>` with `<title>Agnes</title>`.
- **VALIDATE**: Browser tab reads "Agnes".

### Task 27 — RUN full validation suite

- **IMPLEMENT**: Run all four levels (see below). Manual rehearsal twice.
- **VALIDATE**: All commands return zero exit codes; the demo path lands in <30s on stage.

---

## TESTING STRATEGY

### Unit Tests (backend)

`backend/tests/test_list_endpoints.py` — covers default pagination, offset paging, status filter, invalid limit (422), envelope filter by employee+period, used_cents rollup, negative-allocation reversal.

### Integration Tests (backend)

The new GETs are exercised end-to-end via the existing `app` fixture (from `test_runs_api.py` style). No additional integration test scaffolding needed.

### Manual / Visual Tests (frontend)

The frontend has no unit tests for the hackathon. Validation is **manual rehearsal twice in a row**:

1. Boot backend + frontend (dev).
2. Trigger fake Swan webhook (`curl -X POST http://localhost:8000/swan/webhook -H 'x-swan-secret: ...' -d @fixture.json`); confirm a row appears in the ledger within 5s.
3. Click the row; trace drawer opens, populated with lines/traces/decisions/cost.
4. Drag-drop a PDF to the upload zone; progress overlay shows nodes; accrual entry posts within 10s; envelope ring decreases.
5. Trigger `Transaction.Released`; reversal entry appears; envelope ring restores.
6. Switch to Review tab; if a low-confidence entry exists, approve it; row disappears, status updates to `posted`.
7. Switch to Infra tab; "connected" indicator is green.
8. Refresh page; everything hydrates from REST without losing state.
9. Kill backend; "disconnected" indicator turns red; restart backend; reconnects automatically (browser auto-retries `EventSource`).

### Edge Cases

- **Out-of-range limit (>200)** — backend returns 422.
- **Negative allocations** — `used_cents` correctly nets to 0 after reversal.
- **Envelope filtered by employee_id=99 (nonexistent)** — returns `{"items": []}` with 200, not 404.
- **SSE reconnect across backend restart** — browser auto-reconnects; events reaped during downtime are missed (no replay), but next REST refresh hydrates state.
- **PDF upload >50MB** — backend may reject; UI shows alert from the fetch error.
- **Tailwind v4 dynamic class strings** — verified that no component uses `bg-[${var}]` patterns.
- **StrictMode double-mount in dev** — `useSSE` cleanup closes the dev-doubled EventSource.
- **Two browser tabs open** — each opens its own EventSource; backend dashboard bus is fanout-safe (per `event_bus.py`).
- **Money precision** — verify `total_cents`, `cap_cents`, `used_cents`, `cost_micro_usd` all stay as integers in JSON; no `1.0`-style floats.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Backend Syntax & Style

```bash
cd "/home/developer/Projects/HEC Paris"
# Money-path float audit (per CLAUDE.md / RealMetaPRD §7.7)
grep -rE "float\(.+(_cents|amount|cost)" backend/ && echo "FAIL: floats on money path" && exit 1 || echo "OK"
# Python imports / quick syntax check
python -m py_compile backend/api/runs.py backend/api/main.py
```

### Level 2: Backend Unit + Integration Tests

```bash
cd "/home/developer/Projects/HEC Paris"
# New tests
python -m pytest backend/tests/test_list_endpoints.py -v
# Full suite (must stay green; CORS install should be invisible to existing tests)
python -m pytest backend/tests/ -q
```

Per `CLAUDE.md`, run the full suite via Bash with `run_in_background: true` and poll output, given the 15s per-test cap. The new test file should complete in < 5s.

### Level 3: CORS Smoke Test

```bash
cd "/home/developer/Projects/HEC Paris"
AGNES_DATA_DIR=./data uvicorn backend.api.main:app --workers 1 --port 8000 &
SERVER_PID=$!
sleep 2

# Confirm CORS header present
curl -i -H 'Origin: http://localhost:5173' http://localhost:8000/healthz | grep -i 'access-control-allow-origin'
# Expected: access-control-allow-origin: http://localhost:5173

# Confirm the new GETs respond
curl -s 'http://localhost:8000/journal_entries?limit=5' | python -c "import json,sys;d=json.load(sys.stdin);print('items:', len(d['items']),'total:', d['total'])"
curl -s 'http://localhost:8000/envelopes?employee_id=1&period=2026-04' | python -c "import json,sys;d=json.load(sys.stdin);print('rings:', len(d['items']))"

kill $SERVER_PID
```

### Level 4: Frontend Build + Typecheck

```bash
cd "/home/developer/Projects/HEC Paris/frontend"
npx tsc --noEmit             # zero errors
npm run build                 # vite build; emits dist/
ls -la dist/                  # confirm assets present
```

### Level 5: Manual End-to-End Rehearsal

Per the TESTING STRATEGY → Manual section above. Run **twice in a row** on a fresh DB to confirm idempotency.

```bash
# Terminal 1: backend
cd "/home/developer/Projects/HEC Paris"
AGNES_DATA_DIR=./data uvicorn backend.api.main:app --workers 1 --port 8000

# Terminal 2: frontend
cd "/home/developer/Projects/HEC Paris/frontend"
npm run dev

# Browser: http://localhost:5173
# Walk RealMetaPRD §11 demo script.
```

---

## ACCEPTANCE CRITERIA

- [ ] `GET /journal_entries?limit=N&offset=M&status=...` returns `{items, total, limit, offset}` with newest-first ordering.
- [ ] `GET /envelopes?employee_id=X&period=YYYY-MM` returns `{items: [...]}` with rolled-up `used_cents` from `budget_allocations`, including handling negative allocations from reversals.
- [ ] Both endpoints validate query params (422 on out-of-range `limit`, malformed `period`).
- [ ] All 7 tests in `test_list_endpoints.py` pass.
- [ ] Full backend pytest suite remains green.
- [ ] `CORSMiddleware` installed in `main.py` with origin `http://localhost:5173`; preflight + actual responses carry `Access-Control-Allow-Origin`.
- [ ] `frontend/` directory bootstrapped with React 18 + Vite + TS + Tailwind v4 + Zustand 5 + Motion.
- [ ] `frontend/vite.config.ts` proxies all 10 backend prefixes.
- [ ] Six Phase F components rendered: `EnvelopeRings`, `Ledger`, `UploadZone` (+ `RunProgressOverlay`), `TraceDrawer`, `ReviewQueue`, `InfraTab`, plus `Tabs` and `Skeleton`.
- [ ] Single global `useSSE('/dashboard/stream')` subscription on `App.tsx`.
- [ ] Per-run SSE subscription on `RunProgressOverlay` only when `activeRunId != null`.
- [ ] Drag-drop accepts a single PDF, posts to `/documents/upload` with optional `employee_id`, opens per-run progress overlay.
- [ ] Click on any ledger row opens `TraceDrawer` populated from `/journal_entries/{id}/trace`.
- [ ] Approve button on `ReviewQueue` calls `POST /review/{id}/approve` with `approver_id=1`.
- [ ] Money displayed correctly in euros (€XX.YY); raw API responses still in integer cents.
- [ ] Live demo: drop fake Swan webhook → ledger row appears within 5s; envelope ring updates; trace drawer opens on click.
- [ ] Live demo: drop PDF → accrual posted within 10s; ring updates; trace drawer drills down to vision agent decision + cost.
- [ ] Live demo: drop `Transaction.Released` → reversal row appears; ring restores.
- [ ] No TypeScript errors in `frontend/`; build emits to `dist/`.
- [ ] No regressions in any pre-existing backend test.
- [ ] No floats introduced on money paths.
- [ ] No emoji in source files unless explicitly approved by the user (mind-check after Task 19's `RunProgressOverlay`; if user prefers no emoji, swap glyphs).
- [ ] PRD and briefing files unchanged (`git diff --stat Orchestration/PRDs/ "Dev orchestration/"` empty).

---

## COMPLETION CHECKLIST

- [ ] Tasks 1–5 (backend) completed in order.
- [ ] Tasks 6–26 (frontend) completed in order.
- [ ] Each task's validation passed before moving to the next.
- [ ] All five validation levels executed successfully.
- [ ] Manual rehearsal (Level 5) walked twice in a row.
- [ ] No linting, type-checking, or money-path-float errors.
- [ ] Acceptance criteria all met.
- [ ] PRD and briefing files unchanged.
- [ ] Update `README.md` "Status" line + "Project structure" section to add `frontend/` and remove the stale "Not yet implemented" block (lines 154–160).
- [ ] Update `CLAUDE.md` repository-layout section to add `frontend/` if appropriate.

---

## NOTES

### On scope discipline

This plan deliberately does NOT add:
- A `GET /runs?limit=N` listing endpoint (Infra tab gracefully degrades; not on the demo critical path).
- A `GET /employees` endpoint (frontend hardcodes Tim/Marie/Paul per `audit/0002_seed_employees.py`; matches the seed reality).
- Real authentication (no JWT, no session cookies — internal demo).
- Cursor-based pagination (offset is fine for ≤200 rows).
- Server-side filtering by employee on `/journal_entries` (the wedge query lives in the trace, not the list).
- A backend `/dashboard/state` consolidated bootstrap endpoint (the two new GETs + the existing `/healthz` are enough for hydration).
- React 19 (Zustand 5 + React 19 internals friction; React 18.3 is the stable choice for stage).
- Storybook, Playwright, Vitest, ESLint flat-config tweaks (manual rehearsal is the validation gate; tests are backend-only).

If the temptation arises to add any of these "while I'm in here," resist — out of scope.

### On the SSE wire format

Agnes's existing SSE wire format is locked-in:
- `data: {json}\n\n` — single line per event, no `id:`, no `event:`.
- `: heartbeat\n\n` every **15 seconds**.
- Per-run stream terminates after `pipeline_completed` or `pipeline_failed`; dashboard stream is long-lived.

The `REF-SSE-STREAMING-FASTAPI.md` reference describes a **richer** wire format (`id:` + `event:` + 30s heartbeat + 60s grace + `Last-Event-ID` reconnect dedup). **Do not retrofit that onto Agnes.** The frontend's `useSSE` hook uses `onmessage` (not `addEventListener('node_started', ...)`) precisely because the existing wire format has no `event:` line.

### On money safety

The backend keeps money as integer cents end-to-end. The frontend formats with `centsToEuros(cents)`. The CI grep audit (`grep -rE "float\(.+(_cents|amount|cost)" backend/`) must continue to return zero matches after this work lands.

### On Tailwind v4 specifically

Tailwind v4 is content-auto-detecting, no config file. The single CSS line `@import "tailwindcss";` is the entire setup. **Do not** add v3-style `@tailwind base/components/utilities` directives — they're silently ignored under v4 and you'll have unstyled output. **Do not** install `postcss`, `autoprefixer`, or `@tailwindcss/postcss` — those are v3 patterns.

If you see unstyled output, the most likely cause is a stale `tailwind.config.js` left behind by an old guide. Delete it.

### On Vite proxy + SSE

In Vite 5/6, the default `server.proxy` config forwards SSE connections correctly. In Vite 7, some users report buffered streams (vitejs/vite#13522). If the dashboard SSE never fires in the dev server but works against a direct `curl` to the backend, add a `configure` callback on the proxy entries:

```ts
'/dashboard': {
  target: BACKEND, changeOrigin: true,
  configure: (proxy) => proxy.on('proxyRes', (res) => {
    res.headers['x-accel-buffering'] = 'no'
  }),
},
```

The backend already sets `X-Accel-Buffering: no` on SSE responses (`runs.py` and `dashboard.py`), so this is belt-and-suspenders.

### On StrictMode

React 18 StrictMode double-mounts effects in dev. `useSSE` handles this correctly via the cleanup function in `useEffect`. Do not disable StrictMode to silence dev double-connect warnings — production doesn't double-mount, and you'd lose the dev-time guarantee that effects are robust to re-runs.

### Confidence score for one-pass implementation

**8/10.**

Reasons for 8 not 10:
- The two `ledger.entry_posted` payload shapes (nested-`data` from gl_poster vs flat from approve) are a real spec ambiguity. The store reducer handles both, but visual edge cases (e.g. an entry that goes review → approve and is *not* in the ledger yet) may render incomplete data until the next REST refresh. Acceptable for hackathon; flagged here so the implementing agent knows to test approve-on-cold-state.
- Vite 7 SSE proxy is occasionally flaky. The `configure` workaround is documented but not always needed. Implementing agent should test the dashboard SSE path early.
- Tailwind v4 is recent enough (v4.0 GA late 2024) that some old AI-training docs still reference v3 patterns. Insist on the v4 install: one CSS line, `@tailwindcss/vite` plugin, no config.
- Motion (rebranded Framer Motion) — package name and import path were renamed; legacy snippets using `import { motion } from 'framer-motion'` will not work. Use `import { motion, AnimatePresence } from 'motion/react'`.
- The Phase F components have visual-design judgment calls (ring size, hover states, color tones) that the plan can describe but not fully script. Iterate on Task 17, 18, 20 with `npm run dev` open.

Mitigations: every task has an executable validation, the audit phase is unnecessary because Phase 2 backend is already mapped (see CONTEXT REFERENCES), and the manual rehearsal step (Level 5, twice in a row) catches any remaining edge cases before stage.
