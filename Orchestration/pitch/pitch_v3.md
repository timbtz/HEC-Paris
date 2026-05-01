# Fingent — Pitch v3 (Hg Catalyst, 3 minutes — meta-layer cut)

## Delivery notes

- The frontend is on-screen the whole time as **ambient evidence** — do
  not narrate clicks. Each value beat is delivered while a relevant
  surface (live ledger, trace drawer, DAG, wiki, AI-spend page) is
  visible behind the speaker.
- Target ~130 wpm. Total spoken text ≈ 400 words. Pauses at the bold
  beats are deliberate — that is where the judges land on the value.
- Each section maps to **one** value, **one** buzzword Hg already
  uses, and **one** competitive cut. No section is a feature tour.

---

## The pitch (3:00)

### 0:00 — Hook (≈25s)

> Every European SME CFO runs the same broken loop. Books closed thirty
> days late. Four tools that don't talk — Qonto for cash, Pennylane for
> the ledger, Notion for policy, and an Anthropic invoice nobody can
> decompose. They are now paying agents to write the books, and the
> books still take a month. We rebuilt the back-office as one
> **agent-native digital twin of the company's money** — one ledger,
> one audit trace, end-to-end.

### 0:25 — The meta layer (≈30s)

> Fingent is the **live, machine-readable representation of your
> company's financial state**: every transaction, every policy, every
> approval, every agent decision, written into one provenance-stamped
> ledger that humans *and* agents read the same way. Not a dashboard,
> not a copilot — think of it as a **digital twin of your finance
> department** that *executes* with a human approval gate. Exactly the
> shape — system of action, not system of record — that Hg keeps
> asking for.

### 0:55 — Three personas, one surface (≈30s)

> Three audiences, one source of truth, no swivel-chair. **The CFO**
> gets one-click closes, audit-ready period reports, and provenance
> stamped on every line of the P&L. **The chief of staff** gets
> envelopes that decrement in the same write transaction as the GL —
> budgets stop being theatre. **Employees** see their own envelopes,
> AI tokens included, and get cleared without opening a ticket. One
> system. Three views. No CSV exports between them.

### 1:25 — Above Pennylane and Qonto, native to MCP (≈30s)

> Pennylane is *AI assists the accountant.* Qonto is *a bank with
> rails.* We sit one layer above both: an **AI-native operating system
> for finance** that calls them as integrations, not competitors.
> **Every workflow is a YAML pipeline** — period close, VAT return,
> year-end. And our **MCP server** exposes the entire ledger as a typed
> tool surface, so Claude, Cursor, or your own copilot queries the
> books, posts entries, runs a close — with the *same* provenance and
> approval gates the CFO sees on the dashboard. Agents bring their
> own UI; we bring the ground truth.

### 1:55 — The Living Rule Wiki (≈30s)

> Here is the compounding moat nobody else ships. **Your rules, your
> company knowledge, your past reports, every human approval and
> rejection** flow into a markdown wiki the agents themselves re-read
> on the next call. A €300 dinner blew the €250 limit? The post-mortem
> agent drafts the policy tweak, the CFO ratifies in one click, and
> the *next* expense classifier reads the new rule before it decides.
> Every reasoning agent stamps `(page_id, revision_id)` on its
> decision row. **The system gets sharper and more *yours* every
> month.** That is the loop.

### 2:25 — Deterministic visibility, the dark executor (≈25s)

> Hg's lens: agents you can *trust in production.* Open any pipeline
> run and you see a **live DAG** — nodes flashing blue while thinking,
> green on success, red on a tripped invariant — each one tagged with
> model, prompt-hash, wiki revision, and cost in micro-USD. **EU AI
> Act provenance becomes your default view, not a quarterly compliance
> project.** This is what *deterministic agentic AI* looks like when
> it has actually shipped.

### 2:50 — Close (≈10s)

> Pennylane raised €175M at $4.25B on *AI assists the accountant.*
> Brex sold for $5.15B on *agent + spend attribution.* We sit one step
> further out: **the agent IS the accountant, the wiki IS the policy
> manual, every cent has a name on it, all of it queryable through
> MCP.** First in Europe. Twelve-month window.

---

## Appendix — VC-defensive Q&A (rapid-fire, prepared)

**Q: "Why doesn't Pennylane ship this in six months?"**
> They ship AI assist on a 200K-customer codebase. Fingent requires
> `journal_entries` carrying `decision_id`, `prompt_hash`,
> `wiki_revision_id`, and `cost_micro_usd` — a schema migration that
> breaks every existing integration. Plus a wiki primitive their data
> model does not have. We did it on a clean foundation; they cannot
> retrofit it without an outage window measured in quarters.

**Q: "What's the data moat?"**
> The Wiki. Each customer's wiki diverges within weeks of go-live.
> It is their policy memory in markdown, written by their agents and
> ratified by their CFO. After twelve months you cannot lift it out
> without losing every line of the P&L's provenance. The customer
> *becomes* the moat.

**Q: "Per-employee AI-cost attribution — does it actually matter?"**
> URSSAF and DGFIP audit benefits-in-kind per employee. Anthropic at
> €12K/month is now a benefits-in-kind question, not a SaaS line item.
> Ramp ships this for human spend in the US. Nobody ships it for AI
> spend in Europe. **`SELECT employee, SUM(cost_micro_usd) FROM
> agent_costs GROUP BY 1`** — three lines of SQL, sub-millisecond.

**Q: "How does this coexist with the existing stack?"**
> We do not rip out Qonto or Pennylane on day one. Qonto is the bank
> rail — Swan webhooks already plumb to us. Pennylane remains the
> statutory ledger via integration. We become the **agentic layer that
> thinks on top** — the place where rules, agents, costs, and
> approvals converge.

**Q: "What's your evaluation story?"**
> Every agent decision is replayable from `(prompt_hash, model,
> wiki_revision)`. Every period close runs against a frozen seed; CI
> fails on a P&L delta. The trace drawer is the eval surface — golden
> runs are *diffs of provenance*, not screenshots.

**Q: "ROI in year one?"**
> 30-day close → same-day. One avoided finance hire. AI spend cut
> 20–40 % by attribution. ≈€100K saved against a €15–30K SaaS price.
> Year-one self-funding.

**Q: "Why now?"**
> Three things converged in late 2025: EU AI Act enforcement starts
> August 2026, Anthropic invoices crossed the €10K/month line for
> every scale-up, and the MCP standard ratified — meaning agents have
> a calling convention to integrate against. We ship into all three
> at once.

---

## Cheat-sheet — what to point at on screen, when

| Section | Frontend surface visible behind speaker |
|---|---|
| Hook | Live ledger ticking, AI-spend page in the corner |
| Meta layer | Trace drawer half-open on a posted entry |
| Three personas | Envelopes view (chief of staff) → AI Spend (employee) |
| Above Pennylane/Qonto | MCP terminal in a side window running a `run_pipeline` call |
| Living Rule Wiki | Wiki page → its `wiki_revisions` history → an agent decision row referencing it |
| Dark executor | Live DAG of a `period_close` run, costs ticking |
| Close | Per-employee × per-provider matrix on the AI-spend page |
