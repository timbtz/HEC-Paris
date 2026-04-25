"""prompt_hash invariants — model swap, last-message-only, tool reorder."""
from __future__ import annotations

from backend.orchestration.prompt_hash import prompt_hash


def _msg(role: str, content) -> dict:
    return {"role": role, "content": content}


def test_model_swap_changes_hash():
    a = prompt_hash("claude-haiku-4-5",  "sys", [], [_msg("user", "hi")])
    b = prompt_hash("claude-sonnet-4-6", "sys", [], [_msg("user", "hi")])
    assert a != b


def test_only_last_user_contributes():
    """Earlier user/assistant turns should NOT change the hash."""
    base = prompt_hash("m", "sys", [], [_msg("user", "FINAL")])
    chatty = prompt_hash(
        "m", "sys", [],
        [
            _msg("user", "earlier"),
            _msg("assistant", "earlier reply"),
            _msg("user", "FINAL"),
        ],
    )
    assert base == chatty


def test_whitespace_in_last_message_changes_hash():
    a = prompt_hash("m", "sys", [], [_msg("user", "ping")])
    b = prompt_hash("m", "sys", [], [_msg("user", "ping ")])
    assert a != b


def test_tool_reorder_changes_hash():
    """Tools list is part of the policy frame; order matters."""
    t1 = [{"name": "a"}, {"name": "b"}]
    t2 = [{"name": "b"}, {"name": "a"}]
    a = prompt_hash("m", "sys", t1, [_msg("user", "x")])
    b = prompt_hash("m", "sys", t2, [_msg("user", "x")])
    assert a != b


def test_system_change_changes_hash():
    a = prompt_hash("m", "sys A", [], [_msg("user", "x")])
    b = prompt_hash("m", "sys B", [], [_msg("user", "x")])
    assert a != b


def test_returns_16_hex_chars():
    h = prompt_hash("m", "s", [], [_msg("user", "x")])
    assert len(h) == 16
    int(h, 16)  # fully hex


def test_no_user_message_yields_stable_hash():
    """Edge case: messages list with only assistant/system roles still hashes."""
    h1 = prompt_hash("m", "s", [], [_msg("assistant", "x")])
    h2 = prompt_hash("m", "s", [], [_msg("assistant", "x")])
    assert h1 == h2
