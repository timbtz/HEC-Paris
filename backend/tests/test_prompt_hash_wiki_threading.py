"""prompt_hash threads `wiki_context` (page_id, revision_id) into the hash.

Source: PRD-AutonomousCFO §7.3.
"""
from __future__ import annotations

from backend.orchestration.prompt_hash import prompt_hash


def _msg(role: str, content) -> dict:
    return {"role": role, "content": content}


def test_wiki_context_changes_hash():
    base = prompt_hash("m", "sys", [], [_msg("user", "hi")])
    with_wiki = prompt_hash(
        "m", "sys", [], [_msg("user", "hi")],
        wiki_context=[(1, 100)],
    )
    assert base != with_wiki


def test_different_revision_changes_hash():
    a = prompt_hash("m", "sys", [], [_msg("user", "hi")], wiki_context=[(1, 100)])
    b = prompt_hash("m", "sys", [], [_msg("user", "hi")], wiki_context=[(1, 101)])
    assert a != b


def test_different_page_changes_hash():
    a = prompt_hash("m", "sys", [], [_msg("user", "hi")], wiki_context=[(1, 100)])
    b = prompt_hash("m", "sys", [], [_msg("user", "hi")], wiki_context=[(2, 100)])
    assert a != b


def test_wiki_context_order_invariant():
    """The function sorts ascending — passing [(1,100),(2,200)] vs reverse
    must produce the same hash."""
    a = prompt_hash("m", "sys", [], [_msg("user", "hi")],
                    wiki_context=[(1, 100), (2, 200)])
    b = prompt_hash("m", "sys", [], [_msg("user", "hi")],
                    wiki_context=[(2, 200), (1, 100)])
    assert a == b


def test_default_empty_wiki_context_is_stable():
    """Same call with default `wiki_context=None` and explicit `[]` — same hash."""
    a = prompt_hash("m", "sys", [], [_msg("user", "hi")])
    b = prompt_hash("m", "sys", [], [_msg("user", "hi")], wiki_context=[])
    c = prompt_hash("m", "sys", [], [_msg("user", "hi")], wiki_context=None)
    assert a == b == c


def test_returns_16_hex_chars_with_wiki():
    h = prompt_hash("m", "s", [], [_msg("user", "x")], wiki_context=[(7, 42)])
    assert len(h) == 16
    int(h, 16)
