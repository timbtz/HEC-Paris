---
applies_to: [benefits, caps, sachbezug, bik]
jurisdictions: [DE, FR]
revision: 1
agent_input_for: [benefit_classifier_agent, bik_calculator]
---

# Per-employee benefit caps

Default per-employee monthly caps. Tenant can override the cap per
employee via `wiki/employees/{id}.md`.

## DE Sachbezug €50/month — shared envelope

The Sachbezugsfreigrenze caps **all non-cash perks combined** at
**€50/month/employee**. This means:

- Urban Sports Club (default €40)
- EGYM Wellpass (default €40, alternative to USC)
- Gift cards / vouchers
- Any other Sachbezug

…all share the same €50 envelope. If USC at €40 + a €15 voucher both land
in March, the voucher is partially over-cap; the over-portion books as
cash compensation (taxable at the employee's payroll rate).

## JobRad (DE company-bike lease)

- Default cap: **bike list price ≤ €3,500**.
- BIK rate: **0.25%** of gross list price/month if the bike is on top of
  salary; **1%** if salary-conversion. The bik_calculator picks based on
  the employee's payroll-arrangement field on `wiki/employees/{id}.md`.
- One bike per employee at a time.

## Finn (DE car subscription)

- Default cap: **founders only** (manager-tier and above gated off by
  default).
- BIK: **1% rule** on gross list price/month for private use; **0.25%** for
  EVs ≤ €70k 2024–2030; **+0.03%/km/month** for the commute distance.

## FR meal cap (URSSAF)

- **€21.10 per meal per head** in 2025 (URSSAF). Above this, the over-cap
  portion is taxable as cash compensation.
- Per-employee monthly meal envelope default: **€400**.

## Company dinners (FR + DE)

- Per-meal cap default: **€60/head clients**, **€21.10/head staff (FR)**.
- Per-employee monthly envelope default: **€400** (FR), **€300** (DE).

## How agents read this

`benefit_classifier_agent` and `bik_calculator` pull this page on every
benefit invocation. The `applies_to: [benefits, caps]` tags mean any
agent that calls `wiki_reader.fetch(['benefits'])` or
`wiki_reader.fetch(['caps'])` gets this page injected verbatim.
