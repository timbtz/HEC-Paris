---
applies_to: [gl_accounts, classification]
revision: 1
agent_input_for: [gl_account_classifier_agent]
---

# GL account classification — operating principles

This page is the policy frame that the GL classifier reads on every uncached
classification. Treat it as the single source of truth; if the auditor flags a
miscategorisation, edit this page and the next run will use the new revision.

## Pick the most-specific account

The chart of accounts is hierarchical (PCG: 6, 60, 601, 6011…; SKR04 follows
the same shape with German codes). Always pick the *deepest* code that
matches. "626100 — Frais postaux et télécommunications" beats "626" beats
"62" beats "6".

## Class 6 vs class 7

- **6xxx** = expenses (operating costs, OpEx). Most card-debit transactions
  for travel, meals, software, postal, and telecom land here.
- **7xxx** = revenue. Customer payments and revenue-recognition events.

If the transaction is a debit on the company account from an outbound vendor,
it is almost certainly class 6.

## PCG vs SKR04 quick hint

- **PCG (France)** — three-digit roots: `601` raw materials, `606` non-stockable
  supplies, `613` rents, `622` fees & honoraria, `624` transport, `625` travel,
  `626` postal/telecom, `627` bank fees.
- **SKR04 (Germany)** — four-digit codes: `4400` materials, `4500` consumables,
  `4920` postal, `4930` telecom, `6800` rent, `6815` software-as-a-service.

Default to PCG when the company's primary jurisdiction is France; switch to
SKR04 only when the upstream resolver tagged the line with `jurisdictions: DE`.

## Bewirtung (meals) cross-reference

Restaurant invoices in Germany follow the Bewirtungsbeleg rules (70% deductible
+ 30% non-deductible split). In France, business meals over €250 require an
attendees list. See `policies/fr-bewirtung.md` for the FR rules; the DE counterpart
will land under `policies/de-bewirtung.md` once the Steuerberater ratifies.

## Confidence calibration

- ≥ 0.90 — the counterparty + amount unambiguously match the rule. Auto-post.
- 0.70–0.89 — confident but worth a review-queue row.
- < 0.70 — fall through to human review. Do not auto-post.

When confidence is below 0.85, populate the `alternatives` field with at least
two near-miss codes. The reviewer reads them and can override.

## Anti-patterns

- Never pick a "miscellaneous" / "divers" code (PCG 658) when a more-specific
  one matches. That code is a smell, not a destination.
- Never invent a code that isn't in the closed enum. The enum is sourced from
  `chart_of_accounts` at request time.
- Never default to retained earnings (PCG 120) for an operating expense — that
  code is reserved for the year-end closing entry.
