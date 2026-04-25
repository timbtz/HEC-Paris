# PRD — Agnes: The Autonomous CFO

> **Status:** Product PRD for the customer-facing surface of Agnes, layered on top of the Phase 1 + Phase 2 + Phase 3 backend already shipped (see `RealMetaPRD.md` for the engine spec, `CLAUDE.md` for the live state — note that the reporting layer is already in place: SQL-only `/reports/*`, plus `period_close` / `vat_return` / `year_end_close` agentic pipelines).
> **Scope:** Onboarding, daily CFO experience, employee-benefit integrations, living-wiki rule layer, campaign-driven balance sheet, **board-pack and cash-forecast and audit-pack reports on top of the existing reporting layer**. Software-centric, not infrastructural.
> **This PRD is Phase 4.** Phases 1–3 are shipped.
> **Last updated:** 2026-04-25.
> **Companion docs:** `Orchestration/pitch/pitch.md`, `Orchestration/pitch/competitive_teardown.md`, `Orchestration/research/llm-wiki.md`.

---

## 1. Executive Summary

Agnes is the **last piece of finance software a 50–250 person European scale-up CFO will need**. It fuses three things that no competitor combines in one product: a live double-entry ledger fed sub-5-second by Swan webhooks, per-employee budget envelopes that are written in the *same database transaction* as the journal entry, and a decision-trace contract joined to every line on every report — making the EU AI Act provenance regime a free side-effect of how the product works.

On top of that engine, Agnes ships a CFO-grade product surface: a guided onboarding that captures the firm's chart of accounts, fiscal posture, VAT regime, banking provider, and benefit-in-kind elections; native integrations with the six employee-benefit systems an EU SME actually uses (JobRad, JobBicycle, Urban Sports Club, EGYM Wellpass, Finn, company dinners) with per-employee caps and statutory accounting treatment baked in; a **Living Rule Wiki** that lets the CFO and the auditor co-edit markdown documents which the reasoning agents consume verbatim as prompt input — so the CFO writes "company dinners over €250 need attendee names" once, and every future agent decision applies it; agentic reports for monthly close, board pack, DD pack, cash-flow forecast, and VAT return; and a real-time visualization of the agent pipeline executing in the browser so the CFO can audit *which agent decided what, with which prompt, citing which rule, costing how much*, on every line of every report.

**MVP goal:** A CFO of a 100-person Franco-German scale-up can self-onboard in <30 minutes, connect Swan + their HRIS, ratify the auto-generated rule wiki, and within 24 hours have (a) a live ledger with all six benefit integrations classified correctly, (b) per-employee real-time envelopes for each benefit, (c) a one-click monthly board pack with full decision-trace drill-down, and (d) a campaign builder for goal-driven cash actions like *"save €15K for the CNC machine by Q3."*

---

## 2. Mission

**Mission statement.** *Turn the CFO's role from doing the bookkeeping to approving the agent's plan once and reading the result — without ever losing the audit trail that makes that approval defensible to a regulator.*

**Core principles.**

1. **System of action, not system of record.** Agents *execute*. The human approves at the threshold gate, not on every line. (Hg Catalyst's framing — see `hg_catalyst_briefing.md`.)
2. **Provenance is a join key, not a side log.** Every figure on every report traces to a `(decision, cost, employee)` triple. Click any number → see the agent, model, prompt-hash, rule citation, source document. No JSON sidecar. No "agents cite their work" hand-wave.
3. **The rule wiki is the prompt.** Agents read living markdown documents that the CFO and the auditor co-author. When the auditor flags a misbooking in May, the wiki page updates and every future booking inherits the correction. Karpathy's compounding wiki, applied to accounting policy.
4. **Budget and ledger are one transaction.** Eventually-consistent budgets are theatre. Agnes writes the journal entry and the envelope decrement under the same `BEGIN IMMEDIATE`. If one fails, both fail.
5. **The CFO is the user, the accountant is downstream.** Pennylane optimizes for the expert-comptable. Agnes optimizes for the founder-CFO who has to read the report on Sunday before the board call on Monday.

---

## 3. Target Users

### Primary persona — *"Marie, founder-CFO of a 120-person Franco-German B2B SaaS scale-up"*

| | |
|---|---|
| Headcount | 120 (60 FR, 50 DE, 10 remote EU) |
| Revenue | €18M ARR, growing 80% YoY |
| Bank | Swan (primary), Qonto (legacy ops account) |
| Stack today | Pennylane (FR books), DATEV via Steuerberater (DE books), Spendesk for cards, Notion for budgets, Excel for board pack, expert-comptable for monthly close |
| Pain hours | ~12 hrs/week on finance ops; +60 hrs in any fundraising or audit window |
| Tech comfort | High — read API docs, write SQL, can review a YAML pipeline diff |
| AI comfort | Uses Claude / ChatGPT daily; deeply uneasy about non-auditable AI in the books |

### Secondary persona — *"Jean, expert-comptable / Steuerberater partner"*

The accountant who reviews Marie's books and signs the statutory filings. Wants: a clean export to PCG / SKR04, the ability to flag a misbooking and have the rule wiki auto-update, no surprises at year-end. Will block adoption if the agent layer is opaque.

### Secondary persona — *"Paul, employee using JobRad + Urban Sports Club + dinners"*

Doesn't see Agnes directly. Sees: a Slack message *"Your €48 USC subscription used 96% of this month's wellness envelope"* and a Telegram approval prompt for the €280 client dinner with one tap. Trusts the system because the receipt was OCR'd in 4 seconds.

### Pain points (grounded in survey research, see Appendix A)

- **Time tax.** ~80% of finance-team time on transactional work (EY 2024); 75% of accounting tasks automatable but undone (Spendesk State of AI 2025).
- **Slow close.** Median 6.4 days; only 18% close in ≤3 days (Ledge / APQC 2025).
- **Spend-control gap.** Only 10% of SMEs run automated approvals; 28% lack timely spend visibility (Spendesk 2025; Ramp citing IFP).
- **Audit prep.** German GoBD = 8-year e-invoice retention; French PCG = chronological journal in French; both require artefacts most SMEs assemble manually.
- **Shadow IT.** 275 apps/org, $4,830/employee (Zylo 2025), 51% of expensed SaaS miscategorised — invisible to the CFO until year-end.
- **Benefit accounting.** JobRad 0.25%/1% BIK rules, USC's €50 Sachbezugsfreigrenze cap shared across all non-cash perks, FR €21.10 URSSAF lunch cap — every one of these is a footgun that today gets handled by emailing the expert-comptable.
- **EU AI Act.** Aug 2026 enforcement window. Penalties to €15M / 3% turnover for high-risk non-compliance. Provenance for every automated decision must be storable on demand.

---

## 4. MVP Scope

### ✅ In scope (MVP — Phase 4 on top of shipped Phases 1–3)

**Core functionality**
- ✅ Guided 30-minute CFO onboarding wizard (see §7.1)
- ✅ Live ledger from Swan webhook → posted JE in <5s (already shipped, Phase 2)
- ✅ Per-employee budget envelopes transactionally consistent with the GL (already shipped, Phase 2)
- ✅ Period close, VAT return, year-end close pipelines (already shipped, Phase 3 — extend to cite wiki revisions)
- ✅ Six employee-benefit integrations: JobRad, JobBicycle, Urban Sports Club, EGYM Wellpass, Finn, company dinners (see §7.2)
- ✅ Per-employee per-benefit caps with statutory BIK accounting (DE Sachbezug €50/mo, JobRad 0.25%/1%, FR €21.10 lunch, etc.)
- ✅ Living Rule Wiki — markdown documents the CFO edits, agents read as prompt input (see §7.3)
- ✅ DAG executor visualization in the frontend — every pipeline run shows nodes, agent decisions, costs, prompt-hash, citations (see §7.4)
- ✅ Three NEW agentic reports on top of the shipped reporting layer: board pack, cash-flow forecast, audit pack (see §7.5)
- ✅ Campaigns: goal-driven cash/budget reshaping within CFO-approved bounds (see §7.6)
- ✅ Audit drill-down: click any number on any report → decision-trace lineage in ≤2 clicks
- ✅ FR and DE jurisdiction support (PCG + SKR04 chart of accounts, both VAT regimes)

**Technical**
- ✅ Three-DB SQLite (accounting / orchestration / audit) with single-writer locks — shipped
- ✅ FastAPI + SSE backend — shipped
- ✅ Vite + React 18 + TypeScript + Tailwind v4 + Zustand frontend — shipped
- ✅ YAML pipeline DSL for new event types — shipped
- ✅ Anthropic runner with `submit_*` tool forcing — shipped

**Integration**
- ✅ Swan (banking, OAuth + GraphQL + webhooks) — shipped
- ✅ Stripe (external webhook, HMAC verifier registry) — shipped
- ✅ Document upload (PDF + OCR via document_extractor agent) — shipped
- ✅ JobRad / JobBicycle / USC / Wellpass / Finn / dinner-receipt connectors (new for MVP)
- ✅ Email / Slack / Telegram approval gateway for threshold-crossing decisions

**Deployment**
- ✅ Single-tenant Docker + uvicorn `--workers 1`, EU-region cloud (Scaleway / OVHcloud)
- ✅ All data inside the customer perimeter (no third-party LLM training, EU-only model endpoints)

### ❌ Out of scope (deferred)

- ❌ Multi-currency ledger (single-currency MVP — see RealMetaPRD §15 caveats)
- ❌ Multi-entity consolidation (one legal entity per Agnes tenant in MVP)
- ❌ Multi-worker uvicorn (per-DB asyncio.Lock doesn't coordinate cross-process)
- ❌ Native payroll computation (we ingest payslip artefacts; Lohnsteuer/URSSAF computation stays with PayFit / DATEV / Personio)
- ❌ Direct e-invoicing PDP submission (we file-format-validate; submission goes via the customer's chosen PDP partner)
- ❌ Italian / Spanish / Polish / UK localisation (FR + DE only in MVP)
- ❌ Mobile-native apps (web is responsive; mobile push via Slack / Telegram bots)
- ❌ Multi-tenant SaaS hardening (customer-isolated single-tenant deploys for MVP)
- ❌ DD-pack agent for PE/VC use cases (we ship the founder-side fundraising pack only)
- ❌ Direct ERP migration tooling (CSV import + manual opening balance JE in MVP)

---

## 5. User Stories

### Primary stories

**US-1 — Onboard in under 30 minutes.**
*As a* founder-CFO,
*I want to* point Agnes at my Swan account, drop in last year's TB and chart of accounts, and answer ~15 questions in a wizard,
*so that* by the end of the wizard my live ledger is running and my Living Rule Wiki is auto-drafted from my answers.

> *Example:* Marie connects her Swan client_id; Agnes pulls the last 90 days of transactions; the wizard offers FR PCG vs DE SKR04 (Marie picks both — FR primary), asks about VAT regime (réel normal monthly), prompts for per-employee budget defaults (€50 USC, €1,200 JobRad cap, €21.10/meal), confirms accrual basis, and generates 12 wiki pages she ratifies in one pass.

**US-2 — Classify a JobRad employee without thinking about §3 Nr. 37 EStG.**
*As a* CFO,
*I want to* upload a JobRad invoice or receive its webhook and have Agnes book it correctly as a 36-month operating lease with the 0.25% gross-list-price BIK on Paul's payslip and the input VAT recovered,
*so that* I never need to ask my Steuerberater whether the 0.25% rule applies again.

> *Example:* Paul's €99/month JobRad lease lands. Agnes posts the lease instalment, decrements Paul's "company-bike" envelope, queues a Sachbezug payslip line for €X (0.25% × bike list price), and writes a decision-trace citing the *DE Bewirtung & BIK rules* wiki page. Marie sees one row in the review queue with the citation; she clicks approve.

**US-3 — Audit "why is this number on my P&L?" in two clicks.**
*As a* CFO under auditor scrutiny,
*I want to* click any line on the P&L → see every contributing journal line → click any line → see the agent, model, prompt-hash, rule citation, and source document that produced it,
*so that* I can answer the auditor in real time instead of running CSV exports.

> *Example:* Auditor: *"Why is €18,400 in 6257 - Réceptions in March?"* Marie clicks the cell → 14 dinners listed → clicks the largest (€2,140 Bistrot Volnay) → sees: `gl_account_classifier_agent` decision, prompt-hash `a3f8…`, citation to `wiki/policies/fr-bewirtung.md` rev 7, attached PDF with attendees, URSSAF €21.10 cap applied per head, agent cost €0.0034. Done.

**US-4 — See the agent thinking.**
*As a* CFO who doesn't trust black-box AI in the books,
*I want to* watch each pipeline run as a live DAG in the frontend — nodes lighting up, costs accruing, decisions resolving — and pause / step / inspect any node,
*so that* I can build trust in the system before I let it auto-post anything over €500.

> *Example:* A Swan webhook fires for a €4,200 transfer. Marie sees the `transaction_booked` pipeline render in the right pane: `swan_query → counterparty_resolver (cache hit) → gl_account_classifier (agent, $0.002) → confidence_gate (0.91) → invariant_checker → gl_poster → budget_envelope.decrement → publish_event_dashboard` over ~3 seconds, every node clickable.

**US-5 — Edit the rule wiki, change every future decision.**
*As a* CFO whose auditor flagged that meals over €250 must list attendees,
*I want to* edit one markdown page in Agnes — `wiki/policies/fr-bewirtung.md` — to say "≥€250 → require attendee names",
*so that* every future dinner over €250 routes through the document_extractor agent for attendee extraction, and any without attendees lands in review queue, with the wiki page cited as the reason.

> *Example:* Marie pastes the auditor's email into the wiki page edit box. Agnes proposes a 3-line markdown diff with frontmatter `applies_to: dinners | threshold_eur: 250`. Marie commits. Next day, three €280-€340 dinners appear in review queue with the citation; Marie forwards them to the team.

**US-6 — Run a goal-driven cash campaign.**
*As a* CFO who needs to free €15K for a CNC machine by Q3,
*I want to* tell Agnes the goal in plain English; have the agent propose a campaign that tightens specific envelopes (USC →€40, dinners →€18/head, Finn pause for 1 month) within bounds I've pre-approved; and execute the plan on commit,
*so that* I run a savings campaign without spending an afternoon in Excel.

> *Example:* Marie types *"Save €15K by 2026-09-30 — don't touch JobRad, don't pause hiring."* Agnes proposes: USC −€10/employee (-€1,200/mo across 120), dinners cap −€3/head (-€2,800/mo), Finn pause for the founders' two cars 30 days (-€2,400), trim Anthropic budget on the marketing agent by 20% (-€800/mo). Total -€7,200/mo over 5 months = €36K. Marie approves the conservative half. The campaign rewrites envelope ceilings in `accounting.db` under the same `write_tx`; every affected employee gets a Slack message; the campaign is auditable as one decision.

**US-7 — Generate the board pack on Sunday for Monday.**
*As a* CFO heading into a board call,
*I want to* click "Generate March board pack",
*so that* in 4 minutes I get a 12-page Markdown + PDF deck with revenue, COGS, OpEx, cash, runway, headcount cost, AI-credit cost-per-employee, top 10 P&L variances vs. plan with agent-written narrative, every figure click-through to journal lines.

> *Example:* The agentic report pipeline runs `compute_trial_balance → period_lock_check → variance_analysis → narrative_generator → pdf_render`, costs €0.18 in tokens, every paragraph ends with a citation footnote linking to the relevant `journal_entries.id`. Slides export to Marp.

**US-8 — Per-employee AI-API spend, by team and feature.**
*As a* CFO with €120K/year of Anthropic + OpenAI bills,
*I want to* drill down by employee, by API key, by pipeline, by feature,
*so that* I can attribute the marketing agent's €1,800/mo to Paul's pricing-engine project and the research agent's €600/mo to Marie's strategy work, defensibly, on every payroll cycle.

> *Example:* The "AI Spend" tab shows: Anthropic €4,200 this month → Paul (pricing-engine, key `sk-ant-…7f`) €2,150 → Claude Sonnet 4.5 €1,920 + Opus 4.7 €230. Click → 17,400 calls, average $0.0124, p95 latency 3.1s, top 5 prompts.

### Technical user stories

**TS-1 — Add a new benefit provider in 2 hours.**
*As an* implementer onboarding a new customer who uses Swile (FR meal voucher) instead of dinners,
*I want to* drop a `swile.yaml` pipeline + a `swile_extractor` tool + a routing.yaml line,
*so that* no executor surgery is required and the new benefit inherits the BIK rules from the wiki.

**TS-2 — Replay any month against new wiki rules.**
*As an* auditor finding a systematic misclassification in February,
*I want to* update the wiki page, hit "Replay February" on a sandbox tenant,
*so that* I see the corrected decisions diff against the original posts before authorising the production replay.

---

## 6. Core Architecture & Patterns

> **The engine is shipped.** Phase 1 (metalayer foundation) and Phase 2 (Swan + document + frontend) cover the executor, registries, audit spine, dashboard SSE, and live frontend. This PRD layers a **product surface** on top.

### High-level architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Frontend (Vite + React 18 + Tailwind v4 + Zustand + Motion)         │
│  • Onboarding Wizard  • Living Rule Wiki editor (Obsidian-like)      │
│  • Live ledger + envelopes  • DAG run visualizer (per pipeline run)  │
│  • Agentic reports  • Campaign builder  • AI-spend drilldown         │
└──────────────────────────────────────────────────────────────────────┘
           │ SSE (per-run + dashboard)        │ REST (typed)
           ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FastAPI backend (uvicorn --workers 1)                               │
│  • /onboarding/*  • /wiki/*  • /benefits/{provider}/webhook          │
│  • /campaigns/*  • /reports/*  • existing /swan, /documents, /runs   │
└──────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  AgnesContext + four registries (tools / agents / runners / cond.)   │
│  ↳ Pipelines (YAML, Kahn DAG, fail-fast, cross-run cache)            │
│  ↳ propose → checkpoint → commit (audit triple)                      │
└──────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  3 SQLite databases — single-writer per-DB asyncio.Lock              │
│  accounting.db   orchestration.db (run state, cache, wiki revs)      │
│                  audit.db (decisions, costs, employees)              │
└──────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  External: Swan (webhook + GraphQL), Stripe, Anthropic API,          │
│  6 benefit providers (JobRad, JobBicycle, USC, Wellpass, Finn,       │
│  dinners-via-OCR), Slack/Telegram (approvals), email                 │
└──────────────────────────────────────────────────────────────────────┘
```

### New directory structure (added this MVP, on top of the shipped tree)

```
backend/
  api/
    onboarding.py              # POST /onboarding/{step}, GET /onboarding/state
    wiki.py                    # CRUD on living-wiki markdown pages + revs
    benefits.py                # POST /benefits/{provider}/webhook
    campaigns.py               # POST /campaigns/draft, /preview, /commit
    reports.py                 # POST /reports/board_pack, /cash_forecast,
                               #       /audit_pack, /vat_return, /close
  orchestration/
    wiki/
      loader.py                # read wiki/*.md → string for prompt input
      writer.py                # commit wiki edits w/ rev history in orchestration.db
      schema.py                # frontmatter spec (applies_to, threshold_eur, …)
    pipelines/
      benefit_jobrad.yaml
      benefit_jobbicycle.yaml
      benefit_usc.yaml
      benefit_wellpass.yaml
      benefit_finn.yaml
      benefit_dinner.yaml
      monthly_close.yaml
      cash_forecast.yaml
      board_pack.yaml
      audit_pack.yaml
      vat_return.yaml
      campaign_execute.yaml
    tools/
      benefit_classifier.py    # one tool, six provider configs
      bik_calculator.py        # JobRad 0.25/1%, Sachbezug €50, FR €21.10, …
      payroll_writer.py        # write payslip artefact rows for export
      report_renderer.py       # markdown + PDF (via WeasyPrint)
      campaign_planner.py      # goal → envelope-delta plan, bounded by CFO rules
      wiki_reader.py           # tool that reasoning agents call to read wiki/*.md
    agents/
      benefit_classifier_agent.py
      narrative_generator.py   # board-pack / variance commentary
      campaign_proposer.py     # plain-English goal → structured plan
  store/
    migrations/
      accounting/0009_benefit_providers.py
      accounting/0010_employee_benefit_caps.py
      accounting/0011_campaigns.py
      orchestration/0002_wiki_pages.py
      orchestration/0003_wiki_revisions.py
      audit/0004_onboarding_answers.py

frontend/
  src/
    routes/
      Onboarding.tsx           # 8-step wizard
      Wiki.tsx                 # markdown editor + page tree + diff view
      Reports.tsx              # board pack / close / cash / VAT / audit
      Campaigns.tsx            # goal entry + plan preview + commit
      AISpend.tsx              # per-employee × per-key × per-feature
    components/
      DagViewer.tsx             # live pipeline DAG (nodes + costs + traces)
      WikiEditor.tsx            # CodeMirror-based markdown w/ frontmatter
      DecisionTracePanel.tsx    # right-rail drilldown from any number
      EnvelopeGrid.tsx          # employee × benefit matrix
      CampaignPlanCard.tsx
    hooks/useDagRun.ts          # per-run SSE + node-level state
    store/wiki.ts               # Zustand: open page, dirty state, rev tree

wiki/                           # the customer's living rule corpus (versioned in orchestration.db)
  index.md                      # auto-maintained (LLM-Wiki pattern)
  log.md                        # append-only ingest/edit log
  policies/
    chart-of-accounts.md
    vat-regime.md
    expense-thresholds.md
    fr-bewirtung.md
    de-bewirtung-bik.md
    de-sachbezug-50.md
    de-jobrad-bik.md
    de-finn-1pct-rule.md
    benefits-caps.md
  employees/
    {employee_id}.md            # per-employee BIK elections, envelopes, exceptions
  counterparties/               # one page per recurring vendor, agent-maintained
```

### Key design patterns

1. **Living Rule Wiki as prompt input.** Reasoning agents (`benefit_classifier_agent`, `gl_account_classifier_agent`, `narrative_generator`, `campaign_proposer`) call the `wiki_reader` tool to fetch the relevant policy page(s) and inject them verbatim into the system prompt. Wiki edits write a new revision to `orchestration.db`; the prompt-hash changes; the audit trace cites both `wiki_page_id` and `revision_id`. This is Karpathy's compounding-wiki pattern, applied to accounting policy.
2. **Onboarding answers → wiki seed.** The wizard collects ~15 structured answers, then runs an `onboarding_to_wiki` pipeline that drafts 12 markdown pages from those answers. The CFO ratifies in one pass. From day one, the wiki is non-empty and citation-ready.
3. **Single-chokepoint posting.** All journal-entry writes go through `gl_poster.post`. Benefit pipelines write the JE *and* the BIK payroll-line *and* the envelope decrement under one `write_tx`. CI grep enforces.
4. **DAG visualization = `event_bus` projection.** The frontend already subscribes to per-run SSE; we add structured `node.started`, `node.completed`, `agent.decision`, `cost.recorded` events keyed to a deterministic node-id so React can paint a live graph.
5. **Campaign execution = bounded delta plan + dry-run preview + atomic commit.** The campaign agent emits a structured `EnvelopeDelta[]` object; the frontend previews the rewritten envelopes and impacted employees; commit applies all deltas in one `write_tx` and publishes a single `campaign.committed` event.
6. **No floats anywhere on a money path.** Already enforced by CI grep audit.

> **DEFERRED RESEARCH (for the implementing agent):**
> - Map the wiki-loader/wiki-reader contract precisely against `backend/orchestration/agents/*.py` — which agents need wiki access, which prompt slot the markdown is injected into, and how `prompt_hash.py` should incorporate `(wiki_page_id, revision_id)` into its hash to keep cache invalidation correct.
> - Decide whether wiki revisions live in `orchestration.db` (current proposal — they're orchestration state) or a new `wiki.db` (cleaner separation but breaks the 3-DB constant from RealMetaPRD).
> - Audit how the existing `cache.py` cross-run node cache should treat wiki revisions: a wiki edit must invalidate downstream cache hits but not unrelated cache lines.

---

## 7. Tools / Features

### 7.1 Onboarding wizard (8 steps, ~30 minutes)

A guided flow that captures everything the rules engine needs to be safe on day one. **Each answer becomes a frontmatter field on a wiki page.** The CFO ratifies the auto-drafted wiki at the end.

| # | Step | What we capture | Wiki page produced |
|---|---|---|---|
| 1 | Identity & jurisdiction | Legal name, SIREN/HRB, primary country (FR/DE), other operating countries | `wiki/policies/identity.md` |
| 2 | Chart of accounts | FR PCG (default) / DE SKR03 / DE SKR04, plus optional CSV import | `wiki/policies/chart-of-accounts.md` |
| 3 | Fiscal posture | Fiscal year, accrual vs. cash basis (default accrual; cash only for very small entities), fiscal year-end date | `wiki/policies/fiscal-posture.md` |
| 4 | VAT regime | FR: réel normal monthly / réel simplifié quarterly / franchise; DE: monthly UStVA (>€9k) / quarterly (€2-9k) / annual; IOSS election if applicable | `wiki/policies/vat-regime.md` |
| 5 | Banking & e-invoicing | Swan (primary), other banks via Bridge/Tink (later), French PDP partner, German XRechnung readiness | `wiki/policies/banking-einvoicing.md` |
| 6 | Per-employee budget defaults | Default monthly cap per benefit (USC, Wellpass, JobRad bike-list-price ceiling, Finn vehicle category, dinners per-meal cap) | `wiki/policies/benefits-caps.md` |
| 7 | Expense policy thresholds | Receipt-required threshold (default €25), manager-approval threshold (default €500), CFO-approval threshold (default €2,500), auto-post confidence floor (default 0.85) | `wiki/policies/expense-thresholds.md` |
| 8 | Statutory partners | Expert-comptable / Steuerberater contact, audit firm (mandatory if ≥2 of: €5M balance, €10M turnover, 50 FTE in FR), payroll provider (PayFit / DATEV / Personio / Silae) | `wiki/policies/partners.md` |

**Onboarding outputs:**
- 8 frontmatter-rich markdown pages drafted by the `onboarding_to_wiki` pipeline.
- `chart_of_accounts` table seeded from the user's choice (PCG / SKR03 / SKR04).
- One opening-balance journal entry from the imported TB (manual sign-off).
- Swan OAuth credentials stored encrypted; first webhook tested live in step 5.
- One "fixture pipeline run" replays a single past Swan transaction so the CFO sees the DAG visualizer and the rule-citation for the first time, before any production data flows.

> **DEFERRED RESEARCH:** The exact PCG / SKR03 / SKR04 seed CSVs to ship; the SIREN / HRB lookup endpoint for prefill; the regulatory wording on "≥2 of 3" audit thresholds for FR (commissaire aux comptes) post-loi PACTE. Implementer: confirm against ANC and Code de commerce R823-7.

### 7.2 The six employee-benefit integrations

Each benefit ships as: a YAML pipeline + a provider-specific config + a wiki policy page + a per-employee envelope row. The `benefit_classifier_agent` reads the relevant wiki page as prompt input, writes the JE through `gl_poster.post`, decrements the envelope in the same `write_tx`, and queues a Sachbezug / payslip artefact row for export to the customer's payroll provider.

| Benefit | Trigger | Accounting treatment | Tax treatment | Per-employee cap |
|---|---|---|---|---|
| **JobRad** (DE) | Webhook from JobRad portal *or* invoice OCR | 36-month operating lease; instalments hit P&L | BIK at **0.25%** of gross list price/mo if provided in addition to salary, **1%** if salary conversion ([Lease-a-Bike](https://www.lease-a-bike.de/en/faq-employer/tax-matters)) | Bike list price ceiling (default €3,500); one bike per employee at a time |
| **JobBicycle** (DE) | Same as JobRad (alternate provider) | Same | Same | Same |
| **Urban Sports Club** (DE) | Monthly USC corporate invoice CSV + per-member roster | Operating expense; **does not** qualify under §3 Nr. 34 EStG €600/yr health bucket (not a certified BGF programme) | Sachbezug under **€50/month** Sachbezugsfreigrenze — **shared across all non-cash perks** ([USC tax FAQ](https://usccorporate.zendesk.com/hc/en-us/articles/24050394441362-Can-we-take-advantage-of-the-tax-free-benefit-in-kind)) | Default €50/mo; envelope rolls up shared cap with Wellpass + vouchers |
| **EGYM Wellpass** (DE) | Same as USC (monthly invoice + roster) | Same | Same Sachbezug €50 cap, shared envelope | Default €50/mo, shared with USC |
| **Finn** (DE) | Subscription invoice (monthly) + employee assignment | Operating lease; subscription deductible; input VAT pro-rata business-use | **1% rule** on gross list price for private use, **0.25%** for EVs ≤€70k 2024–2030 + 0.03%/km/mo commute ([Carano](https://www.carano.de/en/blog/1-percent-rule/)) | Vehicle-category ceiling per employee (default: founders only); pause-able by campaign |
| **Company dinners** (FR + DE) | Receipt PDF upload (mobile) + extractor agent | FR PCG `6257 - Réceptions` (clients) or `6431 - Repas du personnel` (staff); DE SKR04 `6640 - Bewirtungskosten` | DE: 70% deductible, input VAT 100% recoverable if Bewirtungsbeleg names guests + reason; >€250 needs company address. FR: TVA recoverable if invoice in company name + attendees listed. URSSAF lunch cap €21.10/head 2025. DE restaurant VAT drops to 7% from 2026-01-01 | Per-meal cap (default €60/head clients, €21.10 staff FR / Sachbezug DE), monthly per-employee envelope (default €400) |

**Universal flow (per benefit):**

```
provider_event → external_payload_parser
              → counterparty_resolver (cache hit on next time)
              → benefit_classifier_agent  ← reads wiki/policies/{benefit}.md
              → bik_calculator             ← reads wiki/employees/{id}.md
              → confidence_gate
              → invariant_checker
              → gl_poster.post             ── write_tx ──┐
              → budget_envelope.decrement                │
              → payroll_writer (Sachbezug row)           │
              → publish_event_dashboard ──────────────── ┘
              ↘ if low-confidence or over-cap: review_queue.enqueue
```

> **DEFERRED RESEARCH:** Each provider's actual webhook contract (or absence thereof) — JobRad and Finn publish ~partner APIs; USC and Wellpass typically deliver monthly CSV invoices. Implementer: spend time on `Dev orchestration/swan/` style API references for each provider and codify under `backend/orchestration/integrations/{provider}/`.

### 7.3 The Living Rule Wiki

A markdown corpus owned by the CFO and the auditor, **read by the reasoning agents as prompt input**. Implements Karpathy's LLM-Wiki pattern (`Orchestration/research/llm-wiki.md`).

**Three layers (Karpathy's model):**

- **Raw sources** — auditor emails, regulatory PDFs, accountant notes (uploaded to `wiki/raw/`, immutable).
- **The wiki itself** — LLM-maintained markdown pages under `wiki/policies/`, `wiki/employees/`, `wiki/counterparties/` with frontmatter spec.
- **The schema** — `wiki/SCHEMA.md`, the conventions document the agents read first to know how the wiki is structured.

**Page frontmatter spec** (machine-readable for routing and agent injection):

```yaml
---
applies_to: [dinners, fr, bewirtung]      # routing tags
threshold_eur: 250                        # numeric guards
jurisdictions: [FR]
last_audited_by: jean.dupont@cabinet.fr
last_audited_at: 2026-04-12
revision: 7
agent_input_for: [gl_account_classifier_agent, document_extractor]
---
```

**Operations (matching the LLM-Wiki definition):**

- **Ingest.** Drop a regulatory PDF or paste an auditor email → an `ingest_to_wiki` pipeline reads it, drafts a markdown summary, updates relevant policy pages, appends to `log.md`, refreshes `index.md`.
- **Query.** Agents call the `wiki_reader` tool with a routing tag (e.g. `applies_to=dinners,fr`). The tool returns matching page bodies, which are injected verbatim into the agent's system prompt. The prompt-hash includes `(wiki_page_id, revision_id)` so cache invalidation is correct.
- **Lint.** A `wiki_lint` job runs nightly: contradictions between pages, stale citations (>180 days unaudited), orphan pages, missing frontmatter fields.

**Why this matters for the CFO:**
- The auditor flags an error in May → Marie edits `wiki/policies/fr-bewirtung.md` once → every June dinner is classified correctly with a citation back to that revision.
- The Steuerberater can be given write access to `wiki/policies/de-*` pages without touching the executor.
- Every prompt is cite-able. Every decision has a traceable rule.

> **DEFERRED RESEARCH:** Where in `backend/orchestration/agents/*.py` to inject the `wiki_reader` call, how to thread `(wiki_page_id, revision_id)` into `prompt_hash.py`, and whether to use `qmd` (Tobi Lütke's BM25+vector tool referenced in `llm-wiki.md`) or stay with frontmatter-tag routing for the MVP. Implementer: read `Orchestration/research/llm-wiki.md` end-to-end, then walk through `backend/orchestration/agents/gl_account_classifier_agent.py` line by line and propose the integration patch.

### 7.4 DAG executor visualization

The single most important UI surface for CFO trust. Every pipeline run is a live graph the CFO can watch, pause, and audit.

**What it shows per node:**
- Status: pending / running / completed / failed / cached / skipped (with color)
- Type: tool / agent / condition (with icon)
- Duration (ms)
- For agents: model, tokens (in/out), cost in micro-USD, prompt-hash, **wiki citations**, decision finish_reason
- For tools: inputs / outputs (truncated, expandable)
- For conditions: predicate name + result

**What it shows per run:**
- Total cost, total duration, success / fail / partial
- Decision-trace count (links to `audit.db`)
- Any review-queue rows enqueued
- Any envelope decrements made

**Interactions:**
- Click any node → right-rail panel with the agent's full prompt, model output, tool args/result, and the wiki page bodies that were injected
- "Replay this run with my new wiki rev" → opens a sandbox tenant
- "Why did this go to review?" → highlights the failing condition or invariant

**Built on:**
- The shipped per-run SSE stream (`GET /runs/{id}/stream`)
- New event shape: `{ type: 'node.event', node_id, kind: 'started'|'completed'|..., payload }`
- `DagViewer.tsx` uses `react-flow` for the graph; nodes painted from `executor.py`'s Kahn-layer build

> **DEFERRED RESEARCH:** The executor's current event emission contract — does it already publish per-node events on the run channel, or only run-level events? Implementer: read `backend/orchestration/executor.py` and `backend/api/runs.py` to confirm and propose any missing emissions.

### 7.5 Agentic reports

> **What's already shipped (Phase 3, see `CLAUDE.md`):**
> - **Six SQL-only `/reports/*` endpoints** (deterministic, no agent calls) — trial balance, P&L, balance sheet, etc.
> - **Three agentic pipelines:** `period_close.yaml`, `vat_return.yaml`, `year_end_close.yaml`.
> - **Reporting tools:** `period_aggregator`, `vat_calculator`, `retained_earnings_builder`, `report_renderer`.
> - **`anomaly_flag` agent** for variance / outlier detection.
> - **`accounting_periods` + `period_reports` tables**, period-lock enforcement on `gl_poster.post`.
> - **Reports frontend tab** with `ReportsTab + ReportTypeSelect + PeriodPicker + ReportTable`.
>
> **The implementing agent must read these before designing anything new.** Do not duplicate them.

This PRD adds **three new agentic reports** on top of the shipped reporting layer, plus a wiki-citation upgrade to the existing ones.

| Report | Status | Pipeline | New agent steps | Output formats |
|---|---|---|---|---|
| Period close | **shipped** — extend with wiki citations only | `period_close.yaml` (existing) | (extend `anomaly_flag` + add `accrual_proposer` step that reads wiki) | Existing + add Markdown + PDF |
| VAT return (CA3 / UStVA) | **shipped** | `vat_return.yaml` (existing) | None new — extend `vat_form_renderer` to emit pre-filled CA3 / UStVA exports | Existing + télédéclaration / ELSTER export |
| Year-end close | **shipped** | `year_end_close.yaml` (existing) | None | Existing |
| **Board pack** | **NEW** | `board_pack.yaml` | `narrative_generator` for variance commentary, `kpi_assembler` for headline metrics, **reads the wiki** for company-specific KPI definitions | Markdown + Marp slides + PDF |
| **Cash-flow forecast (12-week direct)** | **NEW** | `cash_forecast.yaml` | `cash_projector` (reads Swan future debits + customer DSO history + wiki AR-policy page) | Markdown + PDF + chart |
| **Audit pack** | **NEW** | `audit_pack.yaml` | None — bundles `journal_entries` + `decision_traces` + **wiki revisions** for the period | ZIP with CSVs + PDFs + a single markdown index |

> **DEFERRED RESEARCH:**
> - Walk the existing `period_close` / `vat_return` / `year_end_close` pipelines and identify the smallest possible patch that lets them cite wiki revisions without changing their public report-shape contract (the Reports tab consumes a stable JSON shape today).
> - Concrete column-by-column mapping of CA3 (FR) and UStVA (DE) form fields to GL account codes. The shipped `vat_return` produces internal totals; the export to the official forms is the new work. Implementer: pull DGFIP form CA3 and BMF UStVA spec; extend `vat_form_renderer` (don't duplicate `vat_calculator`).

### 7.6 Campaigns (goal-driven balance-sheet adjustment)

The CFO types a goal in plain English. The `campaign_proposer` agent, reading `wiki/policies/campaign-bounds.md` (which the CFO ratified in onboarding), emits a structured `EnvelopeDelta[]` plan within those bounds. The frontend previews the impact on every affected employee. On commit, all deltas write under one `write_tx`.

**Plain-English goal examples (and what the agent produces):**

> *"Save €15K by 2026-09-30 — don't touch JobRad, don't pause hiring."*
> → `[USC: 50→40, Wellpass: 50→40, dinners: 60→50, Finn: pause founders 30d, ai_credit_marketing_agent: 1500→1200]` over 5 months ≈ €36K headroom; CFO can tighten or loosen each row.

> *"Build a €40K Q3 reserve for the CNC machine."*
> → Same shape, different deltas, with a `reserve_account: 1068` ledger move that books a transfer to a designated reserve account each month.

> *"Prepare for fundraise in 60 days — clean up the burn line."*
> → Different agent (`fundraise_prep_proposer`), proposes which OpEx categories to flag, generates a draft narrative, populates the board-pack template.

**Hard bounds (set in onboarding, enforceable in code):**
- No envelope below the statutory minimum (BIK rules).
- No headcount changes (the campaign agent is not allowed to write to the `employees` table).
- Total monthly delta capped at X% of last month's OpEx (default 15%).

> **DEFERRED RESEARCH:** Whether the campaign commit should write a single composite journal entry, a series of envelope-only updates with no JE, or both. Implementer: reason through the double-entry implications of "moving €5K from dinners envelope to a CNC reserve" — does that warrant a transfer JE (debit reserve, credit retained earnings appropriation) or is it a pure budgeting metadata change?

### 7.7 AI-spend drilldown

Already half-shipped (the `audit.db` `agent_costs` schema records every call in micro-USD). MVP adds the frontend tab and three pivot views:

- **Per-employee × per-month** — Anthropic + OpenAI + others, by `audit.employees.employee_id`
- **Per-API-key × per-pipeline** — for cost-attribution to specific automation features
- **Per-agent × per-prompt-hash** — to spot prompt regressions or runaway costs

Sharpens our pitch versus Ramp's AI Spend Intelligence (which tags per team / project / model — not per *employee × journal-line*).

---

## 8. Technology Stack

| Layer | Choice | Version | Rationale |
|---|---|---|---|
| Language (backend) | Python | 3.12+ | Already in use; aiosqlite + Anthropic SDK + FastAPI all stable |
| Web framework | FastAPI | latest | Already shipped; SSE first-class |
| ASGI server | uvicorn | latest | `--workers 1` mandatory (per-DB asyncio.Lock) |
| DB | SQLite + WAL | 3.40+ | RealMetaPRD §9; 3-DB split shipped |
| LLM runtime | `anthropic` SDK | latest | `AsyncAnthropic(timeout=4.5, max_retries=2)`; deterministic-fallback on timeout |
| OCR / extraction | Anthropic Claude vision | Sonnet 4.5 | Already shipped via `document_extractor` |
| YAML | `PyYAML` | latest | Strict-key validation in `yaml_loader.py` |
| Frontend | Vite + React | 18 + 5.x | Already shipped |
| TS | TypeScript | 5.x | Strict mode |
| Styling | Tailwind | v4 | Already shipped |
| State | Zustand | 5 | Already shipped |
| Animation | Motion (ex-Framer Motion) | latest | For DAG node transitions |
| DAG rendering | `react-flow` | latest | Industry standard for node-graph visualisations |
| Markdown editor | CodeMirror 6 + `unified` | latest | For the Wiki editor |
| PDF generation | WeasyPrint | latest | HTML/CSS → PDF for reports |
| Slides | Marp CLI | latest | Markdown → board-pack deck |
| Wiki search (later) | `qmd` | — | Optional — frontmatter-tag routing is enough at MVP scale |
| Approvals | Slack SDK + python-telegram-bot | latest | For threshold-gate human approvals |
| Test runner | pytest + pytest-timeout | latest | 15s default per-test ceiling (CLAUDE.md) |
| Deploy | Docker + Scaleway / OVHcloud | — | EU residency mandatory |

**No additions to the LLM provider list at MVP.** Anthropic only. (Mistral or Cerebras is a Phase-N add — see Future Considerations.)

---

## 9. Security & Configuration

### Authentication & authorization

- **Customer perimeter:** single-tenant deploy per customer, no shared DB across tenants.
- **CFO sign-in:** OAuth via Google / Microsoft, with WebAuthn enforced for any user authorised to commit campaigns or approve >€2,500.
- **Auditor / Steuerberater access:** scoped read-only to `wiki/*`, `journal_entries`, `decision_traces`, `audit_pack` exports — never to `accounting.write_tx`.
- **Service tokens:** Swan client_credentials in `SwanOAuthClient`; benefit-provider API keys in `data/secrets.env` (encrypted-at-rest).

### Configuration

Canonical env vars (extends `.env.example`):

```
AGNES_DATA_DIR=./data
AGNES_TENANT_ID=acme-fr
SWAN_CLIENT_ID=
SWAN_CLIENT_SECRET=
SWAN_WEBHOOK_SECRET=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL_DEFAULT=claude-sonnet-4-6
ANTHROPIC_MODEL_DOC=claude-opus-4-7
JOBRAD_API_KEY=
JOBBICYCLE_API_KEY=
USC_INVOICE_INBOX=          # email address for monthly CSV
WELLPASS_INVOICE_INBOX=
FINN_API_KEY=
SLACK_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=
WIKI_DIR=./wiki
WIKI_LINT_CRON="0 3 * * *"
```

### In scope (security MVP)
- ✅ Constant-time webhook secret comparison (already shipped for Swan + Stripe)
- ✅ HMAC-SHA256 verifier registry (already shipped)
- ✅ SHA-256 idempotency on uploaded documents
- ✅ Encrypted secrets at rest (libsodium / age)
- ✅ EU-only hosting + EU-only Anthropic endpoints
- ✅ TLS termination at the reverse proxy
- ✅ Audit-log immutability for `audit_decisions` (append-only enforced at SQL level)

### Out of scope (security MVP)
- ❌ SOC2 / ISO 27001 attestations (Phase 2 commercialisation)
- ❌ End-to-end encryption between frontend and backend over local network (rely on TLS)
- ❌ DLP scanning of uploaded documents
- ❌ HSM-backed Swan secret storage (file-based encryption suffices for MVP)

### Deployment

Single-tenant Docker stack:
- Reverse proxy (Caddy) → uvicorn (`--workers 1`) → Python app
- Volumes: `./data/` (DBs + blobs) + `./wiki/` (markdown corpus)
- Backups: nightly `sqlite3 .backup` + `wiki/` git push to a customer-owned GitHub repo

---

## 10. API Specification

### New endpoints (MVP additions on top of shipped /healthz, /swan/webhook, /external/webhook, /documents/upload, /pipelines/run, /runs, /journal_entries, /envelopes, /review, /dashboard/stream)

```
POST   /onboarding/start
GET    /onboarding/state
POST   /onboarding/answer/{step}
POST   /onboarding/finalize          # runs onboarding_to_wiki pipeline + opening JE

GET    /wiki/pages
GET    /wiki/pages/{path}
GET    /wiki/pages/{path}/revisions
PUT    /wiki/pages/{path}            # writes new revision; agent reads see new content next call
POST   /wiki/lint                    # triggers a wiki-lint pipeline run

POST   /benefits/jobrad/webhook
POST   /benefits/jobbicycle/webhook
POST   /benefits/finn/webhook
POST   /benefits/usc/invoice         # multipart, monthly CSV
POST   /benefits/wellpass/invoice    # multipart, monthly CSV
POST   /benefits/dinner/upload       # multipart, single-receipt PDF (alias of /documents/upload)

GET    /reports                      # list available report types
POST   /reports/board_pack           # body: { period: "2026-03" } → returns run_id
POST   /reports/monthly_close
POST   /reports/cash_forecast
POST   /reports/vat_return
POST   /reports/audit_pack
GET    /reports/{run_id}/artifact    # MD / PDF / Marp / CSV

POST   /campaigns/draft              # body: { goal: "save €15K by Q3 ..." } → EnvelopeDelta[]
POST   /campaigns/preview            # body: { plan } → impact-by-employee preview
POST   /campaigns/commit             # body: { plan } → write_tx all deltas
GET    /campaigns                    # list past campaigns

GET    /ai-spend                     # query params: by={employee|key|pipeline|model} period=YYYY-MM
GET    /ai-spend/timeseries          # for charts
```

### Example payload — `POST /campaigns/draft`

Request:
```json
{
  "goal": "Save €15,000 by 2026-09-30. Don't touch JobRad. Don't pause hiring.",
  "horizon_months": 5
}
```

Response:
```json
{
  "run_id": "run_2026_04_25_abc12",
  "plan": {
    "deltas": [
      { "envelope": "benefit.usc", "scope": "all_employees",
        "current_eur": 50, "proposed_eur": 40, "monthly_savings_eur": 1200 },
      { "envelope": "benefit.dinners", "scope": "all_employees",
        "current_eur": 60, "proposed_eur": 50, "monthly_savings_eur": 2800 },
      { "envelope": "benefit.finn", "scope": ["emp_001", "emp_002"],
        "action": "pause_30d", "monthly_savings_eur": 2400 },
      { "envelope": "ai_credit.marketing_agent", "scope": "team",
        "current_eur": 1500, "proposed_eur": 1200, "monthly_savings_eur": 300 }
    ],
    "estimated_total_savings_eur": 33500,
    "horizon_months": 5,
    "wiki_citations": ["wiki/policies/campaign-bounds.md@rev3", "wiki/policies/benefits-caps.md@rev7"],
    "agent_decision_id": "dec_2026_04_25_xy"
  }
}
```

---

## 11. Success Criteria

### MVP success definition

A self-onboarded design partner CFO of a 100-person Franco-German scale-up:
- ✅ Completes onboarding in ≤30 minutes wall-clock
- ✅ Sees their first live Swan transaction posted to the GL with a wiki citation in ≤5 seconds of the bank's webhook firing
- ✅ Has all six benefit integrations classifying correctly within 7 days of go-live (≥95% confidence-gate auto-pass for recurring vendors after the first month)
- ✅ Generates a monthly board pack in <5 minutes that the board reads without footnote questions
- ✅ Successfully runs one campaign end-to-end (draft → preview → commit) within the first month
- ✅ Edits the rule wiki at least once based on auditor feedback, sees the edit reflected in the next pipeline run

### Functional requirements

- ✅ Live ledger: <5s p95 from Swan webhook → posted JE
- ✅ Single-writer DB discipline maintained (CI grep enforces `gl_poster.post` chokepoint, no float on money paths)
- ✅ Every journal line has a `decision_trace_id` link
- ✅ Every agent call has cost recorded in micro-USD in `audit.agent_costs`
- ✅ Every wiki edit is versioned with revision id, timestamp, author
- ✅ DAG visualizer renders all 12+ nodes of `transaction_booked` correctly, with live state transitions, in a browser
- ✅ Board pack PDF + Marp generated end-to-end without manual editing
- ✅ Campaign commit applies all deltas atomically (one `write_tx`)

### Quality indicators

- Test suite stays green: 50+ existing tests + ~30 new tests (onboarding flow, wiki revisioning, each benefit pipeline, campaign commit atomicity, AI-spend pivots).
- All pipelines complete within their per-pipeline cost budget (`transaction_booked` ≤€0.005/run; `monthly_close` ≤€0.50/run; `board_pack` ≤€0.30/run).
- p95 confidence on `gl_account_classifier_agent` ≥0.85 after 30 days of customer data.
- Zero double-postings under chaos testing (Swan webhook duplicates, document upload retries, campaign re-commits).

### User experience goals

- The CFO never sees a SQL error, a stack trace, or a JSON payload.
- The CFO can explain to their auditor in <30 seconds, on a screen-share, why any number on any report is what it is.
- The Steuerberater / expert-comptable experiences Agnes as a *better-organised expert-comptable, not a replacement* — exports and explanations are at least as good as Pennylane's.
- The employee (Paul) sees only a Slack/Telegram tap and a confirmation, never a UI.

---

## 12. Implementation Phases

> **Numbering:** Phases 1–3 are shipped (metalayer foundation, Swan + frontend, reporting layer). This PRD covers Phase 4 — the CFO product surface.

### Phase 4.A — Onboarding & Living Wiki (Weeks 1-3)

**Goal:** A new tenant can self-onboard in 30 minutes and have a non-empty, agent-readable wiki on day one. The shipped reporting pipelines start citing wiki revisions in their decision traces.

Deliverables:
- ✅ `backend/orchestration/wiki/{loader,writer,schema}.py`
- ✅ `orchestration.db` migrations (`wiki_pages` + `wiki_revisions`) — pick the next free numbers in the orchestration migration sequence (current is 0001 only — see `CLAUDE.md`)
- ✅ `wiki_reader` tool, registered, with frontmatter-tag routing
- ✅ Patch `prompt_hash.py` to incorporate `(wiki_page_id, revision_id)`
- ✅ `audit.db` migration `0004_onboarding_answers`
- ✅ `backend/api/onboarding.py` + `backend/api/wiki.py`
- ✅ `frontend/src/routes/Onboarding.tsx` (8-step wizard)
- ✅ `frontend/src/routes/Wiki.tsx` (CodeMirror editor + revision diff view)
- ✅ Seed PCG / SKR03 / SKR04 CSVs
- ✅ Patch the existing `gl_account_classifier_agent`, `counterparty_classifier`, `document_extractor`, `anomaly_flag` agents to read the wiki (smallest patch — preserve existing report-shape contracts)

Validation:
- Manual onboarding of three test tenants (FR-only, DE-only, FR+DE)
- Wiki edit → next agent call uses the new revision (verified via `prompt_hash` change)
- The shipped `period_close` pipeline runs against a tenant whose only data is the onboarding output, with wiki citations now appearing in `audit.decision_traces`
- All existing tests still green

### Phase 4.B — Benefit integrations + DAG visualizer (Weeks 4-6)

**Goal:** Every recurring benefit invoice and webhook auto-posts; the CFO can watch every run live.

Deliverables:
- ✅ Six benefit pipelines (`benefit_jobrad/jobbicycle/usc/wellpass/finn/dinner.yaml`)
- ✅ `benefit_classifier_agent` + `bik_calculator` + `payroll_writer` tools
- ✅ `accounting.db` migrations 0010 (`benefit_providers`) + 0011 (`employee_benefit_caps`) — note 0009 is already taken by the Phase 3 reporting migration
- ✅ `react-flow` DAG viewer in `frontend/src/components/DagViewer.tsx`
- ✅ Per-node SSE event types audited & emitted from `executor.py` (without breaking the existing dashboard SSE consumers)
- ✅ Decision-trace right-rail panel
- ✅ `EnvelopeGrid.tsx` (employee × benefit matrix)

Validation:
- ≥95% auto-pass rate on recurring USC / Wellpass invoices after first month
- DAG visualizer renders `transaction_booked` (12 nodes), the shipped `period_close` pipeline, and one new benefit pipeline correctly under load
- One full month replayed correctly under `pytest`

### Phase 4.C — Board pack, cash forecast, audit pack, campaigns, AI-spend (Weeks 7-9)

**Goal:** The CFO never opens Excel for board prep, cash forecasting, audit prep, or budget reshapes.

Deliverables (only the *new* reports — period close / VAT return / year-end close are already shipped):
- ✅ `board_pack.yaml` pipeline + `narrative_generator` + `kpi_assembler` agents
- ✅ `cash_forecast.yaml` pipeline + `cash_projector` agent
- ✅ `audit_pack.yaml` pipeline (deterministic — bundles existing JEs + decision traces + wiki revisions)
- ✅ Extend the existing `report_renderer` tool to emit Marp slides for board-pack
- ✅ Extend `vat_form_renderer` (or create one if it isn't yet a separate tool) to emit pre-filled CA3 / UStVA exports
- ✅ `accounting.db` migration 0012 (`campaigns`)
- ✅ `campaign_proposer` agent + `campaign_planner` tool
- ✅ `frontend/src/routes/Campaigns.tsx` + `frontend/src/routes/AISpend.tsx`
- ✅ Extend the existing Reports tab with the three new report types (don't fork it)

Validation:
- A 12-month board pack regenerates from real demo data in <5 minutes
- A campaign committed in March is visible in April's envelope ceilings and in the audit pack
- AI-spend tab matches the underlying `audit.agent_costs` to the cent
- Existing reporting endpoints still pass their Phase 3 tests untouched

### Phase 4.D — Polish, design-partner onboarding, security (Weeks 10-12)

**Goal:** Two design-partner tenants in production; SOC-2-readiness gap analysis done.

Deliverables:
- ✅ Two design-partner tenants live (one FR-primary, one DE-primary)
- ✅ Slack / Telegram approval bots
- ✅ Wiki-lint nightly job
- ✅ Backup / restore tested (sqlite3 .backup + git push of wiki)
- ✅ Security checklist signed off (TLS, encrypted secrets, EU-only endpoints)
- ✅ Updated `README.md`, `CLAUDE.md`, `pitch.md`, `competitive_teardown.md`

Validation:
- Both tenants close March 2026 books on Agnes (using the shipped `period_close` pipeline + the new wiki citations)
- One auditor walks through the new `audit_pack` and signs off without supplemental requests
- A campaign-committed change reverses cleanly via a "revert campaign" action

---

## 13. Future Considerations

- **Multi-currency & multi-entity.** Add a currency column on `journal_lines`, an FX revaluation table, an `entity_id` discriminator. Required to graduate from "single FR/DE legal entity" to a real consolidation tool.
- **Direct PDP / ELSTER submission.** Submit FR e-invoices and DE UStVA via the customer's chosen PDP / ELSTER endpoint, not just file-format-validate.
- **Italian / Spanish / Polish localisation.** Each adds: chart of accounts, VAT regime, e-invoicing format (FatturaPA, FACe, KSeF).
- **Mistral / Cerebras runtime parity.** Already-stubbed `pydantic_ai_runner` and `adk_runner`; activate for cost/latency arbitrage.
- **Embedding-based wiki retrieval (`qmd`).** Once the wiki crosses ~200 pages per tenant, frontmatter-tag routing won't be enough; switch to `qmd`'s BM25 + vector hybrid.
- **DD-pack agent for the PE/VC side.** A productized middle-market DD pack — same engine, different audience, replaces the €50–150K Big-4 service line.
- **Ramp / Brex-class corporate cards** issued natively from Swan, with Agnes-policy enforcement at authorisation time (not just post-auth).
- **A "what-if my March wiki had been correct?" backtester.** Replay arbitrary historical periods against current wiki state and surface the deltas — the auditor's dream tool.
- **Auditor-side product.** A standalone Steuerberater / expert-comptable seat that can flag, comment, and write back to the wiki across multiple client tenants.

---

## 14. Risks & Mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | **The wiki + agent prompt-input contract is brittle.** A bad wiki edit could degrade every downstream classification silently. | Wiki revisions are tracked; every agent decision cites `(page_id, revision_id)`; a regression test suite replays a fixed corpus against the head wiki; lint job flags any page edit that materially changes the prompt-hash distribution. |
| 2 | **Benefit-provider APIs / invoice formats change.** USC, Wellpass, Finn, JobRad each have proprietary monthly artefacts. | Each integration is a small, isolated YAML pipeline + extractor tool. Provider changes touch ≤1 file. Fallback path: a generic `manual_benefit_upload` route into the same pipeline. |
| 3 | **Auditor pushback on AI-posted journal entries.** A French expert-comptable may refuse to sign off if "the agent posted it." | The auditor reads `audit_pack` ZIPs that contain the wiki revisions, decision traces, prompt hashes, and source documents — i.e., they're auditing rules + data, not "the AI." Confidence-gate ensures any low-confidence item routes to human review. |
| 4 | **EU AI Act provenance regime tightens beyond what we ship.** August 2026 enforcement could require artefacts we don't yet produce. | Decision-trace is already the most granular contract in the EU SME finance space; we can extend it without schema breaks. Subscribe to AI Office implementing acts, version the trace schema, never lossy-compress. |
| 5 | **Multi-worker uvicorn temptation.** Customers will ask for high availability; the per-DB asyncio.Lock can't span processes. | Document the `--workers 1` constraint loudly (already in `CLAUDE.md`). Solve scale by sharding tenants across single-worker instances (each tenant gets its own DB anyway), not by multi-worker per tenant. |
| 6 | **The campaign agent commits something the CFO didn't actually approve.** | Hard bounds in `wiki/policies/campaign-bounds.md`; explicit preview-then-commit two-step (no one-click commit on >€2,500); every commit is reversible via a single "revert campaign" button that writes inverse deltas. |

---

## 15. Appendix

### A. Pain-point research (sources)

The CFO pain points in §3 are grounded in:

- [EY — 5 steps for CFOs on finance transformation (2024)](https://www.ey.com/en_us/services/consulting/finance-consulting-services/5-steps-for-cfos-on-the-path-to-finance-transformation) — 80% of finance time is transactional
- [Deloitte Q4 2025 CFO Signals](https://www.deloitte.com/us/en/insights/topics/business-strategy-growth/4q-2025-cfo-signals-survey.html) — 50% name digital transformation as #1 2026 priority
- [Spendesk State of AI in Finance 2025](https://www.spendesk.com/landing/state-of-ai-finance-2025-cfo-connect/) — 75% of accounting tasks automatable; 73% say expense processes too manual
- [Spendesk CFO Tools Report 2025](https://www.spendesk.com/landing/cfo-tools-report-2025-cfo-connect/)
- [Ledge — State of month-end close 2025](https://www.ledge.co/content/month-end-close-benchmarks-for-2025) — median 6.4 days
- [ACFE 2024 Report to the Nations](https://legacy.acfe.com/report-to-the-nations/2024/) — expense-reimbursement fraud median loss $141k for <100-FTE orgs
- [Zylo 2025 SaaS Management Index](https://zylo.com/news/2025-saas-management-index/) — 275 apps/org, $4,830/employee, 51% miscategorised
- [AODocs — GoBD primer](https://www.aodocs.com/blog/gobd-explained-requirements-for-audit-ready-digital-bookkeeping-in-germany-and-beyond/) — 8-year e-invoice retention
- [EU AI Act implementation timeline](https://artificialintelligenceact.eu/implementation-timeline/) — Aug 2 2026 enforcement
- [Avalara — France e-invoicing 2026/27](https://www.avalara.com/blog/en/europe/2025/09/france-e-invoicing-e-reporting-mandate-2026-2027.html)

### B. Benefit accounting references

- [Lease-a-Bike — JobRad tax matters](https://www.lease-a-bike.de/en/faq-employer/tax-matters)
- [Carano — DE 1% rule for company cars](https://www.carano.de/en/blog/1-percent-rule/)
- [Urban Sports Club — Sachbezugsfreigrenze FAQ](https://usccorporate.zendesk.com/hc/en-us/articles/24050394441362-Can-we-take-advantage-of-the-tax-free-benefit-in-kind)
- [EQS Benefits — sport programme](https://benefits.eqs.com/benefit/sportangebot)
- [Invoicedataextraction — Bewirtungsbeleg](https://invoicedataextraction.com/blog/bewirtungsbeleg-in-english)
- [Mooncard — TVA récupérable repas (FR)](https://www.mooncard.co/fr/cas-usage/tva/tva-recuperable/repas)
- [Meridian — DE restaurant VAT 7% from 2026](https://meridianglobalservices.com/germany-to-permanently-reduce-vat-on-restaurant-and-catering-services-to-7-from-1-1-2026/)
- [Steuergo — restricted business expenses (Bewirtung 70%)](https://www.steuergo.de/en/texte/2025/524/restricted_deductible_business_expenses)

### C. Companion documents

- `Orchestration/PRDs/RealMetaPRD.md` — engine spec (the contract every line of code cites)
- `Orchestration/PRDs/MetaPRD.md` — predecessor PRD
- `Orchestration/PRDs/PRD1.md` — original Phase 1 framing
- `Orchestration/Plans/phase1-metalayer-foundation.md`
- `Orchestration/Plans/phase-2-swan-document-frontend.md`
- `Orchestration/Plans/phase-2-list-endpoints-and-frontend.md`
- `Orchestration/research/llm-wiki.md` — Karpathy's pattern, foundation for §7.3
- `Orchestration/pitch/pitch.md` — the elevator pitch
- `Orchestration/pitch/competitive_teardown.md` — what every competitor doesn't do
- `Orchestration/pitch/hg_catalyst_briefing.md` — the investor lens
- `CLAUDE.md` — repo state + hard rules

### D. Open research items for the implementing agent

The user explicitly asked that *the actual orchestration-pipeline integration questions stay open* for the agent that picks up this PRD. The deferred items, gathered:

1. **Wiki integration in agents.** Walk `backend/orchestration/agents/{counterparty,gl_account_classifier,document_extractor}.py` line by line. Decide: where does `wiki_reader` get called? Which prompt slot? How does `(wiki_page_id, revision_id)` thread into `prompt_hash.py` so cache invalidation is correct without false-negatives on unrelated edits?
2. **Wiki storage.** Stay with `orchestration.db` (clean — wiki is orchestration state) or split out `wiki.db` (clean separation, but breaks the 3-DB invariant from RealMetaPRD §9)?
3. **Cross-run cache + wiki revisions.** A wiki edit must invalidate downstream cache hits *only on agents that read that page*. Enumerate the required `cache.py` changes.
4. **DAG event emission.** Audit `executor.py` and `runs.py` SSE: are per-node `started`/`completed`/`agent.decision`/`cost.recorded` events already emitted, or do we need to add them? Don't break existing dashboard SSE consumers.
5. **Benefit-provider API contracts.** For each of JobRad, JobBicycle, USC, Wellpass, Finn — find the actual public webhook / API / invoice-CSV format. Codify `backend/orchestration/integrations/{provider}/`.
6. **Campaign double-entry semantics.** A campaign that "moves €5K from dinners to a CNC reserve" — does it warrant a transfer JE (debit `1068 - Réserves diverses`, credit retained earnings appropriation), or is it a pure budgeting metadata change with no GL impact? Reason from PCG / HGB principles.
7. **VAT form mapping.** Column-by-column map of CA3 (DGFIP) and UStVA (BMF) form fields to GL account codes. Codify in `vat_form_renderer.py`.
8. **PCG / SKR03 / SKR04 seed CSVs.** Source authoritative versions for shipping with onboarding.
9. **`prompt_hash` extension.** Should it hash the full wiki-page bodies or only the `(id, revision)` pair? Trade-off: bytes vs. determinism.
10. **Migration ordering.** New migrations must compose cleanly with existing 0001..0008 (accounting), 0001..0003 (audit), 0001 (orchestration). No re-numbering.

The implementing agent should treat this PRD as the *what*, not the *how*. The *how* is theirs to design, ground-truth against the existing engine, and write up as a new `Orchestration/Plans/phase-3-autonomous-cfo.md`.
