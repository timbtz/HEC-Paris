"""Frontmatter spec for wiki pages.

Source: PRD-AutonomousCFO §7.3 example block:

    ---
    applies_to: [dinners, fr, bewirtung]      # routing tags
    threshold_eur: 250                        # numeric guards
    jurisdictions: [FR]
    last_audited_by: jean.dupont@cabinet.fr
    last_audited_at: 2026-04-12
    revision: 7
    agent_input_for: [gl_account_classifier_agent, document_extractor]
    ---

`parse_frontmatter` is forgiving: a missing fence yields an empty
frontmatter and the full text as body. A malformed YAML block raises
`ValueError`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass(frozen=True)
class WikiFrontmatter:
    """Machine-readable frontmatter (PRD §7.3)."""
    applies_to: list[str] = field(default_factory=list)
    jurisdictions: list[str] | None = None
    agent_input_for: list[str] | None = None
    threshold_eur: int | None = None
    last_audited_by: str | None = None
    last_audited_at: str | None = None
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict — used by writer.upsert_page."""
        return {
            "applies_to": list(self.applies_to),
            "jurisdictions": list(self.jurisdictions) if self.jurisdictions else None,
            "agent_input_for": (
                list(self.agent_input_for) if self.agent_input_for else None
            ),
            "threshold_eur": self.threshold_eur,
            "last_audited_by": self.last_audited_by,
            "last_audited_at": self.last_audited_at,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WikiFrontmatter":
        """Build from a parsed YAML mapping. Unknown keys are silently dropped."""
        applies_to = raw.get("applies_to") or []
        if not isinstance(applies_to, list):
            raise ValueError(
                f"frontmatter.applies_to must be a list, got {type(applies_to).__name__}"
            )
        applies_to = [str(t) for t in applies_to]

        jurisdictions = raw.get("jurisdictions")
        if jurisdictions is not None and not isinstance(jurisdictions, list):
            raise ValueError(
                "frontmatter.jurisdictions must be a list or absent"
            )
        if jurisdictions is not None:
            jurisdictions = [str(j) for j in jurisdictions]

        agent_input_for = raw.get("agent_input_for")
        if agent_input_for is not None and not isinstance(agent_input_for, list):
            raise ValueError(
                "frontmatter.agent_input_for must be a list or absent"
            )
        if agent_input_for is not None:
            agent_input_for = [str(a) for a in agent_input_for]

        threshold_eur = raw.get("threshold_eur")
        if threshold_eur is not None:
            # Money path: integer EUR, no floats. (CLAUDE.md hard rule.)
            if not isinstance(threshold_eur, int) or isinstance(threshold_eur, bool):
                raise ValueError(
                    "frontmatter.threshold_eur must be an integer (EUR, no cents)"
                )

        revision = raw.get("revision", 0)
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ValueError("frontmatter.revision must be an integer")

        return cls(
            applies_to=applies_to,
            jurisdictions=jurisdictions,
            agent_input_for=agent_input_for,
            threshold_eur=threshold_eur,
            last_audited_by=(
                str(raw["last_audited_by"]) if raw.get("last_audited_by") else None
            ),
            last_audited_at=(
                str(raw["last_audited_at"]) if raw.get("last_audited_at") else None
            ),
            revision=revision,
        )


def parse_frontmatter(md_text: str) -> tuple[WikiFrontmatter, str]:
    """Split YAML frontmatter from body.

    Returns (frontmatter, body_md). When the file does not start with a
    `---` fence, returns an empty frontmatter and the full text as body.
    Raises `ValueError` if the YAML inside the fences is malformed or not
    a mapping.
    """
    text = md_text.lstrip("﻿")  # strip BOM if present
    if not text.startswith("---"):
        return WikiFrontmatter(), md_text

    # Find the closing fence. The opening fence must be a line of its own.
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return WikiFrontmatter(), md_text

    closing_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break
    if closing_idx is None:
        # Unclosed fence — treat as body, do not raise.
        return WikiFrontmatter(), md_text

    yaml_block = "\n".join(lines[1:closing_idx])
    body_md = "\n".join(lines[closing_idx + 1:])
    # Drop a single leading blank line on the body (cosmetic).
    if body_md.startswith("\n"):
        body_md = body_md[1:]

    try:
        raw = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed frontmatter YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return WikiFrontmatter.from_dict(raw), body_md
