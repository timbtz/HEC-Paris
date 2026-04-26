"""counterparty_classifier injects wiki body + carries wiki_references.

Source: plan §STEP-BY-STEP Task 13.
"""
from __future__ import annotations

from backend.orchestration.agents.counterparty_classifier import run
from backend.orchestration.context import AgnesContext
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


def _ctx(store, *, node_outputs: dict | None = None) -> AgnesContext:
    return AgnesContext(
        run_id=11,
        pipeline_name="test-counterparty-wiki",
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
        path="policies/cp-classification.md",
        title="Counterparty matching",
        frontmatter=WikiFrontmatter(
            applies_to=["counterparties", "classification"],
            revision=1,
        ),
        body_md="SENTINEL_CP_TOKEN — match by IBAN first, then name alias.",
        author="test",
    )

    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"counterparty_id": None, "confidence": 0.2},
        tool_name="submit_counterparty",
    )

    ctx = _ctx(store, node_outputs={
        "fetch-transaction": {
            "side": "debit",
            "type": "card",
            "amount": {"value": "12.50", "currency": "EUR"},
            "counterparty_label": "ACME",
        }
    })
    result = await run(ctx)

    assert calls
    system_text = calls[0].get("system") or ""
    assert "## Policy reference (Living Rule Wiki)" in system_text
    assert "SENTINEL_CP_TOKEN" in system_text

    refs = list(result.wiki_references)
    assert refs == [(page_id, revision_id)]


async def test_no_wiki_pages_means_no_policy_section(
    store, fake_anthropic, fake_anthropic_message,
):
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"counterparty_id": None, "confidence": 0.2},
        tool_name="submit_counterparty",
    )

    ctx = _ctx(store, node_outputs={
        "fetch-transaction": {
            "side": "debit",
            "type": "card",
            "amount": {"value": "9.99", "currency": "EUR"},
            "counterparty_label": "OTHER",
        }
    })
    result = await run(ctx)

    system_text = calls[0].get("system") or ""
    assert "Policy reference" not in system_text
    assert list(result.wiki_references) == []
