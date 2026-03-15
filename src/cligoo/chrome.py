"""Chrome installation and profile detection utilities.

Reads the Chrome ``Local State`` JSON file to enumerate installed profiles
without requiring Chrome to be running.

Supported platforms:
    macOS   ~/Library/Application Support/Google/Chrome/
    Linux   ~/.config/google-chrome/
    Windows %LOCALAPPDATA%\\Google\\Chrome\\User Data\\
"""

from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path
from typing import Optional

# ── Chrome user-data directory ─────────────────────────────────────────────────


def _user_data_dir() -> Optional[Path]:
    """Return the Chrome user-data directory for the current OS, or None."""
    system = platform.system()
    if system == "Darwin":
        p = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    elif system == "Linux":
        p = Path.home() / ".config" / "google-chrome"
    elif system == "Windows":
        import os

        local = os.environ.get("LOCALAPPDATA", "")
        p = Path(local) / "Google" / "Chrome" / "User Data"
    else:
        return None
    return p if p.is_dir() else None


# ── Public API ─────────────────────────────────────────────────────────────────


def chrome_is_installed() -> bool:
    """Return True if system Google Chrome can be found."""
    system = platform.system()
    if system == "Darwin":
        return Path("/Applications/Google Chrome.app").exists()
    elif system == "Linux":
        return bool(shutil.which("google-chrome") or shutil.which("google-chrome-stable"))
    elif system == "Windows":
        return _user_data_dir() is not None
    return False


def list_profiles() -> list[dict]:
    """Return Chrome profiles sorted with Default first.

    Each entry::

        {
            "dir":   "Default",          # subdirectory name inside user-data-dir
            "name":  "Personal",         # display name shown in Chrome
            "email": "you@example.com",  # Google account email (may be empty)
        }

    Returns ``[]`` if Chrome is not installed or ``Local State`` cannot be read.
    """
    udd = _user_data_dir()
    if udd is None:
        return []

    local_state = udd / "Local State"
    if not local_state.exists():
        return []

    try:
        state = json.loads(local_state.read_text(encoding="utf-8"))
        info_cache: dict = state.get("profile", {}).get("info_cache", {})
    except Exception:
        return []

    profiles = []
    for dir_name, info in info_cache.items():
        profiles.append(
            {
                "dir": dir_name,
                "name": info.get("name") or info.get("shortcut_name") or dir_name,
                "email": info.get("user_name", ""),
            }
        )

    # Default profile first, then alphabetically by directory name
    profiles.sort(key=lambda p: (0 if p["dir"] == "Default" else 1, p["dir"]))
    return profiles


def profile_cookies_path(profile_dir: str) -> Optional[Path]:
    """Return the ``Cookies`` SQLite path for *profile_dir*, or None."""
    udd = _user_data_dir()
    if udd is None:
        return None
    p = udd / profile_dir / "Cookies"
    return p if p.exists() else None


def profile_login_data_path(profile_dir: str) -> Optional[Path]:
    """Return the ``Login Data`` path for *profile_dir*, or None."""
    udd = _user_data_dir()
    if udd is None:
        return None
    p = udd / profile_dir / "Login Data"
    return p if p.exists() else None
