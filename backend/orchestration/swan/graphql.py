"""Swan GraphQL client + canonical query helpers.

Source: SWAN_API_REFERENCE.md:421-458 (mutation union pattern; `Rejection`
interface), :584-601 (mutation errors land in `data`, not `errors`),
:632-640 (canonical `transaction(id)` / `account(id)` queries).
Dev orchestration/swan/CLAUDE.md (refresh-on-401 retry; constant-time
secret compare for webhooks; backend-only). Phase 2 plan §Task 6.

Two error layers exist and must NOT be conflated:
  * GraphQL `errors` array  → system fault (auth, rate limit, malformed
    query). Surfaced as `SwanGraphQLError`.
  * Mutation union members  → business rejection. Surfaced as
    `SwanRejectionError` via `handle_mutation_result`.
"""
from __future__ import annotations

from typing import Any

import httpx


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class SwanGraphQLError(Exception):
    """The GraphQL `errors` array was non-empty — a system fault."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        # Surface the first message in str() so test failures + logs are
        # readable without dumping the full list.
        first_msg = errors[0].get("message", "") if errors else ""
        super().__init__(f"Swan GraphQL error: {first_msg}")


class SwanRejectionError(Exception):
    """A mutation union member implementing `interface Rejection`.

    `fields` is the optional per-field detail Swan supplies for
    `ValidationRejection`-style errors (a list of `{path, message}` dicts).
    """

    def __init__(self, message: str, fields: list[dict[str, Any]] | None = None) -> None:
        self.message = message
        self.fields = fields or []
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Canonical queries (kept inline; no .graphql files in MVP)
# --------------------------------------------------------------------------- #


_TRANSACTION_QUERY = """
query Tx($id: ID!) {
  transaction(id: $id) {
    id
    type
    side
    amount { value currency }
    paymentMethod
    counterparty
    account { id IBAN }
    executionDate
    bookedBalanceAfter
  }
}
""".strip()


_ACCOUNT_QUERY = """
query Acc($id: ID!) {
  account(accountId: $id) {
    id
    IBAN
    name
    holder { id }
    bookedBalance { value currency }
  }
}
""".strip()


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class SwanGraphQLClient:
    """Thin async wrapper around Swan's GraphQL endpoint.

    All requests carry `Authorization: Bearer <token>` and JSON body
    `{query, variables, operationName}`. On 401 we invalidate the token
    once and retry; further failures bubble up unmodified.
    """

    def __init__(
        self,
        graphql_url: str,
        oauth: "SwanOAuthClientLike",
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = graphql_url
        self._oauth = oauth
        self._http: httpx.AsyncClient = http_client or httpx.AsyncClient()
        self._owns_http = http_client is None

    async def query(
        self,
        query_str: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        """POST a GraphQL document; return `body["data"]`.

        Retries exactly once on 401 after invalidating the token cache.
        """
        body = {
            "query": query_str,
            "variables": variables or {},
            "operationName": operation_name,
        }

        response = await self._post(body)
        if response.status_code == 401:
            # Token may have been revoked or rotated server-side. Drop the
            # cache, refresh, retry exactly once.
            await self._oauth.invalidate()
            response = await self._post(body)

        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        if payload.get("errors"):
            raise SwanGraphQLError(payload["errors"])

        return payload.get("data") or {}

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        token = await self._oauth.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        return await self._http.post(self._url, json=body, headers=headers)

    async def fetch_transaction(self, tx_id: str) -> dict[str, Any]:
        """Run the canonical `transaction(id)` query.

        Raises `LookupError` if the transaction is null (caller should treat
        this as a missing-resource condition, not a system fault).
        """
        data = await self.query(_TRANSACTION_QUERY, variables={"id": tx_id}, operation_name="Tx")
        tx = data.get("transaction")
        if tx is None:
            raise LookupError(f"Swan transaction not found: {tx_id}")
        return tx

    async def fetch_account(self, account_id: str) -> dict[str, Any]:
        """Run the canonical `account(id)` query."""
        data = await self.query(_ACCOUNT_QUERY, variables={"id": account_id}, operation_name="Acc")
        account = data.get("account")
        if account is None:
            raise LookupError(f"Swan account not found: {account_id}")
        return account

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()


# --------------------------------------------------------------------------- #
# Mutation-union helper
# --------------------------------------------------------------------------- #


# Names of union members that implement Swan's `Rejection` / `ValidationRejection`
# interfaces. We pattern-match on substrings because Swan defines many
# concrete subtypes (e.g. `ForbiddenRejection`, `InsufficientFundsRejection`,
# `AccountNotFoundRejection`, etc.) — matching the suffix is robust.
_REJECTION_TYPENAME_SUFFIX = "Rejection"


def handle_mutation_result(
    payload: dict[str, Any],
    expected_success_type: str,
) -> dict[str, Any]:
    """Pattern-match on `payload["__typename"]`.

    * Returns `payload` if `__typename` == `expected_success_type`.
    * Raises `SwanRejectionError` for any `*Rejection` typename, surfacing
      `message` and (when present) `validationErrors`/`fields`.
    * Raises `SwanGraphQLError` for an unknown typename — programmer error
      or unannounced schema change.

    Note: this does NOT touch `payload`'s structure beyond the typename
    dispatch. Caller decides what to do with the success payload.
    """
    typename = payload.get("__typename")
    if typename is None:
        raise SwanGraphQLError(
            [{"message": "Mutation result missing __typename; check the selection set."}]
        )

    if typename == expected_success_type:
        return payload

    if typename.endswith(_REJECTION_TYPENAME_SUFFIX):
        message = payload.get("message", f"Swan rejected mutation ({typename})")
        # Swan returns per-field detail under different names depending on
        # the subtype. Accept either common shape.
        fields = (
            payload.get("validationErrors")
            or payload.get("fields")
            or []
        )
        raise SwanRejectionError(message, fields=fields)

    raise SwanGraphQLError(
        [{"message": f"Unexpected Swan mutation typename: {typename!r}"}]
    )


# --------------------------------------------------------------------------- #
# Type alias to avoid a hard import cycle with oauth.py at runtime
# --------------------------------------------------------------------------- #


class SwanOAuthClientLike:  # pragma: no cover - structural typing helper
    """Protocol-ish stub. The real implementation lives in `oauth.py`.

    Declared here so `SwanGraphQLClient.__init__` has a meaningful type
    hint without forcing a circular import.
    """

    async def get_token(self) -> str: ...
    async def invalidate(self) -> None: ...
