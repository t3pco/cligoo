"""Authentication module — login, token refresh, and credential storage.

Tokens are stored via the system keyring (macOS Keychain / GNOME Keyring / etc.)
with a JSON fallback at ~/.config/cligoo/tokens.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
import jwt

from .constants import LOGIN_URL, TOKEN_REFRESH_URL

# ── Config paths ──────────────────────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".config" / "cligoo"
TOKEN_FILE = CONFIG_DIR / "tokens.json"
CRED_FILE = CONFIG_DIR / "credentials.json"

SERVICE_NAME = "cligoo"
# Legacy service names from previous versions of the tool.
# load() / load_credentials() will migrate entries from these to SERVICE_NAME.
_LEGACY_SERVICE_NAMES = ["degoo-cli", "degoo"]


class AuthError(Exception):
    """Raised when authentication fails."""


class TokenStore:
    """Persist and retrieve tokens.

    Tries the system keyring first; falls back to a JSON file.
    """

    def __init__(self) -> None:
        self._use_keyring = True
        self._warned_plaintext = False
        try:
            import keyring as _kr  # noqa: F401

            self._kr = _kr
        except Exception:
            self._use_keyring = False

    # ── public ────────────────────────────────────────────────────────────
    def save(self, token: str, refresh_token: str) -> None:
        if self._use_keyring:
            try:
                self._kr.set_password(SERVICE_NAME, "token", token)
                self._kr.set_password(SERVICE_NAME, "refresh_token", refresh_token)
                return
            except Exception:
                pass
        # Keyring unavailable — fall back to a plaintext file with restricted permissions.
        # Warn once per process lifetime to avoid noise on every token refresh.
        if not self._warned_plaintext:
            print(
                f"⚠  Keyring unavailable — storing token in plaintext file ({TOKEN_FILE}). Keep this file private.",
                file=sys.stderr,
            )
            self._warned_plaintext = True
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _write_private(TOKEN_FILE, json.dumps({"token": token, "refresh_token": refresh_token}))

    def load(self) -> tuple[Optional[str], Optional[str]]:
        if self._use_keyring:
            try:
                tok = self._kr.get_password(SERVICE_NAME, "token")
                ref = self._kr.get_password(SERVICE_NAME, "refresh_token")
                if tok:
                    return tok, ref
            except Exception:
                pass
            # One-time migration from legacy keyring service names
            for legacy in _LEGACY_SERVICE_NAMES:
                try:
                    tok = self._kr.get_password(legacy, "token")
                    ref = self._kr.get_password(legacy, "refresh_token")
                    if tok:
                        self.save(tok, ref or "")
                        for key in ("token", "refresh_token"):
                            try:
                                self._kr.delete_password(legacy, key)
                            except Exception:
                                pass
                        return tok, ref or ""
                except Exception:
                    pass
        if TOKEN_FILE.exists():
            data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            return data.get("token"), data.get("refresh_token")
        return None, None

    def clear(self) -> None:
        if self._use_keyring:
            try:
                self._kr.delete_password(SERVICE_NAME, "token")
                self._kr.delete_password(SERVICE_NAME, "refresh_token")
            except Exception:
                pass
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()

    # ── credentials (email/password) ──────────────────────────────────────
    def save_credentials(self, email: str, password: str) -> None:
        if self._use_keyring:
            try:
                self._kr.set_password(SERVICE_NAME, "email", email)
                self._kr.set_password(SERVICE_NAME, "password", password)
                return
            except Exception:
                pass
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not self._warned_plaintext:
            print(
                f"⚠  Keyring unavailable — storing credentials in plaintext file "
                f"({CRED_FILE}). Keep this file private.",
                file=sys.stderr,
            )
            self._warned_plaintext = True
        _write_private(CRED_FILE, json.dumps({"email": email, "password": password}))

    def load_credentials(self) -> tuple[Optional[str], Optional[str]]:
        if self._use_keyring:
            try:
                email = self._kr.get_password(SERVICE_NAME, "email")
                pw = self._kr.get_password(SERVICE_NAME, "password")
                if email:
                    return email, pw
            except Exception:
                pass
            # One-time migration from legacy keyring service names
            for legacy in _LEGACY_SERVICE_NAMES:
                try:
                    email = self._kr.get_password(legacy, "email")
                    pw = self._kr.get_password(legacy, "password")
                    if email:
                        self.save_credentials(email, pw or "")
                        for key in ("email", "password"):
                            try:
                                self._kr.delete_password(legacy, key)
                            except Exception:
                                pass
                        print(
                            f"⚠  Migrated credentials from '{legacy}' keyring entry to 'cligoo'.",
                            file=sys.stderr,
                        )
                        return email, pw or ""
                except Exception:
                    pass
        if CRED_FILE.exists():
            data = json.loads(CRED_FILE.read_text(encoding="utf-8"))
            return data.get("email"), data.get("password")
        # One-time migration: read credentials from the legacy config.toml [auth]
        # section (written by older versions of cligoo), migrate them to the
        # proper store (keyring or credentials.json), and warn the user to remove
        # that section from their config file.
        toml_email, toml_pw = _load_credentials_from_toml()
        if toml_email:
            self.save_credentials(toml_email, toml_pw or "")
            print(
                f"⚠  Migrated credentials from config.toml to secure storage.\n"
                f"   You can now remove the [auth] section from\n"
                f"   {CONFIG_DIR / 'config.toml'}",
                file=sys.stderr,
            )
            return toml_email, toml_pw
        return None, None


def _load_credentials_from_toml() -> tuple[Optional[str], Optional[str]]:
    """Read email/password from a legacy config.toml [auth] section, if present."""
    toml_file = CONFIG_DIR / "config.toml"
    if not toml_file.exists():
        return None, None
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                return None, None
        data = tomllib.loads(toml_file.read_text(encoding="utf-8"))
        auth = data.get("auth", {})
        email = auth.get("email") or None
        pw = auth.get("password") or None
        if email:
            return email, pw
    except Exception:
        pass
    return None, None


def _write_private(path: Path, content: str) -> None:
    """Write *content* to *path* with owner-only (0o600) permissions.

    Uses :func:`os.open` with the target mode in the ``O_CREAT`` call so the
    file is never readable by other users — even briefly between creation and
    a subsequent ``chmod``.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(content)


_store = TokenStore()

# ── Login rate-limit backoff ───────────────────────────────────────────────────
_LOGIN_BACKOFF_FILE = CONFIG_DIR / ".login_backoff"
_LOGIN_BACKOFF_SECONDS = 900  # 15 minutes — matches Degoo's observed rate-limit window


def _check_login_backoff() -> Optional[float]:
    """Return remaining backoff seconds if a recent 429 was recorded, else None."""
    if not _LOGIN_BACKOFF_FILE.exists():
        return None
    try:
        ts = float(_LOGIN_BACKOFF_FILE.read_text(encoding="utf-8").strip())
        remaining = ts + _LOGIN_BACKOFF_SECONDS - time.time()
        if remaining > 0:
            return remaining
        _LOGIN_BACKOFF_FILE.unlink(missing_ok=True)
    except Exception:
        # Corrupted or unreadable file — delete it so the guard is not permanently bypassed
        _LOGIN_BACKOFF_FILE.unlink(missing_ok=True)
    return None


def _set_login_backoff() -> None:
    """Record that a 429 was received so subsequent attempts back off."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _LOGIN_BACKOFF_FILE.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def _clear_login_backoff() -> None:
    """Remove the backoff file after a successful login."""
    try:
        _LOGIN_BACKOFF_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _token_expired(token: str, margin: int = 60) -> bool:
    """Return True if a JWT is expired (or will be within *margin* seconds).

    Returns ``True`` (treat as expired) if the token cannot be decoded, so the
    caller proceeds to refresh or re-login.  A warning is printed to stderr so
    users can diagnose unexpected credential corruption.
    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("exp", 0) < time.time() + margin
    except Exception as exc:
        print(
            f"⚠  Stored token could not be decoded ({exc}); treating as expired.",
            file=sys.stderr,
        )
        return True


def _api_error_message(resp: "httpx.Response") -> str:
    """Return a human-readable error from an API response.

    Cloudflare rate-limit and WAF pages return HTML or empty bodies — strip
    those down to a one-liner instead of dumping noise to the terminal.
    """
    if resp.status_code == 429:
        return "Rate limited — too many login attempts. Wait a few minutes, then try again."
    content_type = resp.headers.get("content-type", "")
    if "text/html" in content_type or resp.text.lstrip().startswith("<"):
        return "Unexpected HTML response (Cloudflare or proxy error)."
    return resp.text or f"(empty {resp.status_code} response)"


def login(email: str, password: str, *, save: bool = True) -> str:
    """Authenticate with email/password and return an access token.

    The refresh token is persisted so future calls can use ``get_token()``.
    """
    remaining = _check_login_backoff()
    if remaining is not None:
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        wait = f"{mins}m {secs}s" if mins else f"{secs}s"
        raise AuthError(
            f"Login rate-limited — please wait {wait} before trying again.\n"
            "  (Degoo limits how often you can log in with email/password.)"
        )

    resp = httpx.post(
        LOGIN_URL,
        json={"GenerateToken": True, "Username": email, "Password": password},
        timeout=30,
    )
    if resp.status_code == 429:
        _set_login_backoff()
        raise AuthError(
            f"Login rate-limited — please wait {_LOGIN_BACKOFF_SECONDS // 60}m before trying again.\n"
            "  (Degoo limits how often you can log in with email/password.)"
        )
    if resp.status_code != 200:
        raise AuthError(f"Login failed (HTTP {resp.status_code}): {_api_error_message(resp)}")

    data = resp.json()

    # The API may return a Token directly or a RefreshToken that needs exchange
    refresh_token = data.get("RefreshToken") or data.get("Token")
    if not refresh_token:
        raise AuthError(f"Unexpected login response: {data}")

    access_token = _exchange_refresh_token(refresh_token)

    # Clear backoff regardless of save= — a successful login proves we're not rate-limited
    _clear_login_backoff()

    if save:
        _store.save(access_token, refresh_token)
        _store.save_credentials(email, password)

    return access_token


def _exchange_refresh_token(refresh_token: str) -> str:
    """Exchange a refresh token for a short-lived access token."""
    resp = httpx.post(
        TOKEN_REFRESH_URL,
        json={"RefreshToken": refresh_token},
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(f"Token refresh failed (HTTP {resp.status_code}): {_api_error_message(resp)}")

    data = resp.json()
    token = data.get("Token") or data.get("AccessToken")
    if not token:
        raise AuthError(f"Unexpected token response: {data}")
    return token


# Ordered list of browser channels to try, from most to least preferred.
# "chrome" uses the system Google Chrome (no download needed).
# "chromium" uses a Playwright-managed Chromium (~130 MB download via
#   `playwright install chromium`).
_BROWSER_CHANNELS = ["chrome", "msedge", "chromium"]


def _seed_temp_profile(tmp_dir: Path, profile_dir: str) -> None:
    """Copy cookies (and login data) from a real Chrome profile into *tmp_dir*.

    *tmp_dir* is used as the Playwright ``user_data_dir``.  Chrome always loads
    the ``Default`` sub-profile within that directory, so we copy the source
    files there.

    Only two small files are copied — Chrome's cache and history are skipped
    intentionally to keep the operation fast (< 1 s) and leave the original
    profile completely untouched.

    Cookie encryption keys on macOS (Keychain), Linux (GNOME Keyring), and
    Windows (DPAPI) are all per-user-account on the machine, not per-profile
    path, so the copied cookies decrypt correctly in the temporary profile.
    """
    import shutil as _shutil

    from .chrome import profile_cookies_path, profile_login_data_path

    dest_profile = tmp_dir / "Default"
    dest_profile.mkdir(parents=True, exist_ok=True)

    for src in (
        profile_cookies_path(profile_dir),
        profile_login_data_path(profile_dir),
    ):
        if src is not None:
            try:
                _shutil.copy2(src, dest_profile / src.name)
            except Exception:
                pass  # non-fatal — Chrome will recreate missing files


def fetch_token_via_browser() -> tuple[str, str]:
    """Open a headed browser window, wait for Google sign-in, and capture the JWT.

    Returns ``(access_token, refresh_token)``.  ``refresh_token`` is harvested
    from cookies (including HttpOnly ones) via Playwright's CDP layer; it may
    be an empty string if no JWT-format cookie was found — the flow still
    succeeds in that case.

    Profile seeding
    ~~~~~~~~~~~~~~~
    If a Chrome profile has been configured via ``degoo config``, its
    ``Cookies`` and ``Login Data`` files are copied into a fresh temporary
    ``user_data_dir`` before launch.  Because the encryption keys are
    per-user-account (not per-profile-path), the copied session cookies decrypt
    correctly and the user is typically already signed into Google and Degoo —
    the token is captured in seconds without any interaction.

    If Chrome is already running (profile directory locked), the copy uses a
    completely separate temporary directory, so there is no conflict.

    When no profile is configured a fresh empty temporary profile is used
    (the original behaviour).

    Anti-detection
    ~~~~~~~~~~~~~~
    Launches with ``--disable-blink-features=AutomationControlled`` and
    without ``--enable-automation`` so Google OAuth does not show the
    "This browser or app may not be secure" warning.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise AuthError(
            "The 'playwright' package is required for browser-based token fetch.\n  pip install 'cligoo[browser]'"
        )

    import json as _json
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    import time as _time
    from pathlib import Path as _Path

    from .config import get_chrome_profile

    captured: list[str] = []
    _captured_refresh: list[str] = []

    def _on_request(request) -> None:
        if captured or "appsync" not in request.url:
            return
        try:
            body = _json.loads(request.post_data or "{}")
            tok = body.get("variables", {}).get("Token")
            if tok and not _token_expired(tok, margin=0):
                captured.append(tok)
        except Exception:
            pass

    def _on_response(response) -> None:
        """Intercept REST login / token responses for a RefreshToken field."""
        if _captured_refresh:
            return
        if "rest-api.degoo.com" not in response.url:
            return
        try:
            data = response.json()
            rt = data.get("RefreshToken") or data.get("Token")
            if rt and isinstance(rt, str) and len(rt) > 20:
                _captured_refresh.append(rt)
        except Exception:
            pass

    # Build the temporary user_data_dir.
    # If a real Chrome profile is configured, seed it with that profile's
    # cookies so the existing Google / Degoo session carries over.
    tmp_profile = _Path(_tempfile.mkdtemp(prefix="cligoo-login-"))
    profile_dir = get_chrome_profile()
    if profile_dir:
        _seed_temp_profile(tmp_profile, profile_dir)

    try:
        with sync_playwright() as pw:
            context = None
            last_err: Exception = RuntimeError("No browser found")

            for channel in _BROWSER_CHANNELS:
                # "chromium" channel requires playwright install; skip if absent
                if channel == "chromium":
                    cache = _os.path.expanduser("~/.cache/ms-playwright")
                    if not _os.path.isdir(cache):
                        continue

                try:
                    kwargs: dict = dict(
                        user_data_dir=str(tmp_profile),
                        headless=False,
                        # Remove --enable-automation so navigator.webdriver is
                        # not set — prevents Google's "not secure" rejection.
                        #
                        # Also remove --use-mock-keychain and
                        # --password-store=basic which Playwright injects by
                        # default on macOS.  Those flags tell Chrome to skip
                        # the real macOS Keychain, which means Chrome cannot
                        # decrypt the AES-GCM cookie values we copied from the
                        # real profile — every session cookie would silently
                        # decrypt to garbage and no sign-in would be detected.
                        ignore_default_args=[
                            "--enable-automation",
                            "--use-mock-keychain",
                            "--password-store=basic",
                        ],
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                    # "chromium" means the Playwright-bundled build — no channel kwarg.
                    if channel != "chromium":
                        kwargs["channel"] = channel

                    context = pw.chromium.launch_persistent_context(**kwargs)
                    break
                except Exception as exc:
                    last_err = exc

            if context is None:
                raise AuthError(
                    f"Could not launch a browser ({last_err}).\n"
                    "Install system Chrome, or run:  playwright install chromium"
                )

            try:
                page = context.new_page()
                page.on("request", _on_request)
                page.on("response", _on_response)
                page.goto("https://app.degoo.com", wait_until="domcontentloaded")

                # Poll until token captured or 5-minute timeout
                deadline = _time.time() + 300
                while _time.time() < deadline:
                    if captured:
                        break
                    page.wait_for_timeout(1000)

                # Harvest refresh token — try three sources in priority order:
                #   1. Network response from Degoo's REST login endpoint
                #   2. localStorage (Degoo web app persists tokens there)
                #   3. Cookies (including HttpOnly via CDP)
                refresh_token = ""
                if captured:
                    access_token = captured[0]

                    # 1. REST responses already collected via _on_response
                    if _captured_refresh:
                        refresh_token = _captured_refresh[0]

                    # 2. localStorage — Degoo web app stores auth state here
                    if not refresh_token:
                        try:
                            ls_entries = page.evaluate("() => Object.entries(window.localStorage)")
                            for _key, val in ls_entries or []:
                                if (
                                    isinstance(val, str)
                                    and val.startswith("ey")
                                    and len(val) > 50
                                    and val != access_token
                                ):
                                    refresh_token = val
                                    break
                        except Exception:
                            pass

                    # 3. Cookies (HttpOnly included via CDP)
                    if not refresh_token:
                        for cookie in page.context.cookies():
                            val = cookie.get("value", "")
                            if val.startswith("ey") and len(val) > 50 and val != access_token:
                                refresh_token = val
                                break
            finally:
                context.close()

    finally:
        _shutil.rmtree(tmp_profile, ignore_errors=True)

    if not captured:
        raise AuthError("Browser sign-in timed out (5 min). Please try again.")

    return captured[0], refresh_token


def save_token_direct(token: str, refresh_token: str = "") -> None:
    """Persist a token obtained outside of the normal login flow.

    Useful for accounts that authenticate via Google OAuth — the user can
    extract the JWT from Chrome DevTools and pass it here.
    """
    if not token.startswith("ey"):
        raise AuthError("Token does not look like a JWT (expected 'ey…' prefix).")
    _store.save(token, refresh_token)


def get_token() -> str:
    """Return a valid access token, refreshing or re-logging in as needed."""
    token, refresh_token = _store.load()

    # 1) If we have a non-expired token, use it
    if token and not _token_expired(token):
        return token

    # 2) Try refreshing
    if refresh_token:
        try:
            token = _exchange_refresh_token(refresh_token)
            _store.save(token, refresh_token)
            return token
        except AuthError:
            pass

    # 3) Try re-login with saved credentials (if auto_relogin is enabled)
    from .config import get_auto_relogin

    if get_auto_relogin():
        email, password = _store.load_credentials()
        if email and password:
            return login(email, password)

    raise AuthError(
        "No valid token found.\n"
        "  • Email/password accounts: run `cligoo login`\n"
        "  • Google OAuth accounts:   run `cligoo login --browser`\n"
        "    (or extract JWT manually from DevTools and run `cligoo token <TOKEN>`)"
    )


def get_saved_email() -> Optional[str]:
    """Return the saved email address (from keyring or credentials file), or None."""
    email, _ = _store.load_credentials()
    return email


def get_saved_credentials() -> tuple[Optional[str], Optional[str]]:
    """Return the saved (email, password) tuple, or (None, None) if not stored."""
    return _store.load_credentials()


def logout() -> None:
    """Clear all stored tokens and credentials."""
    _store.clear()
