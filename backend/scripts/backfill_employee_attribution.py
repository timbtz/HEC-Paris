"""Backfill employee_id on historical Swan-triggered runs and their agent_costs.

The swan_webhook resolver originally only matched `employees.swan_account_id` /
`swan_iban` against the envelope `resourceId`. The demo replay path passes the
swan transaction id as `resourceId`, which never matches a real account — so
every replay-triggered run landed with `pipeline_runs.employee_id_logical=NULL`
and every `agent_costs.employee_id=NULL`.

Once the resolver is fixed (hash-fallback gated on FINGENT_SWAN_LOCAL_REPLAY),
new traffic attributes correctly. This script retroactively applies the same
hash fallback to historical NULL rows so the killer SQL ("per-employee
Anthropic spend") returns full coverage on the demo dataset.

Usage:
    uv run python -m backend.scripts.backfill_employee_attribution
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from ..orchestration.store.bootstrap import open_dbs

logger = logging.getLogger(__name__)


async def main(data_dir: Path) -> dict[str, int]:
    store = await open_dbs(data_dir, run_migrations=False)
    try:
        cur = await store.audit.execute(
            "SELECT id FROM employees WHERE active = 1 ORDER BY id"
        )
        active = [int(r[0]) for r in await cur.fetchall()]
        await cur.close()
        if not active:
            return {"runs_updated": 0, "costs_updated": 0, "active_employees": 0}

        cur = await store.orchestration.execute(
            "SELECT id, trigger_source, trigger_payload "
            "FROM pipeline_runs "
            "WHERE employee_id_logical IS NULL "
            "  AND trigger_source LIKE 'swan.%'"
        )
        runs = await cur.fetchall()
        await cur.close()

        run_to_emp: dict[int, int] = {}
        for run_id, _trigger_source, payload_json in runs:
            try:
                payload = json.loads(payload_json or "{}")
            except json.JSONDecodeError:
                continue
            resource_id = payload.get("resourceId")
            if not resource_id:
                continue

            cur = await store.audit.execute(
                "SELECT id FROM employees WHERE swan_account_id = ? OR swan_iban = ?",
                (resource_id, resource_id),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is not None:
                run_to_emp[int(run_id)] = int(row[0])
                continue

            cur = await store.accounting.execute(
                "SELECT id FROM swan_transactions WHERE id = ?",
                (resource_id,),
            )
            tx_row = await cur.fetchone()
            await cur.close()
            if tx_row is None:
                continue

            h = int(hashlib.md5(resource_id.encode()).hexdigest(), 16)
            run_to_emp[int(run_id)] = active[h % len(active)]

        runs_updated = 0
        for run_id, emp_id in run_to_emp.items():
            await store.orchestration.execute(
                "UPDATE pipeline_runs SET employee_id_logical = ? WHERE id = ?",
                (str(emp_id), run_id),
            )
            runs_updated += 1
        await store.orchestration.commit()

        costs_updated = 0
        for run_id, emp_id in run_to_emp.items():
            cur = await store.audit.execute(
                "UPDATE agent_costs SET employee_id = ? "
                "WHERE employee_id IS NULL AND decision_id IN ("
                "  SELECT id FROM agent_decisions WHERE run_id_logical = ?"
                ")",
                (emp_id, run_id),
            )
            costs_updated += cur.rowcount or 0
            await cur.close()
        await store.audit.commit()

        # Backfill task_completions so the gamification leaderboard reflects
        # historical AI usage. Auto-credit normally fires inside
        # audit.write_decision's write_tx, but the existing agent_costs rows
        # were inserted before the resolver fix. Idempotent on agent_decision_id.
        from backend.orchestration.gamification import AUTO_COIN_REWARD
        cur = await store.audit.execute(
            "SELECT ac.decision_id, ac.employee_id, d.runner "
            "FROM agent_costs ac "
            "JOIN agent_decisions d ON d.id = ac.decision_id "
            "WHERE ac.employee_id IS NOT NULL "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM task_completions tc "
            "    WHERE tc.agent_decision_id = ac.decision_id"
            "  )"
        )
        backfill_rows = await cur.fetchall()
        await cur.close()
        completions_credited = 0
        for decision_id, emp_id, runner in backfill_rows:
            await store.audit.execute(
                "INSERT INTO task_completions ("
                "  task_id, employee_id, status, coins_awarded, source, "
                "  agent_decision_id, reviewed_at, note"
                ") VALUES (NULL, ?, 'approved', ?, 'auto', ?, CURRENT_TIMESTAMP, ?)",
                (int(emp_id), AUTO_COIN_REWARD, int(decision_id),
                 f"runner={runner}"),
            )
            completions_credited += 1
        await store.audit.commit()

        return {
            "active_employees": len(active),
            "runs_seen": len(runs),
            "runs_updated": runs_updated,
            "costs_updated": costs_updated,
            "completions_credited": completions_credited,
        }
    finally:
        await store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import os
    data_dir = Path(os.environ.get("FINGENT_DATA_DIR", "./data")).resolve()
    summary = asyncio.run(main(data_dir))
    logger.info("backfill.complete %s", summary)
