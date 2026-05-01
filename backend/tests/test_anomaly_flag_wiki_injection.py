"""anomaly_flag_agent injects wiki body + carries wiki_references.

Source: plan §STEP-BY-STEP Task 14. The pipeline-name tag means a
`vat_return`-specific rule is invisible to the `period_close` pass.
"""
from __future__ import annotations

from backend.orchestration.agents.anomaly_flag_agent import run
from backend.orchestration.context import FingentContext
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


def _ctx(store, *, pipeline: str, node_outputs: dict | None = None) -> FingentContext:
    return FingentContext(
        run_id=21,
        pipeline_name=pipeline,
        trigger_source="manual",
        trigger_payload={},
        node_outputs=node_outputs or {},
        store=store,
    )


async def test_wiki_body_appears_in_system_prompt(
    store, fake_anthropic, fake_anthropic_message,
):
    page_id, revision_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/anomaly-rules.md",
        title="Anomaly heuristics",
        frontmatter=WikiFrontmatter(
            applies_to=["anomaly_detection", "period_close"],
            revision=1,
        ),
        body_md="SENTINEL_ANOMALY_TOKEN — flag any debit > 5000 cents.",
        author="test",
    )

    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"anomalies": [], "overall_confidence": 0.95},
        tool_name="submit_anomalies",
    )

    ctx = _ctx(
        store,
        pipeline="period_close",
        node_outputs={
            "compute-trial-balance": {"trial_balance": [], "balanced": True},
            "compute-open-entries": {"count": 0, "open_entries": []},
            "compute-vat": {"lines": [], "totals": {}},
        },
    )
    result = await run(ctx)

    system_text = calls[0].get("system") or ""
    assert "## Policy reference (Living Rule Wiki)" in system_text
    assert "SENTINEL_ANOMALY_TOKEN" in system_text

    assert list(result.wiki_references) == [(page_id, revision_id)]


async def test_pipeline_specific_tags_partition_wiki(
    store, fake_anthropic, fake_anthropic_message,
):
    """A `vat_return`-tagged page must NOT leak into the period_close pass."""
    await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/vat-only-rule.md",
        title="VAT-only rule",
        frontmatter=WikiFrontmatter(
            applies_to=["vat_return"],
            revision=1,
        ),
        body_md="VAT_ONLY_TOKEN — only fires inside vat_return.",
        author="test",
    )

    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"anomalies": [], "overall_confidence": 0.95},
        tool_name="submit_anomalies",
    )

    ctx = _ctx(
        store,
        pipeline="period_close",
        node_outputs={
            "compute-trial-balance": {"trial_balance": [], "balanced": True},
            "compute-open-entries": {"count": 0, "open_entries": []},
            "compute-vat": {"lines": [], "totals": {}},
        },
    )
    result = await run(ctx)

    system_text = calls[0].get("system") or ""
    assert "VAT_ONLY_TOKEN" not in system_text
    assert list(result.wiki_references) == []
