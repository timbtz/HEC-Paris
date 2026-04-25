# Swan Integration — How to Leverage Swan in the B2B Accounting Plan

**Audience:** future Claude Code sessions implementing against Swan.
**Companion docs:** read in this order before writing code —
1. `Dev orchestration/swan/SWAN_API_REFERENCE.md` — exact field names, mutation contracts, webhook contract.
2. `architecure.md` — the three-layer model and the pipelines.
3. `projectbriefing.md` — the *why*.
4. This file — how the two map together for our specific plan.

**Status:** working draft, 2026-04-25.

---

## TL;DR — the mental model in one paragraph

Swan gives you **one bank account per legal entity** (your company). That single account has one IBAN, one balance, and many cards attached through "memberships". Cards are **not** separate balances — they are spending instruments on the shared account, gated by per-card limits. "Each employee has their own credit" is implemented as `Card + SpendingLimit + payment-control hook`, **not** as a sub-account. The money is one pool; the entitlements are per-card.

Inbound (customers paying you) and outbound (you paying suppliers) are two different transaction subtypes on that same account, with two different matching strategies.

---

## 1. Per-employee cards with their own monthly budget

Three Swan objects line up:

| Object | Role | Reference |
|---|---|---|
| `Account` | The company's single bank account. One IBAN, one booked balance. | `SWAN_API_REFERENCE.md` §2.2 |
| `AccountMembership` | Links a `User` (employee) to the account, with explicit booleans (`canInitiatePayments`, `canManageCards`, …) and `spendingLimits`. Swan's RBAC model. | §2.3 |
| `Card` | Issued *against a membership*, with its own `spendingLimits: [SpendingLimit!]`. Each limit has `period: Daily \| Weekly \| Monthly \| Always`, an `amount`, and a `type: AccountHolder \| Partner` (Partner-set wins). | §2.4 |

### Recipe — "Marie has €500/month for office supplies"

1. `addAccountMembership` → creates Marie's membership on the company account.
2. `addCard` with `spendingLimit: { period: Monthly, amount: { value: "500.00", currency: "EUR" } }` → her card.
3. **Real "credit" enforcement happens in the payment-control hook** (Pipeline 5 in `architecure.md`). Swan calls our backend on every card authorization within a **1.5s budget** with `cardId`, `amountValue`, `merchantCategoryCode`, etc. Our code checks "is Marie under her monthly budget? is this MCC allowed?" and returns `{accepted: true/false}`. (`SWAN_API_REFERENCE.md` §8)

### Where the monthly budget lives — two layers

- **Swan's native `SpendingLimit`** — Swan enforces it directly. Hard cap, simple.
- **Our DB (`policies` table — `architecure.md` §5 Domain E)** — we enforce via the payment-control hook. More flexible: per-MCC budgets, "groceries vs travel", manager-approval thresholds, AI-credit tracking. **This is the wedge of the pitch.**

Swan handles the rails; our policy engine handles the meaning.

---

## 2. Manager card for the B2B firm

Same machinery, different membership:

- The **legal representative** (founder/manager) is the `AccountMembership` row marked `legalRepresentative: true, canInitiatePayments: true, canManageCards: true`. Only this membership can issue new cards or send SEPA — and SEPA still triggers SCA (`SWAN_API_REFERENCE.md` §6).
- A manager also gets their own `Card`, possibly with a higher `SpendingLimit` and broader MCC allow-list, but it's **structurally the same object** as Marie's — just different limits.
- "B2B" isn't a Swan concept — it's just that the underlying `AccountHolder` is a *company* (`AccountHolderCompanyInfo`, with `registrationNumber`, `vatNumber`, `businessActivity`) rather than an individual.

So manager-card vs. employee-card is **purely policy on top of identical primitives**. Swan doesn't care; our `policies` table does.

---

## 3. Supplier wants money for supplies — paying out

This is a **SEPA Credit Transfer outbound** — `SEPACreditTransferTransaction`, side `Debit`. Two phases:

### Phase A — initiate (mutation)

`initiateCreditTransfers` (`SWAN_API_REFERENCE.md` §4.1). The critical field is **`externalReference`** — *our* string that round-trips back on the resulting transaction.

- Set it to e.g. `inv:<invoice_id>:try:1`.
- This is the "architectural gift" called out in `architecure.md` §2: because *we* set it, *we* can deterministically reverse-lookup which supplier invoice this payment corresponds to. **No AI needed.**
- Plus `idempotencyKey` so retries don't double-spend.

### Phase B — match on receipt (Pipeline 1 + Pipeline 3)

Webhook `Transaction.Booked` arrives. Query the transaction by id, see `externalReference: "inv:042:try:1"`, look up invoice 042 in `documents` / `expected_payments`, and produce the journal entry:

```
Debit  401 Suppliers   (the AP for this supplier)
Credit 512 Banque      (the Swan account)
```

### Fallback cascade (when `externalReference` is missing — e.g. someone paid out-of-band)

1. Match by **counterparty IBAN** (the supplier's IBAN is a known identifier in `counterparty_identifiers`).
2. Match by **amount + date window**.
3. AI fallthrough.
4. Review queue.

(`architecure.md` §7.)

---

## 4. Customer pays you for a product — receiving

This is a **SEPA Credit Transfer inbound** — same `SEPACreditTransferTransaction` type but side `Credit`. Matching is harder because *the customer* controls what they put in the SEPA `reference` field, and they often get it wrong.

### The clean architectural play — virtual IBAN per customer

Issue one virtual IBAN per customer via `addVirtualIbanEntry` (`SWAN_API_REFERENCE.md` §2.6, §4.2). A virtual IBAN routes incoming SEPA to the same real account, but it's a *separate IBAN* labeled with the customer's id.

- Customer Acme pays to `FR76...VIRTUAL_FOR_ACME`. Webhook fires. We see the virtual IBAN, look it up in `counterparty_identifiers` → **pre-keyed to Acme**. Counterparty resolution is instant and deterministic — no AI, no name fuzzing, no reference parsing.
- Then match against Acme's open `expected_payments` (created when we issued the invoice in Pipeline 4). Usually amount alone disambiguates.

### Journal entry on the inbound match

```
Debit  512 Banque        (the Swan account — money came in)
Credit 411 Customers     (the AR for Acme — invoice closes)
```

### The full lifecycle (issuance → payment)

Pipeline 4 (customer invoicing) posts the accrual at issuance:

```
Debit  411 Customers     (AR — Acme owes us)
Credit 706 Revenue
Credit 4457 VAT collected
```

…and creates the `expected_payment` row. The inbound SEPA match closes the AR. **AR aging reports come from querying open expected_payments by age bucket.**

---

## 5. The differences — side by side

| Thing | Card spend (employee/manager) | Supplier payment out | Customer payment in |
|---|---|---|---|
| **Swan transaction subtype** | `CardTransaction` (`InPersonCardTransaction` / `OnlineCardTransaction`) | `SEPACreditTransferTransaction`, side `Debit` | `SEPACreditTransferTransaction`, side `Credit` |
| **Triggered by** | Card swipe / online checkout | `initiateCreditTransfers` mutation (SCA gated) | Customer's bank pushes SEPA to our IBAN |
| **Real-time gate** | Payment-control hook (1.5s) | SCA consent flow | None — we just receive |
| **Matching key** | `merchantId` + MCC → counterparty cache | `externalReference` (we set it) | Virtual IBAN → customer (or `reference` field) |
| **Document link** | Receipt (Pipeline 2 if uploaded) | Supplier invoice in `documents` | `expected_payment` from invoice we issued |
| **Counterparty type** | Merchant (supplier-by-card) | Supplier | Customer |
| **Per-employee tracking** | `cardId` on the transaction → membership → employee | Approver / initiator on the payment intent | N/A (customer pays the company, not a person) |
| **Default GL pattern** | `Debit Expense + VAT / Credit Bank` | `Debit Supplier (AP) / Credit Bank` | `Debit Bank / Credit Customer (AR)` |

---

## 6. How Swan operates — one-screen summary

- **Swan is a Banking-as-a-Service partner.** They hold the e-money license, run the SEPA rails, issue cards via Mastercard, and expose a single GraphQL API that wraps all of it. (`SWAN_API_REFERENCE.md` §0)
- **One project**, with one or many `AccountHolder`s (legal entities), each with one or many `Account`s (IBANs), each with many `AccountMembership`s (employees), each with many `Card`s.
- **Money movements are one pool** at the `Account` level; cards are entitlements with limits, not separate balances.
- **Webhooks deliver thin envelopes** (`{eventType, eventId, resourceId}`) — re-query for state, idempotent on `eventId`, at-least-once, no order guarantee. (§7)
- **Outbound payments via mutations** that trigger SCA (the legal rep approves in Swan's hosted UI). (§4.1, §6)
- **Swan never does bookkeeping** — no chart of accounts, no VAT, no journal entries. That's all our software. The booking layer (`architecure.md` §3) is the join across Swan transactions, our CRM (expected payments), and our document inbox (invoices/receipts). The matching cascade (deterministic first, AI as cache-warmer) is what makes it cheap and auditable.

---

## 7. The demo wedge — per-employee budgets + AI-credit tracking

The payment-control hook (1.5s budget, called on every card auth) is where the pitch comes alive:

- **"Marie has €50 left for SaaS this month"** → real-time approve/reject decision, deterministic rule from our `policies` table.
- **AI-API spend** (e.g. Anthropic, OpenAI charges hitting an employee card) → intercepted at auth, attributed to the project consuming it, gated against a per-project budget, all in 1.5 seconds.
- Every decision logged with full reasoning into `decision_traces` — same audit substrate as journal entries.

Swan handles the rails; our policy engine + the payment-control hook are the wedge.

---

## 8. Integration plan — a suggested order

1. **Get auth working.** The `client_secret` shipped with the project returns 401; regenerate from Dashboard → Developers → API and load into `.env.local` as `SWAN_CLIENT_SECRET`. Cache the token in-process; refresh on 401 or 60s before TTL.
2. **Wrap every Swan call** in a thin client (`swan_client.transaction(id)`, `swan_client.initiate_credit_transfer(...)`) so it can be mocked, replayed, or short-circuited during the demo.
3. **Bootstrap query** — list account holders, accounts, memberships to confirm the seed data:
   ```graphql
   query Bootstrap {
     accountHolders(first: 10) {
       edges { node {
         id info { __typename name }
         accounts(first: 10) { edges { node { id IBAN paymentLevel } } }
       } }
     }
   }
   ```
4. **Subscribe to webhooks** (`addWebhookSubscription`) for `Transaction.Booked`, `Transaction.Pending`, `Transaction.Settled`, `Card.Created`, `Account.Updated`, `Consent.Granted`. Set a fresh UUIDv4 `secret`, store it as `SWAN_WEBHOOK_SECRET`.
5. **Build Pipeline 1 (booking)** — webhook receiver → verify secret (constant-time) → insert `swan_events` (idempotent on `eventId`) → enqueue → background worker re-queries the transaction → resolves counterparty → classifies GL → posts balanced double-entry → asserts invariants.
6. **Issue virtual IBANs** for seed customers via `addVirtualIbanEntry`. Cache `IBAN → counterparty_id`.
7. **Build Pipeline 5 (payment-control)** — register the URL, implement the deterministic-rules-first handler with hard timeout and default-deny on internal failure.
8. **Demo path:** use the sandbox Event Simulator (Dashboard → Developers → Event Simulator) or `simulateIncomingSepaCreditTransferReception` (admin endpoint) to fire deterministic events. **Do not put live SCA in front of judges.**

---

## 9. Hard rules — non-negotiable

These are the rules from `Dev orchestration/swan/CLAUDE.md` reproduced for context. Deviations need a paragraph in the PR description.

### Money
- Every monetary value is `Amount { value: String, currency: String }`. Convert to integer cents at ingestion: `int_cents = int(round(Decimal(value) * 100))`.
- **Never use float on money.** No exceptions.

### Idempotency
- Webhook handlers idempotent on `eventId` (unique constraint in `swan_events`).
- Outbound payments pass `idempotencyKey` to `initiateCreditTransfers`. Re-runs without it = double-spends.
- Posted journal entries idempotent on `(swan_transaction_id, pipeline_version)`.

### Booking
- **Only post on `Booked` or `Settled`.** Never on `Pending` (card auths) or `Upcoming` (scheduled SCT).
- Use `BookedTransactionStatusInfo.bookingDate` as entry date, `valueDate` as value date.
- On `Released` or `Canceled` after a prior post, **reverse** with a counter-entry. Never delete.
- Capture every rule fired, every cache hit, every AI call (with confidence) into `decision_traces`.

### Webhooks
- `POST application/json`, return `200` within **10 seconds**, never block.
- Verify `x-swan-secret` with **constant-time** equality. Reject with 401.
- The envelope is *only* `{eventType, eventId, eventDate, projectId, resourceId}` — **always re-query** the resource by id.
- Order is **not guaranteed**, same `eventId` may arrive multiple times. Handlers must be idempotent and tolerant of out-of-order delivery.
- Process out-of-band: insert raw event, return 200, enqueue background work.
- Source-IP allowlist (sandbox + live, same): `52.210.172.90`, `52.51.125.72`, `54.194.47.212`.

### Mutation error handling
- **Errors come back as union members, not in GraphQL `errors`.** Always select `__typename` and switch on it. Failure types implement `interface Rejection { message: String! }`.
- Top-level GraphQL `errors` = internal/system fault — log, retry with backoff.

### SCA / consent
- Mutations taking `consentRedirectUrl: String!` trigger SCA. Don't bypass on the demo path; either skip, use sandbox-admin, or feature-flag the SCA UI.
- Don't initiate SCA flows in front of the jury. Pre-record or use admin shortcuts.

### Matching keys
- On `initiateCreditTransfers`, set `externalReference` to a value our system can deterministically reverse-lookup (e.g. `inv:<invoice_id>:try:<n>`).
- For inbound, prefer **virtual IBANs per customer** — matching becomes deterministic without parsing references.

### Don'ts
- Don't query Swan from the browser. Backend-only.
- Don't model GL categories as separate Swan accounts. One Swan account = one row in `chart_of_accounts.512`.
- Don't book on `Pending` or `Upcoming`.
- Don't compute money in floats anywhere in the call chain.
- Don't share OAuth `client_secret`, webhook secret, or payment-control secret across environments.
- Don't use `addAccountMembership` / `addCard` in code paths without a real user (require SCA, will hang).
- Don't block longer than 1.5s in the payment-control handler (live).
- Don't paginate without `first` (max 100).

---

## 10. Environment variables — canonical names

| Var | Purpose |
|---|---|
| `SWAN_CLIENT_ID` | OAuth client id, e.g. `SANDBOX_<uuid>` |
| `SWAN_CLIENT_SECRET` | OAuth client secret |
| `SWAN_GRAPHQL_URL` | `https://api.swan.io/sandbox-partner/graphql` |
| `SWAN_GRAPHQL_ADMIN_URL` | `https://api.swan.io/sandbox-partner-admin/graphql` |
| `SWAN_OAUTH_URL` | `https://oauth.swan.io/oauth2/token` |
| `SWAN_WEBHOOK_SECRET` | Shared secret for the main webhook subscription |
| `SWAN_PAYMENT_CONTROL_SECRET` | Shared secret for the payment-control hook |
| `SWAN_PROJECT_ID` | Project id (also returned as `projectId` on every event) |

Live values for our project are in `.env.local` at the repo root (gitignored). The current `SWAN_CLIENT_SECRET` placeholder needs regeneration from Dashboard → Developers → API.

---

## 11. Architecture mapping — quick reference

| Architecture concept (`architecure.md`) | Swan reality |
|---|---|
| Layer A: bank mirror (`swan_events`) | Webhook envelope, keyed on `eventId`, idempotent insert |
| Layer A: `swan_transactions` | Result of `transaction(id)` GraphQL query, normalized |
| Layer B: `counterparty_identifiers` | (a) `Account.IBAN` of inbound SEPA debtor — exact; (b) `merchantId` — exact; (c) `(MCC, enriched name)` — fuzzy. Each carries `confidence` + `source`. |
| Layer B: virtual IBANs per customer | `addVirtualIbanEntry` per customer; cache `IBAN → counterparty_id` |
| Pipeline 1 (booking) | Webhook → verify → insert `swan_events` → enqueue → query → normalize → resolve → classify → build entry → confidence gate → post |
| Pipeline 5 (payment control) | Synchronous hook → 1.5s budget → deterministic rules → optional bounded Claude call → `{accepted}` |
| Booking date | `executionDate` on the transaction; `bookingDate` from `BookedTransactionStatusInfo` |
| Idempotency key | `eventId` on webhooks; `idempotencyKey` on `initiateCreditTransfers` |
| Money | `Amount.value` (string-decimal) → integer cents at the boundary |
| Outbound matching | `externalReference` we set, round-trips on the resulting transaction → match against open AP |
| Inbound matching | Tier 1: virtual IBAN → customer entity. Tier 2: `reference` field → invoice id. Tier 3: amount + counterparty fuzzy |
| Decision trace | Raw payload + GraphQL response + every rule fired + every cache hit + AI calls (confidence + reasoning), joined to journal entry |
| Invariants on post | (1) `SUM(debits) = SUM(credits)` per entry. (2) Our recorded balance = `Account.balances.booked` returned from GraphQL. |

---

*Last updated: 2026-04-25. Update when integration decisions firm up.*
