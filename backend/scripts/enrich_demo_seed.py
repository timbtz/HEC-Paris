"""One-shot enrichment of the demo seed dataset.

The base seed (90 journal_entries + 200 swan_transactions) classifies
everything via deterministic 'rule' source with zero agent activity.
The pitch promises an "AI Spend per employee" chart, a trace drawer
that shows agent (decision, cost, employee) triples, and envelope
rings — none of which render meaningfully without agent rows and
budget allocations.

This script post-processes the seed in place:
    1. Picks a deterministic ~40-entry sample of journal_entries.
    2. For each picked entry inserts a *flow* of matching triples
       (audit.agent_decisions + audit.agent_costs + accounting.
       decision_traces) — one per node_id in the per-entry chain so
       the AI-spend chart aggregates over a believable call volume
       instead of a single classification per ledger line.
    3. Flips ~6 low-confidence picked entries to status='review' and
       links the first 6 existing review_queue rows to them.
    4. Backfills accounting.budget_allocations against existing
       budget_envelopes so envelope rings show 10–95% spread.

It is idempotent (re-runnable). Insertions are guarded by:
    - audit.agent_decisions: (line_id_logical, node_id) uniqueness check.
    - accounting.decision_traces: (line_id, source='agent') uniqueness.
    - accounting.budget_allocations: (envelope_id, line_id) uniqueness.

Run from project root:
    python -m backend.scripts.enrich_demo_seed
    python -m backend.scripts.enrich_demo_seed --data-dir ./data

Hard rules respected:
    - Money is integer cents on the accounting side.
    - Cost is integer micro-USD via backend.orchestration.cost.micro_usd.
    - Each per-DB block opens with BEGIN IMMEDIATE and commits once at
      the end so a partial run rolls back cleanly. No conn.commit()
      sprinkled mid-transaction.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.orchestration.cost import micro_usd
from backend.orchestration.runners.base import TokenUsage

# ---- Tunables -------------------------------------------------------

# Almost all entries (80 of 90) get an agent flow — modulo a handful of
# trivial intra-bank transfers that legitimately don't trigger any
# reasoning. Picked deterministically. Higher coverage so per-employee
# monthly totals reflect a real small-shop AI bill (€15-€60/month) once
# the rate table in cost.py is at market price.
TARGET_PICKED = 80

# 6 picked entries flip to 'review' — their confidence is forced below
# the LOW_CONF_THRESHOLD so the 'review' branch is causally consistent.
TARGET_REVIEW = 6
LOW_CONF_THRESHOLD = 0.85

# Provider/model distribution. Sonnet is the default classifier; Haiku
# is a sometimes-fallback; gpt-oss-120b is the Cerebras path. Bias 70/15/15.
MODEL_MIX: list[tuple[float, str, str, str]] = [
    # cumulative_p, provider, model, runner_label
    (0.70, "anthropic", "claude-sonnet-4-6", "anthropic"),
    (0.85, "anthropic", "claude-haiku-4-5",  "anthropic"),
    (1.00, "cerebras",  "gpt-oss-120b",      "pydantic_ai"),
]

# Per-entry node flow. Each picked entry generates one decision per node
# in this chain — the canonical agentic path for a posted transaction.
# Token bands per node mirror the real cost shape:
#   counterparty-resolve  — small RAG-style classifier
#   gl-classify           — medium reasoning over wiki rules (decisive)
#   vat-classify          — small, looks up the VAT regime
#   budget-check          — small, hits envelope state
#   flag-anomalies        — medium with optional reasoning trace
#   fraud-check           — small fast veto
#   extract-document      — heavy: PDF / OCR-style ingestion (only fires
#                           on entries whose description suggests a
#                           document arrived, see DOCUMENT_KEYWORDS).
#   summarize             — short, on entries flagged for review
NODE_FLOW: tuple[dict, ...] = (
    {
        "node_id": "counterparty-resolve",
        "input":   (1500,  3500),
        "output":  ( 200,   600),
        "reasoning": (0, 0),
    },
    {
        "node_id": "gl-classify",
        "input":   (4000, 11000),
        "output":  ( 700,  2200),
        "reasoning": (0, 1500),
    },
    {
        "node_id": "vat-classify",
        "input":   (2000,  4500),
        "output":  ( 300,   900),
        "reasoning": (0, 0),
    },
    {
        "node_id": "budget-check",
        "input":   (1800,  3500),
        "output":  ( 250,   700),
        "reasoning": (0, 0),
    },
    {
        "node_id": "flag-anomalies",
        "input":   (5000, 14000),
        "output":  ( 900,  2700),
        "reasoning": (0, 2500),
    },
    {
        "node_id": "fraud-check",
        "input":   (2500,  5000),
        "output":  ( 200,   500),
        "reasoning": (0, 0),
    },
    {
        "node_id": "extract-document",  # heavy, conditional
        "input":   (12000, 35000),
        "output":  ( 2000,  6000),
        "reasoning": (0, 0),
    },
    {
        "node_id": "summarize",  # short, conditional on review
        "input":   ( 800,  2000),
        "output":  ( 150,   500),
        "reasoning": (0, 0),
    },
)

# Counterparty descriptions that imply a PDF / invoice / supplier
# document was ingested — these entries also get the heavy
# extract-document call.
DOCUMENT_KEYWORDS = ("Anthropic", "OFI", "Notion", "Linear", "Hostelo", "SNCF")

# Legacy alias retained for the idempotency guard (the old script wrote
# one decision per entry and used these node_ids — keeping the set lets
# a re-run skip already-seeded rows cleanly even if NODE_FLOW shifts).
NODE_IDS = tuple(n["node_id"] for n in NODE_FLOW)

# account_code prefix → envelope category. Only '6xx' lines (expenses)
# get allocated. '512' is the cash side and never gets an envelope.
ACCOUNT_TO_CATEGORY: dict[str, str] = {
    "613":    "leasing",   # Fin (rent)
    "624":    "travel",    # SNCF, Hostelo
    "6257":   "food",      # Boulangerie Paul
    "626100": "ai_tokens", # Anthropic
    "626200": "saas",      # Notion, OFI, Linear
}

# Counterparty bias for picking — boosts the demo-relevant names so the
# trace drawer shows familiar rows.
PRIORITY_KEYWORDS = ("Anthropic", "SNCF", "Hostelo", "Boulangerie", "OFI")

# Wiki pages live in orchestration.db. We sample one (page_id, revision_id)
# per agent_decision so wiki_page_id / wiki_revision_id columns aren't NULL.
# 'SCHEMA.md' (id=1) is excluded — it isn't a policy page.
POLICY_PAGE_IDS = (2, 3, 4, 5, 6)

# Token counts are passed through to cost.micro_usd verbatim. With the
# rate table now reflecting real market pricing ($3/M Sonnet input,
# etc.) the bands defined in NODE_FLOW yield per-call costs of ~$0.005
# (small classifier) up to ~$0.20 (heavy document extraction). Aggregated
# over ~60 entries × 3-4 decisions each, per-employee 30d totals land
# in the €15-€80 range — believable for a small shop running an
# agentic close, without overstating the bill.

# ---- Helpers --------------------------------------------------------


def _pick_model(rng: random.Random) -> tuple[str, str, str]:
    """Sample a (provider, model, runner) triple from MODEL_MIX."""
    p = rng.random()
    for cum, provider, model, runner in MODEL_MIX:
        if p <= cum:
            return provider, model, runner
    return MODEL_MIX[-1][1:]  # type: ignore[return-value]


def _pick_employee(entry_id: int) -> int:
    """Deterministic Tim/Marie/Paul (1/2/3) by entry_id hash."""
    digest = hashlib.blake2b(str(entry_id).encode(), digest_size=4).digest()
    return (int.from_bytes(digest, "big") % 3) + 1


def _prompt_hash(entry_id: int, node_id: str, model: str) -> str:
    return hashlib.blake2b(
        f"{entry_id}|{node_id}|{model}".encode(),
        digest_size=8,
    ).hexdigest()


def _ts_offset(base_iso: str, seconds: int) -> str:
    """Add `seconds` to an ISO timestamp, return ISO."""
    # base may be 'YYYY-MM-DD HH:MM:SS' (sqlite default) or full ISO.
    base_iso = base_iso.replace("T", " ")
    if "." in base_iso:
        base_iso = base_iso.split(".", 1)[0]
    base_iso = base_iso.split("+", 1)[0].strip()
    t = dt.datetime.fromisoformat(base_iso)
    return (t + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _decision_created_at(entry_id: int, rng: random.Random) -> str:
    """Spread agent_costs.created_at across the trailing 30 days so the
    AI-spend "30d total" chart actually has something to plot.

    Anchored at 2026-04-26 (today per CLAUDE.md / project memory) minus
    a deterministic random offset in [0, 30] days, plus a per-entry
    minute jitter so rows don't pile up at the same instant.
    """
    today = dt.datetime(2026, 4, 26, 9, 0, 0)
    days_back = rng.randint(0, 29)
    minutes_jitter = (entry_id * 137) % (24 * 60)
    ts = today - dt.timedelta(days=days_back, minutes=minutes_jitter)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _category_for_account(account_code: str) -> str | None:
    """Map a 6xx account_code to an envelope category. Cash (512) → None."""
    if account_code in ACCOUNT_TO_CATEGORY:
        return ACCOUNT_TO_CATEGORY[account_code]
    if account_code.startswith("6"):
        return "saas"  # generic expense fallback
    return None


# ---- Picking strategy ----------------------------------------------


def _pick_entries(cur: sqlite3.Cursor) -> list[int]:
    """Return ~TARGET_PICKED entry_ids: prioritise demo-relevant
    counterparties, then fill with every-other entry, deterministic."""
    rows = cur.execute(
        "SELECT id, description FROM journal_entries ORDER BY id ASC"
    ).fetchall()
    by_priority: list[int] = []
    fallback: list[int] = []
    for eid, desc in rows:
        if any(k in (desc or "") for k in PRIORITY_KEYWORDS):
            by_priority.append(eid)
        else:
            fallback.append(eid)
    picked = list(by_priority)
    # Fill from fallback, every-other, until we hit TARGET_PICKED.
    for eid in fallback[::2]:
        if len(picked) >= TARGET_PICKED:
            break
        picked.append(eid)
    picked.sort()
    return picked[:TARGET_PICKED]


def _pick_review_subset(picked: list[int]) -> set[int]:
    """The TARGET_REVIEW lowest-id picked entries get flipped to review."""
    return set(picked[:TARGET_REVIEW])


# ---- Envelope mapping ----------------------------------------------


def _envelope_periods(cur: sqlite3.Cursor) -> list[str]:
    """Distinct seeded envelope periods, sorted (e.g. ['2026-02', '2026-03', '2026-04'])."""
    rows = cur.execute(
        "SELECT DISTINCT period FROM budget_envelopes ORDER BY period"
    ).fetchall()
    return [r[0] for r in rows]


def _envelope_id(
    cur: sqlite3.Cursor,
    employee_id: int,
    category: str,
    period: str,
) -> int | None:
    row = cur.execute(
        "SELECT id FROM budget_envelopes "
        "WHERE scope_kind='employee' AND scope_id=? AND category=? AND period=?",
        (employee_id, category, period),
    ).fetchone()
    return row[0] if row else None


def _resolve_period(
    entry_date: str,
    seeded_periods: list[str],
    entry_id: int,
) -> str:
    """Return the envelope period for an entry.

    Prefers exact YYYY-MM match; if the entry is outside the seeded
    envelope window (e.g. 2025-XX entries vs 2026-02..04 envelopes),
    deterministically cycle through the seeded periods so allocations
    distribute across all months instead of piling onto one.
    """
    ymon = entry_date[:7]
    if ymon in seeded_periods:
        return ymon
    return seeded_periods[entry_id % len(seeded_periods)]


# ---- Existence guards (idempotency) --------------------------------


def _decision_exists(
    cur: sqlite3.Cursor, line_id_logical: str, node_id: str
) -> int | None:
    row = cur.execute(
        "SELECT id FROM agent_decisions "
        "WHERE line_id_logical=? AND node_id=? AND source='agent' "
        "AND finish_reason='end_turn'",
        (line_id_logical, node_id),
    ).fetchone()
    return row[0] if row else None


def _trace_exists(cur: sqlite3.Cursor, line_id: int) -> bool:
    row = cur.execute(
        "SELECT 1 FROM decision_traces WHERE line_id=? AND source='agent'",
        (line_id,),
    ).fetchone()
    return row is not None


def _allocation_exists(
    cur: sqlite3.Cursor, envelope_id: int, line_id: int
) -> bool:
    row = cur.execute(
        "SELECT 1 FROM budget_allocations WHERE envelope_id=? AND line_id=?",
        (envelope_id, line_id),
    ).fetchone()
    return row is not None


# ---- Core enrichment -----------------------------------------------


def enrich(data_dir: Path) -> dict[str, int]:
    audit_path       = data_dir / "audit.db"
    accounting_path  = data_dir / "accounting.db"

    # Counters returned for the summary print.
    n_decisions  = 0
    n_costs      = 0
    n_traces     = 0
    n_reviewed   = 0
    n_alloc      = 0

    with closing(sqlite3.connect(audit_path)) as audit_conn, \
         closing(sqlite3.connect(accounting_path)) as acct_conn:

        audit_conn.execute("PRAGMA foreign_keys = ON")
        acct_conn.execute("PRAGMA foreign_keys = ON")
        audit_cur = audit_conn.cursor()
        acct_cur  = acct_conn.cursor()

        # Pick before opening writes so we can fail fast on stale data.
        picked = _pick_entries(acct_cur)
        review_set = _pick_review_subset(picked)
        seeded_periods = _envelope_periods(acct_cur)

        # Pre-load journal_lines for picked entries and their dates.
        meta = acct_cur.execute(
            f"SELECT id, entry_date, description, created_at "
            f"FROM journal_entries WHERE id IN ({','.join('?'*len(picked))})",
            picked,
        ).fetchall()
        entry_meta: dict[int, tuple[str, str, str]] = {
            r[0]: (r[1], r[2] or "", r[3]) for r in meta
        }
        lines = acct_cur.execute(
            f"SELECT id, entry_id, account_code, debit_cents "
            f"FROM journal_lines WHERE entry_id IN ({','.join('?'*len(picked))}) "
            f"ORDER BY entry_id, id",
            picked,
        ).fetchall()
        # First debit line per entry — the one the agent decision points at.
        first_line_per_entry: dict[int, int] = {}
        # Expense lines by entry (for envelope allocation).
        expense_lines_per_entry: dict[int, list[tuple[int, str, int]]] = {}
        for (lid, eid, acct, debit) in lines:
            if acct.startswith("6") and debit > 0:
                first_line_per_entry.setdefault(eid, lid)
                expense_lines_per_entry.setdefault(eid, []).append((lid, acct, debit))

        # ---- audit.db block --------------------------------------------
        audit_conn.execute("BEGIN IMMEDIATE")
        # Map of entry_id -> (audit_decision_id, employee_id, confidence,
        # provider, model, created_at) so the accounting block can write
        # decision_traces / review_queue with the right cross-DB FKs.
        decision_index: dict[int, dict] = {}

        try:
            for entry_id in picked:
                if entry_id not in first_line_per_entry:
                    # No expense line — skip cleanly (e.g. if an entry was
                    # mis-classified upstream and only has cash legs).
                    continue
                line_id = first_line_per_entry[entry_id]
                line_id_logical = str(line_id)
                description = entry_meta[entry_id][1]

                # Pick the chain of node_ids this entry runs. The base
                # chain is counterparty → gl → vat → budget → anomalies
                # → fraud (6 nodes — the canonical posted-transaction
                # flow). Entries whose description suggests a supplier
                # document also fire extract-document; review entries
                # also fire the summarize node.
                base_chain = list(NODE_FLOW[:6])
                if any(k in description for k in DOCUMENT_KEYWORDS):
                    base_chain.append(NODE_FLOW[6])  # extract-document
                if entry_id in review_set:
                    base_chain.append(NODE_FLOW[7])  # summarize
                chain = tuple(base_chain)

                # Force review subset to land below LOW_CONF_THRESHOLD on
                # the *deciding* node (gl-classify) so the review branch
                # is causally consistent.
                rng = random.Random(entry_id)
                if entry_id in review_set:
                    decisive_confidence = round(rng.uniform(0.55, LOW_CONF_THRESHOLD - 0.01), 4)
                else:
                    decisive_confidence = round(rng.uniform(0.86, 0.97), 4)

                employee_id = _pick_employee(entry_id)

                first_decision_for_entry: int | None = None
                for n_idx, node_spec in enumerate(chain):
                    node_id = node_spec["node_id"]
                    node_rng = random.Random(entry_id * 1009 + n_idx)
                    provider, model, runner = _pick_model(node_rng)

                    # Idempotency: same (line_id_logical, node_id) means
                    # this flow has already been written.
                    existing = _decision_exists(audit_cur, line_id_logical, node_id)
                    if existing is not None:
                        if first_decision_for_entry is None:
                            first_decision_for_entry = existing
                        continue

                    in_lo, in_hi = node_spec["input"]
                    out_lo, out_hi = node_spec["output"]
                    rs_lo, rs_hi = node_spec["reasoning"]
                    input_tokens  = node_rng.randint(in_lo, in_hi)
                    output_tokens = node_rng.randint(out_lo, out_hi)
                    if provider == "cerebras" and rs_hi > 0 and node_rng.random() < 0.7:
                        reasoning_tokens = node_rng.randint(rs_lo, rs_hi)
                    elif rs_hi > 0 and node_rng.random() < 0.2:
                        # Anthropic models ALSO sometimes emit thinking;
                        # 20% chance, smaller draw.
                        reasoning_tokens = node_rng.randint(rs_lo, rs_hi // 2)
                    else:
                        reasoning_tokens = 0
                    latency_ms = node_rng.randint(450, 3200)

                    # Confidence: the gl-classify node carries the
                    # decisive confidence (review-branch trigger);
                    # other nodes wobble around their own band.
                    if node_id == "gl-classify":
                        confidence = decisive_confidence
                    else:
                        confidence = round(node_rng.uniform(0.84, 0.97), 4)

                    wiki_page_id = POLICY_PAGE_IDS[(entry_id + n_idx) % len(POLICY_PAGE_IDS)]
                    wiki_revision_id = wiki_page_id  # 1:1 by inspection

                    created_at = _decision_created_at(entry_id, node_rng)
                    # Stagger the chain by ~latency_ms each step so the
                    # timeline reads left-to-right in the trace drawer.
                    started_at_iso  = _ts_offset(created_at, n_idx * 4)
                    completed_at_iso = _ts_offset(created_at, n_idx * 4 + latency_ms // 1000)

                    alternatives = json.dumps([
                        {"label": "primary",   "score": confidence},
                        {"label": "secondary", "score": round(max(0.05, confidence - 0.18), 4)},
                    ])

                    p_hash = _prompt_hash(entry_id, node_id, model)

                    audit_cur.execute(
                        "INSERT INTO agent_decisions ("
                        "  run_id_logical, node_id, source, runner, model, "
                        "  response_id, prompt_hash, alternatives_json, confidence, "
                        "  line_id_logical, latency_ms, finish_reason, temperature, "
                        "  seed, started_at, completed_at, wiki_page_id, wiki_revision_id"
                        ") VALUES (?, ?, 'agent', ?, ?, ?, ?, ?, ?, ?, ?, 'end_turn', 0.0, ?, ?, ?, ?, ?)",
                        (
                            entry_id,                  # run_id_logical
                            node_id,
                            runner,
                            model,
                            f"resp_demo_{entry_id:04d}_{n_idx}",
                            p_hash,
                            alternatives,
                            confidence,
                            line_id_logical,
                            latency_ms,
                            entry_id * 100 + n_idx,    # seed
                            started_at_iso,
                            completed_at_iso,
                            wiki_page_id,
                            wiki_revision_id,
                        ),
                    )
                    decision_id = audit_cur.lastrowid
                    n_decisions += 1

                    usage = TokenUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                        reasoning_tokens=reasoning_tokens,
                    )
                    cost_micro = micro_usd(usage, provider, model)

                    audit_cur.execute(
                        "INSERT INTO agent_costs ("
                        "  decision_id, employee_id, provider, model, "
                        "  input_tokens, output_tokens, cache_read_tokens, "
                        "  cache_write_tokens, reasoning_tokens, cost_micro_usd, created_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)",
                        (
                            decision_id,
                            employee_id,
                            provider,
                            model,
                            input_tokens,
                            output_tokens,
                            reasoning_tokens,
                            cost_micro,
                            created_at,
                        ),
                    )
                    n_costs += 1

                    if first_decision_for_entry is None:
                        first_decision_for_entry = decision_id
                        # Capture the decisive metadata for the trace /
                        # review-queue join further down.
                        decision_index[entry_id] = {
                            "audit_id": decision_id,
                            "employee_id": employee_id,
                            "confidence": decisive_confidence,
                            "provider": provider,
                            "model": model,
                            "created_at": created_at,
                            "line_id": line_id,
                            "skipped": False,
                        }

                # If every node was a re-run (all skipped), still record
                # an entry in the index so the accounting block writes
                # decision_traces / review_queue idempotently.
                if entry_id not in decision_index and first_decision_for_entry is not None:
                    decision_index[entry_id] = {
                        "audit_id": first_decision_for_entry,
                        "employee_id": employee_id,
                        "confidence": decisive_confidence,
                        "provider": "anthropic",
                        "model": "claude-sonnet-4-6",
                        "created_at": entry_meta[entry_id][2],
                        "line_id": line_id,
                        "skipped": True,
                    }

            audit_conn.commit()
        except Exception:
            audit_conn.rollback()
            raise

        # ---- accounting.db block ---------------------------------------
        acct_conn.execute("BEGIN IMMEDIATE")
        try:
            # 1. decision_traces, one per agent decision.
            for entry_id, info in decision_index.items():
                if entry_id not in first_line_per_entry:
                    continue
                line_id = first_line_per_entry[entry_id]
                if _trace_exists(acct_cur, line_id):
                    continue
                acct_cur.execute(
                    "INSERT INTO decision_traces ("
                    "  line_id, source, rule_id, confidence, "
                    "  agent_decision_id_logical, parent_event_id, created_at"
                    ") VALUES (?, 'agent', NULL, ?, ?, ?, ?)",
                    (
                        line_id,
                        info["confidence"],
                        str(info["audit_id"]),
                        f"entry:{entry_id}",
                        info["created_at"],
                    ),
                )
                n_traces += 1

            # 2. Flip the review subset to status='review' and link the
            #    first 6 unlinked review_queue rows to those entries.
            review_entry_ids = sorted(
                eid for eid in review_set if eid in decision_index
            )
            if review_entry_ids:
                # Flip status.
                acct_cur.execute(
                    f"UPDATE journal_entries SET status='review' "
                    f"WHERE id IN ({','.join('?'*len(review_entry_ids))}) "
                    f"AND status != 'review'",
                    review_entry_ids,
                )
                n_reviewed = acct_cur.rowcount

                # Bind orphan review_queue rows to those entries that
                # don't already have a row pointing at them (idempotent).
                pending_entries = []
                for entry_id in review_entry_ids:
                    bound = acct_cur.execute(
                        "SELECT 1 FROM review_queue WHERE entry_id=? LIMIT 1",
                        (entry_id,),
                    ).fetchone()
                    if bound is None:
                        pending_entries.append(entry_id)
                if pending_entries:
                    orphans = acct_cur.execute(
                        "SELECT id FROM review_queue "
                        "WHERE entry_id IS NULL AND resolved_at IS NULL "
                        "ORDER BY id ASC LIMIT ?",
                        (len(pending_entries),),
                    ).fetchall()
                    for rq_row, entry_id in zip(orphans, pending_entries):
                        info = decision_index[entry_id]
                        acct_cur.execute(
                            "UPDATE review_queue SET entry_id=?, confidence=?, "
                            "reason=? WHERE id=?",
                            (
                                entry_id,
                                info["confidence"],
                                f"agent confidence {info['confidence']:.2f} below {LOW_CONF_THRESHOLD}",
                                rq_row[0],
                            ),
                        )

            # 3. Budget allocations — one per (picked entry, expense line).
            #    Strategy for the 10–95% spread: pre-assign each envelope
            #    a deterministic target burn fraction. Then for every
            #    line that maps to that envelope, scale its debit so the
            #    envelope's running total marches toward the target.
            #    Allocations are real-money cents but here they
            #    represent the demo claim that "this line counts against
            #    that envelope" — the per-envelope cap pacing makes the
            #    rings render with a healthy spread instead of all
            #    sitting at <20%.
            envelope_target_pct: dict[int, float] = {}
            envelope_running:    dict[int, int]   = {}
            # Pre-tiered target burns: deterministic, spread across the
            # 10–95 % band so rings don't all cluster near one value.
            TIERS = (0.12, 0.24, 0.37, 0.51, 0.66, 0.78, 0.88, 0.94)

            def _seed_target(env_id: int) -> float:
                if env_id in envelope_target_pct:
                    return envelope_target_pct[env_id]
                pct = TIERS[env_id % len(TIERS)]
                envelope_target_pct[env_id] = pct
                envelope_running[env_id] = 0
                return pct

            # First pass: count how many picked lines land on each envelope
            # so we can size each allocation to its share of the target.
            envelope_line_count: dict[int, int] = {}
            picked_lines: list[tuple[int, int, int, str, int]] = []
            for entry_id in picked:
                if entry_id not in expense_lines_per_entry:
                    continue
                if entry_id not in decision_index:
                    continue
                employee_id = decision_index[entry_id]["employee_id"]
                entry_date  = entry_meta[entry_id][0]
                period      = _resolve_period(entry_date, seeded_periods, entry_id)
                for (line_id, acct, debit_cents) in expense_lines_per_entry[entry_id]:
                    category = _category_for_account(acct)
                    if category is None:
                        continue
                    env_id = _envelope_id(acct_cur, employee_id, category, period)
                    if env_id is None:
                        continue
                    envelope_line_count[env_id] = envelope_line_count.get(env_id, 0) + 1
                    picked_lines.append((entry_id, env_id, line_id, acct, debit_cents))

            # Second pass: write the allocations.
            for (entry_id, env_id, line_id, acct, debit_cents) in picked_lines:
                if _allocation_exists(acct_cur, env_id, line_id):
                    continue
                cap_row = acct_cur.execute(
                    "SELECT cap_cents FROM budget_envelopes WHERE id=?",
                    (env_id,),
                ).fetchone()
                cap_cents = cap_row[0] if cap_row else debit_cents

                target_pct = _seed_target(env_id)
                target_cents = int(cap_cents * target_pct)
                lines_on_env = max(envelope_line_count.get(env_id, 1), 1)

                rng = random.Random(env_id * 1009 + line_id)
                # Each line gets target_cents / lines_on_env, with a
                # stochastic ±20% wobble so siblings don't all match.
                share = target_cents // lines_on_env
                wobble = rng.uniform(0.85, 1.15)
                amount_cents = max(1, int(share * wobble))
                # Don't blow past the per-envelope target.
                headroom = max(1, target_cents - envelope_running[env_id])
                amount_cents = min(amount_cents, headroom)
                envelope_running[env_id] += amount_cents

                acct_cur.execute(
                    "INSERT INTO budget_allocations ("
                    "  envelope_id, line_id, amount_cents"
                    ") VALUES (?, ?, ?)",
                    (env_id, line_id, amount_cents),
                )
                n_alloc += 1

            acct_conn.commit()
        except Exception:
            acct_conn.rollback()
            raise

    return {
        "decisions":  n_decisions,
        "costs":      n_costs,
        "traces":     n_traces,
        "reviewed":   n_reviewed,
        "allocations": n_alloc,
    }


# ---- Validation queries --------------------------------------------


def _print_validation(data_dir: Path) -> None:
    audit_path      = data_dir / "audit.db"
    accounting_path = data_dir / "accounting.db"

    with closing(sqlite3.connect(audit_path)) as audit_conn:
        audit_cur = audit_conn.cursor()
        print("\n--- audit.agent_costs grouped by employee -----------------")
        rows = audit_cur.execute("""
            SELECT e.full_name,
                   COUNT(*) AS calls,
                   SUM(c.cost_micro_usd) AS micro_usd
            FROM agent_costs c
            JOIN employees e ON e.id = c.employee_id
            GROUP BY e.full_name
            ORDER BY e.full_name
        """).fetchall()
        for full_name, calls, micros in rows:
            eur = (micros or 0) / 1_000_000  # micro-USD -> USD ~ EUR for display
            print(f"  {full_name:<10} {calls:>4} calls  ~${eur:.4f}")

    with closing(sqlite3.connect(accounting_path)) as acct_conn:
        acct_cur = acct_conn.cursor()
        print("\n--- accounting.journal_entries by status -----------------")
        for status, n in acct_cur.execute(
            "SELECT status, COUNT(*) FROM journal_entries GROUP BY status"
        ).fetchall():
            print(f"  {status:<10} {n}")

        print("\n--- top-10 envelope burn (% of cap) ----------------------")
        rows = acct_cur.execute("""
            SELECT be.scope_id, be.category, be.period,
                   SUM(ba.amount_cents)*100.0 / MAX(be.cap_cents) AS pct
            FROM budget_envelopes be
            JOIN budget_allocations ba ON ba.envelope_id = be.id
            GROUP BY be.id
            ORDER BY pct DESC
            LIMIT 10
        """).fetchall()
        for scope_id, category, period, pct in rows:
            print(f"  emp={scope_id} cat={category:<10} {period}  {pct:6.2f}%")


# ---- CLI ------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("./data"),
        help="Directory containing accounting.db / audit.db / orchestration.db",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    if not (data_dir / "accounting.db").is_file():
        raise SystemExit(f"accounting.db not found under {data_dir}")
    if not (data_dir / "audit.db").is_file():
        raise SystemExit(f"audit.db not found under {data_dir}")

    counts = enrich(data_dir)
    print(
        f"Created {counts['decisions']} agent_decisions, "
        f"{counts['costs']} cost rows, "
        f"{counts['allocations']} allocations, "
        f"{counts['traces']} traces, "
        f"{counts['reviewed']} review entries flipped."
    )
    _print_validation(data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
