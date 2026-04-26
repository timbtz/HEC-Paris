"""Cache key incorporates wiki_context — bumping a revision misses cache.

Source: PRD-AutonomousCFO §7.3 + cache.py extension.
"""
from __future__ import annotations

from backend.orchestration import cache as cache_mod


def test_wiki_context_changes_cache_key():
    base = cache_mod.cache_key("agent-x", {"x": 1})
    with_rev_1 = cache_mod.cache_key("agent-x", {"x": 1}, wiki_context=[(7, 100)])
    with_rev_2 = cache_mod.cache_key("agent-x", {"x": 1}, wiki_context=[(7, 101)])
    assert base != with_rev_1
    assert with_rev_1 != with_rev_2


def test_wiki_context_order_invariant():
    """Two pages cited; order does not matter."""
    a = cache_mod.cache_key("n", {"x": 1}, wiki_context=[(1, 10), (2, 20)])
    b = cache_mod.cache_key("n", {"x": 1}, wiki_context=[(2, 20), (1, 10)])
    assert a == b


def test_default_empty_matches_explicit_empty():
    a = cache_mod.cache_key("n", {"x": 1})
    b = cache_mod.cache_key("n", {"x": 1}, wiki_context=[])
    c = cache_mod.cache_key("n", {"x": 1}, wiki_context=None)
    assert a == b == c


async def test_cache_lookup_misses_after_revision_bump(store):
    """End-to-end: store output keyed by wiki_context=(7,100); a revision bump
    to (7,101) produces a different key, so lookup at the new key misses.
    """
    inputs = {"event": "card_outgoing", "amount": 1234}
    key_v1 = cache_mod.cache_key("gl-classify", inputs, wiki_context=[(7, 100)])
    await cache_mod.store(
        store.orchestration, store.orchestration_lock,
        key=key_v1, node_id="gl-classify", pipeline_name="p",
        input_json=inputs, output_json={"output": {"gl_account": "626100"}},
    )
    # Hit on the original key — sanity.
    hit = await cache_mod.lookup(store.orchestration, key_v1)
    assert hit == {"output": {"gl_account": "626100"}}

    # Bump revision — same node_id + same canonical input, different wiki_context
    # produces a fresh key. Lookup misses (cache invalidated for this node only).
    key_v2 = cache_mod.cache_key("gl-classify", inputs, wiki_context=[(7, 101)])
    assert key_v1 != key_v2
    miss = await cache_mod.lookup(store.orchestration, key_v2)
    assert miss is None

    # An unrelated node that did not cite this page is unaffected — its key
    # is computed independently and would already exist in cache normally.
    unrelated_key_v1 = cache_mod.cache_key("post-entry", {"foo": "bar"})
    unrelated_key_v2 = cache_mod.cache_key("post-entry", {"foo": "bar"})
    assert unrelated_key_v1 == unrelated_key_v2
