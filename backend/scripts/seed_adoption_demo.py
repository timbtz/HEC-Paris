"""Seed demo data for the Adoption tab + AI-Spend page + /reports/playbooks.

The base migrations seed three employees (Tim/Marie/Paul) and nine
gamification tasks. With nothing else, the Adoption page shows a
leaderboard of three rows where everyone has 0 coins, an empty streak
strip, an empty manager queue, and the playbooks endpoint returns
nothing. That kills the demo.

This script enriches `audit.db` in place so the Adoption tab and the
playbooks endpoint light up:

  1. Adds four extra demo employees (Sophie/Finance, Lucas/Sales,
     Camille/Legal, Nora/HR) so the per-department leaderboard card
     has real variety.
  2. For each of the seven employees, generates a deterministic stream
     of `agent_decisions` + `agent_costs` rows over the last 14 days,
     reusing a small set of `prompt_hash` values per employee. Marie
     re-runs the SAME prompt_hash 40+ times — that's the
     "Marie ran the same workflow 40×" line for /reports/playbooks.
  3. For every agent_decisions row inserted, also inserts a matching
     `task_completions` row with `source='auto'`, `status='approved'`,
     `coins_awarded=AUTO_COIN_REWARD` so the leaderboard, today
     summary, and 14-day streak all populate.
  4. A handful of manual `task_completions` per employee (some
     approved, some pending) so the manager queue has work and the
     "Manual" leaderboard column is non-zero.
  5. One pending + one approved redemption per top employee so the
     coin balance correctly reflects locked coins.
  6. A couple of `coin_adjustments` so the AdjustCoinsDialog inline
     history isn't empty.

Idempotent: every insert is guarded by either NOT EXISTS, a uniqueness
index, or a sentinel column value (e.g., `note LIKE 'demo-seed:%'`).
Re-running is safe.

Run from project root:
    python -m backend.scripts.seed_adoption_demo
    python -m backend.scripts.seed_adoption_demo --data-dir ./data

Constraints honoured:
    - Money is integer micro-USD on agent_costs.
    - All writes use BEGIN IMMEDIATE + commit-once.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.orchestration.cost import micro_usd
from backend.orchestration.runners.base import TokenUsage


# Mirror backend.orchestration.gamification.AUTO_COIN_REWARD without importing
# the async module (would pull aiosqlite for nothing).
AUTO_COIN_REWARD = 5

# Seed RNG so a re-run produces deterministic counts (helps tests and demo).
RNG_SEED = 42

# Total auto-call volume (and therefore coins) is dominated by Marie so the
# leaderboard reads like a believable adoption story: one power user, two
# regular users, four light users.
EXTRA_EMPLOYEES: list[tuple[str, str, str]] = [
    # (email, full_name, department)
    ("sophie@hec.example",  "Sophie",  "Finance"),
    ("lucas@hec.example",   "Lucas",   "Sales"),
    ("camille@hec.example", "Camille", "Legal"),
    ("nora@hec.example",    "Nora",    "HR"),
]

# (email, node_id, prompt_hash_seed, count, model)
# prompt_hash_seed is short — script derives the actual prompt_hash by
# concatenating it with the email so each employee has DISTINCT hashes
# even when the node is the same. count drives the playbook detection.
USAGE_PATTERNS: list[tuple[str, str, str, int, str]] = [
    # Marie — heavy expense classifier user. 42 calls, same hash → THE
    # "Marie ran the same workflow 40 times" pitch line.
    ("marie@hec.example", "expense_classifier",     "expense-v1",   42, "claude-sonnet-4-6"),
    ("marie@hec.example", "anomaly_flag",           "anomaly-v1",    8, "claude-haiku-4-5"),
    # Sophie — Finance, second pattern variant. 28 calls.
    ("sophie@hec.example", "vendor_resolver",       "vendor-v2",    28, "claude-haiku-4-5"),
    ("sophie@hec.example", "gl_classifier",         "gl-v1",        12, "claude-sonnet-4-6"),
    # Lucas — Sales, lead prioritisation. 35 calls.
    ("lucas@hec.example",  "lead_prioritizer",      "leads-v1",     35, "gpt-oss-120b"),
    ("lucas@hec.example",  "outreach_drafter",      "outreach-v1",  18, "gpt-oss-120b"),
    # Camille — Legal, NDA summariser. 22 calls.
    ("camille@hec.example", "nda_summariser",       "nda-v1",       22, "claude-sonnet-4-6"),
    # Nora — HR, interview transcript. 14 calls.
    ("nora@hec.example",   "interview_summariser",  "interview-v1", 14, "claude-haiku-4-5"),
    # Paul — Operations, occasional document extractor. 12 calls.
    ("paul@hec.example",   "document_extractor",    "doc-v1",       12, "claude-sonnet-4-6"),
    # Tim — Founder, light usage but spread across two patterns.
    ("tim@hec.example",    "exec_dashboard_qna",    "execqna-v1",   10, "claude-opus-4-7"),
    ("tim@hec.example",    "vendor_resolver",       "vendor-v1",     6, "claude-haiku-4-5"),
]


def _ensure_employees(conn: sqlite3.Connection) -> dict[str, int]:
    """Insert the four extra demo employees if missing. Return email→id map."""
    cur = conn.execute("SELECT email, id FROM employees")
    by_email = {row[0]: int(row[1]) for row in cur.fetchall()}
    for email, name, dept in EXTRA_EMPLOYEES:
        if email in by_email:
            continue
        cur = conn.execute(
            "INSERT INTO employees (email, full_name, department, active) "
            "VALUES (?, ?, ?, 1)",
            (email, name, dept),
        )
        by_email[email] = int(cur.lastrowid)
    return by_email


def _decision_exists(conn: sqlite3.Connection, prompt_hash: str, employee_id: int) -> int:
    """How many decisions already exist for this (prompt_hash, employee_id)?"""
    cur = conn.execute(
        "SELECT COUNT(*) FROM agent_decisions d "
        "JOIN agent_costs ac ON ac.decision_id = d.id "
        "WHERE d.prompt_hash = ? AND ac.employee_id = ?",
        (prompt_hash, employee_id),
    )
    return int(cur.fetchone()[0])


def _insert_decision(
    conn: sqlite3.Connection,
    *,
    employee_id: int,
    node_id: str,
    prompt_hash: str,
    model: str,
    started_at: str,
    rng: random.Random,
) -> int:
    """Insert agent_decisions + agent_costs and return the decision id."""
    # Provider derived from model — cost table requires the right pair.
    if model.startswith("claude-"):
        provider = "anthropic"
        runner = "anthropic"
    else:
        provider = "cerebras"
        runner = "pydantic_ai"

    in_tokens = rng.randint(800, 3500)
    out_tokens = rng.randint(120, 700)
    usage = TokenUsage(input_tokens=in_tokens, output_tokens=out_tokens)
    cost = micro_usd(usage, provider, model)

    cur = conn.execute(
        "INSERT INTO agent_decisions ("
        "  run_id_logical, node_id, source, runner, model, prompt_hash, "
        "  confidence, latency_ms, finish_reason, temperature, started_at, completed_at"
        ") VALUES (?, ?, 'agent', ?, ?, ?, ?, ?, 'end_turn', ?, ?, ?)",
        (
            rng.randint(1000, 9999),  # synthetic run_id_logical
            node_id,
            runner,
            model,
            prompt_hash,
            round(rng.uniform(0.78, 0.98), 3),
            rng.randint(420, 2400),
            round(rng.uniform(0.0, 0.7), 2),
            started_at,
            started_at,
        ),
    )
    decision_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO agent_costs ("
        "  decision_id, employee_id, provider, model, "
        "  input_tokens, output_tokens, cost_micro_usd, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (decision_id, employee_id, provider, model, in_tokens, out_tokens, cost, started_at),
    )
    # Auto-credit completion — same shape as auto_credit_for_decision().
    conn.execute(
        "INSERT INTO task_completions ("
        "  task_id, employee_id, status, coins_awarded, source, "
        "  agent_decision_id, reviewed_at, note, created_at"
        ") VALUES (NULL, ?, 'approved', ?, 'auto', ?, ?, ?, ?)",
        (employee_id, AUTO_COIN_REWARD, decision_id, started_at,
         f"runner={runner}", started_at),
    )
    return decision_id


def _spread_timestamps(count: int, rng: random.Random) -> list[str]:
    """Spread `count` timestamps across the last 14 days, weekday-biased.

    The auto-credit task_completions need a ≥1-per-day distribution
    (over the last 14 days) for the streak strip to be solid, but with
    enough variance for the leaderboard to feel real.
    """
    now = dt.datetime.now(dt.timezone.utc)
    out: list[str] = []
    # Base layer: at least one call per day per employee, on weekdays.
    for i in range(min(count, 14)):
        day = now - dt.timedelta(days=13 - i)
        # Anchor to a working hour with random jitter.
        ts = day.replace(
            hour=rng.randint(9, 18),
            minute=rng.randint(0, 59),
            second=rng.randint(0, 59),
            microsecond=0,
        )
        out.append(ts.strftime("%Y-%m-%d %H:%M:%S"))
    # Remaining: random within the 14-day window.
    for _ in range(max(0, count - 14)):
        offset_days = rng.randint(0, 13)
        day = now - dt.timedelta(days=offset_days)
        ts = day.replace(
            hour=rng.randint(8, 20),
            minute=rng.randint(0, 59),
            second=rng.randint(0, 59),
            microsecond=0,
        )
        out.append(ts.strftime("%Y-%m-%d %H:%M:%S"))
    out.sort()
    return out


def _seed_usage(conn: sqlite3.Connection, employees: dict[str, int]) -> dict[str, int]:
    """Insert the agent_decisions / agent_costs / auto-completions stream."""
    rng = random.Random(RNG_SEED)
    counts = {"decisions": 0, "skipped": 0}
    for email, node_id, hash_seed, count, model in USAGE_PATTERNS:
        emp_id = employees.get(email)
        if emp_id is None:
            continue
        prompt_hash = f"sha256:demo-{hash_seed}-{email.split('@')[0]}"
        existing = _decision_exists(conn, prompt_hash, emp_id)
        to_create = max(0, count - existing)
        if to_create == 0:
            counts["skipped"] += 1
            continue
        timestamps = _spread_timestamps(to_create, rng)
        for ts in timestamps:
            _insert_decision(
                conn,
                employee_id=emp_id,
                node_id=node_id,
                prompt_hash=prompt_hash,
                model=model,
                started_at=ts,
                rng=rng,
            )
            counts["decisions"] += 1
    return counts


def _seed_manual_completions(
    conn: sqlite3.Connection, employees: dict[str, int]
) -> dict[str, int]:
    """A few approved + pending manual completions per employee.

    Approved entries give the "Manual" leaderboard column real numbers;
    pending entries populate the manager queue.
    """
    counts = {"approved": 0, "pending": 0, "skipped": 0}
    cur = conn.execute(
        "SELECT id, title, coin_value FROM gamification_tasks WHERE is_active = 1"
    )
    tasks = cur.fetchall()
    if not tasks:
        return counts
    by_dept: dict[str, list[tuple[int, str, int]]] = {}
    for tid, title, coins in tasks:
        cur2 = conn.execute(
            "SELECT department FROM gamification_tasks WHERE id = ?", (tid,)
        )
        dept = cur2.fetchone()[0]
        by_dept.setdefault(dept, []).append((int(tid), title, int(coins)))

    rng = random.Random(RNG_SEED + 1)
    now = dt.datetime.now(dt.timezone.utc)
    tim_id = employees.get("tim@hec.example")

    # Approved manual completions per employee — match by department where possible.
    pairs: list[tuple[str, str]] = [
        ("marie@hec.example",   "Engineering"),
        ("sophie@hec.example",  "Finance"),
        ("lucas@hec.example",   "Sales"),
        ("camille@hec.example", "Legal"),
        ("nora@hec.example",    "HR"),
        ("paul@hec.example",    "Engineering"),
        ("tim@hec.example",     "Finance"),
    ]
    for email, preferred in pairs:
        emp_id = employees.get(email)
        if emp_id is None:
            continue
        choices = by_dept.get(preferred) or [t for ts in by_dept.values() for t in ts]
        if not choices:
            continue
        # 2 approved + 1 pending per employee — light enough to look realistic.
        for _ in range(2):
            tid, title, coins = rng.choice(choices)
            sentinel = f"demo-seed:approved:{email}:{tid}"
            cur = conn.execute(
                "SELECT id FROM task_completions WHERE note = ? LIMIT 1", (sentinel,)
            )
            if cur.fetchone():
                counts["skipped"] += 1
                continue
            ts = (now - dt.timedelta(days=rng.randint(1, 12))).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO task_completions ("
                "  task_id, employee_id, note, status, coins_awarded, source, "
                "  reviewed_by_employee_id, reviewed_at, created_at"
                ") VALUES (?, ?, ?, 'approved', ?, 'manual', ?, ?, ?)",
                (tid, emp_id, sentinel, coins, tim_id, ts, ts),
            )
            counts["approved"] += 1
        # One pending — for manager queue.
        tid, title, coins = rng.choice(choices)
        sentinel = f"demo-seed:pending:{email}:{tid}"
        cur = conn.execute(
            "SELECT id FROM task_completions WHERE note = ? LIMIT 1", (sentinel,)
        )
        if cur.fetchone():
            counts["skipped"] += 1
            continue
        ts = (now - dt.timedelta(hours=rng.randint(1, 36))).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO task_completions ("
            "  task_id, employee_id, note, status, source, created_at"
            ") VALUES (?, ?, ?, 'pending', 'manual', ?)",
            (tid, emp_id, sentinel, ts),
        )
        counts["pending"] += 1
    return counts


def _seed_redemptions(
    conn: sqlite3.Connection, employees: dict[str, int]
) -> dict[str, int]:
    """One pending + one approved redemption against the seeded rewards."""
    counts = {"pending": 0, "approved": 0, "skipped": 0}
    cur = conn.execute("SELECT id, name, coin_cost FROM rewards ORDER BY coin_cost ASC")
    rewards = cur.fetchall()
    if not rewards:
        return counts
    cheapest_id = int(rewards[0][0])
    cheapest_cost = int(rewards[0][2])
    second_id = int(rewards[1][0]) if len(rewards) > 1 else cheapest_id
    second_cost = int(rewards[1][2]) if len(rewards) > 1 else cheapest_cost

    tim_id = employees.get("tim@hec.example")
    marie_id = employees.get("marie@hec.example")
    sophie_id = employees.get("sophie@hec.example")

    seeds: list[tuple[int | None, int, int, str, int | None]] = [
        # (employee_id, reward_id, coin_cost, status, reviewer)
        (marie_id,  cheapest_id, cheapest_cost, "approved", tim_id),
        (sophie_id, second_id,   second_cost,   "pending",  None),
    ]
    now = dt.datetime.now(dt.timezone.utc)
    for emp_id, rid, cost, status, reviewer in seeds:
        if emp_id is None:
            continue
        # Idempotency: skip if a redemption row already exists for
        # (employee, reward, status).
        cur = conn.execute(
            "SELECT id FROM reward_redemptions "
            "WHERE employee_id = ? AND reward_id = ? AND status = ? LIMIT 1",
            (emp_id, rid, status),
        )
        if cur.fetchone():
            counts["skipped"] += 1
            continue
        ts = (now - dt.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        if status == "approved":
            conn.execute(
                "INSERT INTO reward_redemptions ("
                "  reward_id, employee_id, coin_cost, status, "
                "  reviewed_by_employee_id, reviewed_at, created_at"
                ") VALUES (?, ?, ?, 'approved', ?, ?, ?)",
                (rid, emp_id, cost, reviewer, ts, ts),
            )
            counts["approved"] += 1
        else:
            conn.execute(
                "INSERT INTO reward_redemptions ("
                "  reward_id, employee_id, coin_cost, status, created_at"
                ") VALUES (?, ?, ?, 'pending', ?)",
                (rid, emp_id, cost, ts),
            )
            counts["pending"] += 1
    return counts


def _seed_adjustments(
    conn: sqlite3.Connection, employees: dict[str, int]
) -> dict[str, int]:
    """A couple of coin_adjustments so the AdjustCoinsDialog history has data."""
    counts = {"created": 0, "skipped": 0}
    tim_id = employees.get("tim@hec.example")
    if tim_id is None:
        return counts
    seeds: list[tuple[str, int, str]] = [
        ("marie@hec.example",   25,  "Closed Q1 books two days early"),
        ("lucas@hec.example",   15,  "Best-in-class CRM hygiene this week"),
        ("camille@hec.example", -10, "Forgot to log NDAs in Notion (warning, not penalty)"),
    ]
    for email, amount, reason in seeds:
        emp_id = employees.get(email)
        if emp_id is None:
            continue
        sentinel = f"demo-seed: {reason}"
        cur = conn.execute(
            "SELECT id FROM coin_adjustments "
            "WHERE employee_id = ? AND amount = ? AND reason = ? LIMIT 1",
            (emp_id, amount, sentinel),
        )
        if cur.fetchone():
            counts["skipped"] += 1
            continue
        conn.execute(
            "INSERT INTO coin_adjustments ("
            "  employee_id, adjusted_by_employee_id, amount, reason"
            ") VALUES (?, ?, ?, ?)",
            (emp_id, tim_id, amount, sentinel),
        )
        counts["created"] += 1
    return counts


def seed(data_dir: Path) -> dict[str, int]:
    db_path = data_dir / "audit.db"
    if not db_path.is_file():
        raise SystemExit(f"audit.db not found under {data_dir}")
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            employees = _ensure_employees(conn)
            usage = _seed_usage(conn, employees)
            manual = _seed_manual_completions(conn, employees)
            redem = _seed_redemptions(conn, employees)
            adj = _seed_adjustments(conn, employees)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {
        "employees_known":     len(employees),
        "decisions_inserted":  usage["decisions"],
        "patterns_skipped":    usage["skipped"],
        "manual_approved":     manual["approved"],
        "manual_pending":      manual["pending"],
        "redemptions_added":   redem["pending"] + redem["approved"],
        "adjustments_added":   adj["created"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path("./data"),
        help="Directory containing audit.db.",
    )
    args = parser.parse_args()
    counts = seed(args.data_dir.resolve())
    print(
        "Adoption demo seed complete:\n"
        f"  employees in audit.db: {counts['employees_known']}\n"
        f"  agent_decisions inserted: {counts['decisions_inserted']} "
        f"(patterns already complete: {counts['patterns_skipped']})\n"
        f"  manual completions: {counts['manual_approved']} approved + "
        f"{counts['manual_pending']} pending\n"
        f"  redemptions added: {counts['redemptions_added']}\n"
        f"  coin adjustments added: {counts['adjustments_added']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
