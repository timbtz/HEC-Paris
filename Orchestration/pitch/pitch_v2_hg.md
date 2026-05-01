# Fingent — Hg Catalyst pitch (3 minutes)

## Part 1 — The product (≈ 2:00)

**0:00 — Hook**

> Your AI agents are now writing your books. Two problems with that. **One:** when a number looks wrong on the P&L, nobody can answer *why* an agent put it there. **Two:** they are billing your card. Last month Anthropic charged you €12,400 as a single supplier line. Who ordered it? Which workflow? Which employee? Today, nobody in Europe can answer that question.
>
> Fingent can — in one SQL line. And we are going to show you, live, in the next ninety seconds.

**0:30 — The demo wedge (live)**

> One dashboard. A Swan webhook fires — a real bank transaction, in front of you. In **under five seconds**, a journal entry slides into the live ledger. Marie's *AI-tokens* envelope rotates from green to yellow. The review badge increments — the classifier wasn't 100% sure. Click the entry → a trace drawer opens: which agent ran, which model, which prompt-hash, which wiki rule it cited, the cost in micro-USD. One click to approve. Done.
>
> That is **a system of action with a human approval gate** — not a copilot, not a chatbot — the way Hg defines it.

**1:00 — The killer query**

> Now the wedge nobody else has. Three lines of SQL, live:
>
> *"Anthropic billed us €1,240 this month. Marie burned €480 on the pricing agent. Sophie €320 on document extraction. Paul €110."*
>
> `SELECT employee, SUM(cost_micro_usd) FROM agent_costs GROUP BY 1`.
>
> Per-employee, per-provider, per-pipeline AI-credit attribution, transactionally consistent with the GL. Ramp ships this in the US — but at *team* and *project* level. **We ship it per employee × journal-line.** That is the cut a French CFO actually needs under URSSAF and DGFIP scrutiny. **Nobody in Europe has it.** That is the wedge.

**1:30 — Two compounding moats**

> **First, the Living Rule Wiki.** Every monthly close, a post-mortem agent observes what slipped through — *"a €300 dinner passed the €250 limit"* — drafts the policy tweak, files it to a markdown wiki the agents themselves read on the next run. The CFO ratifies in one click. Every reasoning agent stamps `(page_id, revision_id)` on its decision row, so when you click a P&L line, you see the *exact* policy revision the agent trusted. **The system gets cheaper and more accurate every month.** That is the compounding moat.
>
> **Second, the dark executor.** Open any pipeline run → a live DAG. Nodes flash blue while thinking, green on success, red on a tripped invariant. Each node shows its own micro-USD cost. You are watching your ledger think — with a price tag on every neuron. **EU AI Act provenance stops being a compliance project; it becomes your default view.**

**2:00 — Why a smart team can't rebuild this in two weeks**

> Not an LLM wrapper. The boring parts are already solved: three-DB split with single-writer locks, integer-cents enforced by CI grep, one chokepoint for journal writes (`gl_poster.post`) with period-lock backdate protection — also CI-enforced. Pipelines are YAML, not code: a new workflow ships as YAML + a tool + one routing line. Swan, Anthropic, and Cerebras already plumbed. **Compliance by design, embedded in the existing workflow.**

---

## Part 2 — The token-management surface in detail (≈ 1:00)

> Competitors will hear this and say "we'll ship AI Spend Intelligence next quarter." Let me show you why ours is structurally different — because this is engineered, not bolted on.

**One table.** `agent_costs`, primary-keyed on `decision_id`, foreign-keyed to `employees`. Columns: `provider`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`, `cost_micro_usd`. **Integer micro-USD only — no floats anywhere on a money path, enforced in CI.** Indexed on `(employee_id, created_at)` and `(provider, created_at)` — the killer query is sub-millisecond.

**One write path.** Every agent finishes inside `propose_checkpoint_commit` — *one* `write_tx` block on `audit.db` with `BEGIN IMMEDIATE`. It writes the decision (model, prompt_hash, confidence, wiki_page_id, wiki_revision_id, line_id), the cost, *and* auto-credits 5 coins to the employee's gamification balance — **all under one commit, idempotent on `agent_decision_id`.** No eventual consistency. No nightly reconciliation. If the cost is in the database, the journal line is too, and the employee got credit.

**One pinned rate table.** Sonnet 4.6: $3 / $15 per million tokens, cache reads at 10%. Cerebras gpt-oss-120b: live rates. Integer division by 1,000,000 — zero float drift, ever.

**One provenance contract.** Every `agent_decisions` row carries the wiki revision the agent read. So when the CFO clicks a journal line, they don't see *"the AI did it"* — they see the model, the prompt hash, the policy revision, the cost in cents, *and* the alternatives the agent rejected. That is the EU AI Act story, shipped as a SQL JOIN — not a PDF export.

**Close.**

> Pennylane just raised €175M at a $4.25B valuation on *AI assists the accountant*. Capital One paid $5.15B for Brex on *agent + spend attribution*. We sit one step further out: **the agent IS the accountant, the wiki IS the policy manual, and every cent — euro or token — has a name on it.** First in Europe. The window before competitors retrofit is roughly twelve months.
>
> Hg backs the incumbent who becomes AI-first. Today, the EU finance stack does not have one. **It can.**
