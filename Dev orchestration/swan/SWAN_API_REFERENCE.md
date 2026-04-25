# Swan API — Reference & Integration Guide

**Status:** working draft, 2026-04-25 (Paris Fintech Hackathon).
**Audience:** future coding sessions implementing the B2B accounting product against Swan.
**Scope:** everything needed to connect, authenticate, ingest events, and book transactions. Field names quoted here are from the live `sandbox-partner` GraphQL introspection (full SDL at `swan/_probes/swan_schema_full.json.gz`) and from Swan's public docs. Anything unverifiable is marked `(verify)`.

This document is the bridge between `architecure.md`/`briefing.md` (which describe the system we're building) and the actual Swan API surface. Read those two first.

---

## 0. TL;DR — the 30-second model

- **Endpoint:** `POST https://api.swan.io/sandbox-partner/graphql` (one URL, all operations are GraphQL queries or mutations).
- **Auth:** OAuth2 `client_credentials` against `https://oauth.swan.io/oauth2/token` → `Bearer` token, TTL **3600s**, scoped to the entire **project** (every account holder, account, card, transaction in it).
- **GraphQL surface:** **62 top-level queries**, **137 mutations**, **10 transaction subtypes** under one `Transaction` interface, no GraphQL subscriptions (Swan uses webhooks instead).
- **Webhooks:** thin envelope `{eventType, eventId, eventDate, projectId, resourceId}` → re-query GraphQL for state. Signed by **shared-secret equality** on the `x-swan-secret` header (no HMAC). At-least-once, out-of-order, 30s flat retry × 7. Endpoint must respond `200` within **10s**.
- **Payment-control hook:** synchronous HTTPS callback during card authorization. **1.5s** budget live, **10s** in sandbox. Returns `{accepted: bool, partialAuthorizationAmountValue?: number}`. Default-on-timeout configured per project.
- **Money model:** `Amount { value: AmountValue, currency: Currency }`. `AmountValue` is a `String` scalar holding a decimal. Parse to **integer cents** at the boundary; never keep floats.
- **`externalReference` is our field** on outbound transactions — the cleanest matching key for paying-supplier-invoices.
- **Virtual IBANs** are the cleanest matching key for incoming-customer-payments — issue one per customer.
- **Status invariant:** only **book** journal entries on `Booked` or `Settled`. Never on `Pending` or `Upcoming`.

---

## 1. Authentication

### 1.1 Project access token (`client_credentials`) — the back-end token

For everything our orchestration layer does (reading transactions, sending SCT, subscribing to webhooks, querying balances), use a project access token. This is the only token the backend needs.

```bash
curl -X POST https://oauth.swan.io/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=$SWAN_CLIENT_ID" \
  --data-urlencode "client_secret=$SWAN_CLIENT_SECRET"
```

Response:
```json
{ "access_token": "<token>", "token_type": "bearer", "expires_in": 3600, "scope": "" }
```

**Don't send a `scope` parameter.** Project tokens authorise on project membership, not on scopes. Swan returns `"scope": ""` by design.

**Auth method:** `client_secret_post` (credentials in the form body) or `client_secret_basic` (HTTP Basic) — Swan supports both, but pick **one** per request, not both.

**Credential format:** the `client_id` looks like `SANDBOX_<uuid>` for sandbox or `LIVE_<uuid>` for production. Send the full literal — the prefix is part of the id. The `client_secret` is a UUID. Both values come from **Dashboard → Developers → API**.

### 1.2 User access token (`authorization_code`) — only if a real user must consent

Some operations (`addCard`, `initiateCreditTransfers`, `updateCard`, `printPhysicalCard`, `addAccountMembership`, …) require **strong customer authentication (SCA)** — they take a `consentRedirectUrl: String!` and Swan redirects the human through the OAuth UI. The user-token flow only matters when *we* impersonate a user; for the partner-server flow Swan generates the consent URL automatically and sends the user there. See §6.

### 1.3 Bearer-token usage

```
POST https://api.swan.io/sandbox-partner/graphql
Authorization: Bearer <access_token>
Content-Type: application/json

{"query":"...","variables":{...}}
```

- TTL: **3600s** (1 hour). Refresh by re-running `client_credentials`. There is no refresh token for the `client_credentials` grant.
- One project token spans the entire project — every `AccountHolder`, every `Account`, every `Card`. **No per-account scoping required.**
- Rate limits: **2,000 requests / 5 minutes per IP**, complexity ≤ 1,000 fields, depth ≤ 15, ≤ 5 top-level fields per query.

### 1.4 Endpoints by environment

| Environment | GraphQL endpoint |
|---|---|
| Sandbox partner | `https://api.swan.io/sandbox-partner/graphql` ← we are here |
| Sandbox admin (mint test data, impersonate users) | `https://api.swan.io/sandbox-partner-admin/graphql` |
| Live partner | `https://api.swan.io/live-partner/graphql` |

OAuth host is the same `https://oauth.swan.io/oauth2/token` for sandbox and live; the environment is determined by which project the `client_id` belongs to.

### 1.5 Credential status as of 2026-04-25

The `client_id` provided to the team starts with `SANDBOX_` — the literal `SANDBOX_` prefix is part of the id, send it verbatim. The accompanying secret was empirically rejected with `401 invalid_client` across `client_secret_post`, `client_secret_basic`, with and without `scope=openid`, with and without the prefix. OpenID Connect discovery (`https://oauth.swan.io/.well-known/openid-configuration`) confirms the endpoint and supported grants are correct.

**Fix path:** open Dashboard → Developers → API, click **Regenerate** on the Client Secret, copy the new value (only shown once, in the post-regenerate modal), and load both into `.env.local` as `SWAN_CLIENT_ID` and `SWAN_CLIENT_SECRET`. Per Swan's troubleshooting: *"your client secret might be invalid. Generate a new secret on your Dashboard, then try again."*

Public schema introspection is open and works without auth — that is how the SDL in `_probes/swan_schema_full.json.gz` was captured. Any state-touching call (e.g. `projectInfo`, `webhookEventTypes`, anything reading account data) returns `Forbidden` until we have a valid token.

### 1.6 The two MCP servers

Swan publishes two remote MCP servers compatible with Claude Code / Claude Desktop:

```bash
claude mcp add --transport http swan-graphql https://mcp.swan.io/graphql/mcp
claude mcp add --transport http swan-docs    https://mcp.swan.io/docs/mcp
```

- `swan-graphql` exposes `search-graphql-schema` and `introspect-graphql-type` — useful when building queries.
- `swan-docs` exposes `search-documentation` — useful for prose context.

These do **not** replace authenticated API calls. They help Claude Code reason about the schema, that's it.

---

## 2. The data model — what Swan exposes

Swan's GraphQL surface decomposes into seven entity families. Field names below are exact (from live introspection).

### 2.1 `AccountHolder` — the legal entity

The legal owner of one or more accounts. Either a company or an individual. Fields worth caring about:
- `id: ID!`
- `info: AccountHolderInfo!` — interface, concrete `AccountHolderCompanyInfo` or `AccountHolderIndividualInfo` (see end of doc).
- `verificationStatus: VerificationStatus!` (`NotStarted | WaitingForInformation | Pending | Verified | Refused`)
- `accounts(first, after, ...): AccountConnection!`
- `paymentMandates(...): PaymentMandateConnection`
- `onboarding: Onboarding`
- `riskInfo: AccountHolderRiskInfo`
- `fundingLimitSettings: FundingLimitSettings`

### 2.2 `Account` — the bank account

The thing that has an IBAN, a balance, and a list of transactions. **One Swan account = one row in our `chart_of_accounts` (typically `512` Banque).** Don't model GL categories as separate Swan accounts — see `architecure.md` §3.

```
Account {
  id: ID!
  number: AccountNumber!         # the local account number
  IBAN: IBAN                     # the IBAN (scalar)
  BIC: BIC!
  name: String!
  holder: AccountHolder!
  paymentLevel: PaymentLevel!    # Limited | Unlimited
  paymentAccountType: PaymentAccountType!
  cashAccountType: CashAccountType!
  country: AccountCountry!       # FRA | DEU | ESP | NLD | ITA | BEL
  currency: Currency!
  language: AccountLanguage!
  statusInfo: AccountStatusInfo!  # Opened | Suspended | Closing | Closed
  balances: AccountBalances
  bankDetails: String

  # connections (paginated)
  virtualIbanEntries(...): VirtualIBANEntryConnection!
  memberships(...): AccountMembershipConnection!
  transactions(first: Int!, after: String, orderBy, filters): TransactionConnection
  statements(...): StatementConnection
  standingOrders(...): StandingOrderConnection!
  fundingSources(...): FundingSourceConnection
  trustedBeneficiaries(...): TrustedBeneficiaryConnection!
  receivedDirectDebitMandates(...): ReceivedDirectDebitMandateConnection
  merchantProfiles(...): MerchantProfileConnection
}

AccountBalances { available: Amount!, pending: Amount!, booked: Amount!, reserved: Amount! }
```

`paymentLevel` matters: `Limited` accounts have a regulatory cap (typically €5k / month outbound) until the holder completes full identification; `Unlimited` accounts have no Swan-imposed cap. Check this before initiating a large transfer.

### 2.3 `AccountMembership` — the link between a `User` and an `Account`

This is Swan's RBAC model. One row per (user, account) pair, with explicit boolean permissions:
```
AccountMembership {
  id: ID!
  email: String!
  user: User
  account: Account
  legalRepresentative: Boolean!
  canViewAccount: Boolean!
  canManageBeneficiaries: Boolean!
  canInitiatePayments: Boolean!
  canManageAccountMembership: Boolean!
  canManageCards: Boolean!
  spendingLimits: [SpendingLimit!]
  cards(...): CardConnection!
  statusInfo: AccountMembershipStatusInfo!
  accountHolderType: AccountHolderType!
}
```

For a company, the **legal representative** is the human who signed up. Other employees become `AccountMembership` rows via `addAccountMembership` and get cards via `addCard` / `addCards`.

### 2.4 `Card` and friends

```
Card {
  id: ID!
  type: CardType!              # Virtual | VirtualAndPhysical | SingleUseVirtual
  accountMembership: AccountMembership!
  cardProduct: CardProduct!
  mainCurrency: Currency!
  cardMaskedNumber: String!    # e.g. "4***...1234" — never the full PAN
  expiryDate: String
  name: String                 # cardholder name embossed
  withdrawal: Boolean!         # ATM allowed
  international: Boolean!      # outside EEA allowed
  nonMainCurrencyTransactions: Boolean!
  eCommerce: Boolean!
  spending: Spending           # MTD spend
  spendingLimits: [SpendingLimit!]
  physicalCard: PhysicalCard   # nil if virtual-only
  digitalCards(...): DigitalCardConnection!  # Apple Pay / Google Pay tokens
  cardDesignUrl: String!
  statusInfo: CardStatusInfo!  # ConsentPending | Processing | Enabled | Canceling | Canceled
  transactions(...): TransactionConnection
}

SpendingLimit { type: SpendingLimitType, period: SpendingLimitPeriod, amount: Amount!, mode: SpendingLimitMode }
# period: Daily | Weekly | Monthly | Always
# type:   AccountHolder | Partner   (Partner-set limit takes precedence)
```

Card transactions you'll see come back as `CardTransaction`, `OnlineCardTransaction`, or `InPersonCardTransaction` (see §3).

### 2.5 `Transaction` — the heart of the system (§3 has the detail)

Swan's `Transaction` is a GraphQL **interface**. Every concrete subtype carries the common fields below plus subtype extras. Receiving a webhook for `Transaction.Booked` and querying `transaction(id: $id) { ...common, ...on CardTransaction { merchant { ... } } }` is the canonical ingestion shape.

Common (interface) fields:
- `id: ID!` — Swan's stable identifier.
- `reference: String!` — the payment reference. On SEPA: filled by the counterparty (or auto-generated by Swan if empty).
- `externalReference: String` — **our** field. Round-trips on outbound payments we initiated; we use this to match to invoices.
- `paymentMethodIdentifier: String!` — bank-rail identifier (e.g. SEPA E2E).
- `side: TransactionSide!` — `Debit | Credit`.
- `type: TransactionTypeEnum!` — 42 enum values, see §10.
- `amount: Amount!` — `{value: AmountValue (string-decimal), currency: Currency}`.
- `label: String!`
- `statusInfo: TransactionStatusInfo!` — polymorphic, see §3.4.
- `paymentId: String`, `payment: Payment` — links auth → debit → reversal lifecycle.
- `paymentProduct: PaymentProduct!` — the rail (`Card | InPersonCard | OnlineCard | SEPACreditTransfer | SEPADirectDebit | InternalCreditTransfer | InternalDirectDebit | InternationalCreditTransfer | Check | Fees`).
- `counterparty: String!` — display name of the other party.
- `bookedBalanceAfter: Amount` — running balance after this transaction (only meaningful when `Booked`).
- `executionDate: DateTime!` — **the accounting date** (use this, not `createdAt`).
- `requestedExecutionAt: DateTime` — for scheduled/upcoming transactions, the requested date.
- `createdAt: DateTime!`, `updatedAt: DateTime!` — Swan's record timestamps.
- `originTransactionId: String`, `originTransaction: Transaction` — for returns/recalls/reversals, points at the original.
- `account: Account` — the account this hit.
- `projectId: ID!`
- `isCancelable: Boolean!` — true for `Upcoming` SCT until cutoff.
- `statementCanBeGenerated: Boolean`.
- `supportingDocumentCollections(...): SupportingDocumentCollectionConnection!` — receipts/invoices uploaded against this transaction.

### 2.6 `VirtualIBANEntry` — per-customer matching

```
VirtualIBANEntry {
  id: ID!
  IBAN: IBAN!
  BIC: BIC!
  label: String
  status: IBANStatus!     # Enabled | Suspended | Canceled
  blockSDD: Boolean!
  bankDetails: String
}
```

A virtual IBAN is a separate IBAN that routes incoming payments to the same underlying real account. **Architectural play:** issue one virtual IBAN per customer, label it with the customer's id, and the matching layer becomes trivial — every inbound SEPA arrives pre-keyed to the customer entity. See `architecure.md` §2 ("the architectural gift").

### 2.7 `WebhookSubscription` and `WebhookEventLog`

```
WebhookSubscription {
  id: ID!
  label: String!
  endpoint: String!         # our URL Swan POSTs to
  secret: String            # the shared secret echoed back as x-swan-secret
  eventTypes: [String!]!    # e.g. ["Transaction.Booked", "Card.Created", ...]
  statusInfo: WebhookSubscriptionStatusInfo!  # Enabled | Disabled | Broken
  kpi: WebhookSubscriptionKpi
  webhookEventLogs(first, after, filters): WebhookEventLogsConnection
}

WebhookEventLog {
  id: ID!, eventId: ID!, eventDate: DateTime!, eventType: String!
  resourceId: ID!, requestPayload: String!, responsePayload: String
  statusCode: Int, statusText: StatusText  # Success | Failure
  retryCount: Int, duration: Int!
  webhookSubscription: WebhookSubscription
}
```

`WebhookEventLog` is queryable — it's our debugging surface for replaying or auditing webhook delivery. See §7 for the delivery contract.

### 2.8 Other entities to know exist

- `StandingOrder` — recurring SEPA out (`scheduleStandingOrder` mutation). `period: Daily | Weekly | Monthly`.
- `PaymentMandate` (interface) / `ReceivedDirectDebitMandate` — SEPA DD mandates we hold or have given.
- `TrustedBeneficiary` — pre-verified counterparties; useful to bypass SCA on subsequent SCTs.
- `MerchantProfile` / `MerchantPaymentLink` — for businesses **accepting** card payments via Swan (acquiring side, opposite direction from us).
- `SupportingDocumentCollection` — file uploads attached to onboarding, account holder, or transaction.
- `Onboarding` — KYC/KYB flow for new account holders (`createCompanyAccountHolderOnboarding`, `createIndividualAccountHolderOnboarding`).
- `Consent` — SCA consent records; surfaced when mutations require user authentication.

---

## 3. Transactions in detail

### 3.1 The 10 concrete subtypes

| Subtype | Rail | Direction encoded by | Notes |
|---|---|---|---|
| `CardTransaction` | Card (mixed) | `side` | Generic card subtype; in practice you'll see the more specific two below. |
| `OnlineCardTransaction` | OnlineCard | `side` | Card-not-present. |
| `InPersonCardTransaction` | InPersonCard | `side` | Card-present (chip, contactless, magstripe). |
| `SEPACreditTransferTransaction` | SEPACreditTransfer | `side` | SCT (incl. SCT Inst). |
| `SEPADirectDebitTransaction` | SEPADirectDebit | `side` | SDD; `mandate` field links to the `SEPADirectDebitMandate`. |
| `InternalCreditTransfer` | InternalCreditTransfer | `side` | Swan account ↔ Swan account. |
| `InternalDirectDebitTransaction` | InternalDirectDebit | `side` | Internal DD (used for partner billing among other things). |
| `InternationalCreditTransferTransaction` | InternationalCreditTransfer | `side` | Non-EUR / non-SEPA. Carries `currencyExchange`, `fees`, `intermediaryBankFees`. |
| `CheckTransaction` | Check | `side` | French chèques; carries `cmc7`, `rlmcKey`. |
| `FeeTransaction` | Fees | `side` | Bank-fee transactions; `feesType: FeesTypeEnum`. |

For the hackathon: handle `CardTransaction`/`OnlineCardTransaction`/`InPersonCardTransaction`, both `SEPACreditTransferTransaction` directions, `SEPADirectDebitTransaction` (in), `FeeTransaction`. Skip Check, International, Internal — covered by the same generic ingestion code, just not demoed.

### 3.2 Card-specific extras

```
CardTransaction / OnlineCardTransaction / InPersonCardTransaction adds:
  terminalId: String
  originalAmount: Amount!                 # pre-FX, in card-spent currency
  currencyExchange: [ReportExchangeRate!]!
  merchant: CardMerchant                  # union: CardInMerchant | CardOutMerchant
  cardDetails: CardDetails
  authorizationType: AuthorizationType    # Classic | PreAuthorization | DataRequest
  enrichedTransactionInfo: EnrichedTransactionInfo
  transactionTransportType: TransactionTransportType
  reservedAmount: Amount
  reservedAmountReleasedAt: DateTime
```

`CardMerchant` is a UNION of:
- `CardInMerchant { merchantProfile: MerchantProfile! }` — when *we* are the merchant (acquiring).
- `CardOutMerchant { merchantId, merchantName, merchantCity, merchantCountry, merchantPostalCode, merchantCategoryCode (MCC), merchantCategoryDescription, merchantAcquirerId, subMerchantId, category }` — when our cardholder spent at someone else's merchant. **This is the path most card transactions take in our context.**

`EnrichedTransactionInfo`:
```
{ enrichedMerchantName, logoUrl, category: MerchantCategory, subcategory, country, city,
  address, postalCode, longitude, latitude, isSubscription, carbonFootprint,
  contactEmail, contactPhone, contactWebsite }
```
Swan classifies merchants into 14 high-level categories: `Culture, Entertainment, Finance, Groceries, HealthAndBeauty, HomeAndUtilities, Other, ProfessionalServices, PublicAdministrations, Restaurants, Shopping, Software, Transport, Travel`. Useful as a starter prior for GL classification, but the four-digit `merchantCategoryCode` (ISO 18245 MCC) is the higher-resolution signal.

### 3.3 SEPA-specific extras

```
SEPACreditTransferTransaction adds:
  creditor: SEPACreditTransferCreditor!
  debtor: SEPACreditTransferDebtor!
  categoryPurpose: CategoryPurpose       # SalaryPayment | SupplierPayment | TaxPayment | ...
  labelType: LabelType                   # Iso | OgmVcs | Structured | Unstructured
  beneficiary: Beneficiary
  beneficiaryVerificationResult: VerifyBeneficiaryResult
  returnReason: TransactionReasonCode
  settledTransactions(...): TransactionConnection

SEPADirectDebitTransaction adds:
  creditor / debtor (different concrete types)
  mandate: SEPADirectDebitMandate
  reservedAmount, returnReason
  merchant: MerchantProfile  # when the partner is the creditor
```

Both `creditor` and `debtor` are object types carrying name, IBAN, BIC, address. The `side` tells you which one is "us": `Credit` ⇒ we're the creditor (incoming money); `Debit` ⇒ we're the debtor (outgoing money).

### 3.4 The polymorphic `statusInfo`

`TransactionStatusInfo` is an INTERFACE; the concrete type carries status-specific extras. Pattern-match on it in queries:

```graphql
statusInfo {
  __typename
  status
  ... on BookedTransactionStatusInfo  { bookingDate valueDate balanceAfter { amount { value currency } } }
  ... on PendingTransactionStatusInfo { pendingEndDate }
  ... on UpcomingTransactionStatusInfo { executionDate }
  ... on CanceledTransactionStatusInfo { canceledDate }
  ... on RejectedTransactionStatusInfo { reason hasFallback }
  ... on ReleasedTransactionStatusInfo { releaseDate reason }
  ... on SettledTransactionStatusInfo  { settlementTransactionId settledAt }
  # Deferred has no extras worth fetching today (verify)
}
```

`status: TransactionStatus` enum: `Booked | Deferred | Rejected | Pending | Canceled | Upcoming | Released | Settled`.

**Booking rule of thumb for our GL:**
| Status | What we do |
|---|---|
| `Upcoming` | Mirror in Layer A; do **not** post a journal entry. |
| `Pending` | Mirror in Layer A; do **not** post (still reversible). For card auths, optionally surface a "reserved" balance. |
| `Booked` | **Post the journal entry.** Use `bookingDate` for the entry date; use `valueDate` for the value date. |
| `Settled` | For card transactions that go from auth → settled, post or update the entry to the settled state. |
| `Rejected` | Either mark the corresponding entry as canceled or never post (depends on path). Capture `reason` for the trace. |
| `Released` | Reverse if previously posted; capture `releaseDate` and `reason`. |
| `Canceled` | Reverse if previously posted; the user cancelled an Upcoming SCT. |
| `Deferred` | Hold; will move to one of the others. |

### 3.5 `TransactionTypeEnum` (42 values)

Reading `type` together with `paymentProduct` and `side` over-determines the lifecycle stage (auth vs debit, in vs out, normal vs reversal vs return). Full list in §10. The names you'll most often dispatch on:

- `CardOutAuthorization` → reservation; arrives `Pending`. Don't book; track for live spending.
- `CardOutDebit` → settles to `Booked`. Book.
- `CardOutDebitReversal` / `CardOutCreditReversal` → reversal; reverse the original entry.
- `SepaCreditTransferIn` / `SepaInstantCreditTransferIn` → incoming customer payment. Match against `expected_payments`.
- `SepaCreditTransferOut` → outbound payment we initiated. Match against open supplier invoices via `externalReference`.
- `SepaDirectDebitIn` → recurring inbound (us being debited); usually subscription/utility/SaaS.
- `SepaDirectDebitOut` → us debiting someone (rare for accounting platform).
- `FeesIn` / `FeesOut` → bank fees; a single GL bucket.
- `*Return` / `*Recall` → returns/recalls of the matching `In`/`Out`. Reverse the original.

### 3.6 The `Amount` shape and money handling

```
type Amount  { value: AmountValue!, currency: Currency! }
input AmountInput { value: AmountValue!, currency: Currency! }
scalar AmountValue   # serialized as a JSON string holding a decimal, e.g. "12.34"
scalar Currency      # ISO 4217 string, e.g. "EUR"
```

Swan transmits monetary values as **decimal strings**, not numbers. **At ingestion, parse to `int_cents = round(Decimal(value) * 100)` and store integer cents from then on.** Use `Decimal`, not `float`. Re-render at the boundary only (UI / external API responses). See `architecure.md` §9.

### 3.7 Querying transactions

```graphql
query AccountTransactions($accountId: ID!, $first: Int!, $after: String) {
  account(accountId: $accountId) {
    id
    transactions(
      first: $first
      after: $after
      orderBy: { field: createdAt, direction: Desc }
      filters: {
        status: [Booked, Settled]
        isAfterExecutionDate: "2026-04-01T00:00:00Z"
        paymentProduct: [Card, SEPACreditTransfer, SEPADirectDebit, Fees]
      }
    ) {
      pageInfo { hasNextPage endCursor }
      totalCount
      edges {
        cursor
        node {
          id reference externalReference side type
          amount { value currency }
          counterparty label executionDate paymentProduct
          statusInfo { __typename status }
          ... on CardTransaction {
            merchant { ... on CardOutMerchant { merchantName merchantCategoryCode merchantId } }
            enrichedTransactionInfo { enrichedMerchantName category }
          }
          ... on SEPACreditTransferTransaction {
            creditor { name iban } debtor { name iban }
          }
        }
      }
    }
  }
}
```

`TransactionsFiltersInput` accepts `status` (multiple), `paymentProduct` (multiple), `type` (multiple `TransactionTypeEnum`), date ranges (`isAfterUpdatedAt`, `isBeforeUpdatedAt`, `isAfterExecutionDate`, `isBeforeExecutionDate`), amount ranges (`isAboveAmount`, `isBelowAmount`, `amount`), `search` (free-text), and `includeRejectedWithFallback`.

Pagination is **Relay-style cursor**: `first/after`, response carries `pageInfo { hasNextPage, hasPreviousPage, startCursor, endCursor }` and `edges[].cursor`. Use the `endCursor` of one page as the `after` of the next.

---

## 4. Mutations we'll actually use

The full mutation list has **137 entries**. Below are the ones our pipelines will call. Every payment-initiating mutation requires `consentRedirectUrl: String!` because it triggers SCA — see §6.

### 4.1 Send money (SEPA outbound)

```graphql
mutation Pay($input: InitiateCreditTransfersInput!) {
  initiateCreditTransfers(input: $input) {
    ... on InitiateCreditTransfersSuccessPayload {
      payment { id statusInfo { status } }
    }
    ... on Rejection { __typename message }
  }
}
```
```json
{
  "input": {
    "accountId": "<account-id>",
    "consentRedirectUrl": "https://our-app.example/payments/consent-callback",
    "idempotencyKey": "invoice-2026-001-payment-attempt-1",
    "creditTransfers": [
      {
        "amount": { "value": "1234.56", "currency": "EUR" },
        "label": "Invoice 2026-001",
        "reference": "INV2026001",
        "externalReference": "<our-internal-tracking-id>",
        "mode": "Regular",
        "sepaBeneficiary": { "iban": "FR76...", "name": "Supplier SARL", "save": false }
      }
    ]
  }
}
```

- `mode: CreditTransferMode` ∈ `Regular | InstantWithoutFallback | InstantWithFallback`. Use `InstantWithFallback` for time-sensitive SCT Inst with graceful degradation.
- `externalReference` is what we'll match against in pipeline 3. **Set it to a value our system can deterministically reverse-lookup** (e.g. `inv:<invoice_id>:try:<attempt>`). Up to ~140 chars (verify).
- `idempotencyKey` is honoured — the same key returns the same `paymentId` instead of double-spending.
- `requestedExecutionAt` schedules the transfer; `Upcoming` until the cutoff, then `Pending` → `Booked`.

After this mutation, Swan returns a `payment` with status `ConsentPending`. The user must complete SCA at the consent URL Swan provides; then status becomes `Initiated`; then Swan sends `Transaction.Upcoming` / `Transaction.Pending` / `Transaction.Booked` webhooks.

### 4.2 Issue a virtual IBAN (per-customer matching)

```graphql
mutation Vib($input: AddVirtualIbanInput) { addVirtualIbanEntry(input: $input) {
  ... on AddVirtualIbanEntrySuccessPayload {
    virtualIbanEntry { id IBAN BIC status label }
  }
  ... on Rejection { __typename message }
} }
```

Pass `{accountId}` only; Swan generates the IBAN. Save `id` and `IBAN` against the customer entity in our DB; cache `IBAN → counterparty_id` in `counterparty_identifiers` so the matching layer resolves SEPA-ins from this IBAN deterministically.

To deactivate: `cancelVirtualIbanEntry(input: { virtualIbanEntryId })`.

### 4.3 Cards

```graphql
addCard(input: AddCardInput!): AddCardPayload!
# AddCardInput {
#   accountMembershipId: ID!,
#   withdrawal: Boolean!, international: Boolean!, nonMainCurrencyTransactions: Boolean!, eCommerce: Boolean!,
#   consentRedirectUrl: String!,
#   name: String, viewCardNumber: Boolean, cardProductId: ID,
#   spendingLimit: SpendingLimitInput,
#   cardContractExpiryDate: DateTime,
# }
```

- `addCards` (plural) issues a batch.
- `addSingleUseVirtualCard` issues a one-shot card — useful for bounded merchant payments.
- `addDigitalCard` provisions an Apple Pay / Google Pay token.
- `printPhysicalCard` requests a physical card; `activatePhysicalCard` activates it from the cardholder's hand.
- `updateCard` changes flags or the spending limit (also requires SCA consent).
- `cancelCard` / `cancelPhysicalCard` / `cancelDigitalCard` for revocation.
- `viewCardNumbers` / `viewCardNumbersWithConsent` / `viewPhysicalCardPin` — only ever via the user-token flow with explicit consent.

### 4.4 Webhooks

```graphql
addWebhookSubscription(input: AddWebhookSubscriptionInput!): WebhookSubscriptionPayload!
# {label: String!, endpoint: String!, secret: String, eventTypes: [ID!]!, status: WebhookSubscriptionCreationStatus!}
updateWebhookSubscription(input: UpdateWebhookSubscriptionInput!): WebhookSubscriptionPayload!
removeWebhookSubscription(input: WebhookSubscriptionIdInput!): RemoveWebhookSubscriptionPayload!
probeWebhookEndpoint(input: ProbeWebhookEndpointInput!): ProbeWebhookEndpointPayload!  # {endpoint, secret}
replayWebhookEvent(input: ReplayWebhookEventInput!): ReplayWebhookEventPayload!         # {webhookEventId, subscriptionId?}
```

`eventTypes` takes the dotted topic strings (e.g. `"Transaction.Booked"`). The full list of valid topics is the `webhookEventTypes: [String!]!` query at runtime (auth-gated). See §7.4 for the catalogue we know about.

### 4.5 Transaction lifecycle controls

```graphql
cancelTransaction(input: { transactionId: ID! }): CancelTransactionPayload!
# Cancels an Upcoming SCT. Only valid before cutoff.

returnTransaction(input: { transactionId: String!, consentRedirectUrl: String! })
# Returns an inbound SEPA (e.g. unauthorised SDD).

refund(input: { refundTransactions: [...]!, consentRedirectUrl: String! })
# Refunds one or more card transactions to the original cardholder.
```

### 4.6 Other commonly useful ones

- `addAccountMembership(input)` — invite a new employee to the account; pass the boolean permissions.
- `disableAccountMembership` / `suspendAccountMembership` / `resumeAccountMembership` — RBAC lifecycle.
- `scheduleStandingOrder(input)` — recurring SCT (`period`, `firstExecutionDate`, `lastExecutionDate`).
- `addTrustedSepaBeneficiary(input)` — pre-verify a beneficiary so subsequent SCTs to that IBAN bypass per-payment SCA. Useful for repeat suppliers.
- `verifyBeneficiary` — Swan's "Verification of Payee" check before paying.
- `acceptConsent(input: { consentId })` — used in user-token flows to finalize SCA.
- `closeAccount`, `openAccount` — administrative.
- `exportTransactionData` — bulk export.

### 4.7 Mutation error handling — the union-vs-errors trap

**Mutation errors are unions, not GraphQL `errors`.** Anticipated business failures (insufficient funds, validation, forbidden) come back inside the success-or-rejection union; the top-level GraphQL `errors` array fires only on unexpected/system faults. **Always select `__typename` and switch on it.**

```graphql
mutation Pay($input: InitiateCreditTransfersInput!) {
  initiateCreditTransfers(input: $input) {
    __typename
    ... on InitiateCreditTransfersSuccessPayload { payment { id } }
    ... on Rejection { __typename message }
    ... on ValidationRejection { fields { path message } }
    ... on AccountNotFoundRejection { id }
    ... on ForbiddenRejection { __typename }
    ... on InternalErrorRejection { __typename }
  }
}
```

Every union member representing failure implements `interface Rejection { message: String! }`. Anything starting with `*Rejection` is a recoverable business error to surface to the user; anything in the GraphQL `errors` array is an internal/system fault to retry or escalate.

### 4.8 Onboarding (KYB) — one-time setup

For a *new* AccountHolder + Account in sandbox, use:
```graphql
createCompanyAccountHolderOnboarding(input: OnboardCompanyAccountHolderInput): ...
# fields: accountName, name, registrationNumber, companyType, businessActivity, businessActivityDescription,
#         accountCountry, monthlyPaymentVolume, individualUltimateBeneficialOwners[], representatives[],
#         residencyAddress, email, language, isRegistered, vatNumber, taxIdentificationNumber, ...
```

The hackathon scope probably reuses the seed `AccountHolder` + `Account` Swan creates with the project; we don't need to onboard new ones to demo the booking pipeline.

### 4.9 Consent & SCA — what triggers it

`ConsentPurpose` enum lists 30+ operations that require strong customer authentication. The ones we'll hit most:
`InitPayment, AddCard, AddCards, UpdateCard, AddBeneficiary, ScheduleStandingOrder, ActivatePhysicalCard, PrintPhysicalCard, AddAccountMembership, UpdateAccountMembership, ViewCardNumbers, ReturnTransactionForDirectDebit, InitiateInternationalCreditTransfer, ConsentToMultipleConsents, OptOutBulkVerificationOfPayee, EnableMandate, AddDirectDebitPaymentMandate, ResumeAccountMembership, ResumePhysicalCard, CloseAccount`.

Pattern: every SCA-protected mutation accepts `consentRedirectUrl: String!`. Swan returns a `Consent` object with a `consentUrl: String!` — open it in the user's browser; Swan handles the SCA challenge; on success Swan redirects to our `consentRedirectUrl` with a `consentId` query param; we call `acceptConsent` (or it's auto-applied) and the underlying mutation completes.

For the hackathon: **mock the consent flow on the demo path.** Either (a) use sandbox-admin to bypass it, (b) hard-code a "skip consent" toggle, or (c) auto-confirm the consent URL programmatically using sandbox shortcuts. Don't try to demo a live SCA challenge on stage.

---

## 5. Queries we'll use most

```graphql
# Look up one transaction
query Tx($id: ID!) { transaction(id: $id) { ...TxFields } }

# Look up an account + balance
query Acc($id: ID!) {
  account(accountId: $id) {
    id name IBAN BIC paymentLevel statusInfo { status }
    balances { available { value currency } pending { value currency }
               booked { value currency } reserved { value currency } }
    holder { id info { __typename name type ... on AccountHolderCompanyInfo { registrationNumber vatNumber businessActivity } } }
  }
}

# List accounts (project-wide)
query Accounts { accounts(first: 50) { edges { node { id name IBAN } } } }

# List webhook event types you can subscribe to
query EventTypes { webhookEventTypes }

# List your subscriptions
query Subs { webhookSubscriptions(first: 50) { edges { node { id label endpoint eventTypes statusInfo { status } } } } }

# Project-level metadata
query Proj { projectInfo { id name oAuthClientId logoUri webBankingSettings { ... } } }
```

Other useful entry points: `accountHolder(id)`, `accountHolders`, `accountMembership(id)`, `cards(filters)`, `card(id)`, `payments`, `payment(id)`, `paymentMandate(id)`, `standingOrder`, `users`, `consent(id)`, `internationalCreditTransferQuote(...)` (FX preview), `ibanValidation(iban)` (sanity-check an IBAN before sending).

---

## 6. The SCA consent pattern in our architecture

Outbound payments always require SCA. This affects the architecture:

**Pipeline 1 (booking) is unaffected** — it ingests already-completed transactions from webhooks. SCA is upstream.

**Pipeline 4 (issue customer invoice) is unaffected** — issuing an invoice doesn't initiate a payment; the customer pays us. We just predict the inbound SEPA.

**Pipeline 5 (paying suppliers) is affected** — `initiateCreditTransfers` triggers SCA. The shape:
1. Backend builds the `CreditTransferInput` and posts the mutation with `consentRedirectUrl` pointing at `/payments/<intent>/consent-callback`.
2. Swan returns `payment { id statusInfo: { status: ConsentPending } }` plus a `Consent` row whose `consentUrl` we need.
3. We render an "Authorize this payment with Swan" button linking to the `consentUrl`.
4. The legal-rep user authenticates with Swan; Swan redirects to `consentRedirectUrl?consentId=...&status=Accepted`.
5. Webhook `Transaction.Upcoming` arrives. Pipeline 1 ingests as normal.
6. Eventually `Transaction.Booked`.

For the hackathon demo: skip the user-facing SCA UI and either (a) demo only inbound flows (everything happens via Event Simulator, no SCA), or (b) use the sandbox-admin endpoint to short-circuit consent. **Don't put a live SCA flow in front of judges.**

---

## 7. Webhooks

### 7.1 Delivery contract

- **HTTP method / content-type:** `POST application/json` to the `endpoint` of your `WebhookSubscription`.
- **Endpoint timeout:** respond `200` within **10 seconds**. Any non-2xx or timeout = retry.
- **Retry policy:** flat **30 seconds** between retries, up to **7 retries**. **No exponential backoff.** After 7 failures Swan stops auto-redelivering; manually replay via `replayWebhookEvent` or the dashboard.
- **Ordering: NOT guaranteed.** Out-of-order delivery is normal. Don't infer state from event order; re-query.
- **Semantics: at-least-once.** Same `eventId` may be delivered more than once. **Idempotent processing keyed on `eventId` is mandatory.**
- **Source IPs** (sandbox + live, same set):
  - `52.210.172.90`
  - `52.51.125.72`
  - `54.194.47.212`
  Allowlist these on your tunnel/firewall if you can.

### 7.2 Signature verification

Swan does **not** sign webhooks with HMAC, JWS, or asymmetric keys. Verification is a **shared-secret equality check**:

- Header `x-swan-secret: <secret-you-supplied-at-subscription-creation>`
- Companion marker header `x-swan: present`

Recipe:
```python
import hmac
def verify_webhook(req, expected_secret):
    received = req.headers.get("x-swan-secret", "")
    if not hmac.compare_digest(received, expected_secret):
        raise Unauthorized()
    # Optional: enforce req.remote_addr in the IP allowlist.
```

Use a constant-time compare. Do not log the secret. Rotate via `updateWebhookSubscription`; there's no documented dual-secret grace window — it's a hard swap.

**Replay protection** is *not* provided by Swan (no signed timestamp). Mitigate by deduping on `eventId` in the ingestion layer.

### 7.3 Payload shape

Every event uses the same envelope:
```json
{
  "eventType": "Transaction.Booked",
  "eventId": "<UUID-ish>",
  "eventDate": "2021-08-18T09:35:46.673Z",
  "projectId": "<your-project-id>",
  "resourceId": "<the-transaction-id>"
}
```

That's the whole payload. The pattern:
1. Accept the POST, verify secret, return `200`.
2. Insert into `swan_events` keyed on `eventId` (idempotency).
3. Enqueue background processing.
4. Background worker GraphQL-queries the resource by `resourceId` for the full state.
5. Run pipeline 1 against the fetched state.

**Don't process synchronously inside the webhook handler.** The 10s budget is generous, but deferred processing keeps you decoupled from Swan's retry semantics.

### 7.4 Event-type catalogue (the topics we care about)

`webhookEventTypes` returns the authoritative list at runtime; until we have a token, the topics confirmed from Swan's docs include:

| Topic | When fires | Re-query |
|---|---|---|
| `Transaction.Upcoming` | Scheduled SCT enters the window | `transaction(id)` |
| `Transaction.Pending` | Card auth or pre-settlement SEPA | `transaction(id)` |
| `Transaction.Booked` | **Final accounting state — book the entry** | `transaction(id)` |
| `Transaction.Settled` | Card auth → settled | `transaction(id)` |
| `Transaction.Released` | Reservation released without debit | `transaction(id)` |
| `Transaction.Canceled` | Upcoming was canceled | `transaction(id)` |
| `Transaction.Rejected` | SEPA returned, card declined, etc. | `transaction(id)` (capture `reason`) |
| `Transaction.Enriched` | `enrichedTransactionInfo` populated | `transaction(id)` (re-classify) |
| `Card.Created` / `Card.Updated` | Card lifecycle | `card(id)` |
| `Account.Created` / `.Updated` / `.Closing` / `.Closed` | Account lifecycle | `account(id)` |
| `AccountMembership.*` | Membership lifecycle | `accountMembership(id)` |
| `AccountHolder.*` | Holder lifecycle | `accountHolder(id)` |
| `Onboarding.*` | KYB/KYC progress | `onboarding(id)` |
| `ReceivedDirectDebitMandate.Created` / `.Updated` | SDD mandate lifecycle | `receivedDirectDebitMandate(id)` |
| `StandingOrder.Scheduled` / `.Canceled` | Recurring SCT lifecycle | `standingOrder(id)` |
| `Consent.*` (`Created`, `Started`, `Granted`, `Refused`, `Expired`, `Canceled`) | SCA consent lifecycle | `consent(id)` |
| `User.Joined` / `.Updated` / `.Deactivated` | Project users | `user(id)` |
| `MerchantPayment.*` / `MerchantProfile.*` / `MerchantPaymentMethod.*` / `MerchantPaymentLink.*` | Acquiring side (probably out of scope) | various |

Topics we should subscribe to for the hackathon: **everything starting with `Transaction.`**, plus `Card.Created`, `Card.Updated`, `Account.Updated`, `Consent.Granted` (so we know when an SCA-gated mutation is done).

### 7.5 Subscription example

```graphql
mutation Sub($input: AddWebhookSubscriptionInput!) {
  addWebhookSubscription(input: $input) {
    ... on WebhookSubscriptionSuccessPayload {
      webhookSubscription { id label endpoint eventTypes statusInfo { status } }
    }
    ... on Rejection { __typename message }
  }
}
```
```json
{
  "input": {
    "label": "Local dev — main",
    "endpoint": "https://<our-ngrok-tunnel>.ngrok.app/swan/webhook",
    "secret": "<a-fresh-uuidv4>",
    "eventTypes": ["Transaction.Upcoming","Transaction.Pending","Transaction.Booked","Transaction.Settled","Transaction.Released","Transaction.Canceled","Transaction.Rejected","Transaction.Enriched","Card.Created","Card.Updated","Account.Updated","Consent.Granted"],
    "status": "Enabled"
  }
}
```

`secret` is optional but **always set it**. Recommended UUIDv4, ≤ 36 chars. There is no per-account filter — subscriptions are project-wide.

Use `probeWebhookEndpoint` to fire a synthetic test event before pointing real traffic at us — easiest way to validate the signature path in CI.

### 7.6 Operations & debugging

- **`WebhookEventLog`** — every delivery attempt is queryable: `eventId, eventType, resourceId, requestPayload, responsePayload, statusCode, retryCount, duration`. Page on `retryCount >= 7`.
- **Replay** — `replayWebhookEvent({webhookEventId})` re-fires a specific past event. Useful to re-process after a bug fix.
- **Dashboard view** — Dashboard → Developers → Webhooks → [subscription] → history shows the same data with retry buttons.

---

## 8. The payment-control hook (synchronous card-auth)

This is structurally **different from regular webhooks**: Swan blocks on our response during card authorization. It's the integration point for policy-as-code / policy-as-prose (briefing §6, architecture pipeline 5).

### 8.1 Contract

- **Method:** `POST application/json` to a single project-wide URL we register (Dashboard → Developers → Payment Control).
- **Auth headers Swan sends:** `x-swan: present`, `x-swan-secret: <our-payment-control-secret>` (separate from any webhook secret).
- **Time budget:**
  - **Live:** **1.5 s (1500 ms), non-configurable.**
  - **Sandbox:** up to **10 s, configurable.** Use the sandbox slack to step-debug; in prod, the deterministic-rules-first path must finish well under 1.5s.
- **Default on timeout / 5xx:** the project-configured default response (`accept` or `reject`). Swan recommends `accept` "for a better cardholder experience." We probably want `reject` for high-risk MCCs and `accept` otherwise — this is a project-level setting, not per-card.
- **Idempotency:** Swan may retry the same authorization (same `transactionId`) within milliseconds. Handler MUST be idempotent; the dedup key is `transactionId`. Repeated calls must yield the same `accepted` (and `partialAuthorizationAmountValue`).

### 8.2 Request body

All fields optional individually; Swan only sends what's relevant.

```jsonc
{
  "projectId": "...", "transactionId": "...", "paymentId": "...",
  "accountId": "...", "cardId": "...",

  "originalAmountValue": "12.34", "originalAmountCurrency": "EUR",
  "amountValue": "12.34", "amountCurrency": "EUR",

  "merchantId": "...", "merchantName": "...", "merchantCity": "...",
  "merchantPostalCode": "...", "merchantCountry": "FRA",
  "merchantCategoryCode": "5814",      // ISO 18245 MCC
  "terminalId": "...", "merchantAcquirerId": "...", "subMerchantId": "...",

  "dateTime": 1746...,        // epoch ms
  "expirationDateTime": 1746..., "timeoutAt": 1746...,

  "operationType": "Payment",          // AtmWithdrawal | CashBackPayment | Credit | OtcWithdrawal | Payment | Quasicash | VtsOrMdes
  "transactionCategory": "InStore",     // InStore | eCommerce | eCommerceWith3DS | Withdrawal | Other
  "readMode": "Chip",                   // Chip | ContactlessChip | ContactlessStripe | Manual | ManualChip | ManualStripe | Other | PreSavedData | Stripe
  "authorizationType": "Classic",       // Classic | PreAuthorization | DataRequest
  "transactionTransportType": "RealTimeAuthorized",
  "digitalCardWalletProvider": "ApplePay",  // optional
  "allowsPartialAuthorization": true
}
```

### 8.3 Response body

```json
{ "accepted": true }
```
or
```json
{ "accepted": true, "partialAuthorizationAmountValue": "10.00" }
```
or
```json
{ "accepted": false }
```

- `partialAuthorizationAmountValue` only allowed when the request had `allowsPartialAuthorization: true`. Must be `> 0` and `< originalAmountValue`. **EUR only.**
- There is **no `reasonCode` / decline-code field** in the response.
- Per Mastercard rules, **you cannot reject based on `digitalCardWalletProvider`**.

### 8.4 Implementation shape

```
POST /swan/payment-control
   1. Verify x-swan-secret == STORED_PAYMENT_CONTROL_SECRET (constant-time)
   2. Look up cardId in our DB (employee, role, current spending)
   3. Apply deterministic rules first:
        - per-card spending-limit window: refused if exceeded
        - MCC blocklist: refused if in blocklist
        - geographic: refused if merchantCountry not in allowed set
        - time-of-day: refused outside policy window
        - dedup: if seen this transactionId in last 5s, return prior decision
   4. If a deterministic rule fires => return immediately
   5. If no rule fires AND we have an explicit prose policy:
        - tightly-scoped Claude call with hard timeout (e.g. 800ms)
        - on timeout/error => fall through to default (configured per project)
   6. Persist (transactionId, decision, reason, latencyMs) for audit
```

The hard rule is **default-deny on internal failure**; the configurable Swan-side default-on-timeout is for *Swan's* connection timeout to us, which is separate.

### 8.5 Sandbox simulation

Dashboard → Developers → Event Simulator → Cards → "authorization request" tab. Enter `cardId`, amount, optionally MCC / merchant info / outcome. Swan calls our registered payment-control URL with the configured 10s budget.

---

## 9. Sandbox specifics

### 9.1 The Event Simulator (Dashboard UI)

Dashboard → Developers → Event Simulator. Fire test events that produce both webhooks (to subscribed endpoints) and resource-state changes:
- **Card authorization** (also triggers payment-control hook).
- **Card release** (release a reservation without debit).
- **Card debit / reversal** (advance the lifecycle).
- **SEPA Credit Transfer** — inbound and outbound.
- **SEPA Direct Debit** — inbound mandates and collections.
- **SEPA Instant** — inbound and outbound.

Each simulation produces the corresponding `Transaction.*` webhook(s).

### 9.2 The admin endpoint (programmatic simulation)

`https://api.swan.io/sandbox-partner-admin/graphql` is the same project with privileged mutations. Same bearer token works. Documented simulator mutations:

- `createSandboxUser` — provisions a sandbox user (impersonable, skips real KYC).
- `simulateIncomingSepaCreditTransferReception` — generates an inbound SCT to one of our accounts; returns the resulting transaction id.
- Card transaction / SDD inbound simulation mutations exist but exact names need introspection on the admin schema once auth works.

Use this endpoint when scripting integration tests so the demo doesn't depend on clicking through the UI.

### 9.3 Default seed data

A fresh sandbox project ships with at least one test `AccountHolder`, at least one test `Account` with a working IBAN, and the legal-representative user (you). Once auth works:
```graphql
query Bootstrap { accountHolders(first: 10) { edges { node { id info { __typename name } accounts(first: 10) { edges { node { id IBAN paymentLevel } } } } } } }
```
…tells us what we already have.

**Documented test debtor IBAN** (when simulating an inbound SEPA, use this as the originator): `FR2730003000706315734174B93`. The full bank of test IBANs and test card PANs lives inside the authenticated Banking Console; pull them from the dashboard once we have credentials.

### 9.4 Webhook subscription quotas

Swan publishes a per-event-topic cap: **up to 10 subscriptions per event topic in sandbox, 5 in live**. We won't hit this in the hackathon (one subscription suffices), but worth knowing if we ever fan out to multiple environments.

### 9.5 Useful tools

- **API Explorer:** <https://explorer.swan.io/> — paste the bearer token, run live queries.
- **API Reference:** <https://api-reference.swan.io/> — type-by-type docs auto-generated from the schema.

---

## 10. Reference: enums to dispatch on

`TransactionStatus` (8): `Booked, Deferred, Rejected, Pending, Canceled, Upcoming, Released, Settled`.

`TransactionSide` (2): `Debit, Credit`.

`PaymentProduct` (10): `InternalCreditTransfer, SEPACreditTransfer, SEPADirectDebit, Card, Fees, InternalDirectDebit, Check, InternationalCreditTransfer, InPersonCard, OnlineCard`.

`TransactionTypeEnum` (42): `InternalCreditTransferOut, InternalCreditTransferOutReturn, InternalCreditTransferOutRecall, InternalCreditTransferIn, InternalCreditTransferInReturn, InternalCreditTransferInRecall, SepaCreditTransferOut, SepaInstantCreditTransferOut, SepaInstantCreditTransferIn, SepaCreditTransferOutReturn, SepaInstantCreditTransferOutRecall, SepaInstantCreditTransferInRecall, SepaCreditTransferOutRecall, SepaCreditTransferIn, SepaCreditTransferInReturn, SepaCreditTransferInRecall, FeesOut, FeesIn, SepaDirectDebitIn, SepaDirectDebitInReturn, SepaDirectDebitInReversal, SepaDirectDebitOut, SepaDirectDebitOutReturn, SepaDirectDebitOutReversal, CardOutAuthorization, CardOutDebit, CardOutDebitReversal, CardOutCredit, CardOutCreditReversal, InternalDirectDebitIn, InternalDirectDebitInReturn, InternalDirectDebitOut, InternalDirectDebitOutReturn, CheckIn, CheckInReturn, InternationalCreditTransferIn, InternationalCreditTransferOut, InternationalCreditTransferInReturn, InternationalCreditTransferOutReturn, CardInCredit, CardInChargeback, CardInChargebackReversal`.

`MerchantCategory` (14): `Culture, Entertainment, Finance, Groceries, HealthAndBeauty, HomeAndUtilities, Other, ProfessionalServices, PublicAdministrations, Restaurants, Shopping, Software, Transport, Travel`.

`AccountCountry` (6): `FRA, DEU, ESP, NLD, ITA, BEL`. (Hackathon is FRA.)

`PaymentLevel` (2): `Limited, Unlimited`.

`AccountStatus` (4): `Opened, Suspended, Closing, Closed`.

`CardStatus` (5): `ConsentPending, Processing, Enabled, Canceled, Canceling`.

`AccountHolderStatus` (3): `Enabled, Suspended, Canceled`.

`OnboardingStatus` (3): `Finalized, Invalid, Valid`.

`PaymentStatus` (3): `ConsentPending, Initiated, Rejected`.

`WebhookSubscriptionStatus` (3): `Enabled, Disabled, Broken`.

`SpendingLimitPeriod` (4): `Daily, Weekly, Monthly, Always`.

`StandingOrderPeriod` (3): `Daily, Weekly, Monthly`.

`CreditTransferMode` (3): `Regular, InstantWithoutFallback, InstantWithFallback`.

`LabelType` (4): `Iso, OgmVcs, Structured, Unstructured`.

`VerificationStatus` (5): `NotStarted, WaitingForInformation, Pending, Verified, Refused`.

`IdentificationLevel` (3): `Expert, QES, PVID`.

For the very long `RejectedReasonCode` (≈90) and `TransactionReasonCode` (≈80) enums — surface the raw value in the decision trace; don't try to handle each one in code.

---

## 11. How Swan maps onto our architecture

| Architecture concept (from `architecure.md`) | Swan reality |
|---|---|
| Layer A: bank mirror (`swan_events`) | Webhook envelope inserted on receipt, keyed on `eventId`. Idempotent. |
| Layer A: `swan_transactions` | The result of `transaction(id)` GraphQL query, normalized. Common fields go in columns; subtype-specific fields go in JSON or per-subtype columns. |
| Layer B: `counterparty_identifiers` | Populated from: (a) `Account.IBAN` of inbound SEPA debtor — exact match; (b) `CardOutMerchant.merchantId` — exact match; (c) `(merchantCategoryCode, enrichedMerchantName)` patterns — fuzzy. Each identifier carries `confidence` and `source`. |
| Layer B: virtual IBANs per customer | `addVirtualIbanEntry` per customer; cache `IBAN → counterparty_id`. |
| Pipeline 1 ingest | `POST /swan/webhook` → verify secret → insert `swan_events` → enqueue → query `transaction(id)` → normalize → resolve counterparty → classify GL → build entry → confidence gate → post. |
| Pipeline 5 (payment control) | `POST /swan/payment-control` → 1.5s budget → deterministic rules → optional bounded Claude call → respond `{accepted}`. |
| Booking date | `executionDate` on the transaction; `bookingDate` from `BookedTransactionStatusInfo` for the GL entry's date. |
| Idempotency key | `eventId` on webhooks; `idempotencyKey` on `initiateCreditTransfers`. |
| Money | `Amount.value` (string-decimal) parsed to int cents at the boundary. |
| Outbound matching | `externalReference` we set on `initiateCreditTransfers` round-trips on the resulting `SEPACreditTransferTransaction`. Match against open AP. |
| Inbound matching | Tier 1: virtual-IBAN of the credited account → customer entity. Tier 2: `reference` field → invoice id. Tier 3: amount + counterparty name fuzzy match. |
| Decision trace | Capture: raw payload (`swan_events`), the GraphQL response (`swan_transactions`), every rule that fired, every cache hit, any AI call (with confidence + reasoning). Joined to the journal entry. |
| Invariants on post | (1) `SUM(debits) = SUM(credits)` per entry. (2) Our recorded balance for the Swan account = `Account.balances.booked` returned from GraphQL. Re-fetch and compare after each post. |

---

## 12. Gotchas and anti-patterns

- **Don't book on `Pending`.** Card auths, intermediate SEPA states, all reversible. Wait for `Booked` (and for card auths, optionally `Settled`).
- **Don't trust webhook ordering.** Always re-query. Don't infer "this came after that."
- **Don't store the `client_secret` in git.** Use env. Same for webhook secrets.
- **Don't compute money in floats.** `value` is a `String` for a reason. Use `Decimal`. Store `int_cents`.
- **Don't ignore `idempotencyKey`** on `initiateCreditTransfers`. Re-runs without it = double-spends. Generate one per logical payment intent and persist it.
- **Don't put SCA in the demo path.** Either bypass via sandbox-admin or mock the consent step.
- **Don't subscribe to `Transaction.Enriched` and `Transaction.Booked` separately and assume the same processing.** `Enriched` arrives later and updates the merchant info — re-run the classification step but don't re-post.
- **Don't conflate the two secrets.** Webhook subscription secret ≠ payment-control secret ≠ OAuth `client_secret`. Three different things.
- **Don't model GL categories as Swan accounts.** One Swan account = one `chart_of_accounts.512` row. Categories live in our chart.
- **Don't try to replay a webhook from your DB.** Use `replayWebhookEvent({webhookEventId})` — that triggers Swan's own redelivery and exercises the full path.
- **`paymentLevel: Limited`** silently caps outbound volume. Surface this in the UI before SCT initiation.
- **The `secret` field on `WebhookSubscription` is `String`** (nullable). Always set it; never rely on the IP allowlist alone.

---

## 13. Open questions / verify before relying on

1. The `client_secret` we have is rejected — needs regeneration from the Dashboard before any authenticated probing.
2. Exact maximum length of `externalReference` — likely ~140 chars per SEPA SCT spec, not directly confirmed in the schema.
3. Whether `Transaction.Enriched` arrives reliably or only sometimes — needs a sandbox test.
4. The complete list of `webhookEventTypes` — only fully knowable via the auth-gated `webhookEventTypes` query; the catalogue in §7.4 is the documented subset.
5. Whether the partner-server token can call all the mutations we need (payment initiation typically requires user-token + SCA) — practical exercise after auth works.
6. Sandbox-admin endpoint full surface — schema might differ subtly from `sandbox-partner`; introspect both once we have a token.
7. Whether `AddWebhookSubscriptionInput` truly has no per-account filter (it does not in the published schema); if account scoping is required, do it post-receipt in our handler.
8. Whether subscription quota / rate limits exist beyond the 2k req / 5min IP-level rate limit — not in public docs.
9. Webhook secret rotation grace window: Swan's docs imply hard swap — confirm before rotating in production.

---

## 14. Suggested first-day integration plan

1. **Regenerate the `client_secret`** (Dashboard → Developers → API → Regenerate). Save in `.env` as `SWAN_CLIENT_ID` and `SWAN_CLIENT_SECRET`.
2. **Verify token issue:** `curl --data-urlencode grant_type=client_credentials ...` returns `access_token` with `expires_in: 3600`.
3. **Add the two MCP servers** to Claude Code (`claude mcp add swan-graphql ...`, `... swan-docs ...`) for ongoing schema queries.
4. **Discover seed data:** `query { accountHolders(first:5){edges{node{id info{__typename name} accounts(first:5){edges{node{id IBAN paymentLevel}}}}}}}`. Write the result to a fixture file.
5. **Stand up the webhook receiver** (any framework; e.g. FastAPI route `POST /swan/webhook`):
   - Verify `x-swan-secret`.
   - Insert into `swan_events` keyed on `eventId`.
   - Return `200`.
   - Enqueue `process_swan_event(event_id)`.
6. **Ngrok the receiver** (`ngrok http 8000`); copy the HTTPS URL.
7. **Subscribe to webhooks** via `addWebhookSubscription` with the topics in §7.4.
8. **Probe** via `probeWebhookEndpoint` to confirm signature handling.
9. **Fire a test event** in the Event Simulator (e.g. an inbound SCT). Verify the full flow: webhook → DB insert → background fetch → normalized `swan_transactions` row.
10. **Hook in pipeline 1** (counterparty resolve → GL classify → balanced entry → invariants → post).
11. **Wire up payment control** (`POST /swan/payment-control`) only when ready to demo policy.
12. **Skip onboarding new account holders** for the hackathon — reuse seed data.

The end state for day one: an inbound SEPA fired in the simulator results in a balanced journal entry visible in our UI within seconds, with a decision trace that names the rules that fired.

---

## 15. Inputs reference (selected)

```graphql
input AmountInput { value: AmountValue!, currency: Currency! }
input AddressInput { addressLine1: String, addressLine2: String, city: String, postalCode: String, state: String, country: CCA3! }
input ResidencyAddressInput { ...same fields, all optional, country: CCA3 (optional) }

input SepaBeneficiaryInput { iban: IBAN!, name: String!, address: AddressInput, save: Boolean }
input SwanAccountBeneficiaryInput { ... }   # for InternalCreditTransfer to another Swan account

input CreditTransferInput {
  amount: AmountInput!
  externalReference: String        # OUR field — set this for matching
  label: String
  labelType: LabelType
  categoryPurpose: CategoryPurpose # SalaryPayment | SupplierPayment | TaxPayment | ...
  mode: CreditTransferMode         # Regular | InstantWithoutFallback | InstantWithFallback
  reference: String                # the payment reference shown to the beneficiary
  requestedExecutionAt: DateTime   # for scheduled outbound
  sepaBeneficiary: SepaBeneficiaryInput            # one of these three
  swanAccountBeneficiary: SwanAccountBeneficiaryInput
  trustedBeneficiaryId: ID
  beneficiaryVerificationToken: String  # optional Verification of Payee token
}

input InitiateCreditTransfersInput {
  accountId: ID                # one of accountId or accountNumber
  accountNumber: AccountNumber
  consentRedirectUrl: String!
  creditTransfers: [CreditTransferInput!]!
  idempotencyKey: String
}

input AddCardInput {
  accountMembershipId: ID!
  withdrawal: Boolean!, international: Boolean!, nonMainCurrencyTransactions: Boolean!, eCommerce: Boolean!
  consentRedirectUrl: String!
  name: String                  # cardholder display name
  cardProductId: ID, cardContractExpiryDate: DateTime
  spendingLimit: SpendingLimitInput
  viewCardNumber: Boolean
}

input SpendingLimitInput {
  period: SpendingLimitPeriodInput!  # Daily | Weekly | Monthly | Always
  amount: AmountInput!
  mode: SpendingLimitModeInput      # Rolling | CalendarDay | CalendarWeek | CalendarMonth
}

input AddWebhookSubscriptionInput {
  label: String!
  endpoint: String!
  secret: String                # set this — recommended UUIDv4 ≤ 36 chars
  eventTypes: [ID!]!            # e.g. ["Transaction.Booked","Card.Created"]
  status: WebhookSubscriptionCreationStatus!  # Enabled | Disabled
}

input AddAccountMembershipInput {
  accountId: ID!, email: String!
  restrictedTo: RestrictedToInput!  # firstName, lastName, birthDate, phoneNumber
  canViewAccount: Boolean!, canManageBeneficiaries: Boolean!,
  canInitiatePayments: Boolean!, canManageAccountMembership: Boolean!,
  canManageCards: Boolean
  consentRedirectUrl: String, residencyAddress: ResidencyAddressInput,
  taxIdentificationNumber: String, language: AccountLanguage
}
```

---

## 16. Source references

- Swan API authentication overview: <https://docs.swan.io/api/authentication>
- Project access token guide: <https://docs.swan.io/developers/using-api/authentication/guide-get-token-project/>
- User access token guide: <https://docs.swan.io/api/authentication/user-access-token>
- Webhooks reference: <https://docs.swan.io/developers/using-api/webhooks/>
- Payment control: <https://docs.swan.io/developers/using-api/payment-control/>
- Sandbox cards / Event Simulator: <https://docs.swan.io/topics/payments/cards/sandbox/>
- MCP servers: <https://docs.swan.io/developers/tools/mcp-servers/>
- Partner frontend repo (env example): <https://github.com/swan-io/swan-partner-frontend>
- API reference (entry index): <https://api-reference.swan.io/>
- The full live SDL captured for this project: `swan/_probes/swan_schema_full.json.gz` (1676 types).

---

*Last updated: 2026-04-25, pre-event. Update as the slice firms up and as live calls reveal anything that disagrees with this document.*
