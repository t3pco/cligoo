"""Tests for cligoo.constants."""

from cligoo.constants import (
    API_KEY,
    CATEGORY_NAMES,
    CHECKSUM_SEED,
    DEFAULT_HEADERS,
    FOLDER_CATEGORIES,
    GRAPHQL_URL,
    LOGIN_URL,
    TOKEN_REFRESH_URL,
)


def test_graphql_url():
    assert GRAPHQL_URL.startswith("https://")
    assert "graphql" in GRAPHQL_URL


def test_login_url():
    assert LOGIN_URL.startswith("https://")
    assert "login" in LOGIN_URL


def test_token_refresh_url():
    assert TOKEN_REFRESH_URL.startswith("https://")
    assert "access-token" in TOKEN_REFRESH_URL


def test_api_key_format():
    # The bundled key starts with "da2-".  When DEGOO_API_KEY is set in the
    # environment (e.g. CI with a rotated key) the prefix may differ, so we
    # only assert the key is non-empty and of reasonable length.
    import os

    if os.environ.get("DEGOO_API_KEY"):
        assert len(API_KEY) > 5
    else:
        assert API_KEY.startswith("da2-")
        assert len(API_KEY) > 10


def test_default_headers():
    assert "x-api-key" in DEFAULT_HEADERS
    assert "Content-Type" in DEFAULT_HEADERS
    assert DEFAULT_HEADERS["Content-Type"] == "application/json"


def test_category_names():
    assert CATEGORY_NAMES[0] == "File"
    assert CATEGORY_NAMES[2] == "Folder"
    assert CATEGORY_NAMES[10] == "Recycle Bin"


def test_folder_categories():
    assert 2 in FOLDER_CATEGORIES  # Folder
    assert 1 in FOLDER_CATEGORIES  # Device
    assert 0 not in FOLDER_CATEGORIES  # File is not a folder


def test_checksum_seed():
    assert len(CHECKSUM_SEED) == 16
    assert all(isinstance(b, int) for b in CHECKSUM_SEED)
