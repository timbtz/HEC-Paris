# Agnes — The Autonomous CFO

> *Pennylane gave the accountant a co-pilot. We give the SME founder an autonomous CFO.*

---

## Who we are pitching this to

The CFO (or founder-acting-as-CFO) of a 50–250 person European scale-up.
Burning €200K–€2M / month. Two-person finance team. Closing the books
late every month. Approving expenses by gut feel. Watching AI-API spend
climb without a way to attribute it. Subject to the EU AI Act from
August 2026.

---

## 1. The concrete pain — five things that hurt every week

| # | Pain | Why it hurts |
|---|---|---|
| 1 | **The books are dark for ~30 days.** Bank → aggregator → accountant → ledger. | Decisions get made on stale data. Cash position is a guess. |
| 2 | **Budget is theatre.** A Notion doc says Marie has €5K/month for tools. She spent €7K. Nobody knew until close. | Overspend surfaces a month late. Limits aren't enforceable. |
| 3 | **AI-API spend is invisible.** Anthropic / OpenAI / Mistral land as one supplier line. | No way to say *which team*, *which feature*, *which employee* burned the credit. |
| 4 | **Audit prep is two weeks of CSV exports.** Every "why was this booked here?" becomes a Slack thread. | Time tax + reputational risk under the AI Act provenance regime. |
| 5 | **Period close is manual Excel.** Trial balance, accruals, VAT return — rebuilt by hand every month. | One person's bottleneck. One person's mistake. |

---

## 2. What the CFO sees on day one — concrete value

A single dashboard. Three numbers. Updated in **under 5 seconds** of the
cash actually moving:

1. **Live cash position.** Swan webhook → classified → posted to the GL →
   on screen before the bank app refreshes. Pennylane is sub-day via
   polling. We are sub-5s via webhook.
2. **Per-employee budget envelopes.** Tim, Marie, Paul, each with a real
   `swan_iban` and a real envelope. Consumption is **transactionally
   consistent with the ledger** — same `write_tx`, same DB, same period.
   Ramp / Brex have budgets too, but theirs sit *above* the books and
   are eventually consistent. Nobody in the EU has this.
3. **Per-employee AI-credit cost.** Anthropic billed €X this month —
   €Y went to Marie's pricing agent, €Z to Paul's research agent. By
   API key, by employee, by feature, in micro-USD precision. Ramp
   shipped this for human spend in 2025, US-only; for AI-API spend,
   nobody EU.

Click any line item → drill into the **decision trace**: which agent
ran, which model, which tokens, which prompt-hash, which rule fired,
which source PDF. Every cent on the report traces back to a
`(decision, cost, employee)` triple in `audit.db`. The EU AI Act
provenance story writes itself.

---

## 3. How we simplify his day-to-day

| Today | With Agnes |
|---|---|
| *"Get me last month's P&L"* → 4 days, 2 emails, 1 Excel file | One click. SQL over `journal_entries`. Balance sheet, P&L, trial balance, VAT return — native (capabilities.md). |
| *"Why is OpEx up 12%?"* → 90-minute meeting | Drill envelope → agent decision → source receipt PDF. 30 seconds. |
| *"Approve Marie's €1.2K Notion renewal"* | Agent has already classified, posted the accrual, and decremented the right envelope. CFO approves once at the confidence-gate threshold — not on every line. |
| *"Close the month"* | Period-close pipeline: trial balance → unbalanced-entry detection → accrual proposals → `confidence_gate` → auto-post or queue for review. |
| *"Prepare a DD pack for the seed round"* | Agentic report pipeline. Hours, not weeks. Every figure cite-back to a posted journal line. |
| *"Save €15K for the CNC machine by Q3"* | Goal-driven campaign agent reshapes envelopes / coupons inside pre-set bounds. Net-new in EU. |

The CFO's job moves from *doing the bookkeeping* to *approving the
agent's plan once* and *reading the result*. Same human in the loop —
but at the threshold gate, not the line item.

---

## 4. Which problems do we solve?

| Problem | Our wedge |
|---|---|
| **Latency between bank and ledger** | Swan webhook → posted entry in <5s. Live ledger, not nightly batch. |
| **Budget enforcement** | Per-employee envelopes inside the same DB as the GL. Decremented in the same transaction as the journal entry. |
| **AI spend attribution** | `agent_costs` table joined to `agent_decisions` joined to employee. Per API key, per pipeline run. |
| **Audit & EU AI Act provenance** | `decision_trace` is a first-class object joined to every `journal_line`. Not a JSON sidecar. Compliance by construction. |
| **Manual period close** | Period-close, VAT-return, balance-sheet pipelines. Deterministic SQL where possible; agents only on edge cases (prepaid SaaS classification, accrual proposals, narrative). |
| **Long DD prep** | Agentic DD / board-pack reports with cite-back to ledger lines. Big 4 charges €50–150K for middle-market DD; we deliver hours, not weeks. |

---

## 5. Why a smart team can't rebuild this in two weeks

This is not an LLM wrapper. It is the **system of record** and the
**system of action**, fused, with the boring infrastructure already
solved:

- **Three-DB split** (`accounting` / `orchestration` / `audit`) with
  single-writer per-DB locks, canonical PRAGMA block, migration runner,
  bootstrap-replay parity. 50+ tests catch race conditions and PRAGMA
  drift.
- **The decision-trace contract.** Every agent write goes through
  `propose → checkpoint → commit`. `gl_poster.post` is the *single
  chokepoint* for `journal_entries` writes. CI grep enforces both.
- **Money is integer cents end-to-end.** CI grep audits no float ever
  touches a money path.
- **YAML pipeline DSL** with strict-key validation, Kahn topological-
  layer DAG execution, fail-fast cancellation, cross-run cache. New
  event types ship as **YAML + a tool + one routing line** — never
  executor surgery. This is what makes "ship a new workflow per
  customer" cheap.
- **Swan plumbing already wired.** OAuth client_credentials with
  refresh-on-401-and-retry, GraphQL client with the union-error
  pattern, webhook signature verification with constant-time compare,
  idempotency on `(provider, event_id)`.
- **Anthropic runner with deterministic fallback.** `submit_*` tool
  forcing, `APITimeoutError` → no retry, finish_reason='timeout'.
  Cost recorded per call in micro-USD.

The moat is not the model. The moat is the **schema, the chokepoint
discipline, and the trace contract**. Hg Catalyst's own framing:
*"systems of record become systems of action."* That is exactly this.

---

## 6. What is this worth?

### Concrete ROI for a 100-person scale-up

| Lever | Annual value |
|---|---|
| Finance team time saved (~40% of one FTE) | **~€35K** |
| Close cycle 30 → 5 days → working-capital decisions one cycle earlier | **~€20–80K** in deferred receivables / better cash deployment |
| AI-spend attribution & discipline (€100K+/yr of AI bills today, unattributed) | **~10–20%** of AI spend recovered = **€10–20K** |
| DD / board-pack reports replacing Big 4 work | **€50–150K** per fundraise / audit cycle |
| EU AI Act compliance posture (enforcement Aug 2026) | Avoids 4% global turnover fine exposure — **uncapped** |

**Year-one self-funding** at ~€100K saved against a SaaS price point
of €15–30K / year. That is the bar Hg Catalyst publishes (10% of
bookings from AI features, 40% new-logo uplift in year one).

### Comparable benchmarks

- **Pennylane** — $4.25B post-money (Jan 2026) on the *AI-assisted
  accountant* thesis. We sit one step further out: we do the work,
  agent-first, not suggest-and-click.
- **Brex Agent Mesh** — Capital One paid $5.15B (Jan 2026) for the
  Brex platform; agent + spend attribution was the strategic asset.
- **Ramp Agents** — shipped 2025, US-only. EU equivalent is open
  territory.
- **Hg Catalyst portfolio (GTreasury, FE fundinfo, Prophix)** — every
  one is *agentic action with human oversight, narrow scope, embedded
  in the existing workflow, compliance-by-design*. Agnes fits the
  pattern exactly.

---

## The one-line pitch

> **The first live, per-employee, audit-traced ledger in Europe — built
> as an agent runtime, not a chat loop. We turn the SME founder's
> finance stack from a system of record into a system of action.**
