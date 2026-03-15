"""User configuration for cligoo.

Primary store: ``~/.config/cligoo/config.toml`` (TOML).
Legacy fallback: ``~/.config/cligoo/config.json`` (JSON, read-only once TOML exists).

Sections
--------
[api]
    graphql_url   : str   — GraphQL endpoint override
    api_key       : str   — AppSync API key override (env DEGOO_API_KEY takes precedence)
    timeout       : float — HTTP timeout in seconds (default 60)
    debug         : bool  — Enable verbose HTTP logging (default false)

[session]
    login_method      : "browser" | "password" — default login flow
    chrome_profile    : str  — Chrome profile dir name for browser login
    transfer_workers  : int  — Concurrent upload/download threads (default 20)
    auto_relogin      : bool — Re-login on token expiry (default true)
    default_upload_dir: str  — Default remote destination for uploads (default "/Web")
    upload_retries    : int  — Retry attempts for failed GCS uploads (default 5)

[output]
    format       : "table" | "json" — default output format (default "table")
    compact_json : bool — compact vs pretty-printed JSON output (default false)

[advanced]
    standalone_nav : bool — Enable ``cd`` and ``pwd`` as standalone commands (default false).
                            By default these only work inside ``cligoo shell``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Optional

CONFIG_DIR = Path.home() / ".config" / "cligoo"
TOML_FILE = CONFIG_DIR / "config.toml"  # primary — read + write
CONFIG_FILE = CONFIG_DIR / "config.json"  # legacy — read-only fallback

# ── flat key → (toml_section, toml_key) ──────────────────────────────────────
_FLAT_TO_TOML: dict[str, tuple[str, str]] = {
    "login_method": ("session", "login_method"),
    "chrome_profile": ("session", "chrome_profile"),
    "api_key": ("api", "api_key"),
    "transfer_workers": ("session", "transfer_workers"),
    "graphql_url": ("api", "graphql_url"),
    "timeout": ("api", "timeout"),
    "debug": ("api", "debug"),
    "auto_relogin": ("session", "auto_relogin"),
    "default_upload_dir": ("session", "default_upload_dir"),
    "upload_retries": ("session", "upload_retries"),
    "output_format": ("output", "format"),
    "compact_json": ("output", "compact_json"),
    "standalone_nav": ("advanced", "standalone_nav"),
}


def _toml_lib():
    """Return the tomllib/tomli module, or None if unavailable."""
    try:
        import tomllib  # Python 3.11+

        return tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]

            return tomllib
        except ImportError:
            return None


def _load_structured() -> dict[str, Any]:
    """Load configuration from disk as a nested dict.

    Tries TOML_FILE first; falls back to CONFIG_FILE (legacy flat JSON).
    Returns a nested dict with ``api``, ``session``, ``output`` sub-dicts,
    each guaranteed to exist even if empty.
    """
    if TOML_FILE.exists():
        lib = _toml_lib()
        if lib is not None:
            try:
                data = lib.loads(TOML_FILE.read_text(encoding="utf-8"))
                data.setdefault("api", {})
                data.setdefault("session", {})
                data.setdefault("output", {})
                data.setdefault("advanced", {})
                return data
            except Exception:
                pass

    if CONFIG_FILE.exists():
        try:
            import json

            flat = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return _flat_to_structured(flat)
        except Exception:
            pass

    return {"api": {}, "session": {}, "output": {}, "advanced": {}}


def _flat_to_structured(flat: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy flat JSON config dict to the nested structure."""
    structured: dict[str, Any] = {"api": {}, "session": {}, "output": {}, "advanced": {}}
    for flat_key, (section, toml_key) in _FLAT_TO_TOML.items():
        if flat_key in flat:
            structured[section][toml_key] = flat[flat_key]
    return structured


def load_config() -> dict[str, Any]:
    """Return configuration as a flat dict (backward-compatible view).

    Existing callers use flat keys like ``load_config().get("login_method")``.
    """
    data = _load_structured()
    flat: dict[str, Any] = {}
    for flat_key, (section, toml_key) in _FLAT_TO_TOML.items():
        val = data.get(section, {}).get(toml_key)
        if val is not None:
            flat[flat_key] = val
    return flat


def save_config(updates: dict[str, Any]) -> None:
    """Merge *updates* (flat keys) into config.toml and persist atomically.

    Passing ``None`` as a value removes that key from the config.
    Unknown flat keys are silently ignored.
    """
    try:
        import tomli_w
    except ImportError:
        raise RuntimeError("The 'tomli-w' package is required to save configuration.\n  pip install tomli-w")

    current = _load_structured()

    for flat_key, value in updates.items():
        if flat_key not in _FLAT_TO_TOML:
            continue
        section, toml_key = _FLAT_TO_TOML[flat_key]
        current.setdefault(section, {})
        if value is None:
            current[section].pop(toml_key, None)
        else:
            current[section][toml_key] = value

    # Strip empty sections so the TOML stays clean
    current = {k: v for k, v in current.items() if v}

    content = tomli_w.dumps(current)
    TOML_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=TOML_FILE.parent, prefix=".config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, str(TOML_FILE))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Getters ───────────────────────────────────────────────────────────────────


def get_login_method() -> Optional[str]:
    """Return ``"browser"`` or ``"password"``, or ``None`` if not configured."""
    value = load_config().get("login_method")
    if value in ("browser", "password"):
        return value
    return None


def get_chrome_profile() -> Optional[str]:
    """Return the configured Chrome profile directory name, or ``None``."""
    value = load_config().get("chrome_profile")
    return value if isinstance(value, str) and value else None


def get_api_key() -> Optional[str]:
    """Return the API key: env var > config file > None."""
    env_val = os.environ.get("DEGOO_API_KEY")
    if env_val:
        return env_val
    value = load_config().get("api_key")
    return value if isinstance(value, str) and value else None


def get_transfer_workers() -> int:
    """Return the configured number of concurrent transfer workers (default 20)."""
    value = load_config().get("transfer_workers", 20)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 20


def get_api_timeout() -> float:
    """Return the configured HTTP timeout in seconds (default 60)."""
    value = load_config().get("timeout", 60)
    try:
        result = float(value)
        return result if result > 0 else 60.0
    except (TypeError, ValueError):
        return 60.0


def get_api_debug() -> bool:
    """Return True if verbose HTTP debug logging is enabled (default False)."""
    value = load_config().get("debug", False)
    return bool(value)


def get_graphql_url() -> Optional[str]:
    """Return a configured GraphQL URL override, or ``None``."""
    value = load_config().get("graphql_url")
    return value if isinstance(value, str) and value else None


def get_auto_relogin() -> bool:
    """Return True if automatic re-login on token expiry is enabled (default True)."""
    value = load_config().get("auto_relogin", True)
    return bool(value)


def get_output_format() -> str:
    """Return the configured default output format: ``"table"`` or ``"json"``."""
    value = load_config().get("output_format", "table")
    return value if value in ("table", "json") else "table"


def get_compact_json() -> bool:
    """Return True if JSON output should be compact (single line) rather than pretty-printed."""
    value = load_config().get("compact_json", False)
    return bool(value)


def get_default_upload_dir() -> str:
    """Return the default remote upload destination (default ``"/Web"``)."""
    value = load_config().get("default_upload_dir", "/Web")
    return value if isinstance(value, str) and value.strip() else "/Web"


def get_upload_retries() -> int:
    """Return the number of retry attempts for failed GCS uploads (default 5).

    Retries apply to transient network errors (connection reset, timeout) and
    5xx responses from Google Cloud Storage.  4xx responses (policy violations,
    bad content type) are not retried — they will not recover on their own.
    """
    value = load_config().get("upload_retries", 5)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 5


def get_standalone_nav_enabled() -> bool:
    """Return True if ``cd`` / ``pwd`` are allowed as standalone commands (default False).

    By default these commands only work inside ``cligoo shell``.  Set
    ``[advanced] standalone_nav = true`` in config.toml to re-enable them.
    """
    value = load_config().get("standalone_nav", False)
    return bool(value)
