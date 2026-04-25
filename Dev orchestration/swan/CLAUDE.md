# Rules — Swan integration

These rules apply to any code that talks to the Swan banking API in this repo. Treat as load-bearing; deviations need a paragraph in the PR description explaining why.

## Read first

Before writing code that talks to Swan, read in order:
1. `Dev orchestration/swan/SWAN_API_REFERENCE.md` — the long-form reference, with field names, mutation contracts, webhook contract, and architectural mapping.
2. `architecure.md` (root) — the three-layer model and the pipelines.
3. `projectbriefing.md` (root) — the *why*.

The full live SDL is at `Dev orchestration/swan/_probes/swan_schema_full.json.gz` (gunzip in place if you need to grep types). Prefer it over re-fetching.

## Authentication

- One environment variable name per credential: **`SWAN_CLIENT_ID`** and **`SWAN_CLIENT_SECRET`**. Never inline literals.
- Tokens come from `POST https://oauth.swan.io/oauth2/token` with `grant_type=client_credentials`, no `scope` parameter.
- Tokens are valid for **3600s**. Cache in-process; refresh on 401 or 60s before expiry, whichever comes first. Do not refresh on every call.
- Send as `Authorization: Bearer <token>` to `https://api.swan.io/sandbox-partner/graphql`.
- For programmatic test simulation use `https://api.swan.io/sandbox-partner-admin/graphql` (same token).

## Money

- Every monetary value Swan returns is `Amount { value: String, currency: String }` — `value` is a **decimal string**.
- At ingestion, convert with `int_cents = int(round(Decimal(value) * 100))` (or your language's equivalent) and store integer cents from then on.
- **Never use `float` on money.** The codebase has no exceptions to this rule.
- `Decimal` only at the parse/render boundary. Internal arithmetic is integer cents.

## Idempotency

- Webhook handlers MUST be idempotent on `eventId`. Insert into `swan_events` with a unique constraint on `eventId`; on conflict, no-op and return 200.
- Outbound payments MUST pass `idempotencyKey` to `initiateCreditTransfers`. Generate one per logical payment intent and persist it. Re-runs without it = double spends.
- Posted journal entries MUST be idempotent on `(swan_transaction_id, pipeline_version)`. Reprocessing the same Swan transaction must not produce a second entry.

## Webhooks

- Endpoint contract: `POST application/json`, return `200` within **10 seconds**, never block.
- Verify the `x-swan-secret` header with **constant-time** equality against the per-subscription secret. Reject mismatches with 401.
- The payload envelope is **only** `{eventType, eventId, eventDate, projectId, resourceId}` — that is the entire body. **Always re-query** the resource by id; never infer state from the envelope.
- Order is **not guaranteed** and same `eventId` may arrive multiple times. Handlers must be idempotent and tolerant of out-of-order delivery.
- Process out of band: insert raw event, return 200, enqueue background work. Don't run pipelines synchronously inside the request handler.
- Source-IP allowlist (sandbox + live, same): `52.210.172.90`, `52.51.125.72`, `54.194.47.212`. Allowlist on the tunnel/firewall when the deploy target permits.

## Booking rules (interaction with the GL)

- **Only post journal entries on `Booked` or `Settled`.** Do not post on `Pending` (card auths) or `Upcoming` (scheduled SCT).
- Use `BookedTransactionStatusInfo.bookingDate` as the entry date and `valueDate` as the value date. Use `executionDate` (interface field) as the accounting date when no booked status info is yet available.
- On `Released` or `Canceled` after a prior post, **reverse** the entry (create a counter-entry with a link). Never delete.
- Capture every rule that fired, every cache hit, every AI call (with confidence) into `decision_traces`. The trace is part of the entry, not a separate log.

## Mutation error handling

- **Mutation errors come back as union members, not in GraphQL `errors`.** Always select `__typename` and switch on it. Failure types implement `interface Rejection { message: String! }`.
- The top-level GraphQL `errors` array means an internal/system fault — log, escalate, retry with backoff. It is *not* the place where business failures land.

## SCA / consent flow

- Mutations that take `consentRedirectUrl: String!` trigger SCA. Don't try to bypass on the demo path; either skip the operation, use the sandbox-admin endpoint, or feature-flag the SCA UI behind an explicit demo toggle.
- The `Consent` object's `consentUrl` is what the user must visit. The redirect comes back to our `consentRedirectUrl` with a `consentId` query param.
- Don't initiate SCA flows in front of the jury. Pre-record or use admin shortcuts.

## externalReference and reference (matching keys)

- On `initiateCreditTransfers`, set `externalReference` to a value our system can deterministically reverse-lookup (e.g. `inv:<invoice_id>:try:<n>`). It round-trips on the resulting transaction and is the cleanest matching key for outbound payments.
- `reference` is shown to the beneficiary and constrained by `labelType` (`Iso | OgmVcs | Structured | Unstructured`); pick `Structured` or `OgmVcs` only if you understand the formats.
- For inbound, prefer **virtual IBANs per customer** (`addVirtualIbanEntry`) — the matching layer becomes deterministic without parsing reference strings.

## Schema discovery

- Public schema introspection is open; prefer it over guessing field names. Cache the result locally; don't re-introspect on every run.
- Two MCP servers exist for ongoing schema/docs queries inside Claude Code: `https://mcp.swan.io/graphql/mcp` and `https://mcp.swan.io/docs/mcp`. Add via `claude mcp add --transport http swan-graphql https://mcp.swan.io/graphql/mcp`.

## Code patterns

- Wrap every Swan call in a thin abstraction (`swan_client.transaction(id)`, `swan_client.initiate_credit_transfer(...)`) so it can be (a) mocked in tests, (b) replayed from a recording, and (c) short-circuited with a deterministic fallback during the demo.
- Wrap every webhook receive in the same way: a single ingestion function that takes raw bytes + headers, verifies, dedupes, and returns a normalized event the pipeline can consume.
- The Swan client should fail loud on:
  - 401 (token expired) → refresh and retry once, then escalate.
  - 429 (rate limited) → backoff and retry, then escalate.
  - 5xx → exponential backoff, max 3 retries.
  - Anything else → no retry, log full request/response.

## What NOT to do

- Don't query Swan from the browser. Backend-only.
- Don't model GL categories as separate Swan accounts. One Swan account = one row in `chart_of_accounts.512`.
- Don't book on `Pending` or `Upcoming`.
- Don't compute money in floats anywhere in the call chain.
- Don't share the OAuth `client_secret`, the webhook secret, or the payment-control secret across environments. Three different secrets per environment.
- Don't use `addAccountMembership` / `addCard` in code paths that don't have a real user in front of them. They require SCA and will hang waiting for a redirect.
- Don't block longer than 1.5 s in the payment-control handler in live (10 s in sandbox). Default-deny on internal failure.
- Don't paginate without `first` (max 100). Don't assume single-page results.

## Environment variables (canonical names)

| Var | Purpose |
|---|---|
| `SWAN_CLIENT_ID` | OAuth client id, e.g. `SANDBOX_<uuid>` |
| `SWAN_CLIENT_SECRET` | OAuth client secret |
| `SWAN_GRAPHQL_URL` | `https://api.swan.io/sandbox-partner/graphql` |
| `SWAN_OAUTH_URL` | `https://oauth.swan.io/oauth2/token` |
| `SWAN_WEBHOOK_SECRET` | Shared secret for the main webhook subscription |
| `SWAN_PAYMENT_CONTROL_SECRET` | Shared secret for the payment-control hook |
| `SWAN_PROJECT_ID` | Project id (also returned as `projectId` on every event) |

Add to `.env.local` (gitignored). Never commit.
