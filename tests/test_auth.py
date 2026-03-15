"""Tests for cligoo.auth — token store and expiry logic."""

import json
import time
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest

from cligoo.auth import (
    AuthError,
    TokenStore,
    _check_login_backoff,
    _token_expired,
)


# ── Token expiry ──────────────────────────────────────────────────────────────
def _make_jwt(exp_offset: int = 3600) -> str:
    """Create a test JWT with the given expiry offset from now."""
    payload = {"userID": "test", "exp": int(time.time()) + exp_offset}
    return pyjwt.encode(payload, "secret", algorithm="HS256")


def test_token_not_expired():
    token = _make_jwt(exp_offset=3600)  # expires in 1 hour
    assert not _token_expired(token)


def test_token_expired():
    token = _make_jwt(exp_offset=-100)  # expired 100s ago
    assert _token_expired(token)


def test_token_expiring_within_margin():
    token = _make_jwt(exp_offset=30)  # expires in 30s, margin is 60s
    assert _token_expired(token, margin=60)


def test_token_invalid_string():
    assert _token_expired("not-a-jwt")


def test_token_empty():
    assert _token_expired("")


# ── TokenStore file fallback ──────────────────────────────────────────────────
def test_token_store_save_load(tmp_path):
    """TokenStore file fallback saves and loads correctly."""
    token_file = tmp_path / "tokens.json"

    with patch("cligoo.auth.TOKEN_FILE", token_file), patch("cligoo.auth.CONFIG_DIR", tmp_path):
        store = TokenStore()
        store._use_keyring = False  # force file fallback

        store.save("access_tok", "refresh_tok")
        assert token_file.exists()

        tok, ref = store.load()
        assert tok == "access_tok"
        assert ref == "refresh_tok"


def test_token_store_clear(tmp_path):
    token_file = tmp_path / "tokens.json"
    token_file.write_text(json.dumps({"token": "x", "refresh_token": "y"}))

    with patch("cligoo.auth.TOKEN_FILE", token_file):
        store = TokenStore()
        store._use_keyring = False

        store.clear()
        assert not token_file.exists()


def test_token_store_load_missing(tmp_path):
    token_file = tmp_path / "nonexistent.json"

    with patch("cligoo.auth.TOKEN_FILE", token_file):
        store = TokenStore()
        store._use_keyring = False

        tok, ref = store.load()
        assert tok is None
        assert ref is None


# ── Legacy keyring migration ───────────────────────────────────────────────────
def test_token_migration_normalises_none_refresh_token(tmp_path):
    """Migration must return ref='' not ref=None to avoid passing None to login()."""
    mock_kr = MagicMock()
    # Legacy entry has token but no refresh_token (returns None)
    mock_kr.get_password.side_effect = lambda svc, key: "tok123" if svc == "degoo-cli" and key == "token" else None
    mock_kr.delete_password.return_value = None

    token_file = tmp_path / "tokens.json"
    with (
        patch("cligoo.auth.TOKEN_FILE", token_file),
        patch("cligoo.auth.CONFIG_DIR", tmp_path),
    ):
        store = TokenStore()
        store._use_keyring = True
        store._kr = mock_kr
        # Patch save() to avoid writing to real keyring
        store.save = MagicMock()

        tok, ref = store.load()
        assert tok == "tok123"
        assert ref == "", "ref must be '' not None to prevent login(email, None)"


def test_credential_migration_normalises_none_password(tmp_path):
    """Migration must return pw='' not pw=None to avoid passing None to login()."""
    mock_kr = MagicMock()
    mock_kr.get_password.side_effect = lambda svc, key: (
        "user@example.com" if svc == "degoo-cli" and key == "email" else None
    )
    mock_kr.delete_password.return_value = None

    cred_file = tmp_path / "credentials.json"
    with (
        patch("cligoo.auth.CRED_FILE", cred_file),
        patch("cligoo.auth.CONFIG_DIR", tmp_path),
    ):
        store = TokenStore()
        store._use_keyring = True
        store._kr = mock_kr
        store.save_credentials = MagicMock()

        email, pw = store.load_credentials()
        assert email == "user@example.com"
        assert pw == "", "pw must be '' not None to prevent login(email, None)"


# ── Login rate-limit backoff ───────────────────────────────────────────────────
def test_check_login_backoff_corrupted_file_is_deleted_and_returns_none(tmp_path):
    """A corrupt backoff file must be deleted (not left in place) and backoff returns None."""
    backoff_file = tmp_path / ".login_backoff"
    backoff_file.write_text("not-a-float", encoding="utf-8")

    with patch("cligoo.auth._LOGIN_BACKOFF_FILE", backoff_file):
        result = _check_login_backoff()

    assert result is None, "corrupted file should not enforce backoff"
    assert not backoff_file.exists(), "corrupted backoff file must be deleted"


def test_login_raises_rate_limit_error_on_429(tmp_path):
    """login() must immediately raise a friendly rate-limit AuthError on 429."""
    import httpx

    from cligoo.auth import login

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 429
    mock_resp.text = ""

    backoff_file = tmp_path / ".login_backoff"
    with (
        patch("cligoo.auth._LOGIN_BACKOFF_FILE", backoff_file),
        patch("cligoo.auth._check_login_backoff", return_value=None),
        patch("httpx.post", return_value=mock_resp),
    ):
        with pytest.raises(AuthError, match="rate-limited"):
            login("user@example.com", "password", save=False)

    assert backoff_file.exists(), "backoff file must be written on 429"
