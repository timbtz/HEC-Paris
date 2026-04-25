"""Unit tests for `SwanGraphQLClient` and `handle_mutation_result`.

Source: Phase 2 plan §Task 7; SWAN_API_REFERENCE.md:584-601 (mutation
errors live in `data`, not GraphQL `errors`); :632-640 (canonical query
shapes); Dev orchestration/swan/CLAUDE.md (refresh-on-401 retry).
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from backend.orchestration.swan.graphql import (
    SwanGraphQLClient,
    SwanGraphQLError,
    SwanRejectionError,
    handle_mutation_result,
)


# --------------------------------------------------------------------------- #
# Fake OAuth — implements the SwanOAuthClientLike protocol
# --------------------------------------------------------------------------- #


class _FakeOAuth:
    def __init__(self, tokens: list[str] | None = None) -> None:
        # Cycle through tokens; default to one that never changes.
        self._tokens = tokens or ["tok-A"]
        self._cursor = 0
        self.invalidated = 0
        self.get_token_calls = 0

    async def get_token(self) -> str:
        self.get_token_calls += 1
        token = self._tokens[min(self._cursor, len(self._tokens) - 1)]
        return token

    async def invalidate(self) -> None:
        self.invalidated += 1
        self._cursor += 1  # next get_token() returns the next token


# --------------------------------------------------------------------------- #
# query() behaviour
# --------------------------------------------------------------------------- #


async def test_query_returns_data_on_200():
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": {"transaction": {"id": "tx1"}}})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanGraphQLClient("https://api.swan.io/graphql", _FakeOAuth(), http_client=http)

    data = await client.query("query { transaction(id: \"tx1\") { id } }")

    assert data == {"transaction": {"id": "tx1"}}
    assert len(calls) == 1
    assert calls[0].headers["authorization"] == "Bearer tok-A"
    await http.aclose()


async def test_query_raises_swangraphqlerror_when_errors_field_present():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": None,
                "errors": [{"message": "Field 'wat' not found"}],
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanGraphQLClient("https://x/graphql", _FakeOAuth(), http_client=http)

    with pytest.raises(SwanGraphQLError) as exc_info:
        await client.query("query { wat }")

    assert "Field 'wat' not found" in str(exc_info.value)
    assert exc_info.value.errors[0]["message"] == "Field 'wat' not found"
    await http.aclose()


async def test_query_invalidates_and_retries_on_401():
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(401, json={"error": "token expired"})
        return httpx.Response(200, json={"data": {"ping": "pong"}})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    oauth = _FakeOAuth(tokens=["tok-A", "tok-B"])
    client = SwanGraphQLClient("https://x/graphql", oauth, http_client=http)

    data = await client.query("query { ping }")

    assert data == {"ping": "pong"}
    assert len(calls) == 2, "expected one retry after 401"
    assert oauth.invalidated == 1
    # First request used tok-A; the retry must use the freshly refreshed tok-B.
    assert calls[0].headers["authorization"] == "Bearer tok-A"
    assert calls[1].headers["authorization"] == "Bearer tok-B"
    await http.aclose()


async def test_query_does_not_retry_more_than_once_on_persistent_401():
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"error": "still bad"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanGraphQLClient("https://x/graphql", _FakeOAuth(["a", "b"]), http_client=http)

    with pytest.raises(httpx.HTTPStatusError):
        await client.query("query { ping }")

    assert len(calls) == 2, "exactly one retry, then surface the 401"
    await http.aclose()


# --------------------------------------------------------------------------- #
# fetch_transaction / fetch_account
# --------------------------------------------------------------------------- #


async def test_fetch_transaction_returns_payload():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"transaction": {"id": "tx-42", "type": "SepaCreditTransferTransaction"}}},
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanGraphQLClient("https://x/graphql", _FakeOAuth(), http_client=http)

    tx = await client.fetch_transaction("tx-42")

    assert tx == {"id": "tx-42", "type": "SepaCreditTransferTransaction"}
    await http.aclose()


async def test_fetch_transaction_raises_when_null():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"transaction": None}})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanGraphQLClient("https://x/graphql", _FakeOAuth(), http_client=http)

    with pytest.raises(LookupError):
        await client.fetch_transaction("tx-missing")
    await http.aclose()


async def test_fetch_account_returns_payload():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"account": {"id": "acc-1", "IBAN": "FR76..."}}},
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanGraphQLClient("https://x/graphql", _FakeOAuth(), http_client=http)

    acc = await client.fetch_account("acc-1")
    assert acc["id"] == "acc-1"
    await http.aclose()


# --------------------------------------------------------------------------- #
# handle_mutation_result
# --------------------------------------------------------------------------- #


def test_handle_mutation_returns_success_payload():
    payload = {"__typename": "InitiateCreditTransfersSuccessPayload", "paymentId": "pay-1"}
    out = handle_mutation_result(payload, expected_success_type="InitiateCreditTransfersSuccessPayload")
    assert out is payload


def test_handle_mutation_raises_on_validation_rejection():
    payload = {
        "__typename": "ValidationRejection",
        "message": "amount.value is required",
        "validationErrors": [{"path": ["amount", "value"], "message": "required"}],
    }
    with pytest.raises(SwanRejectionError) as exc_info:
        handle_mutation_result(payload, expected_success_type="InitiateCreditTransfersSuccessPayload")
    assert exc_info.value.message == "amount.value is required"
    assert exc_info.value.fields[0]["path"] == ["amount", "value"]


def test_handle_mutation_raises_on_generic_rejection():
    payload = {
        "__typename": "ForbiddenRejection",
        "message": "consent required",
    }
    with pytest.raises(SwanRejectionError) as exc_info:
        handle_mutation_result(payload, expected_success_type="SomeSuccessPayload")
    assert "consent required" in str(exc_info.value)
    assert exc_info.value.fields == []


def test_handle_mutation_raises_on_unknown_typename():
    payload = {"__typename": "TotallyMadeUpType"}
    with pytest.raises(SwanGraphQLError) as exc_info:
        handle_mutation_result(payload, expected_success_type="ExpectedSuccessPayload")
    assert "TotallyMadeUpType" in str(exc_info.value)


def test_handle_mutation_raises_when_typename_missing():
    payload: dict[str, Any] = {"paymentId": "pay-1"}
    with pytest.raises(SwanGraphQLError):
        handle_mutation_result(payload, expected_success_type="ExpectedSuccessPayload")
