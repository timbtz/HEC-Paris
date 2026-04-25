"""cache_key invariants + lookup/store/record_hit round-trip."""
from __future__ import annotations

import pytest

from backend.orchestration import cache as cache_mod


def test_float_one_vs_int_one_differ():
    """Float 1.0 must hash differently than int 1."""
    k_float = cache_mod.cache_key("n", {"x": 1.0})
    k_int = cache_mod.cache_key("n", {"x": 1})
    assert k_float != k_int


def test_dict_insertion_order_invariant():
    a = cache_mod.cache_key("n", {"a": 1, "b": 2})
    b = cache_mod.cache_key("n", {"b": 2, "a": 1})
    assert a == b


def test_whitespace_in_string_differs():
    a = cache_mod.cache_key("n", {"s": "hello"})
    b = cache_mod.cache_key("n", {"s": "hello "})
    assert a != b


def test_nested_arrays_order_matters():
    """List order is meaningful (not a set)."""
    a = cache_mod.cache_key("n", {"xs": [1, 2, 3]})
    b = cache_mod.cache_key("n", {"xs": [3, 2, 1]})
    assert a != b


def test_unicode_stable():
    a = cache_mod.cache_key("n", {"s": "café"})
    b = cache_mod.cache_key("n", {"s": "café"})
    assert a == b


def test_node_id_part_of_key():
    a = cache_mod.cache_key("alpha", {"x": 1})
    b = cache_mod.cache_key("beta",  {"x": 1})
    assert a != b


def test_nan_rejected():
    with pytest.raises(ValueError):
        cache_mod.cache_key("n", {"x": float("nan")})


def test_inf_rejected():
    with pytest.raises(ValueError):
        cache_mod.cache_key("n", {"x": float("inf")})


async def test_lookup_miss_returns_none(store):
    result = await cache_mod.lookup(store.orchestration, "no-such-key")
    assert result is None


async def test_store_then_lookup(store):
    key = cache_mod.cache_key("n1", {"x": 42})
    await cache_mod.store(
        store.orchestration, store.orchestration_lock,
        key=key, node_id="n1", pipeline_name="p",
        input_json={"x": 42},
        output_json={"output": {"y": 100}},
    )
    out = await cache_mod.lookup(store.orchestration, key)
    assert out == {"output": {"y": 100}}


async def test_record_hit_increments(store):
    key = cache_mod.cache_key("n2", {"x": 1})
    await cache_mod.store(
        store.orchestration, store.orchestration_lock,
        key=key, node_id="n2", pipeline_name="p",
        input_json={"x": 1}, output_json={"output": "r"},
    )
    await cache_mod.record_hit(store.orchestration, store.orchestration_lock, key)
    await cache_mod.record_hit(store.orchestration, store.orchestration_lock, key)
    cur = await store.orchestration.execute(
        "SELECT hit_count FROM node_cache WHERE cache_key = ?", (key,))
    assert (await cur.fetchone())[0] == 2
