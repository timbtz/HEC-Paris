"""prompt_hash — the audit boundary identity.

Source: RealMetaPRD §7.8 (lines 1227-1240) verbatim. ANTHROPIC_SDK_STACK_
REFERENCE:901-914. This formula is the audit boundary; do not refactor
"for clarity" — different inputs MUST produce different hashes, and the
last-message-only behavior is intentional (PRD1_VALIDATION_BRIEFING C4).

Phase 4.A extension (PRD-AutonomousCFO §7.3): an optional `wiki_context`
list of `(page_id, revision_id)` pairs threads into the canonical input.
Bumping any cited revision flips the hash so the cross-run cache
invalidates exactly the agents that read that page.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable


def prompt_hash(
    model: str,
    system: str,
    tools: list,
    messages: list,
    wiki_context: Iterable[tuple[int, int]] | None = None,
) -> str:
    last_user = next(
        (m for m in reversed(messages) if m["role"] == "user"),
        None,
    )
    # Sort ascending by (page_id, revision_id) so callers don't have to
    # care about the order they cited pages in. Two-element lists beat
    # tuples in JSON canonicalization (json.dumps emits them as arrays
    # either way, but lists are explicit).
    wiki_pairs: list[list[int]] = sorted(
        ([int(pid), int(rid)] for pid, rid in (wiki_context or [])),
        key=lambda p: (p[0], p[1]),
    )
    canonical = json.dumps(
        {
            "model": model,
            "system": system,
            "tools": tools,
            "user": last_user,
            "wiki_context": wiki_pairs,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
