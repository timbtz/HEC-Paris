"""Swan OAuth client — `client_credentials` token cache.

Source: SWAN_API_REFERENCE.md:27-46 (OAuth endpoint, no `scope` parameter,
3600s token lifetime), Dev orchestration/swan/CLAUDE.md (refresh on 401 or
60s before expiry, whichever comes first), Phase 2 plan §Task 4.

The class is intentionally minimal: one in-process token cache, refreshed
lazily, invalidated on demand by `SwanGraphQLClient` when a 401 comes
back. No background tasks, no retries here — retries live in the GraphQL
layer where the failure context is richer.
"""
from __future__ import annotations

import time
from typing import Any

import httpx


# Refresh this many seconds *before* the token's stated expiry. Keeps us
# away from the boundary where the server may reject just-expired tokens.
_REFRESH_LEAD_SECONDS = 60


class SwanOAuthClient:
    """Client-credentials token cache for Swan's OAuth2 endpoint.

    Construct once per process (or per app); call `get_token()` before each
    GraphQL request. Tokens are cached in-process and refreshed lazily.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        oauth_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._oauth_url = oauth_url
        # Caller-supplied client wins; otherwise we lazily build (and own)
        # one. Tests pass `httpx.AsyncClient(transport=MockTransport(...))`.
        self._http: httpx.AsyncClient = http_client or httpx.AsyncClient()
        self._owns_http = http_client is None

        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get_token(self) -> str:
        """Return a cached token if fresh, otherwise refresh + return."""
        now = time.time()
        if self._token is not None and now < self._expires_at - _REFRESH_LEAD_SECONDS:
            return self._token
        await self._refresh_token()
        # _refresh_token always sets _token; assert for the type-checker.
        assert self._token is not None
        return self._token

    async def _refresh_token(self) -> None:
        """POST `client_credentials` and update the cache.

        GOTCHA: do **not** send a `scope` parameter; Swan returns 400.
        """
        payload: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        response = await self._http.post(self._oauth_url, data=payload)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        self._token = body["access_token"]
        # `expires_in` is seconds; relative to *now*, not the response time.
        # Close enough for a 3600s token cached for ~one hour.
        self._expires_at = time.time() + float(body["expires_in"])

    async def invalidate(self) -> None:
        """Drop the cached token; next `get_token()` will refresh.

        Called by `SwanGraphQLClient` when the API returns 401 — covers the
        edge case where Swan revoked the token before its stated expiry.
        """
        self._token = None
        self._expires_at = 0.0

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_http:
            await self._http.aclose()
