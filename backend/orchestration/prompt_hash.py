"""prompt_hash — the audit boundary identity.

Source: RealMetaPRD §7.8 (lines 1227-1240) verbatim. ANTHROPIC_SDK_STACK_
REFERENCE:901-914. This formula is the audit boundary; do not refactor
"for clarity" — different inputs MUST produce different hashes, and the
last-message-only behavior is intentional (PRD1_VALIDATION_BRIEFING C4).
"""
from __future__ import annotations

import hashlib
import json


def prompt_hash(model: str, system: str, tools: list, messages: list) -> str:
    last_user = next(
        (m for m in reversed(messages) if m["role"] == "user"),
        None,
    )
    canonical = json.dumps(
        {"model": model, "system": system, "tools": tools, "user": last_user},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
