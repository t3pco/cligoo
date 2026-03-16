"""Tests for cligoo.config.

Covers:
- TOML primary: save_config() writes TOML; load_config() reads it back
- JSON fallback: when no TOML exists, config.json is still read
- TOML wins over JSON when both exist
- save_config() atomic write (temp + rename, no orphaned temp files)
- save_config() merge semantics (new keys don't clobber existing ones)
- save_config() None value removes the key
- get_api_key() resolution chain: env var → config file → None
- get_login_method() valid / invalid / absent values
- get_chrome_profile() null / absent / valid string
- get_api_timeout() / get_api_debug() / get_auto_relogin() / get_output_format()
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Redirect all config I/O to a temp directory."""
    import cligoo.config as cfg

    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "TOML_FILE", tmp_path / "config.toml")
    return tmp_path


def _write_toml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ── TOML primary ───────────────────────────────────────────────────────────────


def test_save_config_writes_toml(isolated_config, tmp_path):
    """save_config() must write config.toml, not config.json."""
    from cligoo.config import save_config

    save_config({"login_method": "browser"})

    assert (tmp_path / "config.toml").exists()
    assert not (tmp_path / "config.json").exists()


def test_save_config_toml_content_round_trips(isolated_config):
    """Values written by save_config() are readable back via load_config()."""
    from cligoo.config import get_login_method, save_config

    save_config({"login_method": "browser"})
    assert get_login_method() == "browser"


def test_save_config_merges_without_clobbering(isolated_config):
    """A second save adds new keys without removing existing ones."""
    from cligoo.config import get_chrome_profile, get_login_method, save_config

    save_config({"login_method": "password"})
    save_config({"chrome_profile": "Default"})

    assert get_login_method() == "password"
    assert get_chrome_profile() == "Default"


def test_save_config_none_removes_key(isolated_config):
    """Passing None for a value should remove that key from the config."""
    from cligoo.config import get_chrome_profile, save_config

    save_config({"chrome_profile": "Default"})
    assert get_chrome_profile() == "Default"

    save_config({"chrome_profile": None})
    assert get_chrome_profile() is None


def test_save_config_leaves_no_temp_files(isolated_config, tmp_path):
    """The atomic write pattern must not leave any .config-* temp files."""
    from cligoo.config import save_config

    save_config({"login_method": "browser"})

    leftovers = list(tmp_path.glob(".config-*"))
    assert leftovers == [], f"Orphaned temp files: {leftovers}"


def test_save_config_atomic_rename(isolated_config, tmp_path, monkeypatch):
    """os.replace() must be called for atomicity, targeting config.toml."""
    import cligoo.config as cfg

    replace_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src: str, dst: str) -> None:
        replace_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(cfg.os, "replace", spy_replace)
    cfg.save_config({"login_method": "browser"})

    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert dst == str(tmp_path / "config.toml")
    assert Path(src).parent == tmp_path
    assert Path(src).name.startswith(".config-")


# ── JSON fallback (legacy) ─────────────────────────────────────────────────────


def test_load_config_falls_back_to_json(isolated_config, tmp_path):
    """When no TOML file exists, config.json is read as fallback."""
    from cligoo.config import get_login_method

    (tmp_path / "config.json").write_text(json.dumps({"login_method": "browser"}), encoding="utf-8")
    assert get_login_method() == "browser"


def test_toml_wins_over_json(isolated_config, tmp_path):
    """When both config.toml and config.json exist, TOML takes precedence."""
    from cligoo.config import get_login_method

    _write_toml(tmp_path / "config.toml", '[session]\nlogin_method = "browser"\n')
    (tmp_path / "config.json").write_text(json.dumps({"login_method": "password"}), encoding="utf-8")
    assert get_login_method() == "browser"


def test_load_config_json_chrome_profile(isolated_config, tmp_path):
    """Legacy JSON chrome_profile key is read correctly via fallback."""
    from cligoo.config import get_chrome_profile

    (tmp_path / "config.json").write_text(json.dumps({"chrome_profile": "Profile 1"}), encoding="utf-8")
    assert get_chrome_profile() == "Profile 1"


# ── get_api_key ────────────────────────────────────────────────────────────────


def test_get_api_key_from_env_var(isolated_config, monkeypatch):
    monkeypatch.setenv("DEGOO_API_KEY", "env-key-123")
    from cligoo.config import get_api_key

    assert get_api_key() == "env-key-123"


def test_get_api_key_from_toml(isolated_config, tmp_path, monkeypatch):
    monkeypatch.delenv("DEGOO_API_KEY", raising=False)
    _write_toml(tmp_path / "config.toml", '[api]\napi_key = "toml-key-xyz"\n')
    from cligoo.config import get_api_key

    assert get_api_key() == "toml-key-xyz"


def test_get_api_key_from_json_fallback(isolated_config, tmp_path, monkeypatch):
    monkeypatch.delenv("DEGOO_API_KEY", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"api_key": "json-key"}), encoding="utf-8")
    from cligoo.config import get_api_key

    assert get_api_key() == "json-key"


def test_get_api_key_env_overrides_config(isolated_config, tmp_path, monkeypatch):
    monkeypatch.setenv("DEGOO_API_KEY", "env-wins")
    _write_toml(tmp_path / "config.toml", '[api]\napi_key = "config-loses"\n')
    from cligoo.config import get_api_key

    assert get_api_key() == "env-wins"


def test_get_api_key_returns_none_when_absent(isolated_config, monkeypatch):
    monkeypatch.delenv("DEGOO_API_KEY", raising=False)
    from cligoo.config import get_api_key

    assert get_api_key() is None


def test_get_api_key_returns_none_for_empty_string(isolated_config, tmp_path, monkeypatch):
    monkeypatch.delenv("DEGOO_API_KEY", raising=False)
    _write_toml(tmp_path / "config.toml", '[api]\napi_key = ""\n')
    from cligoo.config import get_api_key

    assert get_api_key() is None


# ── get_login_method ──────────────────────────────────────────────────────────


def test_get_login_method_browser_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", '[session]\nlogin_method = "browser"\n')
    from cligoo.config import get_login_method

    assert get_login_method() == "browser"


def test_get_login_method_password_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", '[session]\nlogin_method = "password"\n')
    from cligoo.config import get_login_method

    assert get_login_method() == "password"


def test_get_login_method_browser_from_json(isolated_config, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"login_method": "browser"}), encoding="utf-8")
    from cligoo.config import get_login_method

    assert get_login_method() == "browser"


def test_get_login_method_invalid_returns_none(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", '[session]\nlogin_method = "oauth"\n')
    from cligoo.config import get_login_method

    assert get_login_method() is None


def test_get_login_method_absent_returns_none(isolated_config):
    from cligoo.config import get_login_method

    assert get_login_method() is None


# ── get_chrome_profile ────────────────────────────────────────────────────────


def test_get_chrome_profile_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", '[session]\nchrome_profile = "Profile 1"\n')
    from cligoo.config import get_chrome_profile

    assert get_chrome_profile() == "Profile 1"


def test_get_chrome_profile_absent_returns_none(isolated_config):
    from cligoo.config import get_chrome_profile

    assert get_chrome_profile() is None


# ── New settings: api.timeout, api.debug, session.auto_relogin, output.format ─


def test_get_api_timeout_default(isolated_config):
    from cligoo.config import get_api_timeout

    assert get_api_timeout() == 60.0


def test_get_api_timeout_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", "[api]\ntimeout = 120\n")
    from cligoo.config import get_api_timeout

    assert get_api_timeout() == 120.0


def test_get_api_debug_default(isolated_config):
    from cligoo.config import get_api_debug

    assert get_api_debug() is False


def test_get_api_debug_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", "[api]\ndebug = true\n")
    from cligoo.config import get_api_debug

    assert get_api_debug() is True


def test_get_auto_relogin_default(isolated_config):
    from cligoo.config import get_auto_relogin

    assert get_auto_relogin() is True


def test_get_auto_relogin_false_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", "[session]\nauto_relogin = false\n")
    from cligoo.config import get_auto_relogin

    assert get_auto_relogin() is False


def test_get_output_format_default(isolated_config):
    from cligoo.config import get_output_format

    assert get_output_format() == "table"


def test_get_output_format_json_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", '[output]\nformat = "json"\n')
    from cligoo.config import get_output_format

    assert get_output_format() == "json"


def test_get_output_format_invalid_returns_table(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", '[output]\nformat = "csv"\n')
    from cligoo.config import get_output_format

    assert get_output_format() == "table"


def test_get_compact_json_default(isolated_config):
    from cligoo.config import get_compact_json

    assert get_compact_json() is False


def test_get_compact_json_true_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", "[output]\ncompact_json = true\n")
    from cligoo.config import get_compact_json

    assert get_compact_json() is True


def test_get_upload_retries_default(isolated_config):
    from cligoo.config import get_upload_retries

    assert get_upload_retries() == 5


def test_get_upload_retries_custom_from_toml(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", "[session]\nupload_retries = 10\n")
    from cligoo.config import get_upload_retries

    assert get_upload_retries() == 10


def test_get_upload_retries_zero_allowed(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", "[session]\nupload_retries = 0\n")
    from cligoo.config import get_upload_retries

    assert get_upload_retries() == 0


def test_get_upload_retries_invalid_returns_default(isolated_config, tmp_path):
    _write_toml(tmp_path / "config.toml", '[session]\nupload_retries = "bad"\n')
    from cligoo.config import get_upload_retries

    assert get_upload_retries() == 5
