# SQLite Backbone Reference Guide
## Autonomous-CFO Hackathon — Two-DB Data Layer

> Research-validated reference for Claude instances building the SQLite layer that backs the meta-PRD foundation
> (`Orchestration/PRDs/MetaPRD.md`). Companion to `REF-FASTAPI-BACKEND.md` and `REF-SSE-STREAMING-FASTAPI.md`.
> Stack: Python 3.10+, `aiosqlite` over stdlib `sqlite3`, FastAPI, two SQLite files (`accounting.db`,
> `orchestration.db`), WAL mode, integer-cents money, declarative pipeline runtime.

This document is a **guide, not a spec**. The PRD locks the *shape* of the data layer (two DBs, append-only events,
real `decision_traces` table, integer cents). What the next Claude instance still has to choose — driver-level details,
the writer-serialization mechanism, the migration runner, the trigger-vs-app trade-offs — is what this guide is for.
Read it before you write the first `CREATE TABLE`.

---

## Why This Layer Exists (Mapping to the Meta-PRD)

Every higher-order capability in the demo (live ledger, per-employee budget envelopes, decision-trace UI,
goal-driven campaigns, agentic DD pack) compiles down to four obligations on the data layer:

| Obligation (PRD §) | Concrete demand on SQLite |
|---|---|
| §6 Two-database split | `accounting.db` (canonical domain) + `orchestration.db` (run history). Both WAL, both `foreign_keys=ON`. Path injected per pipeline run via `AgnesContext`. |
| §6 Append-only, idempotent, replayable | `swan_events`, `pipeline_runs`, `pipeline_events` are insert-only; provider event_id is the idempotency boundary. |
| §6 Integer cents, no floats | All money columns `INTEGER`. VAT splits with documented rounding. Floats are an outage waiting to happen. |
| §7.2 Hard invariants | `SUM(debit_cents) = SUM(credit_cents)` per entry. Recorded balance = Swan booked balance after every post. Every `journal_lines.id` has at least one `decision_traces` row. |
| §7.4 Decision trace is a real table, not JSON sidecar | `decision_traces` joined to every line. Lint rule: PR fails if a `journal_lines` insert ships without a sibling `decision_traces` insert. |
| §12 Phase-3 exit | Postgres at ~10k tx/day. Schema portability matters from Day 1. |

If a design decision below conflicts with one of these, the PRD wins. Everything else is a judgment call.

---

## Project Structure

```
backend/
├── api/
│   ├── main.py               # FastAPI app, lifespan opens both DBs
│   └── agnes_context.py      # holds the two aiosqlite connections + per-DB write locks
├── db/
│   ├── connection.py         # open_db(path), _configure_pragmas()
│   ├── transactions.py       # write_tx() async context manager — the only sanctioned write path
│   ├── reconcile.py          # cross-DB invariant checks (decision_trace ↔ pipeline_run)
│   └── backup.py             # VACUUM INTO helper
├── schema/
│   ├── accounting.sql        # bootstrap-only — fresh DB shape
│   ├── orchestration.sql     # bootstrap-only
│   └── seeds/
│       ├── pcg_subset.sql
│       └── demo_seed.sql
└── migrations/
    ├── __init__.py            # registry runner — applies in name order, skips _migrations rows
    ├── 0001_init_accounting.py
    ├── 0002_init_orchestration.py
    ├── 0003_decision_trace.py
    └── ...
```

The split between `schema/` and `migrations/` is load-bearing — see *Migrations and the bootstrap/migration split* below.

---

## Driver Landscape — What to Import

| Option | Sync / Async | Real concurrency on SQLite? | Verdict for this project |
|---|---|---|---|
| `sqlite3` (stdlib) | Sync, blocking | N/A (SQLite is single-writer regardless) | Fine for migrations, CLI tools, tests. Will block the event loop if called from a request handler. |
| `aiosqlite` | "Async" — single dedicated thread per `Connection`, asyncio queue in front | Doesn't add parallelism, but keeps the event loop unblocked | **Best fit.** Matches the FastAPI ref doc. Coroutines sharing one connection are auto-serialized through that thread, so no thread-safety footguns inside the event loop. |
| SQLAlchemy 2.0 async / SQLModel | Async wrapper (greenlet bridge) | Same underlying SQLite single-writer constraint | Heavyweight for a hackathon. Adds a layer between you and CHECK constraints, triggers, ATTACH, PRAGMAs — all of which the PRD wants you to think about explicitly. Reconsider only if Phase 3+ Postgres migration is imminent. |

**Recommendation, not a lock-in.** Start with `aiosqlite` + raw SQL. Pydantic v2 models live at the I/O boundary
(webhook payloads, agent outputs). The data layer stays SQL-first because the PRD's invariants are easier to express
in SQL than in an ORM.

```python
# requirements.txt (data-layer slice)
aiosqlite>=0.19
pydantic>=2.5
pyyaml>=6.0
rapidfuzz>=3.0          # counterparty fuzzy match (PRD §8)
# stdlib: sqlite3, decimal, json, hmac
```

> **Version check on first boot.** Python's bundled SQLite varies by base image. JSON `->`/`->>` operators landed in
> SQLite **3.38** (Feb 2022); generated columns in 3.31. Verify on startup and refuse to boot below 3.38:
> ```python
> import sqlite3
> assert tuple(map(int, sqlite3.sqlite_version.split("."))) >= (3, 38, 0), \
>     f"SQLite {sqlite3.sqlite_version} too old; need 3.38+ for JSON operators"
> ```

---

## The Two-Database Split

### What goes where

```
accounting.db                        orchestration.db
─────────────────                    ────────────────
counterparties                       swan_events           ← raw webhook envelopes
counterparty_identifiers             pipeline_runs         ← one row per run
journal_entries                      pipeline_events       ← per-node trace (append-only)
journal_lines                        _migrations
decision_traces ───────────┐
account_rules              │
budget_envelopes           │  agent_run_id is a LOGICAL FK
budget_allocations         │  across DB files (not enforced)
_migrations                └──→ pipeline_runs.id (string identity)
```

`accounting.db` is the system of record. Wiping it loses real money state. `orchestration.db` is the journal of what
happened — wiping it loses audit history but not domain truth. They run on different lifecycles and (eventually)
different backup cadences.

### Cross-database references — the part that bites

SQLite enforces foreign keys *only within one file*. The PRD's `decision_traces.agent_run_id → pipeline_runs.id` is a
**logical FK**, not enforced by the engine. Three patterns to keep it honest:

1. **Application-level invariant.** Wrap writes so the `pipeline_runs` row is inserted before the `decision_traces`
   row that references it. Both inside the same coroutine, both committed before the webhook returns 2xx.
2. **`ATTACH DATABASE` for read-side joins.** Open one connection with both DBs attached for reporting / debug / replay.
   *Do not* use ATTACH for the hot write path — it complicates lock semantics.
3. **Periodic reconciliation.** A startup or hourly job runs:
   ```sql
   ATTACH DATABASE 'orchestration.db' AS orch;
   SELECT id FROM main.decision_traces
   WHERE agent_run_id NOT IN (SELECT id FROM orch.pipeline_runs);
   ```
   Log violations; do not auto-delete (audit trail).

> **Cross-DB transactions are NOT atomic in WAL.** With WAL on `main`, a multi-DB COMMIT is per-file atomic, not
> as a set. A host crash mid-COMMIT can leave one updated and the other not. Treat `orchestration.db` as the source
> of truth for run identity and write to it *first*; if the second write fails, replay re-creates the dependent row.

### ATTACH DATABASE caveats

- ATTACH must happen **outside** an active transaction.
- `PRAGMA foreign_keys = ON` enforces FKs across *all* attached schemas on that connection — surprising if a table
  name collides.
- Default cap of attached DBs is 10; configurable up to `SQLITE_MAX_ATTACHED` (compile-time, often 125).

---

## Connection Lifecycle — Where to Open, Where to Close

The FastAPI `lifespan` pattern from `REF-FASTAPI-BACKEND.md` extends to two DBs cleanly. One long-lived connection
per DB, stored on `app.state`, plus one `asyncio.Lock` per DB to serialize writes.

```python
# api/main.py
from contextlib import asynccontextmanager
import aiosqlite
import asyncio
from fastapi import FastAPI

from .config import ACCOUNTING_DB, ORCHESTRATION_DB
from db.connection import _configure_pragmas
from db.migrations import run_migrations

@asynccontextmanager
async def lifespan(app: FastAPI):
    accounting = await aiosqlite.connect(ACCOUNTING_DB)
    orchestration = await aiosqlite.connect(ORCHESTRATION_DB)
    accounting.row_factory = aiosqlite.Row
    orchestration.row_factory = aiosqlite.Row

    await _configure_pragmas(accounting)
    await _configure_pragmas(orchestration)

    await run_migrations(accounting, target_db="accounting")
    await run_migrations(orchestration, target_db="orchestration")

    app.state.accounting = accounting
    app.state.orchestration = orchestration
    app.state.accounting_write_lock = asyncio.Lock()
    app.state.orchestration_write_lock = asyncio.Lock()

    yield
    await accounting.close()
    await orchestration.close()
```

> **Why one connection per DB and not a pool?** With `aiosqlite`, every connection has its own dedicated thread.
> Multiple connections to the same DB don't add real parallelism (SQLite still single-writer-locks the file) and
> *do* multiply the chance of `SQLITE_BUSY`. A second connection becomes worth it only when read latency under
> long-running reporting queries starts hurting the webhook path — at which point, add a *read-only* connection,
> not a pool.

> **`check_same_thread` trap.** Stdlib `sqlite3.Connection` defaults to `check_same_thread=True` and refuses
> cross-thread use. `aiosqlite` sets it `False` because it owns the worker thread for you. If you ever drop down to
> raw `sqlite3` from a FastAPI threadpool sync handler, set `check_same_thread=False` *and* serialize externally with
> a lock. Easier path: don't drop down.

---

## PRAGMAs — Set Every One, Justify Every One

PRAGMAs are *per-connection* (with a few exceptions like `journal_mode` which persist on the file). Re-set them on
every connection at open time. Source: [sqlite.org/pragma.html](https://www.sqlite.org/pragma.html).

```python
# db/connection.py
async def _configure_pragmas(conn):
    await conn.execute("PRAGMA journal_mode = WAL")           # required by PRD
    await conn.execute("PRAGMA foreign_keys = ON")            # required; off by default
    await conn.execute("PRAGMA synchronous = NORMAL")         # WAL + NORMAL is durable, faster than FULL
    await conn.execute("PRAGMA busy_timeout = 5000")          # 5s — sleep-and-retry on lock
    await conn.execute("PRAGMA wal_autocheckpoint = 1000")    # ~4MB of WAL before passive checkpoint
    await conn.execute("PRAGMA journal_size_limit = 67108864")# 64MB physical cap on -wal file
    await conn.execute("PRAGMA temp_store = MEMORY")          # spills go to RAM, not disk
    await conn.execute("PRAGMA cache_size = -65536")          # 64MB page cache (negative = KiB)
    await conn.execute("PRAGMA mmap_size = 134217728")        # 128MB read-side mmap; set 0 to disable
    await conn.commit()
```

| PRAGMA | Why for *this* workload |
|---|---|
| `journal_mode = WAL` | Concurrent reader + single writer. Persists across reopens. PRD-mandated. |
| `foreign_keys = ON` | OFF by default for historical reasons. Without it, the FK from `journal_lines.trace_id → decision_traces.id` is decorative. |
| `synchronous = NORMAL` | Right default for WAL. `FULL` adds an fsync per commit — measurable slowdown for high-frequency event ingestion. `NORMAL` only loses the *last-committed transaction* on power loss. Acceptable for the demo (Swan retries); reconsider for a regulated production system. |
| `busy_timeout = 5000` | Sleep-and-retry on lock acquisition. Cheaper and more correct than rolling your own retry loop. **Does not protect read→write upgrade**; see WAL section below. |
| `wal_autocheckpoint = 1000` | Default. Auto-runs PASSIVE checkpoint when WAL hits 1000 pages. Don't disable unless you have a manual checkpoint loop. |
| `journal_size_limit = 64MB` | Physical cap on the `-wal` file after a successful checkpoint. Last line of defence against unbounded WAL growth. |
| `cache_size = -64MB` | Default 2MB will thrash under join-heavy reports. Per-connection. |
| `temp_store = MEMORY` | Temp tables and sort spills to RAM. Cheap win on a small dataset. |
| `mmap_size = 128MB` | Memory-maps reads. Skips userland buffer copies. Helpful for read-heavy reporting endpoints. |

> **Don't set without justification.** `locking_mode = EXCLUSIVE` (breaks multi-process access),
> `synchronous = OFF` (can corrupt the DB on crash), `journal_mode = MEMORY` (loses durability entirely).
> If you find yourself reaching for these to fix a perf problem, the problem is upstream.

---

## WAL Semantics and the Single-Writer Pattern

Per [sqlite.org/wal](https://www.sqlite.org/wal.html):

- **One writer at a time per file.** Structural — there is exactly one WAL.
- **Readers don't block writers, writers don't block readers.** Each reader pins an "end mark" in the WAL.
- WAL itself does not lift the single-writer ceiling. It changes who-blocks-whom.

### `BEGIN IMMEDIATE` vs DEFERRED — the rule

```python
# WRONG: silent SQLITE_BUSY upgrade failure
await conn.execute("BEGIN")               # DEFERRED, starts as a reader
row = await conn.execute("SELECT ...").fetchone()
# ... think ...
await conn.execute("INSERT ...")          # tries to upgrade; if another writer slipped in,
                                          # this raises SQLITE_BUSY and busy_timeout DOES NOT HELP
```

```python
# RIGHT: claim the write lock at BEGIN
await conn.execute("BEGIN IMMEDIATE")     # waits up to busy_timeout, then acquires or fails cleanly
row = await conn.execute("SELECT ...").fetchone()
await conn.execute("INSERT ...")
await conn.commit()
```

**Rule.** Any transaction that *will* write uses `BEGIN IMMEDIATE`. DEFERRED is for read-only blocks only.
In WAL mode `IMMEDIATE` and `EXCLUSIVE` are identical — both grab the write lock at BEGIN.

### The "single writer per DB" mitigation, in code

The PRD's mitigation for risk #5 ("SQLite WAL contention") is "single writer per DB by convention." Two viable
shapes:

```python
# Shape A — module-level asyncio.Lock per DB. Simple, obvious, hackathon-correct.
async with app.state.accounting_write_lock:
    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute(...)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
```

```python
# Shape B — dedicated writer task pulling from an asyncio.Queue. Pipeline coroutines enqueue
# write intents; one consumer drains. Better fairness, gives natural backpressure, costs more code.
# Reach for this only if Shape A starts contending visibly.
```

**Pick Shape A for Phase 1.** It maps directly onto the PRD's wording. The `write_tx` context manager below makes
forgetting impossible.

### `write_tx` — the only sanctioned write path

```python
# db/transactions.py
from contextlib import asynccontextmanager

@asynccontextmanager
async def write_tx(conn, lock):
    """Every write to either DB goes through this. Commit on success, rollback on exception."""
    async with lock:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

# Usage:
async with write_tx(app.state.accounting, app.state.accounting_write_lock) as db:
    await db.execute("INSERT INTO journal_entries ...")
    await db.executemany("INSERT INTO journal_lines ...", lines)
    # commit happens automatically on context exit
```

> **Code-review rule.** A bare `await db.execute("INSERT ...")` outside `write_tx` fails review. Forgotten
> `commit()` is the #1 silent SQLite bug — `aiosqlite` is autocommit-off and will swallow the write when the
> connection closes with no exception raised.

### WAL file growth — the checkpoint starvation trap

`accounting.db-wal` can grow without bound if a long-lived reader pins the WAL — the auto-checkpoint can't reclaim
space because the reader's snapshot might still need old frames. Loke.dev hit a 20 GB WAL on a 2 GB main DB this way
([loke.dev/blog/sqlite-checkpoint-starvation-wal-growth](https://loke.dev/blog/sqlite-checkpoint-starvation-wal-growth)).

Mitigations for this project:

- `journal_size_limit = 64MB` (set above) — physical cap after each successful checkpoint.
- Periodic background task: every 60s run `PRAGMA wal_checkpoint(TRUNCATE)`. Schedule on an idle window, don't run
  it inside a hot webhook path. `TRUNCATE` waits for all readers to drain and then zeroes the file.
- Never hold a read transaction across `await` points that wait on slow I/O (LLM calls, HTTP). Read into Python,
  close the cursor, then await.
- Detect starvation: `PRAGMA wal_checkpoint(PASSIVE)` returns `(busy, log_pages, checkpointed)`. If the file size
  doesn't drop, a reader is pinning it.

---

## Append-Only Event Tables and Idempotency

The three append-only tables (`swan_events`, `pipeline_runs`, `pipeline_events`) are the spine of replayability.
Never `UPDATE`, never `DELETE`. `INSERT OR IGNORE` is the idempotency boundary.

```sql
-- orchestration.db
CREATE TABLE swan_events (
    id          INTEGER PRIMARY KEY,
    event_id    TEXT NOT NULL UNIQUE,            -- Swan's eventId; idempotency key
    event_type  TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    payload     TEXT NOT NULL,                   -- raw envelope JSON
    processed   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (json_valid(payload))                  -- reject garbage at write time
) STRICT;

CREATE INDEX idx_swan_events_unprocessed ON swan_events(processed) WHERE processed = 0;
```

> **`STRICT` tables.** SQLite's default type affinity is advisory — you can store text in an INTEGER column.
> Add `STRICT` to every new table from Day 1 and SQLite enforces declared types. Cheap insurance now, eliminates
> the worst class of Postgres-portability bugs later.

### The idempotent claim — two-statement pattern

`INSERT OR IGNORE` alone protects only the *insert*. The downstream pipeline still has to know whether *this*
coroutine is the one that should run.

```python
async def claim_swan_event(db, lock, event_id, event_type, payload):
    async with write_tx(db, lock) as tx:
        cur = await tx.execute(
            "INSERT OR IGNORE INTO swan_events(event_id, event_type, project_id, resource_id, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, event_type, ...),
        )
        if cur.rowcount == 0:
            return None                          # duplicate redelivery; another coroutine owns it

        # Atomically claim the work in the same tx
        await tx.execute(
            "UPDATE swan_events SET processed = 1 WHERE event_id = ? AND processed = 0",
            (event_id,),
        )
        return event_id
```

**The trap.** People set `processed=1` *after* the pipeline succeeds. A crash mid-pipeline then leaves
`processed=0` forever (replay never happens) or, with retries, runs the pipeline twice. The two-phase fix:
`processed=1` *claims* the work (atomically, in the same tx as the INSERT); a separate `result_json` /
`completed_at` records terminal state. The replay tool re-runs anything claimed-but-not-completed past a stale
threshold.

### Why `event_id TEXT NOT NULL UNIQUE` and not internal sequence numbers

The provider's event ID is the only thing both sides agree on. An internal autoincrement `id` gets reassigned on
retries, replays, restores, or import-from-prod-to-staging — it's not a stable identity. The provider's `event_id`
is the deduplication key; the internal `INTEGER PRIMARY KEY` is just for fast joins / ordering.

### Out-of-order webhook delivery — derive state, don't sequence it

The PRD calls out tolerance for `Booked` arriving before `Pending`. Two reconciliation models:

- **Sequence-based.** Trust an ordered field (Swan's `version` or `updated_at`); last-write-wins on the highest
  version. Loses transitions you didn't see if the highest one is corrupt or arrives alone.
- **State-based, commutative.** Treat each event as a *set membership claim*: this transaction is now in state X.
  The journal-entry-builder reads the *union* of all events for that transaction id and produces the journal entry
  from the current terminal state, regardless of arrival order. Idempotent re-derivation.

**Prefer state-based for this PRD.** The accounting impact is a function of `(transaction_id → terminal_state)`.
The pipeline DAG becomes safely re-runnable: replaying events in any order converges to the same `swan_transactions`
row.

---

## Money — Integer Cents, End to End

```sql
CREATE TABLE journal_lines (
    id           INTEGER PRIMARY KEY,
    entry_id     INTEGER NOT NULL REFERENCES journal_entries(id),
    account      TEXT NOT NULL,                 -- PCG account code
    debit_cents  INTEGER NOT NULL DEFAULT 0,
    credit_cents INTEGER NOT NULL DEFAULT 0,
    description  TEXT,
    CHECK (debit_cents >= 0 AND credit_cents >= 0),
    CHECK (NOT (debit_cents > 0 AND credit_cents > 0)),       -- a line is debit OR credit, not both
    CHECK (debit_cents + credit_cents > 0),                   -- no zero-amount lines
    CHECK (typeof(debit_cents) = 'integer'),                  -- paranoia: defeat REAL affinity
    CHECK (typeof(credit_cents) = 'integer')
) STRICT;
```

`INTEGER` in SQLite is variable-length up to 8 bytes (signed 64-bit) — well past any plausible cents value.
Never `REAL`. Never `NUMERIC` masquerading as a float.

### Where floats sneak in (concrete list)

- `int / int` returns `float` in Python 3 — use `//` for integer division.
- Pydantic v2: a `float` annotation will silently coerce JSON `1234` to `1234.0`. Use `int` (or `Decimal` if you
  must), never `float`.
- JSON serialization: a Swan payload with `"amount": "12.34"` is a *string* — handle as
  `int(round(Decimal(payload_str) * 100))`. Never `float("12.34") * 100` (yields `1233.9999999999998`).
- `rapidfuzz` similarity scores are 0–100 floats. If you ever multiply a score *into* an amount, you've corrupted
  money. Keep score columns separate.
- SQLite REAL affinity: a column declared `amount REAL` will store integers as floats. Always declare money columns
  `INTEGER NOT NULL` and add the `CHECK(typeof(...) = 'integer')` above.

### VAT split with integer cents

100.00 € net at 20% VAT split across 3 lines: VAT total = 2000 cents. Naive split = 666 + 666 + 666 = 1998. Two cents
missing.

- **Largest-remainder method (Hare/Hamilton).** Floor each share, then distribute the leftover cents to lines with
  the largest fractional residual. Deterministic, auditor-friendly.
- **Banker's rounding (round-half-to-even).** Reduces directional bias over many transactions. Some EU tax regimes
  require it.

```python
from decimal import Decimal, ROUND_HALF_EVEN

def cents_round(d: Decimal) -> int:
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))

def split_vat(gross_cents: int, vat_rate_bps: int) -> tuple[int, int]:
    """vat_rate_bps = 1900 means 19.00%."""
    vat = cents_round(Decimal(gross_cents * vat_rate_bps) / Decimal(10000))
    net = gross_cents - vat
    return net, vat
```

> **Audit hygiene.** When you allocate a residual cent to a particular line, emit a `journal_lines` row tagged
> `description='rounding'` (or a dedicated `kind` column). The double-entry invariant must still hold *including*
> the residual. Auditors expect to see the rounding, not have it disappear.

### Enforcing `SUM(debit) = SUM(credit)` per entry

Two viable approaches:

- **Trigger-based** (`AFTER UPDATE OF posted_at` on `journal_entries`, RAISE(ABORT) if unbalanced). Defense in
  depth — any code path that posts a journal entry is checked. But: harder to debug, harder to port to Postgres
  (different syntax), and the "post via flag flip" pattern is ugly. Plain `AFTER INSERT ON journal_lines` doesn't
  work because lines are written one at a time and the sum is unbalanced *during* the insert sequence.
- **Application-level invariant tool** (`InvariantCheckerTool` per PRD §7.4). One Python function, deterministic,
  mockable, emits structured violations into the trace. Easy to extend ("every journal_line has a non-null
  decision_trace_id" is the next invariant).

**The PRD picks application-level.** Single chokepoint function plus a healthcheck endpoint that runs
`SELECT entry_id, SUM(debit_cents)-SUM(credit_cents) FROM journal_lines GROUP BY entry_id HAVING ... <> 0` as a
tripwire.

```python
async def post_journal_entry(db, lock, entry_id, lines, decision_trace_id):
    async with write_tx(db, lock) as tx:
        await tx.execute("INSERT INTO journal_entries ...")
        await tx.executemany("INSERT INTO journal_lines ...", lines)

        cur = await tx.execute(
            "SELECT COALESCE(SUM(debit_cents),0) - COALESCE(SUM(credit_cents),0) "
            "FROM journal_lines WHERE entry_id = ?",
            (entry_id,),
        )
        (delta,) = await cur.fetchone()
        if delta != 0:
            raise InvariantViolation(f"entry {entry_id} unbalanced: delta={delta}")
        # write_tx commits on context exit
```

> **"Freeze on failure" is not optional.** A single unbalanced journal entry corrupts every downstream report.
> The correct fix requires human review; there is no safe automated recovery. The PRD makes invariant violation
> halt the pipeline, page someone, and *refuse new writes* on that DB — because if you keep accepting writes on
> top of a violation, you make the human investigation harder, not easier.

---

## Decision Trace as a Real Table (Not a JSON Sidecar)

PRD §14 risk #7: "Decision trace becomes a JSON sidecar." Mitigation: `decision_traces` is a real table with FKs
from Day 1. Every `GLPosterTool` invocation MUST write the trace before it commits. Lint rule fails the PR if
`journal_lines` is inserted without a sibling `decision_traces` insert in the same code path.

```sql
-- accounting.db
CREATE TABLE decision_traces (
    id              INTEGER PRIMARY KEY,
    line_id         INTEGER REFERENCES journal_lines(id),
    source          TEXT NOT NULL,                  -- 'webhook' | 'agent' | 'rule' | 'human'
    agent_run_id    TEXT,                           -- LOGICAL FK to orchestration.pipeline_runs.id
    model           TEXT,                           -- e.g. 'claude-sonnet-4-6' (PRD pins per agent)
    prompt_hash     TEXT,                           -- SHA-256 of the rendered prompt
    alternatives    TEXT,                           -- JSON array of {label, score}
    rule_id         INTEGER,
    confidence      REAL,                           -- 0..1; multiplicative across pipeline (PRD §6)
    approver_id     INTEGER,
    approved_at     TEXT,
    parent_event_id TEXT,                           -- swan event_id or campaign id
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (alternatives IS NULL OR json_valid(alternatives)),
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
) STRICT;

CREATE INDEX idx_decision_traces_line ON decision_traces(line_id);
CREATE INDEX idx_decision_traces_run  ON decision_traces(agent_run_id);
```

The drill-back path the demo depends on:

```
journal_line → decision_trace → (logical) pipeline_run → pipeline_events (per-node trace)
                                                       ↓
                                                       parent_event_id → swan_events
```

This is what answers the judge's "what does the AI actually *do*?" question — every numeric claim in the demo
drills to a journal line and a decision trace.

---

## JSON Columns and Indexing

JSON1 is built into SQLite ≥ 3.38 with the `->`/`->>` operators. Use it for queryable JSON columns; validate at
write time with `CHECK (json_valid(...))`.

```sql
-- pipeline_events.data is a JSON TEXT column; we want fast filtering on node_name
ALTER TABLE pipeline_events ADD COLUMN node_name TEXT
    GENERATED ALWAYS AS (data ->> '$.node_name') VIRTUAL;
CREATE INDEX idx_pipeline_events_node ON pipeline_events(node_name);
```

A VIRTUAL generated column doesn't bloat storage; the index *is* materialized. Now
`WHERE data ->> '$.node_name' = ?` uses the index ([SQLite generated columns](https://sqlite.org/gencol.html)).

> **The silent NULL trap.** If the Swan payload changes `accountId` to `account.id`, every existing row returns
> NULL for the old extractor, every new row matches the new one, and no error fires. Defenses:
> - Version every JSON payload: `payload_version INTEGER NOT NULL`. Extractors switch on it.
> - Backfill eagerly when the path changes — one-shot UPDATE.
> - Add a CHECK that the extractor's result is non-NULL when you expect a value, so the schema rejects malformed
>   payloads at write time.

FTS5 (free-text search over decision rationale, counterparty names) is enabled by default in modern Python builds.
Verify on startup:

```python
try:
    cur = await conn.execute("CREATE VIRTUAL TABLE _fts_check USING fts5(x)")
    await conn.execute("DROP TABLE _fts_check")
except aiosqlite.OperationalError:
    log.warning("FTS5 unavailable")
```

For 100s of rows on hackathon scale, plain `LIKE '%foo%'` is fine — defer FTS5 until the DD-pack agent (Phase 4).

---

## Migrations and the Bootstrap/Migration Split

The PRD added `_migrations` from Day 1 because the reference doc flagged its absence as a gap. Two separate
artifacts, two separate jobs:

| Artifact | Job | Trap if conflated |
|---|---|---|
| `schema/*.sql` (bootstrap) | Fresh DB shape, used in tests, CI, dev-reset. Always reflects the *current* desired schema. | If you let bootstrap drift (edit a `CREATE TABLE` directly without writing a paired migration), production DBs (which run migrations) and fresh DBs (which run bootstrap) diverge silently. Tests pass against new bootstrap; prod runs old schema; bug surfaces under real data. |
| `migrations/*.py` (history) | Sequential, applied-once changes that carry an existing DB forward. | If you use migrations alone, a fresh test DB has to replay 50 migrations. If you use bootstrap alone, an existing DB has no upgrade path. |

**Discipline.** Any change to `schema/*.sql` requires a paired migration in the same PR. Add a CI test that boots
a fresh DB from `schema/`, then boots another from migrations-from-empty, then asserts the resulting schemas
(compare `sqlite_master`) are identical. Drift fails the build.

### Minimal migration runner

```python
# db/migrations/__init__.py
import importlib, pkgutil

async def run_migrations(conn, target_db: str):
    await conn.execute("""
      CREATE TABLE IF NOT EXISTS _migrations (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      ) STRICT
    """)
    cur = await conn.execute("SELECT name FROM _migrations")
    applied = {row[0] for row in await cur.fetchall()}

    for mod_info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if mod_info.name.startswith("_") or mod_info.name in applied:
            continue
        m = importlib.import_module(f"{__name__}.{mod_info.name}")
        if getattr(m, "TARGET_DB", "accounting") != target_db:
            continue
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await m.up(conn)
            await conn.execute("INSERT INTO _migrations(name) VALUES (?)", (mod_info.name,))
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
```

```python
# migrations/0003_add_decision_trace.py
TARGET_DB = "accounting"

async def up(conn):
    cur = await conn.execute("PRAGMA table_info(decision_traces)")
    if not await cur.fetchall():
        await conn.execute("""
            CREATE TABLE decision_traces (...) STRICT
        """)
```

The `PRAGMA table_info(...)` guard makes each migration idempotent on its own — a re-run is a no-op even if the
`_migrations` registry got out of sync (e.g., a previous run died between the ALTER and the registry insert).

### SQLite ALTER TABLE limits — the 12-step rewrite

`ALTER TABLE` is limited:

- DROP COLUMN added in 3.35 (March 2021), but **a column referenced by index, FK, view, trigger, or non-trivial
  CHECK still cannot be dropped directly**.
- No "ALTER COLUMN type"; no "MODIFY CHECK".

Use the [12-step rewrite](https://www.sqlite.org/lang_altertable.html#otheralter) when you need any of those:

```
1. PRAGMA foreign_keys = OFF;
2. BEGIN TRANSACTION;
3. Snapshot related schema (sqlite_master)
4. CREATE TABLE new_X (... new shape ...);
5. INSERT INTO new_X SELECT ... FROM X;
6. DROP TABLE X;
7. ALTER TABLE new_X RENAME TO X;
8. Recreate indexes / triggers / views
9. (Re-build any view referencing X)
10. PRAGMA foreign_key_check;     -- must return zero rows
11. COMMIT;
12. PRAGMA foreign_keys = ON;
```

> **Forgetting step 12 leaves FK enforcement silently OFF for the rest of the connection.** `PRAGMA foreign_keys`
> is per-connection, not global. New connections get the *default* (off, unless you set it in the connection-open
> hook — which we do above, but the running connection is now broken). Always set inside `_configure_pragmas`
> *and* explicitly re-enable at the end of any 12-step migration.

---

## Time, Timestamps, Time Zones

Single failure mode: a `created_at` written by SQLite (`CURRENT_TIMESTAMP` = UTC) gets compared in Python to
`datetime.now()` (local-time, naive). Off by your timezone.

**Rules.**

- `DEFAULT CURRENT_TIMESTAMP` returns TEXT, UTC, ISO-like format `"YYYY-MM-DD HH:MM:SS"`. Always UTC. Always TEXT.
- `datetime.utcnow()` is **deprecated in Python 3.12**. Use `datetime.now(timezone.utc)`.
- `datetime.now()` (no arg) is local-time, naive. Never use for storage.
- For new columns, prefer **INTEGER unix-millis** (`event_ts_ms INTEGER NOT NULL`). Sortable, no parsing, no zone
  ambiguity. Use TEXT only where SQL `CURRENT_TIMESTAMP` defaults already exist.
- If you must compare a SQLite TEXT timestamp to a Python value, parse it as UTC explicitly:
  `datetime.fromisoformat(s).replace(tzinfo=timezone.utc)`.
- Pydantic v2: pin `datetime` fields with `model_config = ConfigDict(...)` so naive datetimes are rejected, not
  silently coerced.

---

## Replayability

PRD §6 principle 4: "Every external event lands in an immutable table keyed on the provider's event ID. Pipeline
runs are reconstructable from `(pipeline_runs, pipeline_events)`."

What that buys you concretely:

```python
# Replay an entire pipeline run from its row id
async def replay_pipeline(run_id: int):
    cur = await orchestration.execute(
        "SELECT pipeline_name, pipeline_version, trigger_payload FROM pipeline_runs WHERE id = ?",
        (run_id,),
    )
    name, version, payload = await cur.fetchone()
    await execute_pipeline(name, trigger_payload=json.loads(payload), replay_of=run_id)
```

Three things must be true for replay to work:

1. **Triggers are pure.** A pipeline node reading from outside the DB (Swan GraphQL) must re-fetch from Swan, not
   from cached state.
2. **Writes are conditional.** A `GLPosterTool` running on replay sees the journal entry already exists and no-ops
   instead of doubling it. Idempotent on `(event_id, pipeline_version)`.
3. **No clock-dependent decisions in nodes.** "Is this within the SLA window?" must use the *event's* timestamp,
   not `now()`.

---

## Backup and Recovery

> **`cp accounting.db backup.db` is wrong.** With WAL, the real state lives across `accounting.db`,
> `accounting.db-wal`, and `accounting.db-shm`. Copying only the main file gives you a backup missing every commit
> since the last checkpoint.

Correct approaches:

- **`VACUUM INTO 'backup.db';`** — single read transaction, produces a defragmented single-file copy. Safe with
  active writers. CPU-intensive; for a hackathon DB (<1 GB) it's seconds. Recommended.
- **`sqlite3.Connection.backup()`** (Online Backup API) — page-by-page copy that tolerates concurrent writers via
  retries. Better for large DBs.
- **File-level copy** is acceptable only if you `cp` *all three* files (main + `-wal` + `-shm`) and held a
  `BEGIN IMMEDIATE` lock during the copy. Easy to get wrong.

```python
# db/backup.py
async def backup_to(conn, dest_path: str):
    await conn.execute(f"VACUUM INTO '{dest_path}'")
```

For the hackathon: a `/admin/backup` FastAPI route running `VACUUM INTO` per DB on demand. Don't try to be clever
with rsync.

---

## Postgres Exit Door — Portability Watchouts

When the time comes to migrate at ~10k tx/day:

| SQLite-ism | Postgres reality |
|---|---|
| Type affinity (advisory column types) | Postgres rejects type-mismatched inserts. Use `STRICT` tables in SQLite from Day 1 to catch this early. |
| `AUTOINCREMENT` | Postgres: `GENERATED BY DEFAULT AS IDENTITY` (preferred) or `SERIAL`. Plain `INTEGER PRIMARY KEY` (no AUTOINCREMENT) maps cleanly to `BIGINT GENERATED ... AS IDENTITY`. |
| `WITHOUT ROWID` | Not a Postgres concept; drop the clause. |
| `datetime('now')`, `CURRENT_TIMESTAMP` returning TEXT | Postgres has native `timestamptz` and `now()`. The TEXT-vs-timestamptz translation is mechanical but pervasive. |
| `INSERT OR IGNORE` | Postgres: `INSERT ... ON CONFLICT (event_id) DO NOTHING`. Slightly more verbose, requires naming the conflict target. |
| `INSERT OR REPLACE` | Postgres: `INSERT ... ON CONFLICT (...) DO UPDATE SET ...`. Semantics differ — SQLite deletes-then-inserts (cascading triggers fire), Postgres updates in place. |
| `ATTACH DATABASE` | No equivalent. Use schemas (`CREATE SCHEMA orchestration`) within a single Postgres DB. Plan to merge into one DB on migration. |
| JSON `->>` operator | Postgres has the same operators on `jsonb`. Use `jsonb`, not `json`, for indexed access. Largely portable. |
| Triggers | Postgres syntax differs significantly (PL/pgSQL function + CREATE TRIGGER). Another reason to favor app-level invariants. |
| `PRAGMA` | Not a thing in Postgres. Use `SET`, `ALTER SYSTEM`, postgresql.conf. Configuration is global, not per-connection. |
| `busy_timeout` | No equivalent — Postgres uses MVCC, no whole-DB write lock. The "single writer" problem disappears. |
| FTS5 | Postgres: `tsvector` / `tsquery`. Different API. |

Cheap insurance now: `STRICT` on every table; app-level invariants over triggers; two schemas (not two attached
DBs) is the migration target — code that uses ATTACH for read-side joins will translate to qualified schema
references with one search-and-replace.

---

## Watchouts — Quick Reference

| Trap | Do |
|---|---|
| Concurrent async writers | Per-DB `asyncio.Lock` + `BEGIN IMMEDIATE` (always) |
| `SQLITE_BUSY` on read→write upgrade | `BEGIN IMMEDIATE` for any tx that will write — `busy_timeout` does not protect upgrades |
| Forgotten `commit()` | Wrap every write in `write_tx` context manager; bare `db.execute("INSERT ...")` fails review |
| Unbounded WAL growth | `journal_size_limit = 64MB` + periodic `wal_checkpoint(TRUNCATE)`; never hold a read tx across `await` on slow I/O |
| Cross-DB FK | App-level invariant + reconciliation job; treat orchestration.db as source of truth for run identity |
| Cross-DB tx atomicity | Not guaranteed in WAL — write orchestration first, accounting second |
| At-least-once webhooks | `INSERT OR IGNORE` + atomic claim UPDATE in same tx; two-phase `processed` / `completed_at` |
| Out-of-order events | Derive state from event union, not arrival order |
| Float money | `int` cents end-to-end, `INTEGER` columns, `CHECK(typeof()='integer')`, never `float` in Pydantic |
| VAT residual cent | Largest-remainder method, emit a tagged `journal_lines` row, never let the cent disappear |
| Double-entry invariant | App-level `InvariantCheckerTool`, freeze on violation, healthcheck endpoint runs the GROUP BY tripwire |
| Migrations | `_migrations` registry + 12-step rewrite for non-trivial ALTER + schema-fingerprint CI test |
| Bootstrap drift | CI compares fresh-from-bootstrap and fresh-from-migrations schemas; drift fails the build |
| FK enforcement off after 12-step migration | Always re-set `PRAGMA foreign_keys=ON` — it's per-connection |
| Timestamps | UTC TEXT or INTEGER unix-millis; `datetime.now(timezone.utc)`, never `datetime.now()` |
| Backup | `VACUUM INTO`, never `cp` (WAL + SHM live in sibling files) |
| JSON evolution | Version payloads, eager backfill, generated columns for indexed paths, CHECK extracted-value-not-null where expected |
| Connection sharing | `aiosqlite.Connection` is safe across coroutines (single worker thread); stdlib `sqlite3.Connection` is not |
| Long read transactions | Read into Python, close cursor, then `await` — never hold a read tx across slow I/O |

---

## What This Document Does Not Decide

The PRD locks the *shape* of the data layer. The next Claude instance still owns these calls:

- **Trigger vs application-level invariants.** PRD leans application-level; if defense-in-depth wins, write a
  trigger that fires on `posted_at` flip and document the migration cost.
- **Write-serialization mechanism.** `asyncio.Lock` (Shape A) vs dedicated writer task (Shape B). Start with A;
  switch only on observed contention.
- **JSON shape inside `pipeline_events.data`.** The PRD says it carries `input/output/error` per event. The
  exact keys are agent-author conventions, not data-layer concerns.
- **Whether to use `STRICT` tables.** Strongly recommended here, but a judgment call if a migration from a non-STRICT
  prototype is painful.
- **Backup cadence and retention.** Single host, single container — start with on-demand, add cron at Phase 2.
- **When to add a read-only connection.** Only when reporting queries start hurting the webhook path.

---

## Sources

**Primary (sqlite.org)**
- [Write-Ahead Logging](https://www.sqlite.org/wal.html)
- [PRAGMA reference](https://www.sqlite.org/pragma.html)
- [BEGIN TRANSACTION](https://www.sqlite.org/lang_transaction.html)
- [ATTACH DATABASE](https://www.sqlite.org/lang_attach.html)
- [INSERT / ON CONFLICT](https://www.sqlite.org/lang_conflict.html)
- [JSON1](https://www.sqlite.org/json1.html)
- [FTS5](https://www.sqlite.org/fts5.html)
- [Generated Columns](https://sqlite.org/gencol.html)
- [ALTER TABLE — 12-step rewrite](https://www.sqlite.org/lang_altertable.html)
- [Type Affinity](https://www.sqlite.org/datatype3.html)
- [STRICT tables](https://www.sqlite.org/stricttables.html)

**Python ecosystem**
- [Python sqlite3 module](https://docs.python.org/3/library/sqlite3.html)
- [aiosqlite documentation](https://aiosqlite.omnilib.dev/en/stable/)

**Operational guides**
- [SQLite concurrent writes and "database is locked" — tenthousandmeters.com](https://tenthousandmeters.com/blog/sqlite-concurrent-writes-and-database-is-locked-errors/)
- [The 20GB WAL File That Shouldn't Exist — loke.dev](https://loke.dev/blog/sqlite-checkpoint-starvation-wal-growth)
- [How SQLite Scales Read Concurrency — Fly.io / Ben Johnson](https://fly.io/blog/sqlite-internals-wal/)
- [Backup strategies for SQLite in production — oldmoe.blog](https://oldmoe.blog/2024/04/30/backup-strategies-for-sqlite-in-production/)
- [datetime.utcnow is deprecated — Miguel Grinberg](https://blog.miguelgrinberg.com/post/it-s-time-for-a-change-datetime-utcnow-is-now-deprecated)

**Postgres migration**
- [Render: SQLite to Postgres migration guide](https://render.com/articles/how-to-migrate-from-sqlite-to-postgresql)
- [Bytebase: SQLite to Postgres](https://www.bytebase.com/blog/database-migration-sqlite-to-postgresql/)

**Companion docs in this repo**
- `Orchestration/PRDs/MetaPRD.md` — the spec this guide implements
- `Dev orchestration/_exports_for_b2b_accounting/03_SQLITE_BACKBONE.md` — the upstream pattern catalog the PRD lifts from
- `Dev orchestration/tech framework/REF-FASTAPI-BACKEND.md` — connection lifecycle and lifespan pattern
- `Dev orchestration/tech framework/REF-SSE-STREAMING-FASTAPI.md` — SSE wiring on top of `pipeline_events`
