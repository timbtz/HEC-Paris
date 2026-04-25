# Competitive Teardown — Agnes vs. the EU SME Finance Stack

> *As of 2026-04-25. Sources cited inline. "Shipped & live" is distinguished from "announced / roadmap" throughout — Hg Catalyst's first filter.*

---

## TL;DR — where each competitor stops, and where Agnes starts

| Layer | Pennylane | Qonto | Spendesk / Payhawk / Pleo | Ramp / Brex (US, EU-bound) | Sage Intacct (Hg-adjacent) | **Agnes** |
|---|---|---|---|---|---|---|
| Bank → ledger latency | sub-day, aggregator-polled | 3×/day batch sync to Pennylane | n/a (cards only) | sub-minute (US Treasury rails) | ERP-batch | **<5s, Swan webhook → posted JE** |
| Native GL? | yes | **no** (sells Regate to accountants) | no | no | yes | **yes (3-DB, single-writer)** |
| Per-employee budget envelopes | spending limits, no envelope primitive | per-card limits + add-on at €69-89/mo | real-time, but on top of GL | real-time, on top of GL | budgets in close | **same `write_tx` as the JE** |
| Decision-trace per journal line | not exposed | n/a | audit logs | "agents cite their work" | audit trails | **first-class table joined to every line** |
| AI-API spend attribution | none | none | none | **AI Spend Intelligence** (team / project) | none | **per-employee × journal-line** |
| Agentic action vs. suggest-and-click | "AI-assisted" / Copilot — human commits | one shipped agent (Twin invoice retriever) | "first agents launch 2026" | shipped — Brex Agent Mesh, Ramp AP agents | Close Assistant + Subledger Reconciliation (US/UK GA Nov 2025) | **agents execute through `gl_poster.post` chokepoint with confidence-gate human approval** |
| EU AI Act provenance | not a product | not a product | not a product | not a product | audit trails | **`(decision, cost, employee)` triple, native** |

The honest read: **no single EU competitor combines a live ledger, transactionally-consistent per-employee budgets, decision-trace as a join key, and per-employee AI-credit attribution** in one product. Each axis is partially covered by someone; the *combination* is open.

---

## 1. Pennylane — the obvious comparison, and why we are one layer further out

**What they do today.** All-in-one financial OS for French SMEs and accounting firms. Bookkeeping, ledger, invoicing, expense, cash-flow, plus a Swan-powered Pro Account ([Swan blog](https://www.swan.io/blog-posts/pennylane-wallet)). AI Copilot auto-categorises, OCRs receipts, generates one-click reports, answers accounting questions — **the accountant clicks to commit** ([GetApp](https://www.getapp.com/finance-accounting-software/a/pennylane/), [TechFundingNews](https://techfundingnews.com/pennylane-raises-200m-to-lead-european-accounting-os/)).

**Bank ingest.** Aggregator-polled for third-party banks (likely Bridge); Swan-native for their own Pro Account. Webhook-out to downstream automation, not webhook-in for ingestion ([Pennylane API docs](https://pennylane.readme.io/)). Capterra reviews flag bank-connection failures as a recurring complaint ([Capterra](https://www.capterra.com/p/218672/Pennylane/reviews/)).

**Business posture.** Series E **€175M / $4.25B**, Jan 2026, lead TCV ([Bloomberg](https://www.bloomberg.com/news/articles/2026-01-20/tcv-blackstone-back-startup-pennylane-at-4-25-billion-value)). ~1,000 staff, ~800K customers + 6K partner firms, **PA-registered for the Sept 2026 e-invoicing mandate** ([Pennylane PDP page](https://www.pennylane.com/fr/fiches-pratiques/facture-electronique/liste-des-pdp)). Germany launched Nov 2025; UK explicitly deprioritised.

**The wedge against them.**
- **Speed:** sub-5s vs. sub-day. Pennylane's CEO publicly conceded SME adoption hasn't accelerated — most growth is via accounting firms ([Maddyness](https://www.maddyness.com/uk/2026/01/29/pennylane-new-e175-million-funding-round-for-the-french-unicorn/)). The CFO is not their primary user; the accountant is.
- **AI posture:** "AI-assisted" — Agnes ships agents that *execute* through a single `gl_poster.post` chokepoint with `propose → checkpoint → commit`.
- **Decision trace:** not a product surface. Agnes makes it a join key on every journal line — the EU AI Act provenance story Pennylane will retrofit; we ship.
- **Per-employee budgets / AI-credit cost:** absent. Pure greenfield.

**One-line framing.** *Pennylane gave the accountant a co-pilot. We give the SME founder an autonomous CFO.*

---

## 2. Qonto — the partner that owns the rails, not the books

**What they do today.** Business banking across 8 EU markets, **600K+ customers** (Sep 2025), still on a 2018 EMI license but **filed for a full credit-institution license with the ACPR in mid-2025** ([TechCrunch](https://techcrunch.com/2025/07/02/french-b2b-fintech-qonto-reaches-600000-customers-files-for-banking-license/)). Per-card limits, virtual instant cards, multi-layer approval workflows — sold as an Expense & Spend Management add-on at €69-89/mo ([qonto.com/en/team](https://qonto.com/en/team)). **No native GL** — bought **Regate** (Mar 2024) to ship accountant-facing tooling ([TechCrunch](https://techcrunch.com/2024/03/06/flush-with-cash-french-fintech-unicorn-qonto-acquires-regate/)). For now, the books live in Pennylane via a **3×/day sync** ([qonto.com/en/integrations/pennylane](https://qonto.com/en/integrations/pennylane)).

**AI shipped.** The **Twin "Invoice Operator"** — an OpenAI CUA-powered browser agent that logs into vendor portals to retrieve missing invoices. Beta: 1,500 users, 500 invoices, 50 providers ([TechCrunch](https://techcrunch.com/2025/03/27/twins-first-ai-agent-is-an-invoice-retrieval-agent-for-qonto-customers/)). Narrow — login + download. Otherwise Prot's public AI focus is fraud detection, not autonomous booking ([Sifted podcast, Jun 2025](https://sifted.eu/articles/qonto-ceo-alexandre-prot-sifted-podcast)).

**Business posture.** Last priced: **€486M Series D, Jan 2022, €4.4B valuation** ([FintechFutures](https://www.fintechfutures.com/venture-capital-funding/french-fintech-qonto-secures-486m-series-d-funding-to-boost-european-growth)). Profitable since 2023, ~2,300 staff, expanding upstack via the credit license + Regate.

**The wedge against them.**
- **The 3×/day sync is the gap.** Qonto stops at "card was authorised"; Pennylane catches up three times a day. Agnes is the layer that closes that gap to <5 seconds and writes the journal entry, the budget decrement, and the audit trace in one transaction.
- **Webhook surface.** Qonto's documented webhooks are narrow — onboarding events (`registrations.submitted`, `capital_deposit_*`), not a transaction stream ([docs.qonto.com](https://docs.qonto.com/api-reference/onboarding-api/webhooks/webhooks)). Swan, by contrast, gives us per-transaction events — which is exactly what Agnes is built on.
- **AI breadth.** One narrow agent in production. Agnes ships counterparty resolution, GL classification, document extraction, period-close, and DD reports as a registry of named, swappable agents.

**Watch for collision.** Regate was a Pennylane competitor; the credit license lets Qonto move up the stack. The Pennylane × Qonto partnership is structurally fragile — Agnes is the agent layer above both.

---

## 3. The spend-management cohort — Spendesk, Payhawk, Pleo, Mooncard

| | Spendesk | Payhawk | Pleo / Mooncard |
|---|---|---|---|
| Agentic | "first agents launch 2026" — announced, not shipped ([Spendesk Connect 2025](https://www.spendesk.com/blog/spendesk-connect-2025/)) | **4 enterprise AI agents shipped Fall 2025** ([Payhawk](https://payhawk.com/en-us/blog/payhawk-launches-four-enterprise-ready-ai-agents)) — Procurement Agent applies budgets/policy pre-approval | n/a |
| Real-time budgets | "proactive budgeting" — separate writes from GL | real-time utilization, multi-forecast, PO-line tracking — separate writes | card limits + reporting, no GL transaction |
| Native GL | no | no | no |
| Decision trace | n/a | audit logs | n/a |
| AI-API spend | n/a | n/a | n/a |

**The shared structural gap.** None of the four document writing the budget decrement and the journal entry inside the same DB transaction. Two systems kept in sync — eventually consistent, exposed to drift at month-end. Agnes's `BEGIN IMMEDIATE` + `(decision, cost, employee)` triple under one commit appears net-new in EU mid-market. (Caveat: vendor-internal architecture isn't always public — "unknown" is the honest fallback for whether one of them quietly does it server-side.)

**The wedge.** They sell a layer *above* the books and bolt it on through nightly reconciliation. Agnes makes the budget *be* the books.

---

## 4. Ramp & Brex — the US benchmarks now crossing into EU

**Brex Agent Mesh + Capital One.** Confirmed: definitive agreement Jan 22, 2026, **$5.15B, 50% cash / 50% stock**, completed Q2 2026 ([Capital One IR](https://investor.capitalone.com/news-releases/news-release-details/capital-one-acquire-brex), [Coindesk](https://www.coindesk.com/markets/2026/01/22/capital-one-acquires-fintech-firm-brex)). The line items that show what was actually transferred: **$500M capitalized software (3-yr amortization) + $350M other identifiable intangibles (5-yr)** — that is the corporate-spend platform plus Agent Mesh ([Airwallex analysis](https://www.airwallex.com/us/blog/capital-one-acquired-brex)). 99% expense-process automation in the customer cohort that has Agent Mesh fully on. Brex secured an **EU Payment Institution license Aug 2025** ([PR Newswire](https://www.prnewswire.com/news-releases/brex-secures-eu-payment-institution-license-unlocking-next-phase-of-global-expansion-302524388.html)) — 1,500 customers with EU operations as of that date.

**Ramp.** Production AP agents — auto-coding, fraud, approvals, payment optimization, citing their work back for audit. The headline EU-relevant feature: **AI Spend Intelligence** (a.k.a. AI Token Spend Management). Token-level data pulled from Anthropic, OpenAI, OpenRouter, attributed by **team / project / model / use case** ([Ramp support](https://support.ramp.com/hc/en-us/articles/50665591644051-AI-Spend-Intelligence), [product page](https://ramp.com/ai-cost-monitoring)). Ramp **acquired Billhop (Stockholm/London) Mar 13, 2026** to get EEA + UK regulatory cover — direct EU customer onboarding now starting ([Tech.eu](https://tech.eu/2026/03/13/expense-management-startup-ramp-takes-on-rival-brex-with-european-acquisition/)).

**The wedge against Ramp / Brex in EU.**
- **Per-employee, not per-team.** Ramp's unit of attribution for AI spend is *team / project / model* — Agnes's is **employee × journal-line**. That's the cut a French CFO actually needs for payroll-attached accountability under URSSAF / DGFIP scrutiny.
- **Native ledger, not bolt-on.** Ramp/Brex are corporate cards + spend that hand off to a separate accounting system. Agnes is the ledger.
- **EU AI Act provenance native.** Ramp's "agents cite their work" is product copy; Agnes's `decision_traces` is a schema contract.
- **EU posture.** Brex is post-acquisition under Capital One — strategic uncertainty for a French SME signing a 3-year contract. Ramp just landed via Billhop and is in onboard mode. Agnes is EU-native, Swan-rails, French-mandate-aware (PDP / PA path), and Anthropic-priced in micro-USD.

---

## 5. Sage Intacct — the most credible mid-market displacer

The most concrete agentic accounting stack in the EU-relevant universe is **Sage Intacct**. As of Nov 2025: **Close Automation GA in US/UK** (Close Workspace, Close Assistant, Subledger Reconciliation, Variance Analysis); Finance Intelligence Agent in early access US/UK from Dec 2025; Close, AP, Time, Assurance Agents already live ([Sage press](https://www.sage.com/en-us/news/press-releases/2025/11/sage-intacct-delivers-new-capabilities-that-transform-how-finance-teams-close/)). They also opened **AI Developer Solutions** so certified third parties can ship agents into Copilot ([Sage AI Developer Solutions](https://www.sage.com/en-us/news/press-releases/2025/11/sage-launches-ai-developer-solutions-to-accelerate-partner-led/)).

**Wedge against Sage.**
- **Different customer.** Sage is enterprise / upper-mid-market US/UK with multi-year ERP migrations. Agnes targets the 50–250-person EU scale-up where the CFO can install a SaaS in a weekend.
- **Speed.** Sage runs ERP-batch close cycles. Agnes's entire architecture is webhook-first.
- **EU specificity.** Sage's GA is US/UK; the FR PDP path, the German XRechnung path, the EU AI Act trace — all of these are home turf for an EU-native product.

**Watch.** Sage's developer marketplace + Serrala's acquisition of Cevinio (closed **Feb 6, 2026** — [Serrala](https://www.serrala.com/news/serrala-acquires-e-invoicing-and-accounts-payable-specialist-cevinio)) signal that the Hg-portfolio mid-market is consolidating fast. Agnes either ships standalone *or* pitches into a Catalyst-style portfolio embed inside Visma / Serrala / Cevinio.

---

## 6. The other adjacencies — and why they don't compete

- **Agicap (FR)** — cash-flow forecasting on top of ERPs. ML for late-payer detection and dunning. **Not a ledger.** Complementary.
- **Visma (Hg, NO)** — invoice OCR + bookkeeping suggestions across e-conomic / Business NXT. **Aug 2025 acquired milia.io and Taxy.io** for DACH AI capability ([Tech.eu](https://tech.eu/2025/08/22/visma-strengthens-foothold-in-dach-with-ai-driven-acquisitions-of-miliaio-and-taxyio/)). MCP integration is roadmap. **No agentic CFO product shipped.**
- **Cevinio / Serrala / Azets (Hg)** — Serrala consolidated Cevinio Feb 2026; named IDC MarketScape Leader for AI-Enabled Mid-Market Treasury 2025-2026 ([Serrala](https://www.serrala.com/news/serrala-leader-idc-marketscape-ai-enabled-midmarket-treasury-risk-management)). **Treasury / AP, not the GL spine.**
- **Numeric** — $51M Series B Nov 2025 (IVP, Menlo, Founders Fund) ([PR Newswire](https://www.prnewswire.com/news-releases/numeric-raises-51m-series-b-expanding-from-close-management-to-comprehensive-finance-platform-302619774.html)). MCP-based custom agents, technical-accounting agent, flux-explanation. **US-anchored close-management; closest spiritual cousin in the agentic-FP&A space, but no live-ledger and no per-employee envelope.**
- **Mosaic** — $18M Series A Apr 2026 (Radical Ventures), 5 of top-10 PE firms ([PR Newswire](https://www.prnewswire.com/news-releases/mosaic-raises-18m-series-a-to-build-ai-driven-operating-system-for-deal-makers-302749611.html)). Deal modeling, not bookkeeping.
- **Big 4 (Deloitte Zora, PwC One, KPMG Workbench, EY)** — all shipping agentic stacks, but as **partner-led services, not productized self-serve DD packs**. The €50–150K mid-market DD pack is still labour-arbitraged. Agnes's DD-pack agent survives if it's a *productized* SaaS feature, not a consultancy service. ([Emerj](https://emerj.com/ai-in-the-accounting-big-four-comparing-deloitte-pwc-kpmg-and-ey/))
- **Trovata, Tesorio** — US-anchored treasury platforms; no concrete EU customer disclosures 2025-2026.

---

## 7. The EU AI Act window — and how long it stays open

**Aug 2, 2026** is when most remaining obligations apply and the AI Office gets full enforcement powers (info requests, model recall orders, mitigations, fines) ([artificialintelligenceact.eu](https://artificialintelligenceact.eu/implementation-timeline/)). Article 6(1) high-risk obligations: Aug 2, 2027.

**There is no accounting-specific obligation** — accounting software is a *deployer* of GPAI, not a *provider*. But the *evidentiary regime* the Act creates (you must show how an automated decision was made) collides directly with how every competitor today logs agent activity: **side-channel JSON, opaque tool logs, "agents cite their work."** None expose `(journal_line_id, decision_id)` as a queryable contract.

That makes provenance-as-a-join-key Agnes's most defensible differentiator *today* — and the one most likely to compress through 2027 as competitors retrofit. The window to plant a flag is roughly **12–18 months**.

---

## 8. The Agnes feature combination — what nobody else has, in one paragraph

**Live ledger** (Swan webhook → posted JE in <5s) **+ per-employee budget envelopes written in the same `write_tx` as the journal entry** (no eventual consistency, no nightly reconciliation) **+ decision-trace as a first-class table joined to every journal line** (not a JSON sidecar, not "agents cite their work" in chat) **+ per-employee AI-API spend attribution at micro-USD precision** (not per-team / per-project — per-employee) **+ a YAML pipeline DSL with single-chokepoint posting through `gl_poster.post`** (so new event types ship as YAML + a tool, never executor surgery) **+ an EU-native rails posture** (Swan, French PDP path, AI Act provenance baked in).

Pennylane has the customers and the ledger but not the agents. Qonto has the rails but not the books. Spendesk and Payhawk have the budgets but not the GL. Ramp and Brex have the agents but not the EU posture. Sage Intacct has the close automation but not the speed or the SME fit. Numeric has the close agents but not the live ledger. Big 4 have the reports but not the productisation.

**Agnes is the combination.**

---

## 9. Pitch-line tightening (post-research)

Two phrases to drop or sharpen, given what the research turned up:

1. **"First in Europe to ship live ledger."** Sharpen to: *"First in Europe to ship sub-5-second Swan-webhook → posted journal entry, with per-employee budget enforcement and decision-trace in the same DB transaction."* (Pennylane is webhook-out / aggregator-in; nobody else publishes a sub-5s claim.)
2. **"Per-employee AI-API spend."** Acknowledge Ramp's **AI Spend Intelligence** — but specify that Ramp attributes per *team / project*, while Agnes attributes per **employee × journal-line**, joined to the GL. That distinction is real and defensible.

## Sources

All research grounded in public sources gathered 2026-04-25; full URL list available in the three research briefs that fed this teardown (Pennylane, Qonto, EU landscape).
