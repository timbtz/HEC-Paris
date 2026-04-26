"""Phase 4.B: gamification layer (DailyAI port, single-tenant flavour).

Source: TACL-GROUP/pulse-ai-grow (`.lovable/plan.md`) ported into Agnes
without the Supabase/RLS/multi-tenant scaffolding. Their `workspaces`,
`profiles`, `user_roles`, `invitations` tables collapse because Agnes is
already single-tenant — `employees.email` is the implicit identity, and
`x-agnes-author` is the auth seam (same convention as wiki writes).

Adds to `audit.db`:

1. `employees.is_manager` (BOOLEAN flag) — gates POST endpoints that mutate
   tasks, rewards, completions, redemption status. Tim is seeded as manager.
2. `gamification_tasks` — manager-curated AI-adoption challenges with a
   coin reward.
3. `task_completions` — submissions waiting for manager approval, plus
   the `source` discriminator: `manual` (employee self-declared) vs
   `auto` (created by the audit hook on every real `agent_decisions`
   insert — that's the wedge: actual API spend feeds the leaderboard).
4. `rewards` + `reward_redemptions` — direct port. Coins are deducted
   on redemption (pending), refunded on rejection, locked on approval.
5. `coin_adjustments` — manual debits/credits by managers, audit trail.

Why audit.db? Employees + agent_costs already live here. Real FKs on
`employee_id` work inside one DB; cross-DB FKs are not enforced
(RealMetaPRD §6.5). The audit-hook auto-credit then runs inside the
same `write_tx` block as the existing agent_decisions/agent_costs
INSERT — one transaction, no nested locks.

The `coins_balance` derived view stays computed at read time (sum of
approved completions + adjustments − approved redemptions). We do NOT
store a balance column — that drifts under partial failures.
"""
from __future__ import annotations

import aiosqlite


async def _has_column(conn: aiosqlite.Connection, table: str, column: str) -> bool:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    await cur.close()
    return any(r[1] == column for r in rows)


async def up(conn: aiosqlite.Connection) -> None:
    # 1. employees.is_manager ---------------------------------------------------
    if not await _has_column(conn, "employees", "is_manager"):
        await conn.execute(
            "ALTER TABLE employees ADD COLUMN is_manager INTEGER NOT NULL DEFAULT 0"
        )
    # Seed: Tim is the demo manager.
    await conn.execute(
        "UPDATE employees SET is_manager = 1 WHERE email = 'tim@hec.example'"
    )

    # 2. gamification_tasks -----------------------------------------------------
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS gamification_tasks ("
        " id INTEGER PRIMARY KEY,"
        " title TEXT NOT NULL,"
        " description TEXT,"
        " department TEXT NOT NULL DEFAULT 'All',"
        " coin_value INTEGER NOT NULL CHECK(coin_value >= 0),"
        " is_active INTEGER NOT NULL DEFAULT 1,"
        " created_by_employee_id INTEGER REFERENCES employees(id),"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ") STRICT"
    )

    # 3. task_completions -------------------------------------------------------
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS task_completions ("
        " id INTEGER PRIMARY KEY,"
        " task_id INTEGER REFERENCES gamification_tasks(id),"
        " employee_id INTEGER NOT NULL REFERENCES employees(id),"
        " note TEXT,"
        " status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected'))"
        "        DEFAULT 'pending',"
        " coins_awarded INTEGER NOT NULL DEFAULT 0,"
        " source TEXT NOT NULL CHECK(source IN ('manual','auto')) DEFAULT 'manual',"
        " agent_decision_id INTEGER REFERENCES agent_decisions(id),"
        " reviewed_by_employee_id INTEGER REFERENCES employees(id),"
        " reviewed_at TEXT,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ") STRICT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_completions_employee_created "
        "ON task_completions(employee_id, created_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_completions_status "
        "ON task_completions(status, created_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_completions_decision "
        "ON task_completions(agent_decision_id)"
    )

    # 4a. rewards ---------------------------------------------------------------
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS rewards ("
        " id INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " description TEXT,"
        " emoji TEXT DEFAULT '🎁',"
        " coin_cost INTEGER NOT NULL CHECK(coin_cost > 0),"
        " is_active INTEGER NOT NULL DEFAULT 1,"
        " created_by_employee_id INTEGER REFERENCES employees(id),"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ") STRICT"
    )

    # 4b. reward_redemptions ----------------------------------------------------
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS reward_redemptions ("
        " id INTEGER PRIMARY KEY,"
        " reward_id INTEGER NOT NULL REFERENCES rewards(id),"
        " employee_id INTEGER NOT NULL REFERENCES employees(id),"
        " coin_cost INTEGER NOT NULL,"
        " status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected'))"
        "        DEFAULT 'pending',"
        " reviewed_by_employee_id INTEGER REFERENCES employees(id),"
        " reviewed_at TEXT,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ") STRICT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_redemptions_employee_created "
        "ON reward_redemptions(employee_id, created_at DESC)"
    )

    # 5. coin_adjustments -------------------------------------------------------
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS coin_adjustments ("
        " id INTEGER PRIMARY KEY,"
        " employee_id INTEGER NOT NULL REFERENCES employees(id),"
        " adjusted_by_employee_id INTEGER REFERENCES employees(id),"
        " amount INTEGER NOT NULL,"
        " reason TEXT,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ") STRICT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_adjustments_employee_created "
        "ON coin_adjustments(employee_id, created_at DESC)"
    )

    # Seed a handful of demo tasks + rewards so the empty UI doesn't look broken.
    # Use Tim (seeded manager) as the creator.
    cur = await conn.execute(
        "SELECT id FROM employees WHERE email = 'tim@hec.example'"
    )
    row = await cur.fetchone()
    await cur.close()
    creator_id = int(row[0]) if row else None

    seed_tasks = [
        ("Quarterly SaaS Burn-Rate Analysis",
         "Process AWS/Azure billing exports to categorise infra spend vs R&D credits.",
         "Finance", 55),
        ("Categorise Staff-Lunch Expenses",
         "Scan receipts and tag as 'Meals' or 'Entertainment'.",
         "Finance", 10),
        ("CRM Lead Prioritisation",
         "Score 500 prospects from LinkedIn profiles + company size.",
         "Sales", 50),
        ("Personalised Email Outreach",
         "Draft 50 custom intro emails based on recent target-account news.",
         "Sales", 35),
        ("Open-Source Licence Audit",
         "Scan repo for AGPL/high-risk licences; produce compliance report.",
         "Legal", 60),
        ("Summarise Weekly NDAs",
         "Extract key dates and termination clauses from vendor NDAs.",
         "Legal", 25),
        ("Technical-Interview Summarisation",
         "Transcribe interview audio; highlight Kubernetes-related answers.",
         "HR", 45),
        ("PR-Description Generator",
         "Analyse diffs of 10 commits → generate a Pull Request summary.",
         "Engineering", 40),
        ("Unit-Test Generation",
         "Draft boilerplate Jest tests for 5 new API endpoints.",
         "Engineering", 30),
    ]
    for title, desc, dept, coins in seed_tasks:
        await conn.execute(
            "INSERT INTO gamification_tasks (title, description, department, "
            "coin_value, created_by_employee_id) "
            "SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM gamification_tasks WHERE title = ?)",
            (title, desc, dept, coins, creator_id, title),
        )

    seed_rewards = [
        ("Coffee on the house", "Free espresso bar voucher.", "☕", 50),
        ("Friday off", "One paid Friday afternoon off, your pick.", "🌴", 800),
        ("Team-lunch credit", "€30 added to next team lunch.", "🥗", 200),
        ("Conference budget bump", "+€200 to your next-conf budget.", "🎤", 1500),
    ]
    for name, desc, emoji, cost in seed_rewards:
        await conn.execute(
            "INSERT INTO rewards (name, description, emoji, coin_cost, "
            "created_by_employee_id) "
            "SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM rewards WHERE name = ?)",
            (name, desc, emoji, cost, creator_id, name),
        )
