"""DAG-viewer `agent.decision` event ships RESOLVED wiki citations.

Source: PRD-AutonomousCFO §7.3 + §7.4. The frontend NodeTraceDrawer needs
`path` + `title` + `revision_number` so a CFO sees what policy the agent
read, not just opaque IDs. The executor calls
`_resolved_wiki_citations` → `wiki.loader.resolve_references` between
`propose_checkpoint_commit` and the SSE emit.
"""
from __future__ import annotations

import json
from dataclasses import replace

from backend.orchestration.agents import noop_agent
from backend.orchestration.executor import execute_pipeline
from backend.orchestration import registries as registries_mod
from backend.orchestration.runners.base import AgentResult
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


async def _events_for_run(store, run_id: int) -> list[tuple[str, str | None, dict]]:
    cur = await store.orchestration.execute(
        "SELECT event_type, node_id, data FROM pipeline_events "
        "WHERE run_id = ? ORDER BY id",
        (run_id,),
    )
    rows = list(await cur.fetchall())
    await cur.close()
    return [(r[0], r[1], json.loads(r[2] or "{}")) for r in rows]


async def test_agent_decision_carries_resolved_wiki_citations(
    store, fake_anthropic, fake_anthropic_message, monkeypatch,
):
    """Wiki-citing agent → event payload carries title + path + rev_no."""
    fm = WikiFrontmatter(applies_to=["dinners"], jurisdictions=["FR"], revision=1)
    page_id, rev_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/fr-bewirtung.md", title="FR Bewirtung",
        frontmatter=fm, body_md="meal-allowance text", author="alice",
    )

    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok"}, tool_name="submit_test",
    )

    # Wrap the noop agent so the AgentResult carries a wiki citation.
    real_run = noop_agent.run

    async def run_with_citation(ctx):
        result = await real_run(ctx)
        if isinstance(result, AgentResult):
            return replace(result, wiki_references=[(page_id, rev_id)])
        return result

    monkeypatch.setattr(noop_agent, "run", run_with_citation)
    # Bust the lru_cache so executor's get_agent('agents.noop:run') re-resolves
    # against the patched module attribute.
    registries_mod._resolve.cache_clear()

    run_id = await execute_pipeline(
        "noop_demo",
        trigger_source="manual",
        trigger_payload={},
        store=store,
        employee_id=1,
        background=False,
    )

    events = await _events_for_run(store, run_id)
    decisions = [(node_id, data) for et, node_id, data in events
                 if et == "agent.decision"]
    assert len(decisions) == 1, [e[0] for e in events]
    _, data = decisions[0]

    citations = data["wiki_citations"]
    assert len(citations) == 1, citations
    c = citations[0]
    assert c["page_id"] == page_id
    assert c["revision_id"] == rev_id
    assert c["revision_number"] == 1
    assert c["path"] == "policies/fr-bewirtung.md"
    assert c["title"] == "FR Bewirtung"


async def test_empty_wiki_references_still_renders_empty_list(
    store, fake_anthropic, fake_anthropic_message,
):
    """Default path: no wiki agent → wiki_citations is `[]` (not missing)."""
    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok"}, tool_name="submit_test",
    )

    run_id = await execute_pipeline(
        "noop_demo",
        trigger_source="manual",
        trigger_payload={},
        store=store,
        employee_id=1,
        background=False,
    )
    events = await _events_for_run(store, run_id)
    decisions = [data for et, _, data in events if et == "agent.decision"]
    assert len(decisions) == 1
    assert decisions[0]["wiki_citations"] == []
