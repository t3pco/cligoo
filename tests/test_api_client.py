"""Tests for cligoo.api.DegooClient construction behaviour.

Covers:
- DEFAULT_HEADERS isolation: each DegooClient gets an independent header dict
  so httpx normalisation on one instance never mutates the shared module-level
  dict or another client's headers.
- Token passthrough: when a token is supplied to the constructor the client
  returns it via `.token` without making a second call to get_token().
"""

from __future__ import annotations

from unittest.mock import patch

from cligoo.api import DegooClient
from cligoo.constants import DEFAULT_HEADERS

# ── DEFAULT_HEADERS isolation ─────────────────────────────────────────────────


def test_client_headers_are_independent_of_module_dict():
    """Mutating a client's HTTP headers must not change DEFAULT_HEADERS."""
    client = DegooClient(token="test-token")
    try:
        # Simulate what httpx does: normalise header names to lowercase
        client._http.headers["x-injected"] = "yes"

        assert "x-injected" not in DEFAULT_HEADERS
    finally:
        client.close()


def test_two_clients_have_independent_headers():
    """Mutating one client's headers must not affect a second client's headers."""
    c1 = DegooClient(token="tok1")
    c2 = DegooClient(token="tok2")
    try:
        c1._http.headers["x-only-c1"] = "1"

        assert "x-only-c1" not in c2._http.headers
    finally:
        c1.close()
        c2.close()


def test_default_headers_unchanged_after_client_creation():
    """DEFAULT_HEADERS must be identical before and after constructing a client."""
    snapshot = dict(DEFAULT_HEADERS)
    client = DegooClient(token="tok")
    try:
        assert dict(DEFAULT_HEADERS) == snapshot
    finally:
        client.close()


# ── Token passthrough ──────────────────────────────────────────────────────────


def test_token_passthrough_does_not_call_get_token():
    """When token= is supplied, .token must return it without fetching."""
    with patch("cligoo.api.get_token") as mock_get:
        client = DegooClient(token="supplied-token")
        try:
            assert client.token == "supplied-token"
            mock_get.assert_not_called()
        finally:
            client.close()


def test_token_fetched_lazily_when_not_supplied():
    """When no token is supplied, get_token() is called on first .token access."""
    with patch("cligoo.api.get_token", return_value="lazy-token") as mock_get:
        client = DegooClient()
        try:
            tok = client.token
            assert tok == "lazy-token"
            mock_get.assert_called_once()
        finally:
            client.close()


def test_token_fetched_on_every_access_for_auto_refresh():
    """Without an explicit token, .token must call get_token() each time so
    that short-lived access tokens are renewed transparently during long-running
    operations (shell sessions, bulk uploads, etc.)."""
    with patch("cligoo.api.get_token", return_value="fresh-token") as mock_get:
        client = DegooClient()
        try:
            assert client.token == "fresh-token"
            assert client.token == "fresh-token"
            assert mock_get.call_count == 2  # called on every access — intentional
        finally:
            client.close()
