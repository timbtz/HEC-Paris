# Architecture Reference — B2B AI Accounting

**Companion to:** Project Briefing (the *why* and *what*)
**Purpose:** Shared mental model for whoever (or whatever) is writing code. This document describes the *shape* of the system without committing to specific implementations. Read alongside the briefing; this doc assumes you've read it.
**Status:** Working draft. Update as decisions firm up.

---

## How to read this document

This is a reference, not a spec. It describes the entities, pipelines, and matching logic the system needs, at the level of abstraction where the right answer is still "it depends." The briefing locks in strategy; this doc maps the surface area; the code makes the concrete choices.

Two principles run through everything below:

- **Three layers, kept separate.** Bank movements (Swan), accounting entries (the GL), and counterparties (the entity layer) are three distinct concerns. They reference each other but they are not the same table, the same identifier space, or the same lifecycle. Conflating them is the most common way fintech systems get into trouble.
- **AI populates caches; rules serve traffic.** Every classification or matching decision should be deterministic the second time it's seen. AI's job is to teach the system, not to run it. If you find yourself routing steady-state traffic through an LLM, step back.

---

## 1. Data sources (ingestion channels)

The system consumes from at least four channels. Each has a different trust profile, latency, and structure. Treat them as separate inputs that converge in the booking layer, not as a single stream.

**Bank rails (Swan).** Webhook-driven, near-real-time, structured. This is ground truth for money movements. Every event carries an `eventId` and Swan delivers at-least-once, so idempotency on that key is non-negotiable. Webhook payloads are deliberately thin — they tell you something happened and you query the API for the full resource. Card transactions carry MCC, merchant ID, and Swan's enriched merchant info; SEPA transactions carry the counterparty IBAN, name, and a `reference` field that the counterparty controls plus an `externalReference` that we control.

**Document inbox.** Inbound supplier invoices and receipts, outbound customer invoices we issued, contracts, and any other PDF/image artifact that carries accounting-relevant information. In production this is email (IMAP, Gmail API, dedicated forwarding addresses) plus user upload. For the hackathon, a watched folder or a mock endpoint is sufficient — the *shape* of what arrives matters more than how it arrived.

**CRM / billing.** The system needs to know who its customers and suppliers are, what's been invoiced, what's expected to be paid, and on what terms. In a real deployment this might integrate with an existing CRM or be the system's own billing module. For the hackathon, this is seeded data plus a small surface for issuing invoices. The key insight is that issued customer invoices create *expected payments* — they're predictions about future Swan events that the matching layer will resolve.

**Configuration / policy.** The chart of accounts, VAT rates per category, employee-to-card mappings, approval thresholds, spending policies, confidence gates. Mostly user-managed, mostly stable, but it's data not code — it should live in tables and be editable without redeploying.

A useful frame: the booking layer is a **join across these four channels**. A SEPA-in event from Swan only becomes "Customer X paid invoice 042" after it joins against an expected payment from the billing channel. A card swipe only becomes "office supplies for Marie" after it joins against the employee-card mapping from configuration. The match is where the value is.

---

## 2. The Swan surface (what the bank rails actually give us)

Worth being concrete about, since downstream design follows from this.

**Transaction subtypes** Swan exposes through its GraphQL `Transaction` interface: `CardTransaction`, `OnlineCardTransaction`, `InPersonCardTransaction`, `SEPACreditTransferTransaction` (in or out, distinguished by `side`), `SEPADirectDebitTransaction`, `InternalCreditTransfer`, `InternalDirectDebitTransaction`, `InternationalCreditTransferTransaction`, `CheckTransaction`, `FeeTransaction`. The hackathon doesn't need to handle all of these — pick a slice — but the code that ingests them should be polymorphic from the start so adding subtypes later is trivial.

**Common fields** every transaction carries: a stable `id`, an `amount` with `value` and `currency`, a `side` (`Debit`/`Credit`), a `type`, a `counterparty` (string — the other party's display name), `paymentMethodIdentifier`, `label`, `reference` (the payment reference; mandatory on SEPA, populated by Swan if empty), `externalReference` (only visible to us — this is the field to lean on for matching outbound payments to invoices), `executionDate`, `statusInfo.status` (Upcoming / Pending / Booked / Rejected / Released / Canceled), and `bookedBalanceAfter`.

**Card-specific fields:** `merchant.category` (the MCC, four-digit ISO 18245 code), `merchant.name`, `merchantId`, `terminalId`, plus `enrichedTransactionInfo` with cleaned-up merchant name, logo, and normalized category. Card transactions also have authorization-then-clearing lifecycle — the same `paymentId` links the auth, the debit, any partial debits, refunds, and chargebacks.

**Webhook events** are namespaced (`transaction.created`, `transaction.updated`, `card.created`, `account.updated`, etc.). Subscribe selectively. The event payload is small; query the resource by ID for full data.

**Payment control hook.** Swan can call our backend during card authorization (1.5s window) for an approve/reject decision. This is an optional but high-impact integration — it's where policy-as-code or policy-as-prose gets real-time teeth.

**What Swan doesn't give us, restated:** no bookkeeping, no chart of accounts, no balance sheet, no VAT, no invoice parsing. All of that is our software.

**The architectural gift:** since `externalReference` is *our* field on outbound transactions, and `reference` is what *we* tell our customers to put on inbound transactions (via virtual IBANs and invoice references), a well-designed invoicing flow gives us deterministic matching on most of the volume — no AI needed for the common case.

---

## 3. The three layers, separated

The system is best understood as three independent layers that reference each other through clear foreign keys.

### Layer A — The bank mirror

A faithful, append-only reflection of what Swan tells us. Raw webhook payloads and normalized transaction rows. This layer is the audit trail for the money side. It does not interpret, classify, or book anything. If the rest of the system burned down, this layer plus Swan's own data would let you reconstruct everything else.

### Layer B — The entity layer (counterparties)

Who the world contains, from our point of view. Customers, suppliers, employees, tax authorities, banks, and any other party we transact with. An entity is identified by *us*, not by Swan or by any single document. An entity has many *identifiers* — IBANs, merchant IDs, MCC patterns, email domains, VAT numbers, internal reference codes — and the matching layer's job is to map an incoming transaction or document to the right entity through those identifiers.

This is also where the matching cache lives. When AI classifies a novel merchant, it doesn't just answer the immediate question — it adds an identifier to the entity, so the next transaction from that merchant resolves deterministically.

A given party can wear multiple hats — a freelancer can be both a supplier and a customer; an employee can be both an internal entity and a counterparty on a reimbursement. Model this as roles or kinds, not as separate tables.

### Layer C — The accounting layer (the GL)

Double-entry bookkeeping over the chart of accounts. Journal entries with balanced debit/credit lines, each line tagged with the GL account it hits, the entity involved (if any), and the source artifacts (Swan transaction ID, document ID) that justify it. This layer is the *books*. The balance sheet, P&L, and every report come from queries over this layer.

Each journal entry should also carry a decision trace — the structured record of how the booking was decided (which rules fired, what the AI said, what the confidence was). The trace lives alongside the entry, not in a separate log.

The critical invariant: **for every journal entry, the sum of debits equals the sum of credits, exactly, in integer cents.** No exceptions. Enforce this at write time, not at report time.

### The bridge between Swan accounts and GL accounts

A Swan "account" is a bank account — a place where money sits, with an IBAN, a balance, and cards attached. In the chart of accounts this is a single asset account (typically `512` Banque or equivalent). One Swan account = one row in `chart_of_accounts`. **Do not** model GL categories (travel, supplies, salaries) as separate Swan accounts. The Swan layer is "where the money is"; the GL layer is "what the money is *for*". Different questions, different shapes.

Virtual IBANs attached to the Swan account are an exception worth thinking about. They're still the same bank account from the GL's perspective (one `512` account), but they're a powerful matching tool — assigning one virtual IBAN per customer means inbound payments arrive pre-matched without any reference parsing. Treat virtual IBANs as identifiers on the customer entity, not as separate GL accounts.

---

## 4. The pipelines

There are several pipelines, not one. Each has a clear input and output and can be developed and tested independently. They share the data layer but they don't share control flow.

### Pipeline 1 — Booking

**Input:** a Swan webhook event.
**Output:** a balanced, posted journal entry, or a queued review item, plus a decision trace.

The pipeline from the briefing (§5). Stages: ingest and verify webhook, normalize the transaction, resolve the counterparty, classify the GL account, generate balanced double-entry, handle VAT, gate on confidence, assert invariants, post.

The critical structural point: classification and matching are *separate* from journal-entry generation. Classification is fuzzy and may use AI. Generation is deterministic and never does. Once the GL account is decided, the contra-entry follows mechanically from a small set of patterns (card-out, SEPA-in, SEPA-out, SDD, internal transfer, fee). Encode each pattern as code; AI is locked out of this stage.

### Pipeline 2 — Document ingestion

**Input:** a PDF or image arriving via email, upload, or folder watch.
**Output:** a structured `documents` row with extracted fields (counterparty, amount, date, line items, VAT, due date, IBAN, reference) and a link to the original file.

This pipeline does not book anything. It produces structured documents that wait to be matched against transactions or to drive their own bookings (e.g., a supplier invoice received but not yet paid creates an accrual entry against `401 Suppliers`).

Extraction is mostly Claude vision with a strict JSON schema for the output. Validate aggressively — if line-item totals don't sum to the invoice total, the document goes to review, not to the books.

### Pipeline 3 — Matching

**Input:** an unmatched transaction and the universe of unmatched documents (or vice versa).
**Output:** a confirmed link with a confidence score, or a queued review item.

The cascade, ordered cheapest-correct-method-first:

1. **Exact reference match.** Swan transaction `reference` or `externalReference` equals an invoice's reference. Free, deterministic.
2. **Identifier match.** Counterparty IBAN matches a known entity, plus amount and date window match an open invoice for that entity.
3. **Fuzzy counterparty plus amount.** Embedding or edit-distance similarity on counterparty name, plus amount match. Catches manual transfers where the reference is sloppy.
4. **AI fallthrough.** Claude gets the transaction and a small set of pre-filtered candidate documents and picks one (or none), with a confidence score.
5. **Review queue.** Anything still unmatched.

Each step that succeeds writes back to the entity layer's identifier cache so the next occurrence is deterministic. Matching is the area where AI's role-as-cache-warmer pays off most visibly.

### Pipeline 4 — Customer invoicing

**Input:** a user action ("issue invoice for X to customer Y").
**Output:** a customer-facing invoice document, an `expected_payment` row predicting the eventual SEPA-in, and a journal entry recognizing revenue and VAT collected.

The expected payment is the linchpin. When pipeline 3 later matches a SEPA-in to it, the AR entry closes automatically. AR aging reports come from querying open expected payments by age bucket.

If the system uses virtual IBANs per customer, the matching is essentially automatic: the SEPA-in arrives on a virtual IBAN that's already linked to the customer entity, and the only remaining question is which of that customer's open invoices it's paying — usually answerable from the amount alone.

### Pipeline 5 — Policy / payment control

**Input:** a Swan card-authorization request via the payment-control hook.
**Output:** approve or reject, within 1.5 seconds, with a logged reason.

Deterministic rules first (per-card limits, MCC blocks, time-of-day, geographic restrictions). Natural-language policies evaluated by a tightly-scoped Claude call only when no deterministic rule fires. Every decision logged with full reasoning. This is the highest-stakes pipeline for latency — the implementation must have a hard timeout and a default-deny fallback if the AI doesn't respond in budget.

### Pipeline 6 — Query / copilot

**Input:** a natural-language question from the user.
**Output:** a textual answer with cited numbers, plus the underlying query so the user can verify.

Claude has read-only tool access to the GL via a small set of well-typed query primitives (or directly via SQL with read-only credentials, depending on how much trust we extend). Claude writes the query, our code executes it, the results come back, Claude frames them in prose. **Claude never computes the numbers itself** — it composes queries, the database does the math. The numbers are correct because they came from SQL; the explanation is fluent because Claude wrote it.

---

## 5. Tables (the data model, sketched)

Five domains. The exact column lists are deliberately not specified here — they'll evolve as the slice firms up. What matters at this stage is which entities exist, how they relate, and which invariants they enforce.

### Domain A — Bank mirror (append-only, Swan-faithful)

- `swan_events` — every webhook payload, raw, keyed on Swan's `eventId` for idempotency. Stores `eventType`, `resourceId`, raw JSON, signature verification status, processing status, timestamps. Never updated; only inserted.
- `swan_transactions` — normalized one row per Swan transaction `id`. All the common fields plus subtype-specific columns (or a JSON column for subtype detail, depending on taste). Updated when Swan emits `transaction.updated` events (status changes).

### Domain B — Entity layer

- `counterparties` — unified suppliers, customers, employees, tax authorities, banks. Has `kind` (or roles, if multi-role), display name, primary contact, status. The entity ID is what the rest of the system references.
- `counterparty_identifiers` — many-to-one against `counterparties`. Each row is one way to recognize this entity from raw data: an IBAN, a Swan merchant ID, an MCC + name pattern, an email domain, a VAT ID, a stripe customer ID, etc. This is the matching cache. Has a `confidence` and a `source` (rule / config / AI / user-confirmed) so the matching layer knows how much to trust each identifier.
- `employees` — the subset of counterparties who hold company cards. Links to Swan card IDs and per-employee policy.

### Domain C — Documents

- `documents` — invoices (issued and received), receipts, contracts, statements. Has `kind`, `direction` (inbound / outbound / internal), structured extracted fields, link to the original file blob, link to the issuing or receiving counterparty.
- `document_line_items` — line-level detail for documents that have it (most invoices). Carries amount, VAT rate, GL account hint.
- `expected_payments` — for outbound issued invoices: who owes us what, by when, on what terms. Status tracks the AR lifecycle (open / partially-paid / paid / overdue / written-off). Closed by the matching pipeline when a SEPA-in matches.

### Domain D — Accounting

- `chart_of_accounts` — GL accounts. Each has a code (e.g., `512`, `401`, `606100`), a name, a `type` (asset / liability / equity / revenue / expense / contra), a parent for hierarchy. For the hackathon a small subset of the French Plan Comptable Général is fine; structure the schema so a fuller chart can be loaded later without migration.
- `journal_entries` — one row per accounting event. Has a date, description, source (which pipeline created it), status (draft / posted / reversed), and a foreign key to its decision trace.
- `journal_lines` — the debits and credits. Each line has `entry_id`, `account_id`, `debit_cents`, `credit_cents` (exactly one of which is nonzero), optional `counterparty_id`, optional `swan_transaction_id`, optional `document_id`. The invariant `SUM(debit_cents) = SUM(credit_cents)` per `entry_id` is enforced at write — ideally via a database trigger or a check at the boundary, never just at read.
- `decision_traces` — structured record of how the booking was decided. Lists rules that fired, cache lookups, AI calls (with confidence and reasoning), the inputs each stage saw. References `entry_id`. This is what makes the books auditable in the way the briefing promises.

### Domain E — Configuration / policy

- `account_rules` — patterns mapping a key (MCC, counterparty ID, IBAN pattern, merchant name regex) to a default GL account. Populated by both manual configuration and AI-learned cache writes. Has a precedence order so more specific rules win.
- `vat_rates` — per GL account or per counterparty, the default VAT rate to apply. Versioned by date so historical entries use the rate that was in force at the time.
- `policies` — natural-language and structured spending policies, scoped per card, per employee, or per company. Used by pipeline 5.
- `confidence_thresholds` — per-customer settings for when a booking auto-posts versus queues for review. Inputs include AI involvement, transaction size, novelty.

---

## 6. The booking patterns that need to exist

A small catalogue of the deterministic templates pipeline 1 generates. Each is a pure function of (Swan transaction, resolved counterparty, matched document if any, classified GL account). The hackathon scope likely covers a subset; the production system covers all of them and more. Listed not to constrain but to make sure none get forgotten when the scope is being chosen.

| Event | Debits | Credits | Source |
|---|---|---|---|
| Customer pays invoice (SEPA in) | Bank | Customer (AR) | Swan + matched expected payment |
| Issue customer invoice (no money yet) | Customer (AR) | Revenue + VAT collected | Pipeline 4 only |
| Pay supplier invoice (SEPA out) | Supplier (AP) | Bank | Swan + matched supplier invoice |
| Receive supplier invoice (not yet paid) | Expense + VAT deductible | Supplier (AP) | Pipeline 2 only |
| Card spend, business expense | Expense + VAT deductible | Bank | Swan card transaction |
| Card spend reimbursable to employee (paid personally) | Expense | Employee receivable | Document pipeline + manual flag |
| Reimburse employee | Employee receivable | Bank | Swan SEPA-out matching reimbursement claim |
| Recurring direct debit (subscription, utility) | Expense + VAT | Bank | Swan SDD, classified by counterparty pattern |
| Salary | Salary expense | Bank (and various social charge accounts) | Swan SEPA-out + payroll source |
| Bank fee | Bank fees expense | Bank | Swan FeeTransaction |
| Manual journal (correction) | any | any | User action with mandatory reason |

The first four cover ~80% of B2B SME volume by transaction count. The next three cover most of the rest. The full list is what a real deployment needs eventually but not what the hackathon needs to demo.

---

## 7. Matching, in detail

The deterministic matching cascade, expressed at the level a coding agent can implement against:

```
on swan_transaction T with status Booked:

  # 1. Resolve counterparty
  cp = None

  if T involves an IBAN (SEPA, SDD, internal, international):
      cp = lookup counterparty by IBAN
      if cp is None and T has a counterparty name:
          cp = fuzzy_match counterparty by name
              (only auto-accept above a high similarity threshold;
               otherwise propose to AI fallthrough)

  if T is a card transaction:
      cp = lookup counterparty by Swan merchant ID
      if cp is None:
          cp = lookup by (merchant name, MCC) pattern in account_rules
      if cp is None:
          cp = ai_classify_counterparty(T)
              # On success, AI writes a counterparty_identifiers row
              # so this merchant is deterministic next time.

  # 2. Match to a document, if applicable
  doc = None

  if T is SEPA-in and cp is a customer:
      doc = match_expected_payment(cp, T.amount, T.reference, T.execution_date)
            (try exact reference first, then amount + date window;
             handle partial payments and over-payments explicitly)

  if T is SEPA-out and cp is a supplier:
      doc = match_open_supplier_invoice(cp, T.amount, T.externalReference, T.execution_date)

  # 3. Classify GL account
  account = rule_lookup(cp, T.mcc, T.type)
            or default_account_for(cp.kind, T.type)
            or ai_classify_account(T, cp, doc)
                # Constrained tool call: model picks from existing chart of accounts only.
                # Validate the response is in the chart before proceeding.

  # 4. Build the journal entry (deterministic, no AI)
  entry = build_entry_for_pattern(T, cp, doc, account)

  # 5. Confidence gate
  if confidence_policy.requires_review(entry, ai_used_in_any_step, T, cp):
      queue_for_review(entry)
      return

  # 6. Post and assert
  post(entry)
  assert sum(entry.debits) == sum(entry.credits)
  assert recorded_bank_balance == swan_reported_balance(T.account)
  if any assertion fails: freeze books, alert
```

The order matters. Counterparty resolution comes before account classification because the counterparty is often the strongest signal for which GL account applies (a supplier we always book to `606100` will get booked there regardless of MCC). Document matching comes between them because a matched document can override the rule-based account (the supplier invoice says it's a software subscription, not generic supplies). Entry generation is dead last and never sees AI.

---

## 8. The AI surface, bounded

Listing every place AI is used, so the surface stays small and reviewable:

- **Counterparty classification** for novel merchants and unrecognized IBANs. Output: a counterparty (existing or new) plus a confidence. Side effect: write to `counterparty_identifiers` so it's deterministic next time.
- **Document extraction** from invoice/receipt PDFs. Output: structured JSON conforming to a strict schema. Side effect: a `documents` row.
- **Account classification** when rules and counterparty defaults don't decide. Constrained tool call, model picks from the existing chart of accounts only. Output validated against the chart before booking.
- **Document-to-transaction matching** in the AI fallthrough step. Pre-filtered candidates, model picks one or none.
- **Natural-language query** — composes SQL or tool calls over the read-only books.
- **Management commentary** — narrates trends grounded in deterministic numbers.
- **Policy evaluation** at the payment-control hook, when the policy is expressed in prose.

Outside these spots, AI is not used. **AI never computes balances, never produces journal entries directly, never handles VAT arithmetic, never approves transactions for booking without invariant checks passing.** This is the guarantee that makes the system defensible.

---

## 9. Cross-cutting concerns

Things that don't belong to any one pipeline but every pipeline has to respect.

**Money is integer cents.** Everywhere. No floats, ever, anywhere in the codebase. Every amount in the database is an integer cents column. Every conversion to decimal happens at the rendering boundary and only there. VAT splits use integer arithmetic with a documented rounding rule (typically: VAT is computed on the gross, rounded half-up to cents; net is gross minus VAT; if line items don't sum exactly, the difference goes to a configured rounding-loss account).

**Idempotency.** Every external event has a stable key (Swan's `eventId`, document file hash, etc.) and the system is safe to re-process the same event any number of times. The natural place to enforce this is at the ingestion layer (refuse to re-insert a known event) and at the booking layer (one journal entry per source, never two).

**Reversibility.** Mistakes happen. Every posted journal entry should be reversible by a counter-entry with a clear link back to the original. Never delete posted entries; reverse them. The audit trail is more important than tidiness.

**External call wrapping.** Every Swan call, every AI call, every webhook delivery is wrapped in a thin abstraction so it can be mocked, replayed from a recording, or short-circuited with a deterministic fallback during a demo. The wrapper logs the call and its response so the decision trace can include them.

**Confidence and fallback.** Every AI call has a deterministic fallback path. If the model is slow, returns malformed output, fails schema validation, or low-confidences below threshold, the pipeline degrades to "needs review" — never to "book it anyway and hope."

**Time.** Transactions have multiple timestamps that don't always agree: when the user initiated, when Swan executed, when the webhook arrived, when we processed. Pick which one is the "accounting date" deliberately and document the choice. (Usually `executionDate`.)

**Currency.** Multi-currency is out of scope for the hackathon. The system should still record `currency` on every amount so the schema doesn't have to change later — just hard-fail anything that isn't EUR for now.

---

## 10. Demo posture and what's deferred

Restating the briefing's scope guardrails in a form that's actionable while building:

**Defer and fake:** real email/IMAP/Gmail integration (folder watcher is fine), production-grade OAuth flows for any external service, multi-tenancy and user auth, billing of the system itself, multi-currency, full French PCG, comprehensive receipt OCR (cover the few demo cases, skip the long tail), retraining loops on AI corrections (the user-correction-to-rule writeback is the hackathon-credible version).

**Do not defer:** integer-cent arithmetic, idempotency on Swan events, decision traces on every booking, the invariant checks (debits=credits, our-balance=Swan's-balance), the deterministic-rules-first cascade. These are what make the system credible. They cost little to build right and they're essentially impossible to retrofit.

**Demo-time risks worth pre-mitigating:**
- Network failures during demo → seeded SQLite the demo can run from offline; pre-recorded backup video.
- AI hallucinating an invalid GL account → constrained tool calls, validation against the chart, queue-for-review on validation failure.
- Webhook ordering or duplication → idempotent ingestion, status-driven processing (only book on `Booked`, not on `Pending`).
- Floating-point bugs surfacing on stage → no floats, anywhere, ever.

---

## 11. What's locked, what's open

Mirrors the briefing but at the implementation layer, so the coding agent knows where it has discretion and where it doesn't.

**Locked at the architecture level:**
- Three layers (bank mirror / entity / GL) kept separate.
- Pipelines as separate components with clear inputs and outputs.
- Integer-cent money handling.
- Deterministic rules first; AI as cache-warmer; AI never in arithmetic or entry generation.
- Decision traces alongside every entry.
- Idempotent ingestion keyed on stable external IDs.
- Hard invariants asserted on every post.

**Open, decide while building:**
- Database engine (SQLite leading, but the schema should be portable).
- Language and framework.
- Whether to model receipts at all in the demo, or rely on Swan merchant data and the SEPA reference field.
- How much of pipeline 5 (policy / payment control) actually ships in the demo.
- How much of pipeline 6 (copilot) is read-only Q&A versus also action-taking.
- The exact subset of the chart of accounts.
- The shape of the seed dataset — driven by the demo story, which is itself still being chosen.
- Whether matching uses embeddings, edit distance, or both, for fuzzy counterparty resolution.

---

## 12. Glossary

- **GL** — general ledger. The system of double-entry accounts that constitutes the books.
- **AR / AP** — accounts receivable (money customers owe us) / accounts payable (money we owe suppliers).
- **PCG** — Plan Comptable Général, the French standardized chart of accounts.
- **MCC** — Merchant Category Code. Four-digit ISO 18245 code carried on every card transaction identifying the merchant's industry.
- **SEPA** — Single Euro Payments Area. The European banking interchange system; SCT is credit transfers, SDD is direct debits, SCT Inst is instant transfers.
- **IBAN** — International Bank Account Number. Identifies an account.
- **Virtual IBAN** — an IBAN routed to an underlying real account, useful for assigning per-customer payment addresses without managing per-customer real accounts.
- **Decision trace** — the structured record of how a booking was decided. Lives next to the journal entry.
- **Cache (in this system)** — the `counterparty_identifiers` and `account_rules` tables, populated by AI and rules, serving steady-state traffic deterministically.
- **Confidence gate** — the policy that decides whether a booking auto-posts or queues for review.

---

*Last updated: pre-event draft. Update as decisions firm up and as the slice narrows.*
