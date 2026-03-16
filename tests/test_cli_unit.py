"""Unit tests for cligoo.cli — all commands via Click test runner.

All external I/O (DegooClient, file system) is mocked so these run offline.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cligoo.api import DegooAPIError, DegooClient
from cligoo.cli import main

# ── Helpers ───────────────────────────────────────────────────────────────────


def _runner():
    return CliRunner()


def _mock_client(**kwargs):
    """Return a MagicMock that looks like DegooClient with sensible defaults."""
    m = MagicMock()

    # Default user info — CLI uses FirstName+LastName, not a single "Name" key
    m.get_user_info.return_value = {
        "FirstName": "Test",
        "LastName": "User",
        "Email": "test@example.com",
        "AccountType": 3,
        "UsedQuota": str(1024**3),
        "TotalQuota": str(10 * 1024**3),
        "FileSizeLimit": str(50 * 1024**3),
    }

    # Default root listing
    _device = {
        "ID": "111",
        "Name": "Web",
        "Category": 1,
        "Size": "0",
        "LastModificationTime": "1000",
        "LastUploadTime": "1000",
        "ParentID": "0",
        "FilePath": "/Web",
        "URL": "",
    }
    m.list_dir.return_value = [_device]

    # Default resolve_path
    m.resolve_path.return_value = _device

    # Default search results
    m.search.return_value = []

    # Default item
    m.get_item.return_value = {
        "ID": "999",
        "Name": "photo.jpg",
        "Category": 6,
        "Size": "500000",
        "ParentID": "111",
        "LastModificationTime": "1700000000",
        "LastUploadTime": "1700000000",
        "CreationTime": "1700000000",
        "FilePath": "/Web/photo.jpg",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "https://cdn.example.com/photo.jpg",
        "ThumbnailURL": "",
    }

    m.get_feed.return_value = []
    m.list_trash.return_value = []
    m.list_shared.return_value = []
    m.list_collections.return_value = []

    m.mkdir.return_value = True
    m.delete.return_value = True
    m.move.return_value = True
    m.rename.return_value = True

    # is_folder is a static method on DegooClient — delegate to the real
    # implementation so tests that exercise the folder/file branch work correctly.
    m.is_folder.side_effect = DegooClient.is_folder

    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def _patch_client(mock):
    """Patch cligoo.cli._client to return *mock*."""
    return patch("cligoo.cli._client", return_value=mock)


# ── Auth commands ─────────────────────────────────────────────────────────────


def test_token_command(tmp_path):
    """degoo token <TOKEN> persists the token."""
    with patch("cligoo.cli.save_token_direct") as mock_save:
        result = _runner().invoke(main, ["token", "eyJfaketoken123"])
        assert result.exit_code == 0
        mock_save.assert_called_once_with("eyJfaketoken123", "")


def test_logout_command(tmp_path):
    """degoo logout calls the underlying auth logout function."""
    with patch("cligoo.auth.logout") as mock_logout:
        result = _runner().invoke(main, ["logout"])
        assert result.exit_code == 0, result.output
        mock_logout.assert_called_once()


def test_login_429_sets_backoff_and_next_call_is_blocked(tmp_path):
    """A 429 from Degoo sets a backoff; subsequent login attempts fail locally."""
    import pytest

    from cligoo.auth import AuthError, login

    backoff_file = tmp_path / ".login_backoff"

    # Patch the module-level backoff file path
    with patch("cligoo.auth._LOGIN_BACKOFF_FILE", backoff_file):
        # Simulate a 429 response
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.text = ""

        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(AuthError) as exc_info:
                login("user@example.com", "pw")
            assert "429" in str(exc_info.value) or "rate" in str(exc_info.value).lower()

        # Backoff file should now exist
        assert backoff_file.exists()

        # Attempting login again should fail immediately (without hitting the network)
        with patch("httpx.post") as mock_post:
            with pytest.raises(AuthError) as exc_info2:
                login("user@example.com", "pw")
            assert "rate" in str(exc_info2.value).lower() or "wait" in str(exc_info2.value).lower()
            mock_post.assert_not_called()  # no HTTP call made


def test_login_bare_uses_password_by_default(tmp_path):
    """degoo login with no flags uses the email/password flow when not configured."""
    with (
        patch("cligoo.cli.get_login_method", return_value=None),
        patch("cligoo.cli.get_saved_credentials", return_value=(None, None)),
        patch("cligoo.cli.fetch_token_via_browser") as mock_browser,
        patch("cligoo.auth.login", return_value="tok"),
    ):
        _runner().invoke(main, ["login"], input="user@example.com\npassword\n")
        # browser flow must NOT have been called
        mock_browser.assert_not_called()


def test_login_bare_uses_browser_when_configured():
    """degoo login with no flags triggers browser flow when login_method=browser."""
    with (
        patch("cligoo.cli.get_login_method", return_value="browser"),
        patch("cligoo.cli.fetch_token_via_browser", return_value=("eyTok", "")) as mock_browser,
        patch("cligoo.cli.save_token_direct") as mock_save,
        patch("cligoo.config.get_chrome_profile", return_value=None),
        patch("cligoo.chrome.list_profiles", return_value=[]),
    ):
        result = _runner().invoke(main, ["login"])
        mock_browser.assert_called_once()
        mock_save.assert_called_once_with("eyTok", "")
        assert result.exit_code == 0, result.output


def test_login_browser_flag_always_uses_browser():
    """degoo login --browser always triggers browser flow regardless of config."""
    with (
        patch("cligoo.cli.get_login_method", return_value="password"),
        patch("cligoo.cli.fetch_token_via_browser", return_value=("eyTok", "")) as mock_browser,
        patch("cligoo.cli.save_token_direct"),
        patch("cligoo.config.get_chrome_profile", return_value=None),
        patch("cligoo.chrome.list_profiles", return_value=[]),
    ):
        result = _runner().invoke(main, ["login", "--browser"])
        mock_browser.assert_called_once()
        assert result.exit_code == 0, result.output


# ── whoami / quota ────────────────────────────────────────────────────────────


def test_whoami_no_token_shows_clean_error():
    """_client() must catch AuthError eagerly and print a clean ✗ line."""
    from cligoo.auth import AuthError

    # _client() re-imports get_token on every call (local import inside the
    # function body), so the correct patch target is the module-level name.
    with patch(
        "cligoo.auth.get_token",
        side_effect=AuthError("No valid token found."),
    ):
        result = _runner().invoke(main, ["whoami"])
    assert result.exit_code != 0
    # Should contain the ✗ marker, not a Python traceback
    assert "No valid token found" in result.output
    assert "Traceback" not in result.output
    assert "AuthError" not in result.output


def test_whoami():
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "Test User" in result.output
    assert "test@example.com" in result.output


def test_quota():
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["quota"])
    assert result.exit_code == 0, result.output
    assert "Used" in result.output
    assert "Total" in result.output


def test_quota_api_error():
    mock = _mock_client()
    mock.get_user_info.side_effect = DegooAPIError("quota error")
    with _patch_client(mock):
        result = _runner().invoke(main, ["quota"])
    assert result.exit_code != 0


def test_quota_json_free_bytes_never_negative():
    """quota --output json free_bytes must be >= 0 when TotalQuota is absent/zero."""
    mock = _mock_client()
    # TotalQuota absent → defaults to 1; UsedQuota large → would be negative without guard
    mock.get_user_info.return_value = {"UsedQuota": 999_000_000_000, "TotalQuota": 0}
    with _patch_client(mock):
        result = _runner().invoke(main, ["quota", "--output", "json"])
    assert result.exit_code == 0, result.output
    import json

    data = json.loads(result.output)
    assert data["free_bytes"] >= 0, f"free_bytes was negative: {data['free_bytes']}"


# ── pwd / cd ──────────────────────────────────────────────────────────────────


def test_pwd_disabled_by_default():
    """pwd should fail with a helpful message when standalone_nav is off."""
    with patch("cligoo.cli.get_standalone_nav_enabled", return_value=False):
        result = _runner().invoke(main, ["pwd"])
    assert result.exit_code != 0
    assert "standalone_nav" in result.output


def test_cd_disabled_by_default():
    """cd should fail with a helpful message when standalone_nav is off."""
    with patch("cligoo.cli.get_standalone_nav_enabled", return_value=False):
        result = _runner().invoke(main, ["cd", "/Web"])
    assert result.exit_code != 0
    assert "standalone_nav" in result.output


def test_pwd_enabled(tmp_path):
    """When standalone_nav is enabled, pwd shows the stored CWD."""
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text(json.dumps({"path": "/Web"}))
    with patch("cligoo.cli.get_standalone_nav_enabled", return_value=True), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["pwd"])
    assert result.exit_code == 0, result.output
    assert "/Web" in result.output


def test_pwd_enabled_default(tmp_path):
    """When CWD file is absent, pwd shows / (the default)."""
    cwd_file = tmp_path / "cwd.json"  # does not exist
    with patch("cligoo.cli.get_standalone_nav_enabled", return_value=True), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["pwd"])
    assert result.exit_code == 0, result.output
    assert "/" in result.output


def test_cd_root_enabled(tmp_path):
    """cd / should succeed when standalone_nav is enabled."""
    cwd_file = tmp_path / "cwd.json"
    mock = _mock_client()
    with (
        patch("cligoo.cli.get_standalone_nav_enabled", return_value=True),
        _patch_client(mock),
        patch("cligoo.cli._CWD_FILE", cwd_file),
    ):
        result = _runner().invoke(main, ["cd", "/"])
    assert result.exit_code == 0, result.output


def test_cd_valid_path_enabled(tmp_path):
    """cd into an existing folder should succeed when standalone_nav is enabled."""
    cwd_file = tmp_path / "cwd.json"
    mock = _mock_client()
    folder = {"ID": "111", "Name": "Web", "Category": 1, "Size": "0", "LastModificationTime": "1000", "URL": ""}
    mock.resolve_path.return_value = folder
    with (
        patch("cligoo.cli.get_standalone_nav_enabled", return_value=True),
        _patch_client(mock),
        patch("cligoo.cli._CWD_FILE", cwd_file),
    ):
        result = _runner().invoke(main, ["cd", "/Web"])
    assert result.exit_code == 0, result.output
    assert "Web" in result.output


def test_cd_not_found_enabled(tmp_path):
    """cd into a non-existent path should fail when standalone_nav is enabled."""
    cwd_file = tmp_path / "cwd.json"
    mock = _mock_client()
    mock.resolve_path.return_value = None
    with (
        patch("cligoo.cli.get_standalone_nav_enabled", return_value=True),
        _patch_client(mock),
        patch("cligoo.cli._CWD_FILE", cwd_file),
    ):
        result = _runner().invoke(main, ["cd", "/nonexistent"])
    assert result.exit_code != 0


def test_cd_file_rejected_enabled(tmp_path):
    """cd into a real file (has URL) should be rejected even when standalone_nav is enabled."""
    cwd_file = tmp_path / "cwd.json"
    mock = _mock_client()
    mock.resolve_path.return_value = {
        "ID": "999",
        "Name": "photo.jpg",
        "Category": 6,
        "URL": "https://cdn.example.com/photo.jpg",
    }
    with (
        patch("cligoo.cli.get_standalone_nav_enabled", return_value=True),
        _patch_client(mock),
        patch("cligoo.cli._CWD_FILE", cwd_file),
    ):
        result = _runner().invoke(main, ["cd", "/Web/photo.jpg"])
    assert result.exit_code != 0


# ── ls ────────────────────────────────────────────────────────────────────────


def test_ls_root():
    mock = _mock_client()
    mock.list_dir.return_value = [
        {
            "ID": "111",
            "Name": "Web",
            "Category": 1,
            "Size": "0",
            "LastModificationTime": "1000",
            "LastUploadTime": "1000",
            "ParentID": "0",
            "URL": "",
        },
    ]
    with _patch_client(mock), patch("cligoo.cli._load_cwd", return_value="/"):
        result = _runner().invoke(main, ["ls"])
    assert result.exit_code == 0, result.output
    assert "Web" in result.output


def test_ls_long():
    mock = _mock_client()
    mock.list_dir.return_value = [
        {
            "ID": "111",
            "Name": "Web",
            "Category": 1,
            "Size": "0",
            "LastModificationTime": "1700000000",
            "LastUploadTime": "1700000000",
            "ParentID": "0",
            "URL": "",
        },
    ]
    with _patch_client(mock), patch("cligoo.cli._load_cwd", return_value="/"):
        result = _runner().invoke(main, ["ls", "-l"])
    assert result.exit_code == 0, result.output
    assert "111" in result.output  # ID shown in long form


def test_ls_depth():
    mock = _mock_client()
    root_items = [
        {
            "ID": "111",
            "Name": "Web",
            "Category": 1,
            "Size": "0",
            "LastModificationTime": "1000",
            "LastUploadTime": "1000",
            "ParentID": "0",
            "URL": "",
        },
    ]
    child_items = [
        {
            "ID": "222",
            "Name": "Photos",
            "Category": 2,
            "Size": "0",
            "LastModificationTime": "1000",
            "LastUploadTime": "1000",
            "ParentID": "111",
            "URL": "",
        },
    ]
    mock.list_dir.side_effect = lambda pid, **kw: root_items if pid == "0" else child_items
    with _patch_client(mock), patch("cligoo.cli._load_cwd", return_value="/"):
        result = _runner().invoke(main, ["ls", "-d", "2"])
    assert result.exit_code == 0, result.output
    assert "Web" in result.output


def test_ls_file_shows_info():
    """ls on a file path shows item detail, not a listing."""
    mock = _mock_client()
    mock.resolve_path.return_value = {
        "ID": "999",
        "Name": "photo.jpg",
        "Category": 6,
        "Size": "500000",
        "LastModificationTime": "1700000000",
        "LastUploadTime": "1700000000",
        "CreationTime": "1700000000",
        "ParentID": "111",
        "FilePath": "/Web/photo.jpg",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "https://cdn.example.com/photo.jpg",
        "ThumbnailURL": "",
    }
    with _patch_client(mock):
        result = _runner().invoke(main, ["ls", "/Web/photo.jpg"])
    assert result.exit_code == 0, result.output
    assert "photo.jpg" in result.output


def test_ls_path_not_found():
    mock = _mock_client()
    mock.resolve_path.return_value = None
    with _patch_client(mock):
        result = _runner().invoke(main, ["ls", "/nonexistent"])
    assert result.exit_code != 0


# ── tree ──────────────────────────────────────────────────────────────────────


def test_tree():
    mock = _mock_client()
    mock.list_dir.return_value = [
        {
            "ID": "111",
            "Name": "Photos",
            "Category": 2,
            "Size": "0",
            "LastModificationTime": "1000",
            "LastUploadTime": "1000",
            "ParentID": "0",
            "URL": "",
        },
    ]
    with _patch_client(mock), patch("cligoo.cli._load_cwd", return_value="/"):
        result = _runner().invoke(main, ["tree"])
    assert result.exit_code == 0, result.output
    assert "Photos" in result.output


# ── info ──────────────────────────────────────────────────────────────────────


def test_info():
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["info", "999"])
    assert result.exit_code == 0, result.output
    assert "photo.jpg" in result.output
    assert "999" in result.output


def test_info_api_error():
    mock = _mock_client()
    mock.get_item.side_effect = DegooAPIError("not found")
    with _patch_client(mock):
        result = _runner().invoke(main, ["info", "000"])
    assert result.exit_code != 0


def test_info_with_path():
    """degoo info /Web/Photos/photo.jpg resolves via resolve_path, not get_item.

    Regression guard: previously info only called get_item() and could not
    accept path strings.
    """
    mock = _mock_client()
    mock.resolve_path.return_value = {
        "ID": "999",
        "Name": "photo.jpg",
        "Category": 6,
        "Size": "500000",
        "ParentID": "111",
        "LastModificationTime": "1700000000",
        "LastUploadTime": "1700000000",
        "CreationTime": "1700000000",
        "FilePath": "/Web/Photos/photo.jpg",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "https://cdn.example.com/photo.jpg",
        "ThumbnailURL": "",
    }
    with _patch_client(mock):
        result = _runner().invoke(main, ["info", "/Web/Photos/photo.jpg"])
    assert result.exit_code == 0, result.output
    assert "photo.jpg" in result.output
    mock.resolve_path.assert_called()
    mock.get_item.assert_not_called()


def test_info_folder_shows_content_size():
    """info on a folder should walk children and report total content size."""
    mock = _mock_client()
    folder = {
        "ID": "200",
        "Name": "Photos",
        "Category": 2,  # CATEGORY_FOLDER
        "Size": "0",
        "ParentID": "111",
        "LastModificationTime": "1700000000",
        "CreationTime": "1700000000",
        "FilePath": "/Web/Photos",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "",
        "ThumbnailURL": "",
    }
    file_a = {"ID": "201", "Name": "a.jpg", "Category": 6, "Size": "1000000", "URL": "https://x"}
    file_b = {"ID": "202", "Name": "b.jpg", "Category": 6, "Size": "2000000", "URL": "https://x"}
    mock.get_item.return_value = folder
    mock.is_folder.side_effect = lambda item: item.get("Category", 0) in {1, 2, 3}
    mock.iter_dir.side_effect = lambda fid, **_: iter([file_a, file_b] if fid == "200" else [])
    with _patch_client(mock):
        result = _runner().invoke(main, ["info", "200"])
    assert result.exit_code == 0, result.output
    assert "Content Size" in result.output
    # Exactly 2 files should be reported on the Files row (not Sub-folders)
    import re

    lines = result.output.splitlines()
    files_line = next((ln for ln in lines if "Files" in ln and "Sub-folders" not in ln), None)
    assert files_line is not None, "Expected a 'Files' row in output"
    assert re.search(r"\b2\b", files_line), f"Expected file count 2 on Files row, got: {files_line}"


def test_info_folder_no_size_flag():
    """--no-size should skip content size calculation and report 'skipped'."""
    mock = _mock_client()
    folder = {
        "ID": "200",
        "Name": "Photos",
        "Category": 2,
        "Size": "0",
        "ParentID": "111",
        "LastModificationTime": "1700000000",
        "CreationTime": "1700000000",
        "FilePath": "/Web/Photos",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "",
        "ThumbnailURL": "",
    }
    mock.get_item.return_value = folder
    mock.is_folder.side_effect = lambda item: item.get("Category", 0) in {1, 2, 3}
    with _patch_client(mock):
        result = _runner().invoke(main, ["info", "--no-size", "200"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    mock.iter_dir.assert_not_called()


def test_info_folder_size_tolerates_float_string_sizes():
    """_compute_folder_size must count files whose Size is a float-string like '1048576.0'."""
    mock = _mock_client()
    folder = {
        "ID": "300",
        "Name": "Floats",
        "Category": 2,
        "Size": "0",
        "ParentID": "0",
        "LastModificationTime": "0",
        "CreationTime": "0",
        "FilePath": "/Floats",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "",
        "ThumbnailURL": "",
    }
    children = [
        {"ID": "301", "Name": "a.mp4", "Category": 8, "Size": "1048576.0", "URL": "http://x"},
        {"ID": "302", "Name": "b.mp4", "Category": 8, "Size": "2097152.0", "URL": "http://y"},
    ]
    mock.get_item.return_value = folder
    mock.is_folder.side_effect = lambda item: item.get("Category", 0) in {1, 2, 3}
    mock.iter_dir.return_value = iter(children)
    with _patch_client(mock):
        result = _runner().invoke(main, ["info", "300"])
    assert result.exit_code == 0, result.output
    # 1 048 576 + 2 097 152 = 3 145 728 bytes = 3.0 MiB — must appear in output
    assert "3.0" in result.output or "3 MB" in result.output or "3145728" in result.output, result.output


def test_info_folder_incomplete_on_api_error():
    """When iter_dir raises DegooAPIError mid-walk, Content Size row shows (incomplete)."""
    from cligoo.api import DegooAPIError as _APIError

    mock = _mock_client()
    folder = {
        "ID": "200",
        "Name": "Photos",
        "Category": 2,
        "Size": "0",
        "ParentID": "111",
        "LastModificationTime": "1700000000",
        "CreationTime": "1700000000",
        "FilePath": "/Web/Photos",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "",
        "ThumbnailURL": "",
    }
    mock.get_item.return_value = folder
    mock.is_folder.side_effect = lambda item: item.get("Category", 0) in {1, 2, 3}
    mock.iter_dir.side_effect = _APIError("token expired")
    with _patch_client(mock):
        result = _runner().invoke(main, ["info", "200"])
    assert result.exit_code == 0, result.output
    assert "incomplete" in result.output


# ── search ────────────────────────────────────────────────────────────────────


def test_search_no_args():
    """Missing TERM should print help, not a bare error."""
    result = _runner().invoke(main, ["search"])
    assert "Usage:" in result.output or "Usage:" in (result.output)
    assert "TERM" in (result.output)


def test_search_with_results():
    mock = _mock_client()
    mock.search.return_value = [
        {"ID": "1", "Name": "holiday.jpg", "Category": 6, "Size": "1024000", "FilePath": "/iPhone/Photos/holiday.jpg"},
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["search", "holiday"])
    assert result.exit_code == 0, result.output
    assert "holiday.jpg" in result.output


def test_search_no_results():
    mock = _mock_client()
    mock.search.return_value = []
    with _patch_client(mock):
        result = _runner().invoke(main, ["search", "zzznomatch"])
    assert result.exit_code == 0, result.output
    assert "No results" in result.output


def test_search_api_error():
    mock = _mock_client()
    mock.search.side_effect = DegooAPIError("search failed")
    with _patch_client(mock):
        result = _runner().invoke(main, ["search", "test"])
    assert result.exit_code != 0


# ── mkdir ─────────────────────────────────────────────────────────────────────


def test_mkdir_full_path(tmp_path):
    cwd_file = tmp_path / "cwd.json"
    mock = _mock_client()
    parent = {"ID": "111", "Name": "Web", "Category": 1, "Size": "0", "LastModificationTime": "1000", "URL": ""}
    mock.resolve_path.return_value = parent
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["mkdir", "/Web/NewFolder"])
    assert result.exit_code == 0, result.output
    assert "NewFolder" in result.output
    mock.mkdir.assert_called_once_with("NewFolder", "111")


def test_mkdir_bare_name_uses_cwd(tmp_path):
    """mkdir FolderName (no slash) uses CWD as parent."""
    cwd_file = tmp_path / "cwd.json"
    cwd_file.parent.mkdir(parents=True, exist_ok=True)
    # _load_cwd() reads key "path"
    cwd_file.write_text(json.dumps({"path": "/Web"}))
    mock = _mock_client()
    parent = {"ID": "111", "Name": "Web", "Category": 1, "Size": "0", "LastModificationTime": "1000", "URL": ""}
    mock.resolve_path.return_value = parent
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["mkdir", "NewFolder"])
    assert result.exit_code == 0, result.output
    mock.mkdir.assert_called_once_with("NewFolder", "111")


def test_mkdir_api_error(tmp_path):
    cwd_file = tmp_path / "cwd.json"
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.mkdir.side_effect = DegooAPIError("creation error")
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["mkdir", "/Web/Fail"])
    assert result.exit_code != 0


# ── upload ────────────────────────────────────────────────────────────────────


def test_upload_to_cwd(tmp_path):
    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello")
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text(json.dumps({"path": "/Web"}))
    mock = _mock_client()
    parent = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.resolve_path.return_value = parent
    mock.upload.return_value = True
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["upload", str(test_file)])
    assert result.exit_code == 0, result.output
    assert "1 uploaded" in result.output
    mock.upload.assert_called_once()


def test_upload_with_explicit_path(tmp_path):
    test_file = tmp_path / "data.bin"
    test_file.write_bytes(b"\x00" * 100)
    cwd_file = tmp_path / "cwd.json"
    mock = _mock_client()
    parent = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.resolve_path.return_value = parent
    mock.upload.return_value = True
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["upload", str(test_file), "--dest", "/Web"])
    assert result.exit_code == 0, result.output


def test_upload_api_error(tmp_path):
    test_file = tmp_path / "fail.txt"
    test_file.write_text("oops")
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text(json.dumps({"path": "/Web"}))
    mock = _mock_client()
    parent = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.resolve_path.return_value = parent
    mock.upload.side_effect = DegooAPIError("upload failed")
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["upload", str(test_file)])
    assert result.exit_code != 0


def test_upload_directory_without_r_errors(tmp_path):
    """Passing a directory without -r must error with a helpful message."""
    local_dir = tmp_path / "mydir"
    local_dir.mkdir()
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text(json.dumps({"path": "/Web"}))
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["upload", str(local_dir)])
    assert result.exit_code != 0
    assert "directory" in result.output.lower() or "recursive" in result.output.lower()


def test_upload_directory_recursive(tmp_path):
    """Recursive directory upload: mkdir is called for the folder, upload per file."""
    local_dir = tmp_path / "holiday"
    local_dir.mkdir()
    (local_dir / "beach.jpg").write_bytes(b"\xff" * 100)
    (local_dir / "sunset.jpg").write_bytes(b"\xfe" * 200)
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text(json.dumps({"path": "/Web"}))
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    # resolve_path_under returns the newly-created folder so _upload_dir_recursive
    # can recover its ID without needing an absolute path
    mock.resolve_path_under.return_value = {"ID": "222", "Name": "holiday", "Category": 2}
    mock.upload.return_value = True
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["upload", str(local_dir), "--dest", "/Web", "-r"])
    assert result.exit_code == 0, result.output
    mock.mkdir.assert_called_once_with("holiday", "111")
    assert mock.upload.call_count == 2


def test_upload_multi_file(tmp_path):
    """Multiple source files in one command → upload called once per file."""
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.jpg"
    f1.write_bytes(b"\xff" * 10)
    f2.write_bytes(b"\xfe" * 10)
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text(json.dumps({"path": "/Web"}))
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.upload.return_value = True
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(main, ["upload", str(f1), str(f2), "--dest", "/Web"])
    assert result.exit_code == 0, result.output
    assert mock.upload.call_count == 2


def test_upload_exclude_pattern(tmp_path):
    """--exclude glob pattern skips matching files during recursive upload."""
    local_dir = tmp_path / "proj"
    local_dir.mkdir()
    (local_dir / "main.py").write_text("code")
    (local_dir / "temp.tmp").write_text("temp")
    (local_dir / ".DS_Store").write_text("mac")
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text(json.dumps({"path": "/Web"}))
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.resolve_path_under.return_value = {"ID": "222", "Name": "proj", "Category": 2}
    mock.upload.return_value = True
    with _patch_client(mock), patch("cligoo.cli._CWD_FILE", cwd_file):
        result = _runner().invoke(
            main,
            ["upload", str(local_dir), "--dest", "/Web", "-r", "--exclude", "*.tmp", "--exclude", ".DS_Store"],
        )
    assert result.exit_code == 0, result.output
    # Only main.py should be uploaded; *.tmp and .DS_Store are excluded
    assert mock.upload.call_count == 1


# ── download ──────────────────────────────────────────────────────────────────


def test_download_by_id(tmp_path):
    mock = _mock_client()
    mock.download.return_value = tmp_path / "photo.jpg"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "999", "--dest", str(tmp_path)])
    assert result.exit_code == 0, result.output
    mock.download.assert_called_once()


def test_download_api_error():
    mock = _mock_client()
    mock.download.side_effect = DegooAPIError("no url")
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "000"])
    assert result.exit_code != 0


def test_download_folder_without_r_errors(tmp_path):
    """Passing a folder path without -r must error with a helpful message."""
    mock = _mock_client()
    mock.resolve_path.return_value = {
        "ID": "111",
        "Name": "Photos",
        "Category": 1,
        "Size": "0",
        "URL": "",
    }
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "/Web/Photos", "--dest", str(tmp_path)])
    assert result.exit_code != 0
    assert "folder" in result.output.lower() or "recursive" in result.output.lower()


def test_download_folder_recursive(tmp_path):
    """Recursive folder download: list_dir is called, client.download per file."""
    mock = _mock_client()
    folder_item = {"ID": "111", "Name": "Photos", "Category": 1, "Size": "0", "URL": ""}
    file_item = {
        "ID": "999",
        "Name": "beach.jpg",
        "Category": 6,
        "Size": "500000",
        "URL": "https://cdn.example.com/beach.jpg",
    }
    mock.resolve_path.return_value = folder_item
    mock.list_dir.return_value = [file_item]
    mock.download.return_value = tmp_path / "Photos" / "beach.jpg"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "/Web/Photos", "--dest", str(tmp_path), "-r"])
    assert result.exit_code == 0, result.output
    mock.list_dir.assert_called()
    mock.download.assert_called_once()


def test_download_multi_item(tmp_path):
    """Multiple item paths in one command → download called once per file."""
    mock = _mock_client()
    # get_item returns the default photo item for both IDs
    mock.download.return_value = tmp_path / "photo.jpg"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "111", "999", "--dest", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert mock.download.call_count == 2


def test_download_skip_existing(tmp_path):
    """--skip-existing omits files already present in the local destination."""
    mock = _mock_client()
    folder_item = {"ID": "111", "Name": "Photos", "Category": 1, "Size": "0", "URL": ""}
    existing_file = {"ID": "998", "Name": "old.jpg", "Category": 6, "Size": "100", "URL": "x"}
    new_file = {"ID": "999", "Name": "new.jpg", "Category": 6, "Size": "100", "URL": "x"}

    # Create the local dir that _collect_download_tasks will make, plus the existing file
    local_photos = tmp_path / "Photos"
    local_photos.mkdir()
    (local_photos / "old.jpg").write_bytes(b"existing")

    mock.resolve_path.return_value = folder_item
    mock.list_dir.return_value = [existing_file, new_file]
    mock.download.return_value = local_photos / "new.jpg"

    with _patch_client(mock):
        result = _runner().invoke(
            main,
            ["download", "/Web/Photos", "--dest", str(tmp_path), "-r", "--skip-existing"],
        )
    assert result.exit_code == 0, result.output
    # old.jpg already existed → skipped; only new.jpg downloaded
    assert mock.download.call_count == 1
    call_args = mock.download.call_args[0]
    assert call_args[0] == "999"


# ── mv / cp / rename / rm ─────────────────────────────────────────────────────


def test_mv():
    mock = _mock_client()
    # "111" is the destination folder; "999" is the source file.
    # get_item must return the right item per ID so mv can distinguish them.
    _folder = {"ID": "111", "Name": "Web", "Category": 1, "ParentID": "0"}
    _file = mock.get_item.return_value
    mock.get_item.side_effect = lambda item_id: _folder if str(item_id) == "111" else _file
    with _patch_client(mock):
        # Numeric ID dest → always "move inside", no trailing slash needed
        result = _runner().invoke(main, ["mv", "999", "111"])
    assert result.exit_code == 0, result.output
    mock.move.assert_called_once_with(["999"], "111")


def test_mv_path_exists_without_slash_errors():
    """Passing an existing path dest without trailing slash must error."""
    mock = _mock_client()
    # resolve_path returns the default device for any path → destination exists
    with _patch_client(mock):
        result = _runner().invoke(main, ["mv", "999", "/Web/Photos"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_mv_rename_when_dest_absent():
    """Passing a path that does not exist → rename in-place."""
    mock = _mock_client()
    # resolve_path returns None for dest (not found), but parent exists
    mock.resolve_path.side_effect = lambda p: None if p == "/Web/NewName" else {"ID": "0", "Name": "/", "Category": 2}
    mock.get_item.return_value["ParentID"] = "0"
    with _patch_client(mock):
        result = _runner().invoke(main, ["mv", "999", "/Web/NewName"])
    assert result.exit_code == 0, result.output
    mock.rename.assert_called_once_with("999", "NewName")


def test_cp():
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["cp", "999", "111"])
    assert result.exit_code == 0, result.output
    mock.move.assert_called_once_with(["999"], "111", copy=True)


def test_rename():
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["rename", "999", "new_name.jpg"])
    assert result.exit_code == 0, result.output
    mock.rename.assert_called_once_with("999", "new_name.jpg")


def test_rm_to_trash():
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["rm", "999"])
    assert result.exit_code == 0, result.output
    mock.delete.assert_called_once_with(["999"], permanent=False)


def test_rm_permanent():
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["rm", "--permanent", "999"])
    assert result.exit_code == 0, result.output
    mock.delete.assert_called_once_with(["999"], permanent=True)


def test_rm_multiple():
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["rm", "1", "2", "3"])
    assert result.exit_code == 0, result.output
    mock.delete.assert_called_once_with(["1", "2", "3"], permanent=False)


def test_mv_move_inside_with_trailing_slash():
    """Trailing slash on dest → move SRC inside the existing folder."""
    mock = _mock_client()
    archive_folder = {"ID": "222", "Name": "Archive", "Category": 2, "ParentID": "111"}
    mock.resolve_path.return_value = archive_folder
    with _patch_client(mock):
        result = _runner().invoke(main, ["mv", "999", "/Web/Archive/"])
    assert result.exit_code == 0, result.output
    mock.move.assert_called_once_with(["999"], "222")


def test_mv_trailing_slash_dest_not_found_errors():
    """Trailing slash on a dest that does not exist → error."""
    mock = _mock_client()
    mock.resolve_path.return_value = None
    with _patch_client(mock):
        result = _runner().invoke(main, ["mv", "999", "/Web/Missing/"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_mv_api_error():
    mock = _mock_client()
    mock.move.side_effect = DegooAPIError("invalid input")
    # Numeric dest 111 that resolves to a folder so we reach move()
    _folder = {"ID": "111", "Name": "Web", "Category": 1, "ParentID": "0"}
    _file = mock.get_item.return_value
    mock.get_item.side_effect = lambda item_id: _folder if str(item_id) == "111" else _file
    with _patch_client(mock):
        result = _runner().invoke(main, ["mv", "999", "111"])
    assert result.exit_code != 0


# ── trash / shared / feed ─────────────────────────────────────────────────────


def test_trash_empty():
    mock = _mock_client()
    mock.list_trash.return_value = []
    with _patch_client(mock):
        result = _runner().invoke(main, ["trash"])
    assert result.exit_code == 0, result.output
    assert "empty" in result.output.lower() or result.output


def test_trash_with_items():
    mock = _mock_client()
    mock.list_trash.return_value = [
        {"ID": "777", "Name": "old.txt", "Category": 6, "Size": "100", "LastModificationTime": "1700000000"},
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["trash"])
    assert result.exit_code == 0, result.output
    assert "old.txt" in result.output


def test_trash_api_error():
    mock = _mock_client()
    mock.list_trash.side_effect = DegooAPIError("trash error")
    with _patch_client(mock):
        result = _runner().invoke(main, ["trash"])
    assert result.exit_code != 0


def test_shared_empty():
    mock = _mock_client()
    mock.list_shared.return_value = []
    with _patch_client(mock):
        result = _runner().invoke(main, ["shared"])
    assert result.exit_code == 0, result.output


def test_shared_with_items():
    mock = _mock_client()
    mock.list_shared.return_value = [
        {"ID": "888", "Name": "shared.jpg", "Category": 6, "Size": "2048000", "LastModificationTime": "1700000000"},
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["shared"])
    assert result.exit_code == 0, result.output
    assert "shared.jpg" in result.output


def test_feed_empty():
    mock = _mock_client()
    mock.get_feed.return_value = []
    with _patch_client(mock):
        result = _runner().invoke(main, ["feed"])
    assert result.exit_code == 0, result.output


def test_feed_with_items():
    mock = _mock_client()
    mock.get_feed.return_value = [
        {
            "ID": "555",
            "Name": "2023-01-01.jpg",
            "Category": 6,
            "Size": "1024000",
            "CreationTime": "2023-01-01T10:00:00Z",
        },
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["feed"])
    assert result.exit_code == 0, result.output
    assert "2023-01-01.jpg" in result.output


def test_feed_api_error():
    mock = _mock_client()
    mock.get_feed.side_effect = DegooAPIError("feed error")
    with _patch_client(mock):
        result = _runner().invoke(main, ["feed"])
    assert result.exit_code != 0


# ── DegooGroup error handling ─────────────────────────────────────────────────


def test_missing_required_arg_shows_help():
    """Commands with missing required args should print their usage/help."""
    for cmd in ("search", "info", "rename", "mv", "cp"):
        result = _runner().invoke(main, [cmd])
        combined = result.output
        assert "Usage:" in combined, f"{cmd}: expected Usage: in output"


def test_unknown_command():
    result = _runner().invoke(main, ["nosuchcommand"])
    assert result.exit_code != 0


# ── _client() token handling ──────────────────────────────────────────────────


def test_client_validates_token_and_creates_dynamic_client():
    """_client() validates auth eagerly but creates DegooClient without a fixed token.

    DegooClient must be constructed with no explicit token so that its .token
    property calls get_token() on every request — this enables transparent
    token refresh during long-running uploads/downloads (5+ hours).
    Passing token= explicitly would snapshot the token at startup and cause
    all requests to fail after the access token expires (~1 hour).
    """

    from cligoo.cli import _client

    get_token_calls: list[str] = []

    def fake_get_token() -> str:
        get_token_calls.append("called")
        return "the-token"

    with (
        patch("cligoo.auth.get_token", side_effect=fake_get_token),
        patch("cligoo.cli.DegooClient") as MockClient,
    ):
        _client()

    # get_token is called once for eager validation in _client().
    assert len(get_token_calls) == 1, f"get_token called {len(get_token_calls)} times; expected 1"
    # DegooClient must NOT receive a fixed token — dynamic refresh requires token=None.
    MockClient.assert_called_once_with()


# ── Relative path resolution (_resolve_item CWD-awareness) ────────────────────


def test_resolve_bare_name_uses_cwd(tmp_path):
    """Commands that accept PATH|ID should resolve a bare filename against CWD.

    e.g.  degoo info photo.jpg  (when CWD is /Web)  → resolves /Web/photo.jpg
    """
    photo = {
        "ID": "999",
        "Name": "photo.jpg",
        "Category": 6,
        "Size": "500000",
        "ParentID": "111",
        "LastModificationTime": "1700000000",
        "LastUploadTime": "1700000000",
        "CreationTime": "1700000000",
        "FilePath": "/Web/photo.jpg",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "https://cdn.example.com/photo.jpg",
        "ThumbnailURL": "",
    }
    mock = _mock_client()
    mock.resolve_path.return_value = photo

    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text('{"path": "/Web"}')

    with (
        _patch_client(mock),
        patch("cligoo.cli._CWD_FILE", cwd_file),
    ):
        result = _runner().invoke(main, ["info", "photo.jpg"])

    assert result.exit_code == 0, result.output
    # resolve_path must have been called with the CWD-prefixed absolute path
    mock.resolve_path.assert_called_with("/Web/photo.jpg")


def test_resolve_bare_name_root_cwd(tmp_path):
    """Bare name with CWD=/ resolves to /<name>."""
    photo = {
        "ID": "42",
        "Name": "Web",
        "Category": 1,
        "Size": "0",
        "ParentID": "0",
        "LastModificationTime": "1700000000",
        "LastUploadTime": "1700000000",
        "CreationTime": "1700000000",
        "FilePath": "/Web",
        "IsInRecycleBin": False,
        "Description": "",
        "URL": "",
        "ThumbnailURL": "",
    }
    mock = _mock_client()
    mock.resolve_path.return_value = photo

    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text('{"path": "/"}')

    with (
        _patch_client(mock),
        patch("cligoo.cli._CWD_FILE", cwd_file),
    ):
        result = _runner().invoke(main, ["info", "Web"])

    assert result.exit_code == 0, result.output
    mock.resolve_path.assert_called_with("/Web")


# ── empty-trash ────────────────────────────────────────────────────────────────


def test_empty_trash_aborts_on_wrong_first_confirmation():
    """empty-trash must abort if the user does not type YES at first prompt."""
    mock = _mock_client()
    mock.list_trash.return_value = [
        {"ID": "10", "Name": "old.jpg", "Category": 6, "Size": "1000"},
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["empty-trash"], input="no\n")
    assert result.exit_code == 0
    mock.delete.assert_not_called()


def test_empty_trash_aborts_on_wrong_second_confirmation():
    """empty-trash must abort if the user does not type EMPTY TRASH at second prompt."""
    mock = _mock_client()
    mock.list_trash.return_value = [
        {"ID": "10", "Name": "old.jpg", "Category": 6, "Size": "1000"},
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["empty-trash"], input="YES\nnope\n")
    assert result.exit_code == 0
    mock.delete.assert_not_called()


def test_empty_trash_deletes_on_correct_confirmations():
    """empty-trash deletes all items after YES + EMPTY TRASH."""
    mock = _mock_client()
    mock.list_trash.return_value = [
        {"ID": "10", "Name": "old.jpg", "Category": 6, "Size": "1000"},
        {"ID": "11", "Name": "temp.txt", "Category": 5, "Size": "200"},
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["empty-trash"], input="YES\nEMPTY TRASH\n")
    assert result.exit_code == 0, result.output
    mock.delete.assert_called_once_with(["10", "11"], permanent=True)


def test_empty_trash_skips_confirmation_with_yes_flag():
    """empty-trash --yes must skip prompts and delete immediately."""
    mock = _mock_client()
    mock.list_trash.return_value = [
        {"ID": "99", "Name": "file.zip", "Category": 5, "Size": "5000"},
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["empty-trash", "--yes"])
    assert result.exit_code == 0, result.output
    mock.delete.assert_called_once_with(["99"], permanent=True)


def test_empty_trash_reports_empty_bin():
    """empty-trash with an empty bin must not call delete."""
    mock = _mock_client()
    mock.list_trash.return_value = []
    with _patch_client(mock):
        result = _runner().invoke(main, ["empty-trash", "--yes"])
    assert result.exit_code == 0
    mock.delete.assert_not_called()
