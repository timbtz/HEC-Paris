---
applies_to: [meta, schema]
revision: 1
---

# Wiki schema

This is the conventions document for the Living Rule Wiki (PRD-AutonomousCFO §7.3).
Reasoning agents read pages from `wiki/` verbatim as system-prompt input. Every
agent decision cites the `(page_id, revision_id)` pair of the pages it read,
so a wiki edit is fully traceable.

## Layout

- `wiki/SCHEMA.md` — this file.
- `wiki/policies/` — LLM- and human-edited policy pages (jurisdiction-scoped or
  global). Read by the reasoning agents.
- `wiki/employees/` — per-employee pages (envelopes, benefits, custom rules).
- `wiki/counterparties/` — per-counterparty pages (recurring vendors, rates).
- `wiki/raw/` — immutable source material (auditor PDFs, regulatory excerpts).
  Never edited by agents.

## Frontmatter contract

Every page begins with a YAML frontmatter block bracketed by `---` lines.

```yaml
---
applies_to: [dinners, fr, bewirtung]      # routing tags (required)
threshold_eur: 250                        # numeric guard (optional, integer EUR)
jurisdictions: [FR]                       # optional; FR / DE / EU / …
last_audited_by: jean.dupont@cabinet.fr   # optional but recommended
last_audited_at: 2026-04-12               # ISO date
revision: 7                               # bump on every meaningful edit
agent_input_for: [gl_account_classifier_agent, document_extractor]  # optional hint
---
```

Required fields: `applies_to` (list of routing tags) and `revision` (integer).
All other fields are optional. The wiki_reader tool routes a page to an agent
when at least one tag in the agent's call list intersects `applies_to`. When
the agent passes a `jurisdiction`, pages with a non-empty `jurisdictions` list
that does not contain that code are filtered out; pages with no `jurisdictions`
field are jurisdiction-agnostic and always included.

## Money invariants

Money in frontmatter is **integer EUR only**, never a float and never cents.
The CLAUDE.md hard rule "no floats on money path" applies here too. If you
need cents, use a body section, not frontmatter.

## Revisions

Every save produces a new immutable row in `wiki_revisions` (orchestration.db).
Reasoning agents always read the latest revision; the audit row records the
exact `revision_id` they cited so a replay against an older wiki is exact.
