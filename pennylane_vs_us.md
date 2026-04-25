# Pennylane vs Us — How We Win, and What to Build

**Companion to:** Pitch Research, Architecture Reference, Project Briefing
**Purpose:** Sharpen the differentiation against Pennylane (the obvious comparison the jury will make), define what "AI-native" means in finance in 2026, and lock the infrastructure decisions needed to deliver agentic finance — campaigns, autonomous reports, and the brain-for-SME-finance positioning.
**Status:** Working draft, written 2026-04-25.

---

## TL;DR — the elevator answer

> **Pennylane is a beautiful co-pilot for accountants.** The books, the e-invoice, the Swan-backed card. We're not building that.
>
> **We're building the autonomous CFO sitting *behind* the books** for SMEs that don't have one. Swan-native and live to the second, every AI decision auditable down to the prompt, with a goal-driven campaign engine and agents that produce the DD pack a Big-4 firm charges €80K for.
>
> Pennylane sells to the cabinet that serves the SME. We sell to the SME founder who's tired of being served by a stack glued together with PDF exports.

---

## 1. What Pennylane actually delivers (be honest)

Pennylane is well-built and well-funded ($204M @ $4.25B, Jan 2026; 350K+ companies and accountants). They are not a strawman.

### Pricing (FR, EUR HT/month)

| Plan | Self-employed | 1–5 emp. | 6–15 emp. |
|---|---|---|---|
| **Basique** — invoicing, expense tracking, Pro account | €14 | €29 | €49 |
| **Essentiel** — auto follow-ups, recurring invoices, cash forecast, integrations | €24 | €49 | €99 |
| **Premium** — full GL, validation workflows, VAT/tax filing, monthly close | €79 | €149 | €199 |

E-invoicing (Plateforme Agréée for the Sept 2026 mandate) bundled across all tiers. Cabinet pricing is on-demand.

### Feature surface (concrete)

- **Bookkeeping & GL.** Full double-entry, French PCG, automated categorization (rules + AI), validation workflows, monthly/annual close, VAT and tax-return generation. Premium only.
- **Bank connectivity.** 300+ aggregator connectors. **Polling cadence** — daily/intraday sync, not webhook-streamed. Reviews flag intermittent reconciliation gaps.
- **Pennylane Banking ("Compte Pro") — Swan-backed.** French IBAN, unlimited free Mastercards, Apple/Google Pay, instant SEPA, batch SEPA (XML), payments to 130+ countries, segregated funds at BNP Paribas. From €7/month bundled with Starter. **Pennylane is *not* a payment institution; Swan is.**
- **E-invoicing for Sept 2026.** Registered Plateforme Agréée; tested against AIFE/SIM/Peppol; Factur-X / UBL / CII issuance + reception in every plan.
- **Expense management / cards.** Receipt photo capture, OCR, blocked card if missing receipt, expense reports + mileage (Essentiel+), validation flows (Premium). **No first-class per-employee budget envelopes.**
- **Cash flow / treasury.** Forecasting (Essentiel+), aggregated dashboards. **No active treasury actions** — no auto-sweep, no FX trigger, no surplus push.
- **AI features.** "Co-pilot for accountants" — NL Q&A, categorization suggestions, OCR, rule-based anomalies. Strictly **assistive**: AI suggests, human clicks. No published acceptance-rate numbers.
- **Reporting / dashboards.** Custom dashboards (Premium), analytical dimensions by project/dept/product. **Board pack and management report = manual export.**
- **Mobile app.** iOS/Android, strong receipt capture, push notifications. Receipt-loss complaints recurring on App Store.
- **Multi-entity / consolidation.** Multi-entity yes; **true group consolidation offloaded to a partnership with [Joiin](https://www.joiin.co/pennylane/)**. They do not own this primitive.
- **Integrations.** 300+ via native + Zapier/Make: HubSpot, Stripe, Shopify, Silae (payroll), GoCardless, Qonto.

### Go-to-market

**Both, but accountant-led.** ~5,000 partner cabinets onboard the SME; the SME logs in for invoicing and expense capture; the accountant runs the books inside the same product. Direct SME signup exists but the buyer journey is dominated by cabinet referral.

### What customers actually complain about

- **Bank connectivity drift** — "connectivity issue with my bank account" recurring on Capterra; 38+ outages in 11 months on StatusGator.
- **Tier-1 support quality** — "recurring login issues and unreliable Tier 1 support causing work stoppages" on G2. High variance: "time saver" alongside "everything about it is crap."
- **Onboarding & verification** — long KYC; e-commerce integration changes have broken reconciliation.
- **Receipt loss in mobile** — "multiple loss of receipts" via the photo flow.
- **Implementation latency** — slow setup for cabinets onboarding multi-entity SMEs.

These are not nicheniggles. **Latency, reliability, and explainability** are the consistent complaint vectors — exactly the surfaces an agentic, webhook-native, decision-traced architecture attacks at the foundation, not as features.

---

## 2. The attack surface — what Pennylane structurally cannot do well

| Capability | Pennylane today | Why we win |
|---|---|---|
| Truly live balance sheet from a banking event | Polling sync; sub-day not sub-second | Swan webhooks → <5s end-to-end |
| Per-employee budget envelopes tied to GL | Tracked above the books | Native primitive, transactionally consistent |
| Decision-trace per AI categorization | Categorization shown; model/prompt/alternatives hidden | First-class object, click-to-audit |
| Agentic AI (proposes a chain, executes on one approval) | Suggests, then human clicks every line | Planner + tool registry + checkpoint graph |
| Multi-step DD prep / board pack / investor reporting | Manual export | Agent-produced, cite-back to source |
| Treasury actions (sweep, FX, surplus push) | Read-only forecast | Agent-executed under policy guardrails |
| Goal-driven spending campaigns | Static budgets | Net-new primitive |
| Cross-system write-back (CRM/PM/HR) | Read-mostly | Agent calls all tools both ways |
| AI-API-spend as first-class GL object | Not modeled | Yes — the demo set-piece |
| EU AI Act audit trail readiness | Partial | By construction |

The architectural reason: **Pennylane's data model is an accountant's GL plus an integration bus.** There is no agent runtime, no tool registry the AI can call to write into HubSpot/Linear/Lucca, no checkpoint/approval graph, and no decision-trace object stored alongside each posting. Adding any of this means a re-platform, not a feature shipment.

---

## 3. What "AI-native" actually means in finance in 2026

The bar has moved from "AI suggests, human clicks" to **"AI proposes a workflow, human approves once at a checkpoint, AI executes the chain."** Reference points:

- **Ramp Agents (July 2025).** Controller-grade agents auto-approve low-risk expenses, flag outliers, answer employees over SMS, suggest policy improvements. 99% policy enforcement claim, 15× more out-of-policy spend caught vs. baseline. Roadmap: procurement, vendor onboarding, reconciliation, budgeting, reporting agents. Claim **$163M annual salary dollars freed, 208K hours/month saved** in 2025.
- **Brex Agent Mesh (2025-26).** Many narrow agents (treasury, policy, payouts, accounting) communicate in plain English over a shared message bus. **"Less orchestration"** — agents decide, humans observe. Powering OpenAI's global spend. **Capital One paid $5.15B for this platform in Jan 2026**, the explicit thesis being agentic finance at scale.
- **Digits AGL + AI Agents (Mar/Jun 2025).** Trained on $825B of transactions, claims **95% of bookkeeping automated**, 97.8% accuracy, runs the close in the background and presents a near-complete checklist for human approval.
- **Mercury / GTreasury / Kyriba.** Agentic treasury: auto-sweep, auto-rebalance under policy guardrails, conversational forecast queries. US Bank × Kyriba launched Liquidity Manager Nov 2025.

**The architectural bar for "the brain for all financial things in an SME":**

1. **Tool registry** the agent can call (Swan banking, GL writer, e-invoicing emitter, HubSpot, Linear, Slack, Anthropic billing, Stripe…).
2. **Memory of company state** — chart of accounts, employees, policies, vendor relationships, past decisions, ongoing campaigns.
3. **Planner** that decomposes a goal into a sequence of tool calls.
4. **Checkpoint graph** with human approval gates only at risk-graded moments. LangGraph-style `approval_required` is the de facto pattern.
5. **Decision trace** persisted per action — model, prompt, alternatives considered, rule, confidence, who approved.
6. **Rollback/undo** — every agent action reversible inside an audit window.

If we ship the demo on a chat-completion loop, the pitch collapses the moment a judge asks "what does the AI actually *do*?"

---

## 4. The crucial functions to focus on

These are the four primitives that, taken together, define "the brain for SME finance" — and none of them exist in Pennylane.

### 4.1 Live double-entry from Swan webhooks (the foundation)

Already in the architecture doc. Critical because every other primitive layers on it. **Wire one Swan webhook end-to-end before anything else.** If `transaction.created` doesn't drive a journal entry within 5 seconds, no demo moment works.

### 4.2 Per-employee budget envelopes (the wedge)

New `Domain F — Budgets` with `budget_envelopes` and `budget_allocations` (already sketched in `pitch_research.md`). Invariant: every allocation references a real `journal_line`. Envelope balance is computed from the GL, not stored separately. This is what makes our budgets **transactionally consistent** with the books — Ramp's are eventually consistent.

### 4.3 Goal-driven spending campaigns (the net-new primitive)

A `Campaign` is a first-class object the agent operates on:

```
campaign(
  goal: "save €15,000 for CNC machine purchase",
  deadline: 2026-09-30,
  metric: "free_cash_balance",
  bounds: {
    may_lower: ["card_cap.marketing", "card_cap.travel"],
    max_reduction_pct: 30,
    may_not_touch: ["envelope.salary", "envelope.food.salaried"],
    may_propose: ["surplus_sweep_to_interest", "vendor_discount_request"]
  },
  checkpoints: ["weekly_summary", "before_executing_any_card_cap_change"]
)
```

**Closest precedents:** Ramp Budgets (auto-tracks but human authors rules), Brex Spend Controls (compliance-oriented), Mercury Treasury (savings sweeps only). **Nobody actually delivers goal-driven dynamic budget reshaping.** This is genuinely net-new in Europe.

The runtime loop: every n hours the planner re-runs cash forecast vs. trajectory; if the goal slips, proposes adjustments at the next checkpoint. Inside `bounds` the agent acts; outside, it asks. Every campaign action writes to the `decision_trace` the same way every booking does.

### 4.4 Agentic reports — DD pack, board pack, management commentary (the awe-moment for investors)

**Today's market:** Big 4 + boutique transaction-services firms run middle-market financial DD for **€50–150K and 10–20 business days**. 85% of QoE reports adjust the seller's number. Aleph/Mosaic/Cube are FP&A surfaces, not connected to a live GL with a decision trace.

**Our agent's plausible delivery:**

1. **Planner** reads a "DD pack" template, breaks into ~25 sub-reports (EBITDA bridge, one-time/owner add-backs, cohort retention, customer concentration, working-capital normalization, debt-like items, cash proof, KPI deck).
2. **Sub-agents** each call SQL tools against the GL + a contracts-RAG against the document store.
3. **Writer** drafts narrative; every number cites back to the journal entries that produced it (decision-trace native).
4. **Human reviews** at three checkpoints: scope, draft, final.
5. **Output:** PDF + interactive web pack where every cell drills to source. Time: hours not weeks.

The same scaffolding, with a different template, produces the monthly board pack (P&L vs budget vs forecast, 13-week cash, AR/AP aging, top variances with commentary, KPIs, risk register). One framework, many outputs.

**Why this matters for the pitch:** it converts the product from "better accounting" to "automation of work an SME today literally pays a Big 4 firm to do." That's a different valuation conversation.

---

## 5. Infrastructure decisions to make in the next 6 hours

These three decisions are load-bearing for everything above. Get them wrong and the pitch collapses under any judge question about the agentic claims.

### Decision 1 — Adopt an agent runtime now (LangGraph or equivalent), not next sprint

Every demo moment in the pitch — cash sweep, campaign reshaping budgets, DD pack generator, free-text policy enforcement — requires:

- A **planner** (decomposes goal → sequence of tool calls)
- A **tool registry** (Swan SDK, GL writer, e-invoice emitter, HubSpot, Slack, Anthropic billing API, vendor-discount API)
- A **checkpoint graph** with human approval gates at risk-graded moments
- **Persistent agent memory** of company state and ongoing campaigns

**Pick LangGraph** (most mature, good HITL primitives, persistent state graph) **or** the Anthropic SDK with explicit state graph wrappers. **Decide today, freeze the interface today.** Don't ship a chat loop and call it agentic.

### Decision 2 — Make `decision_trace` a first-class table joined to every `journal_line`, not a JSON sidecar

Schema:

```sql
CREATE TABLE decision_traces (
  id              INTEGER PRIMARY KEY,
  line_id         INTEGER REFERENCES journal_lines(id),
  source          TEXT NOT NULL,        -- 'webhook' | 'agent' | 'rule' | 'human'
  agent_run_id    INTEGER,              -- nullable
  model           TEXT,                 -- nullable
  prompt_hash     TEXT,                 -- nullable
  alternatives    JSONB,                -- model's other candidates with scores
  rule_id         INTEGER,              -- nullable
  confidence      REAL,
  approver_id     INTEGER,              -- nullable
  approved_at     TIMESTAMP,            -- nullable
  parent_event_id INTEGER,              -- swan event id or campaign id
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Every agent write goes through a single `propose → checkpoint → commit` function so the trace is **impossible to forget**. This is the moat *and* the EU AI Act story. Building it later means rewriting the GL.

### Decision 3 — Commit to Swan webhooks as the primary ingress, not bank-feed polling

Pennylane lives on polled aggregators; that's why their ledger is sub-day not sub-second. The 5-second loop demo only works if a Swan `transaction.created` webhook drives the entire chain (classify → post → decrement budget envelope → update P&L → push notification to employee phone).

**Wire one webhook end-to-end today.** Everything else (campaigns, agents, DD reports) layers on the same primitive.

---

## 6. The architecture, restated as an agentic system

Three layers, kept separate (from the architecture doc), plus three new horizontal capabilities that turn a passive ledger into a brain:

```
┌────────────────────────────────────────────────────────────────────┐
│  AGENT RUNTIME — planner, tool registry, checkpoint graph, memory  │
│  (LangGraph-style; HITL only at risk-graded checkpoints)           │
└────────────────────────────────────────────────────────────────────┘
        │                        │                       │
        ▼                        ▼                       ▼
 ┌─────────────┐          ┌─────────────┐         ┌─────────────┐
 │ Campaign    │          │ Reports     │         │ Policy &    │
 │ engine      │          │ agents      │         │ Treasury    │
 │ (goal-driven│          │ (DD, board, │         │ agents      │
 │  budgets)   │          │  monthly)   │         │ (sweep, FX) │
 └─────────────┘          └─────────────┘         └─────────────┘
        │                        │                       │
        └─────────┬──────────────┴───────────────────────┘
                  ▼
   ┌──────────────────────────────────────────────────────────┐
   │  DECISION TRACE — first-class table joined to every line │
   │  Every agent action propose→checkpoint→commit through it │
   └──────────────────────────────────────────────────────────┘
                  │
                  ▼
   ┌──────────────────────────────────────────────────────────┐
   │  GL (double-entry)  ←  Budgets (Domain F)  ←  Entities   │
   │       ↑                                          ↑       │
   │  Bank mirror (Swan webhooks)            Documents (PDF)  │
   └──────────────────────────────────────────────────────────┘
```

Read top-to-bottom: a goal becomes a campaign, the campaign asks the planner to act, the planner calls tools, every tool call writes a decision trace, the trace is joined to a journal line, the journal line debits/credits both the GL and a budget envelope, the envelope movement is visible on the employee phone within seconds.

That is what "the brain for all financial things in a small-to-medium-sized company" actually looks like as an architecture.

---

## 7. The pitch line

> "Pennylane gave the accountant a co-pilot. We're giving the SME founder an autonomous CFO. Same Swan rails. Sub-second instead of sub-day. Every AI decision auditable down to the prompt. And when the founder sets a goal — *save €15K for that machine, cut AI spend 20%, raise the German team's sales budget* — the system reshapes itself to hit it. The DD pack a Big 4 firm charges €80K for, our agent produces in two hours, and every number drills back to a journal line. That's not accounting software. That's the brain."

---

## Sources

- [Pennylane $4.25B valuation — SiliconANGLE](https://siliconangle.com/2026/01/20/accounting-software-startup-pennylane-raises-204m-reported-4-25b-valuation/)
- [Pennylane tarifs (FR)](https://www.pennylane.com/fr/tarifs/) · [GetApp pricing](https://www.getapp.com/finance-accounting-software/a/pennylane/)
- [Pennylane Compte Pro (Swan-backed)](https://www.pennylane.com/fr/compte-pro) · [Swan × Pennylane](https://www.swan.io/blog-posts/pennylane-wallet)
- [Pennylane PA (ex-PDP) help](https://help.pennylane.com/fr/articles/305925-designer-pennylane-comme-plateforme-agreee-pour-vos-clients)
- [Pennylane G2 reviews](https://www.g2.com/products/pennylane/reviews) · [Trustpilot](https://www.trustpilot.com/review/pennylane.com) · [StatusGator outages](https://statusgator.com/services/pennylane)
- [Pennylane × Joiin consolidation](https://www.joiin.co/pennylane/)
- [Ramp Agents launch](https://ramp.com/blog/ramp-agents-announcement) · [Ramp Budgets](https://ramp.com/blog/ramp-budgets-launch)
- [Brex Agent Mesh — VentureBeat](https://venturebeat.com/orchestration/brex-bets-on-less-orchestration-as-it-builds-an-agent-mesh-for-autonomous) · [Brex × OpenAI](https://www.brex.com/journal/press/brex-helps-power-open-ai-global-spend-and-financial-operations) · [Capital One acquires Brex $5.15B](https://markets.financialcontent.com/stocks/article/marketminute-2026-3-24-capital-ones-515-billion-brex-acquisition-a-new-era-of-ai-driven-b2b-dominance)
- [Digits AI Agents launch](https://www.globenewswire.com/news-release/2025/06/23/3103524/0/en/Digits-Launches-First-AI-Agents-for-Accounting-Workflows-Built-on-Digits-Autonomous-General-Ledger.html) · [Accounting Today on Digits 95%](https://www.accountingtoday.com/news/digits-says-its-new-ai-agents-can-automate-95-of-bookkeeping-tasks)
- [GTreasury — Agentic AI in Treasury](https://www.gtreasury.com/posts/agentic-ai-treasury-management)
- [Aleph FP&A](https://www.getaleph.com/) · [Anders QoE guide](https://anderscpa.com/learn/blog/quality-of-earnings-report-analysis-due-diligence-guide/) · [Eton — DD timing/cost](https://etonvs.com/transaction-valuation-advisory/financial-due-diligence/)
- [Permit.io — HITL for AI agents](https://www.permit.io/blog/human-in-the-loop-for-ai-agents-best-practices-frameworks-use-cases-and-demo)
- [France 2026 mandate — Avalara](https://www.avalara.com/blog/en/europe/2025/09/france-e-invoicing-e-reporting-mandate-2026-2027.html)

---

*Written 2026-04-25. Revise as the agent runtime is chosen and the slice firms up.*
