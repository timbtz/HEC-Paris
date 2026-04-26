"""GL classifier injects wiki body into the system prompt and threads
`(page_id, revision_id)` into AgentResult.wiki_references.

Source: PRD-AutonomousCFO §7.3 + §12 Phase 4.A.
"""
from __future__ import annotations

from backend.orchestration.agents.gl_account_classifier_agent import run
from backend.orchestration.context import AgnesContext
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


def _ctx(store, *, node_outputs: dict | None = None) -> AgnesContext:
    return AgnesContext(
        run_id=42,
        pipeline_name="test-gl-wiki",
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
        path="policies/gl-classification.md",
        title="GL classification",
        frontmatter=WikiFrontmatter(
            applies_to=["gl_accounts", "classification"],
            revision=1,
        ),
        body_md="# GL classification\n\nSENTINEL_BODY_TOKEN — pick the most-specific code.",
        author="test",
    )

    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"gl_account": "626100", "confidence": 0.9},
        tool_name="submit_gl_account",
    )

    ctx = _ctx(store, node_outputs={
        "resolve-counterparty": {"counterparty_legal_name": "WikiVendor"},
    })
    result = await run(ctx)

    # The runner saw the wiki body verbatim in the system prompt.
    assert calls, "expected the runner to have called the fake client"
    system_text = calls[0].get("system") or ""
    assert "## Policy reference (Living Rule Wiki)" in system_text
    assert "SENTINEL_BODY_TOKEN" in system_text

    # AgentResult.wiki_references carries the (page_id, revision_id) pair.
    refs = list(result.wiki_references)
    assert refs == [(page_id, revision_id)]


async def test_no_wiki_pages_means_no_policy_section(
    store, fake_anthropic, fake_anthropic_message,
):
    """When zero wiki pages match, the agent runs exactly as before — no
    `Policy reference` section in the system prompt and `wiki_references`
    is empty.
    """
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"gl_account": "626100", "confidence": 0.85},
        tool_name="submit_gl_account",
    )

    ctx = _ctx(store, node_outputs={
        "resolve-counterparty": {"counterparty_legal_name": "AnyVendor"},
    })
    result = await run(ctx)

    system_text = calls[0].get("system") or ""
    assert "Policy reference" not in system_text
    # The default empty list, not None.
    assert list(result.wiki_references) == []
    # Behavior preserved — chosen GL is still surfaced.
    assert result.output["gl_account"] == "626100"
