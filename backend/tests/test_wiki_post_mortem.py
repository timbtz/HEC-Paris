"""wiki_post_mortem agent + wiki_writer tool — auto-file vs. ratification gate.

Source: plan §STEP-BY-STEP Tasks 14, 16, 22 (integration test).
"""
from __future__ import annotations

from backend.orchestration.agents.wiki_post_mortem_agent import run as draft_run
from backend.orchestration.context import FingentContext
from backend.orchestration.tools.wiki_writer import run as write_run
from backend.orchestration.wiki.loader import load_pages_for_tags


def _ctx(store, *, run_id: int, period_id: str = "2026-Q1", node_outputs=None) -> FingentContext:
    return FingentContext(
        run_id=run_id,
        pipeline_name="period_close",
        trigger_source="manual",
        trigger_payload={"period_id": period_id},
        node_outputs=node_outputs or {},
        store=store,
    )


async def test_post_mortem_files_observation_under_post_mortems(
    store, fake_anthropic, fake_anthropic_message,
):
    """Run #1 with no prior post-mortems: agent drafts, writer files."""
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={
            "title": "period_close 2026-Q1 — clean run",
            "body_md": "## Summary\n\nNo anomalies flagged.",
            "requires_human_ratification": False,
        },
        tool_name="submit_post_mortem",
    )

    ctx = _ctx(
        store,
        run_id=42,
        node_outputs={
            "flag-anomalies": {"anomalies": [], "overall_confidence": 0.95},
            "summarize-period": {"period_id": "2026-Q1"},
            "gate-confidence": {"ok": True, "computed_confidence": 0.95},
        },
    )

    result = await draft_run(ctx)
    draft = result.output
    assert draft["path"] == "post_mortems/2026-Q1/period_close_42.md"
    assert draft["frontmatter"]["applies_to"] == ["period_close", "post_mortem", "2026-Q1"]
    assert draft["requires_human_ratification"] is False

    # Now route through the writer tool.
    ctx.node_outputs["draft-post-mortem"] = draft
    writer_out = await write_run(ctx)
    assert writer_out["enqueued"] is False
    assert writer_out["path"] == "post_mortems/2026-Q1/period_close_42.md"
    assert isinstance(writer_out["page_id"], int)

    # Self-improvement loop — a second pass with `period_close` +
    # `post_mortem` tags now finds the page.
    pages = await load_pages_for_tags(
        store.orchestration,
        tags=["period_close", "post_mortem"],
    )
    assert any(p.path == "post_mortems/2026-Q1/period_close_42.md" for p in pages)


async def test_rule_change_routes_to_review_queue(
    store, fake_anthropic, fake_anthropic_message,
):
    """`requires_human_ratification=True` enqueues review_queue, no upsert."""
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={
            "title": "Propose tightening anomaly threshold",
            "body_md": "Lower the dinner threshold from 250 to 200 EUR.",
            "requires_human_ratification": True,
            "proposed_policy_path": "policies/fr-bewirtung.md",
        },
        tool_name="submit_post_mortem",
    )

    ctx = _ctx(
        store,
        run_id=99,
        node_outputs={
            "flag-anomalies": {
                "anomalies": [{"kind": "outlier_expense", "confidence": 0.8}],
                "overall_confidence": 0.7,
            },
        },
    )

    result = await draft_run(ctx)
    draft = result.output
    assert draft["requires_human_ratification"] is True

    ctx.node_outputs["draft-post-mortem"] = draft
    writer_out = await write_run(ctx)
    assert writer_out["enqueued"] is True
    assert writer_out["page_id"] is None
    assert writer_out["revision_id"] is None

    # No new wiki page under post_mortems/.
    pages = await load_pages_for_tags(
        store.orchestration,
        tags=["period_close", "post_mortem"],
    )
    assert not any("/period_close_99.md" in p.path for p in pages)

    # The review_queue row landed.
    cur = await ctx.store.accounting.execute(
        "SELECT kind, reason FROM review_queue WHERE kind = 'wiki_rule_change'"
    )
    rows = await cur.fetchall()
    await cur.close()
    assert rows
    assert "policies/fr-bewirtung.md" in rows[0][1]


async def test_post_mortem_self_grounds_on_prior_runs(
    store, fake_anthropic, fake_anthropic_message,
):
    """After a first run, the second run's draft sees the first run's post-mortem
    in the system prompt — that's the self-improvement loop."""
    calls, fake = fake_anthropic

    # Run #1 — files a post-mortem with a sentinel token.
    fake.messages._response = fake_anthropic_message(
        tool_input={
            "title": "First run",
            "body_md": "PRIOR_RUN_TOKEN — flagged a duplicate vendor.",
            "requires_human_ratification": False,
        },
        tool_name="submit_post_mortem",
    )
    ctx1 = _ctx(store, run_id=1)
    result1 = await draft_run(ctx1)
    ctx1.node_outputs["draft-post-mortem"] = result1.output
    await write_run(ctx1)

    # Run #2 — clear the call log to inspect just this run.
    calls.clear()
    fake.messages._response = fake_anthropic_message(
        tool_input={
            "title": "Second run",
            "body_md": "Clean.",
            "requires_human_ratification": False,
        },
        tool_name="submit_post_mortem",
    )
    ctx2 = _ctx(store, run_id=2)
    await draft_run(ctx2)

    assert calls, "expected the runner to have been called for run #2"
    system_text = calls[0].get("system") or ""
    assert "Prior post-mortems for this pipeline" in system_text
    assert "PRIOR_RUN_TOKEN" in system_text
