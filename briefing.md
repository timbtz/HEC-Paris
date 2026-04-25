# Project Briefing — B2B AI Accounting

**Event:** Paris Fintech Hackathon — Solve with AI · 25–26 April 2026
**Hosts:** SP AI (Sciences Po) · ASES France (HEC & Stanford) · Google Developer Groups
**Status:** Working draft. Expect to iterate this document several times before and during the event.

---

## 1. The product, in one sentence

A B2B accounting platform for European SMEs that books every transaction automatically the moment it happens, keeps a continuously-live balance sheet, and answers management questions in natural language — built on real banking rails so the books are *the source of truth*, not a downstream copy of one.

## 2. The market and the user

The customer is a small-to-mid European company (5–50 employees) that today juggles a business bank account, a separate accounting tool (Pennylane, Indy, Sage, DATEV), an expense-management product (Spendesk, Pleo), and a spreadsheet the founder maintains in secret. Closing the books is a monthly event that takes days. The CFO or founder finds out about cash-flow problems in retrospect, not in real time.

Our user is the person who currently owns this mess — usually the founder in companies under 20 people, a part-time bookkeeper or fractional CFO above that. They are not accountants by training. They want answers, not ledgers.

The wedge is **time-to-truth**: the gap between a financial event happening and the books reflecting it. Today that gap is days to weeks. We collapse it to seconds.

## 3. What the system does

The product replaces the bank-account-plus-accounting-tool stack with one integrated thing. Concretely it should:

- Issue company bank accounts and team cards under the customer's brand (rails provided by Swan).
- Capture every inbound and outbound money movement at source — card spend, supplier transfers, customer payments, recurring subscriptions, expense reimbursements.
- Classify each movement into the right accounting category, generate balanced double-entry journal entries, and post them to a live ledger.
- Maintain a balance sheet, P&L, and cash position that update within seconds of any event.
- Surface insights conversationally: per-employee spend, category trends, anomalies, runway, AR aging — answered in plain language with the underlying numbers cited.
- Provide an auditable trail showing exactly *how* every booking was decided — rule, lookup, or AI — so the books can be defended to an auditor or tax authority.

The hackathon scope is necessarily narrower than this. We pick a tight slice that demos end-to-end and tells the full story.

## 4. The strategic positioning

Many fintech hackathon teams will demo "an AI agent that talks to your bank." That demo is forgettable because the AI is doing all the work and a finance jury instinctively distrusts AI doing all the work with money.

Our positioning is the opposite: **most of our system is deliberately not AI.** AI is used surgically, where deterministic methods can't reach, and every AI decision is logged with its reasoning so it can be inspected, contested, and converted into a deterministic rule for next time. The AI gets *more useful and less needed* as the system learns — the inverse of the typical AI-product trajectory.

This framing matters for two reasons:

- **It's the right architecture.** Money systems need to be correct, auditable, and cheap to operate. Routing every transaction through an LLM fails on all three counts.
- **It's the right pitch.** Saying "our AI never touches arithmetic — it classifies and explains, that's it" is a credibility move that finance judges respond to. It's also true.

We want the jury's first reaction to be *"these people have actually thought about this"* before they see any AI. The AI should land as additive intelligence on a foundation they already trust.

## 5. Architecture, in layers

We think of the system as a pipeline. Each stage uses the cheapest correct technique. Most stages are deterministic; AI is reserved for stages that genuinely need language understanding.

### Stage 0 — Ingestion

Webhooks from Swan arrive, are signature-verified, deduplicated, and stored raw. This is the audit trail. Pure code, no interpretation.

### Stage 1 — Normalization

Raw payloads are parsed into clean structured rows. Pure code. Swan provides all fields structured already (merchant, MCC, amount, IBAN, timestamp), so this stage is mostly a flattening and type-coercion exercise.

### Stage 2 — Counterparty resolution

"Who is this party in our world?"

Tried in this order:

1. Cache lookup — have we seen this merchant or IBAN before?
2. MCC code — Mastercard's structured merchant categorization gives us a strong prior for many transactions.
3. Known-counterparty match — does the IBAN belong to a registered customer or supplier?
4. Fall through to AI — Claude classifies, the result is written back to the cache so this transaction type is deterministic from then on.

The cache is the central architectural idea. After bootstrap, the overwhelming majority of transactions never reach the AI. The AI's job is to *populate the cache*, not to handle steady-state traffic.

### Stage 3 — Account classification

"Which GL account does this hit?"

Same pattern as Stage 2: rules first, configured policies second, AI as a constrained fall-through. When AI is used, it picks from the existing chart of accounts via tool-calling — it cannot invent new accounts. The response is validated against the allowed set before booking. If validation fails, the transaction goes to a review queue rather than getting booked incorrectly.

### Stage 4 — Double-entry generation

Strictly deterministic. Once we know the GL account, the contra-entry is mechanical. There are a small number of patterns (card payment, incoming SEPA, outgoing SEPA, reimbursement, etc.). Each is encoded as code. AI is never used here. This is where correctness must be absolute.

### Stage 5 — Tax handling

VAT and similar splits are arithmetic on top of categorized transactions. Per-category rates live in configuration. Computation uses integer arithmetic on cents to avoid rounding drift. No AI.

### Stage 6 — Confidence gating

A deterministic policy decides whether a booking auto-posts or queues for human review. Inputs include which stages used AI, AI confidence scores, transaction size, and customer-configured thresholds. The policy itself is data, not code, so it can be tuned per company.

### Stage 7 — Invariant checking

After every booking, hard invariants are asserted:

- Debits equal credits within each transaction.
- Our recorded bank balance equals Swan's reported balance.
- Subledger totals match general-ledger control accounts.

If any invariant fails, the books freeze and an alert fires. We deliberately demo this — breaking an invariant on purpose and showing the system catching it is a credibility moment.

### Stage 8 — Reporting and insights

Two flavors, both worth showing.

**Deterministic dashboards** are SQL queries rendered as charts. Per-employee spend, category breakdown, AR aging, runway. Always correct, always instant.

**Conversational copilot** is a chat interface where the AI has read-only access to the books via a small set of tools. Critically, the AI does not compute numbers — it writes queries, our code executes them deterministically, and the results come back to the model for natural-language framing. The numbers are always right because they came from SQL; the explanation is fluent because Claude wrote it.

## 6. The Swan layer

Swan is the regulated banking infrastructure underneath the product. It gives us, in a free public sandbox we can sign up for in minutes:

- A real (sandbox) business account with a real IBAN per company.
- Virtual IBANs we can attach to the main account — useful for assigning one IBAN per customer so incoming payments are auto-matched to invoices.
- Branded virtual and physical Mastercards for team members, with configurable spending limits.
- SEPA Credit Transfer, SEPA Instant, and SEPA Direct Debit for outbound and recurring flows.
- Webhooks for every state change, in near-real-time.
- A payment-control hook that lets our backend approve or reject card transactions in a 1.5-second window before authorization — the natural insertion point for policy-as-code or policy-as-prose rules.
- Two official MCP servers (schema and docs) that let Claude introspect Swan's API natively. Useful during development; less critical at runtime.

What Swan does not give us is more important to be clear about. Swan does not do bookkeeping, does not maintain a chart of accounts, does not produce balance sheets, does not handle VAT, does not parse invoices. All of that is our software. Swan is the rails; we are the train.

A subtle architectural point worth getting right early: **Swan "accounts" are bank accounts, not accounting accounts.** One Swan account per customer company, with virtual IBANs for receivables and cards for payables, is the right shape. Do not model GL categories as separate Swan accounts — that conflates the money layer with the accounting layer and creates problems we don't want to spend the hackathon debugging.

## 7. Auditability as a feature, not a checkbox

Every journal entry in the system carries a structured record of how it was decided: which rules fired, which cache entries hit, whether AI was consulted, what the AI's confidence was, what reasoning it gave. This is stored alongside the entry, not in a separate log.

Two consequences:

- The user (or their auditor) can click any line in the balance sheet and trace it back through the booking decision to the original Swan transaction and any attached document.
- AI decisions are inspectable, contestable, and convertible. If a user disagrees with how the AI classified something, they correct it once, and the correction becomes a deterministic rule for the future.

This is the feature that lets us truthfully claim *the first AI-assisted books that explain themselves*. It also happens to be the feature that turns a one-off correction by the user into permanent learning by the system, which is good product economics.

## 8. Where AI earns its place

Concretely, we expect AI to be valuable in a small number of well-bounded spots:

- Classifying novel merchants and counterparties on first encounter.
- Reading free-text invoice content (PDF, image) and extracting structured fields.
- Resolving ambiguous transactions where rules conflict or under-specify.
- Translating natural-language management questions into queries over the books.
- Drafting management commentary — "why did travel spend rise in March" — grounded in deterministic numbers.
- Optionally, evaluating spending policies expressed in natural language at the payment-control hook ("no alcohol on the company card, escalate anything over 500€").

Outside these spots, AI is not used. Specifically: AI never computes balances, never produces journal entries directly, never handles VAT arithmetic, never approves transactions for booking without invariant checks passing.

## 9. The technology direction

Choices here are deliberately under-specified. We will pick concretely as we build, and the right choice depends on what we discover in the first few hours. The constraints are:

- The whole system needs to run on one laptop for the demo, with no dependencies that can fail mid-pitch.
- Every monetary value is integer cents. No floats, anywhere, ever.
- Every external call (Swan, AI, anything) is wrapped so it can be mocked for the demo if the network flakes.
- Every AI call has a deterministic fallback path. If the model is slow, wrong, or unreachable, the system degrades to "needs review" rather than booking wrong numbers.

For the database, a single embedded SQLite file is the leading candidate — it's fast enough, transactional, portable, and removes a class of demo-day failure modes that come with networked databases. We may revisit if we hit a feature we genuinely need (vector search at scale, multi-process writes), but for hackathon scope we expect to stay on SQLite.

For the AI layer, we expect to use Claude across multiple model sizes — smaller and faster for high-volume classification, larger for reasoning over documents and conversational queries. Tool-calling with strict schema validation is the default pattern. We aim to keep the AI surface small and well-tested rather than sprinkled everywhere.

## 10. Demo strategy

The demo is the deck. We get one shot, three to five minutes, in front of judges who have seen many AI demos that day.

The opening should be deliberately understated — show the architecture diagram with most boxes gray and a few highlighted, and explain that most of the system is not AI. This earns the credibility for the AI moments to land.

The middle is the live demo: a transaction happens (Event Simulator firing a card swipe or incoming SEPA), and the balance sheet updates visibly within seconds. Click the new entry, show the decision trace. Ask a question to the conversational copilot, get an answer with cited numbers. Optionally, demonstrate a policy-as-prose card rejection.

The close is the strategic frame: this is what AI-native accounting looks like when you take correctness seriously. Live books, audit-ready by construction, getting smarter over time. Not a chatbot wrapping a database — a financial system that happens to have an AI surface.

We do not try to pack everything in. Two or three crisp moments, well-rehearsed, beats a kitchen-sink demo every time.

## 11. Risks and how we treat them

A short list of things we know can go wrong, ordered by how much they'd hurt:

- **Unbalanced books on stage.** A debit-credit mismatch in front of a fintech jury kills credibility. Mitigated by hard invariants asserted in tests and at runtime, integer-cent arithmetic, and a known-good seeded dataset we don't mutate destructively during the demo.
- **AI hallucinating an account that doesn't exist.** Mitigated by constrained tool calls — the model picks from the existing chart of accounts only — and validation of the response before any booking happens.
- **Webhook delivery being unordered or duplicated.** Mitigated by idempotent processing keyed on Swan's event ID.
- **Live demo network failures.** Mitigated by pre-recording a backup demo video and by having the seeded dataset baked into the SQLite file so the demo works offline if needed.
- **Scope creep.** The single biggest risk in a 24-hour build. Mitigated by locking the slice early (see §13) and refusing to add features after hour six.

## 12. What we are not building

A short list, equally important. We are not, in 24 hours, building:

- A general-purpose receipt OCR pipeline. We rely on Claude's vision capabilities for the small number of receipt examples we demo, or skip receipts entirely.
- A complete French Plan Comptable Général. We use a small, demo-relevant subset of accounts.
- Multi-currency support. Euro only.
- Multi-tenancy, user authentication, billing, or any other production-shaped infrastructure.
- A real production AI fall-through with proper retraining loops. The cache-warming story we tell is conceptually right and will be visible in the demo, but the production-grade version is post-hackathon work.

Saying these out loud now keeps us honest when the temptation hits at hour fourteen.

## 13. What we lock in versus what stays open

This briefing is meant to outlast several rounds of iteration. Some things should stay stable across iterations because they shape everything downstream; others are deliberately left open until we have more information.

**Locked:**

- B2B positioning, SME segment, European market.
- The strategic framing: deterministic by default, AI where it earns its place, auditable end-to-end.
- The layered pipeline as the architectural model.
- Swan as the banking rails.
- Integer-cent money handling, hard invariants, AI never touching arithmetic.
- The two-flavor reporting layer: deterministic dashboards plus conversational copilot.

**Open, to be decided as we build:**

- The exact slice of the product we demo (which transaction types, how many employees, how much history, which insights).
- Database, framework, and language choices beyond the leaning-toward-SQLite default.
- How aggressive we get with the policy-as-prose payment-control demo — it's high-impact but high-risk.
- Whether we model receipts at all, or rely on merchant data and cardholder confirmation.
- The exact shape of the conversational copilot — read-only Q&A, or also the ability to take actions like marking an invoice paid.
- The chart of accounts. We'll pick a small set as we go, driven by what our seed transactions need.

## 14. Next steps before the event

Things worth doing before we walk into the venue, in rough order:

- Sign up for Swan's sandbox, get OAuth credentials, verify the API responds.
- Connect Claude Desktop or Claude Code to Swan's two MCP servers.
- Run one full end-to-end test: webhook fires, our code receives it, a journal entry lands in SQLite.
- Sketch the seed dataset — roughly how many transactions, across what time range, what kinds. The demo is only as good as the story this dataset tells.
- Agree on the demo slice and the three crisp moments we want the jury to remember.
- Decide who owns which layer of the pipeline during the build, so we're not fighting over the same files at hour eighteen.

This document gets revised after each of those steps reveals something we didn't know.

---

*Last updated: pre-event draft. Expect revision.*
