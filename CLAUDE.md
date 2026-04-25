# CLAUDE.md

> **Maintenance flag — read first.**
> This file and `README.md` describe the live state of the project.
> Whenever a new piece of functionality lands (new module, new pipeline,
> new schema migration, new endpoint, new dependency), update both:
> - `README.md` — what works today + project layout.
> - `CLAUDE.md` (this file) — directory map + reference-doc pointers.
> Out-of-date scaffolding here is worse than missing scaffolding.

## Product, in one paragraph

Agnes is a YAML-driven DAG executor over three SQLite databases
(`accounting.db` / `orchestration.db` / `audit.db`). Every agent call
writes a `(decision, cost, employee)` triple, so the wedge query
"how much did Anthropic bill us this month, per employee" is a single
SQL `GROUP BY`. Phase 1 (metalayer foundation) is in place; Phases D
(Swan), E (documents), and F (frontend) are next.

## Repository layout

```
backend/                         # implementation (see README.md for detail)
  api/                           # FastAPI app — Phase 1 ships /healthz only
  orchestration/                 # executor, DSL, registries, cache, audit, runners
  tests/                         # 12 test files, 70 cases
data/                            # runtime DB files + PDF blobs
Orchestration/
  PRDs/RealMetaPRD.md            # the contract — every Phase 1 line cites a § here
  PRDs/MetaPRD.md                # predecessor PRD
  PRDs/PRD1.md                   # original Phase 1 framing
  Plans/phase1-metalayer-foundation.md   # the executed plan
  research/                      # ANTHROPIC_SDK_STACK_REFERENCE, CEREBRAS_*
  from agents for agents/PRD1_VALIDATION_BRIEFING.md
Dev orchestration/
  _exports_for_b2b_accounting/   # 01..05 — orchestrator, DSL, sqlite, agents, swan
  tech framework/                # REF-FASTAPI, REF-SQLITE, REF-SSE, REF-ADK, briefing
  swan/                          # Swan API reference (used Phase D onwards)
pyproject.toml                   # deps + optional [adk] / [pydantic_ai] extras
pytest.ini                       # asyncio_mode = auto
.env.example                     # canonical env-var names
```

## Hard rules carried over from RealMetaPRD

- Money is **integer cents**. No floats on a money path. Enforced by a
  CI grep audit (see plan §VALIDATION).
- DB writes go through `store.writes.write_tx` (BEGIN IMMEDIATE +
  per-DB lock). Never call `conn.commit()` directly.
- Run `uvicorn` with `--workers 1`. The per-DB `asyncio.Lock` does not
  coordinate across processes (RealMetaPRD §9.5).
- Pipelines are data, not code. New event types ship as YAML + a tool +
  a routing.yaml line — never executor surgery.

## When to update this file

- New Phase lands → refresh "in one paragraph" + add the new top-level
  module to the layout.
- New reference doc dropped under `Dev orchestration/` or
  `Orchestration/research/` → list it.
- New env var or runtime invariant → add a bullet under "Hard rules".
