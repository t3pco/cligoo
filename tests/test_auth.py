"""Tests for cligoo.auth — token store and expiry logic."""

import json
import time
from unittest.mock import patch

import jwt as pyjwt

from cligoo.auth import TokenStore, _token_expired


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
