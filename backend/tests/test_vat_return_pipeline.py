"""End-to-end test for the `vat_return` pipeline (Phase 3 Slice D).

Asserts that the pipeline computes VAT box totals correctly with the
seeded `vat_rates` (migration 0009) and writes a single `period_reports`
row of type `vat_return` with the deductible/collected breakdown.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from backend.orchestration import event_bus
from backend.orchestration.executor import execute_pipeline
from backend.orchestration.store.writes import write_tx


async def _seed_ledger_q1(store) -> None:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        # accrual expense WITH 20% VAT (deductible 4456)
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('accrual', '2026-02-10', 'test', 1, 'posted')"
        )
        e1 = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, '626100', 5000, 0), (?, '4456', 1000, 0), (?, '401', 0, 6000)",
            (e1, e1, e1),
        )
        # accrual revenue WITH 20% VAT (collected 445)
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('accrual', '2026-03-10', 'test', 1, 'posted')"
        )
        e2 = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, '411', 12000, 0), (?, '706000', 0, 10000), (?, '445', 0, 2000)",
            (e2, e2, e2),
        )


async def test_vat_return_computes_box_totals(store, fake_anthropic):
    calls, fake_client = fake_anthropic
    fake_client.messages._response = SimpleNamespace(
        id="msg_1", model="claude-sonnet-4-6", stop_reason="tool_use",
        content=[SimpleNamespace(
            type="tool_use", id="tu_1", name="submit_anomalies",
            input={"anomalies": [], "overall_confidence": 0.95},
        )],
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=20,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )

    await _seed_ledger_q1(store)

    q = await event_bus.subscribe_dashboard()
    try:
        await execute_pipeline(
            "vat_return",
            trigger_source="test",
            trigger_payload={"period_code": "2026-Q1"},
            store=store,
            background=False,
        )
    finally:
        await event_bus.remove_dashboard_subscriber(q)

    cur = await store.accounting.execute(
        "SELECT period_code, report_type, status, payload_json "
        "FROM period_reports WHERE report_type = 'vat_return'"
    )
    rows = list(await cur.fetchall())
    await cur.close()
    assert len(rows) == 1, rows
    payload = json.loads(rows[0]["payload_json"])

    # The summarize-period node bundles VAT under `vat`. Net due:
    #   collected (445)  = 2000c
    #   deductible (4456) = 1000c
    #   net_due           = 1000c
    vat = payload["vat"]
    assert vat["totals"]["collected_cents"] == 2000
    assert vat["totals"]["deductible_cents"] == 1000
    assert vat["totals"]["net_due_cents"] == 1000

    # No journal entries posted — vat_return is read-only.
    # (We added two seed entries above; assert no NEW entries beyond those.)
    cur = await store.accounting.execute("SELECT COUNT(*) FROM journal_entries")
    after = int((await cur.fetchone())[0])
    await cur.close()
    assert after == 2
