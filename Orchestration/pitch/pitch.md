# Agnes — The Autonomous CFO

> *The first ledger in Europe where every cent — the euro you spent
> AND the AI token used to book it — has an employee's name on it.*

**Audience.** The CFO (or founder-as-CFO) of a 50–250 person European
scale-up. Two-person finance team. Books closed 30 days late. AI bill
climbing every month with no idea who triggered it. EU AI Act
enforcement starts August 2026.

---

## ~3-minute pitch (read aloud)

### 0:00–0:30 — The hook

Your AI agents are now writing your books. Two problems with that.
**One:** they're a black box — when something looks wrong on the P&L,
nobody can answer *why* it was booked there. **Two:** they're billing
your card. Last month Anthropic charged you €12,400 as a single
supplier line. Who ordered it? Which feature? Which employee?

Today nobody in Europe can answer that question. Agnes can.

### 0:30–1:30 — The demo wedge (live)

One dashboard. A Swan webhook fires — a real bank transaction.

In **under 2 seconds**: a journal entry slides into the live ledger.
Marie's *AI-tokens* envelope ring rotates green → yellow. The review
queue badge increments — the classifier wasn't 100% sure. Click the
entry → the **trace drawer** opens: which agent ran, which model,
which prompt-hash, which Wiki rule it cited, the cost in micro-USD.
One click to approve. Done.

Now the killer query — three lines of SQL, live:

> *"Anthropic billed us €1,240 this month. Marie burned €480 on the
> pricing agent. Sophie €320 on document extraction. Paul €110."*

`SELECT employee, SUM(cost_micro_usd) FROM agent_costs GROUP BY 1.`
That's it. Per-employee, per-provider, per-pipeline AI-credit
attribution, transactionally consistent with the GL. Ramp shipped this
for human spend in the US in 2025. **Nobody in the EU has it for AI
spend.** That's the wedge.

### 1:30–2:15 — Two new things that make us un-rebuildable

**The Living Rule Wiki.** Every monthly close, a post-mortem agent
observes what slipped through — *"a €300 dinner passed the €250
limit"* — drafts the policy tweak, files it to a markdown wiki the
agents themselves read on the next run. CFO ratifies the change in one
click. Every reasoning agent stamps `(page_id, revision_id)` on its
decision, so cache invalidation is surgical and the audit trail is
exact: click any line on the P&L → see the *exact* wiki revision the
agent trusted when it booked. **The system gets cheaper and more
accurate every month.** That's the compounding moat.

**The dark executor.** Open any pipeline run → live DAG graph. Boxes
flash blue while thinking, green on success, red on a tripped
invariant. Each node shows its own micro-USD cost. You are watching
your ledger think, in real time, with a price tag on every neuron.
EU AI Act provenance is no longer a compliance project — it's the
default view.

### 2:15–2:45 — Why a smart team can't rebuild this in two weeks

Not an LLM wrapper. The boring parts are already solved:

- **Three-DB split** (`accounting` / `orchestration` / `audit`),
  single-writer locks, integer-cents-only enforced by CI grep.
- **One chokepoint** for journal writes (`gl_poster.post`) with
  period-lock backdate protection, also CI-enforced.
- **YAML pipelines.** A new workflow ships as YAML + a tool +
  one routing line — never executor surgery. That's how we ship a
  custom close per customer in days, not quarters.
- **Swan + Anthropic + Cerebras already plumbed.** Provider-swap
  by env var. Cost recorded per call in micro-USD.

### 2:45–3:00 — Close

Pennylane is at $4.25B post-money on *AI assists the accountant*.
Capital One paid $5.15B for Brex on *agent + spend attribution*.

We sit one step further out: **the agent IS the accountant, the wiki
IS the policy manual, and every cent — euro or token — has a name on
it.** First in Europe. Year-one self-funding at ~€100K saved versus a
€15–30K SaaS price.

---

## The one-line pitch

> **A live, per-employee, audit-traced ledger that learns from itself
> — and the only place in Europe where you can ask "who burned this
> month's Anthropic bill?" and get an answer in one SQL line.**

---

## Appendix — five concrete pains, one line each

1. Books dark 30 days. We post in <5s of the bank moving.
2. Budgets are theatre. Ours decrement in the same `write_tx` as the GL line.
3. AI spend is invisible. We attribute it per employee, per pipeline, per call.
4. Audit prep is a 2-week CSV export. Ours is a click-through from the P&L.
5. Period close is manual Excel. Ours is a YAML pipeline that closes itself and writes its own post-mortem.
