"""Degoo CLI — rich command-line interface for Degoo cloud storage.

Usage:
    cligoo login
    cligoo login --browser
    cligoo logout
    cligoo token <TOKEN> [--refresh TOKEN]
    cligoo whoami
    cligoo pwd
    cligoo cd [PATH]
    cligoo ls [PATH] [--long] [--depth N] [--limit N]
    cligoo tree [PATH] [--depth N]
    cligoo info <ID>
    cligoo search <TERM> [--limit N]
    cligoo mkdir <PATH>
    cligoo upload <FILE> [PARENT_PATH] [--name NAME]
    cligoo download <ID_OR_PATH> [DEST]
    cligoo mv <ID> <DEST_FOLDER_ID>
    cligoo cp <ID> <DEST_FOLDER_ID>
    cligoo rename <ID> <NEW_NAME>
    cligoo rm <ID>... [--permanent]
    cligoo trash [--limit N]
    cligoo shared [--limit N]
    cligoo share <ID> [USERNAMES...]
    cligoo unshare <ID>
    cligoo quota
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

import click
from rich import box
from rich.console import Console
from rich.markup import escape as _esc
from rich.table import Table
from rich.tree import Tree as RichTree

from . import __version__
from .api import DegooAlreadyExistsError, DegooAPIError, DegooClient
from .auth import AuthError, fetch_token_via_browser, get_saved_credentials, save_token_direct
from .config import (
    get_compact_json,
    get_default_upload_dir,
    get_login_method,
    get_standalone_nav_enabled,
    get_transfer_workers,
)
from .constants import CATEGORY_NAMES, FOLDER_CATEGORIES

console = Console()
err_console = Console(stderr=True)

# ── Persistent CWD ────────────────────────────────────────────────────────────
_CWD_FILE = Path.home() / ".config" / "cligoo" / "cwd.json"


def _load_cwd() -> str:
    """Return the stored working directory path (default '/')."""
    try:
        if _CWD_FILE.exists():
            return json.loads(_CWD_FILE.read_text()).get("path", "/")
    except Exception:
        pass
    return "/"


def _save_cwd(path: str) -> None:
    _CWD_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CWD_FILE.write_text(json.dumps({"path": path}))



def _err(msg: object) -> None:
    """Print an error message, safely escaping Rich markup in the text."""
    err_console.print(f"[red]✗[/red] {_esc(str(msg))}")


def _json_output(data: Any) -> str:
    """Serialise *data* to JSON, compact or pretty based on config."""
    if get_compact_json():
        return json.dumps(data, default=str)
    return json.dumps(data, indent=2, default=str)


def _to_absolute(path: str) -> str:
    """Return *path* as an absolute Degoo path.

    If *path* already starts with ``/`` it is returned unchanged.  Otherwise
    it is joined to the current working directory so that bare filenames like
    ``photo.jpg`` or relative paths like ``2024/summer.jpg`` work the same way
    they would in a POSIX shell.
    """
    if path.startswith("/"):
        return path
    cwd = _load_cwd()
    if cwd == "/":
        return "/" + path
    return cwd.rstrip("/") + "/" + path


def _client() -> DegooClient:
    from .auth import get_token as _get_token

    try:
        token = _get_token()  # eagerly validate — raises AuthError if no valid token
        return DegooClient(token=token)  # reuse token; avoids a second round-trip
    except AuthError as e:
        _err(e)
        raise SystemExit(1)


def _humanize_size(size: int | str | None) -> str:
    if size is None:
        return "—"
    try:
        import humanize

        return humanize.naturalsize(int(size), binary=True)
    except Exception:
        return str(size)


def _format_time(ts: str | int | None) -> str:
    if not ts:
        return "—"
    try:
        from datetime import datetime, timezone

        if isinstance(ts, str):
            # Try ISO 8601 first (e.g. "2023-05-16T16:03:19+00:00")
            try:
                dt = datetime.fromisoformat(ts)
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
            # Fall back to unix timestamp string
            ts = int(ts)
        # Degoo timestamps arrive in seconds, milliseconds, or (rarely) microseconds.
        # Thresholds use year-5138 (~1e11 s) and year-2001 (~1e15 µs) as boundaries
        # so all realistic timestamps are classified correctly.
        if ts > 1e15:  # microseconds
            ts = ts // 1_000_000
        elif ts > 1e11:  # milliseconds
            ts = ts // 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _category_icon(cat: int | None) -> str:
    icons = {
        0: "📄",
        1: "💻",
        2: "📁",
        3: "🖼️",
        4: "🎬",
        5: "🎵",
        6: "📃",
        10: "🗑️",
    }
    return icons.get(cat or 0, "❓")


# ═══════════════════════════════════════════════════════════════════════════════
# Custom group — prints subcommand help when a required arg is missing
# ═══════════════════════════════════════════════════════════════════════════════
class DegooGroup(click.Group):
    """Click group that prints the failing subcommand's help on UsageError."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except click.UsageError as exc:
            # Print the subcommand's full help, then the error — avoid the
            # double-usage-line that click's standalone_mode would produce.
            if exc.ctx is not None:
                click.echo(exc.ctx.get_help())
                click.echo()
            click.echo(f"Error: {exc.format_message()}", err=True)
            raise SystemExit(2)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI group
# ═══════════════════════════════════════════════════════════════════════════════
@click.group(
    cls=DegooGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, prog_name="cligoo")
def main():
    """Degoo CLI — manage your Degoo cloud storage from the terminal."""


# ── Auth ──────────────────────────────────────────────────────────────────────
@main.command()
@click.option("--email", default=None, help="Degoo account email")
@click.option("--password", default=None, help="Degoo account password")
@click.option(
    "--browser",
    "-b",
    is_flag=True,
    help="Open a browser window to sign in (for Google OAuth / SSO accounts)",
)
def login(email: Optional[str], password: Optional[str], browser: bool):
    """Authenticate with your Degoo account.

    \b
    Email / password accounts:
      cligoo login
      cligoo login --email user@example.com

    \b
    Google OAuth / SSO accounts (no password):
      cligoo login --browser

    \b
    The default method can be configured once with:
      cligoo config
    """
    # If no explicit flag was given, check the saved preference
    if not browser and email is None and password is None:
        if get_login_method() == "browser":
            browser = True

    if browser:
        from .chrome import list_profiles as _list_profiles
        from .config import get_chrome_profile

        profile_dir = get_chrome_profile()
        if profile_dir:
            # Resolve the display name for the configured profile
            profiles_map = {p["dir"]: p for p in _list_profiles()}
            p_info = profiles_map.get(profile_dir)
            label = (
                f"[bold]{p_info['name']}[/bold]" + (f"  [dim]({p_info['email']})[/dim]" if p_info.get("email") else "")
                if p_info
                else f"[bold]{profile_dir}[/bold]"
            )
            console.print(f"  Opening browser using Chrome profile {label}…")
            console.print("  [dim]Your existing session will be used if still active.[/dim]")
        else:
            console.print("  Opening browser… Sign in with Google when prompted.")
            console.print("  [dim]The browser will close automatically once the token is captured.[/dim]")
            console.print(
                "  [dim]Tip: run [bold]cligoo config[/bold] to use your real Chrome profile"
                " and skip the sign-in step next time.[/dim]"
            )
        try:
            tok, ref = fetch_token_via_browser()
            save_token_direct(tok, ref)
            if ref:
                console.print("[green]✓[/green] Token and refresh token captured and saved.")
            else:
                console.print("[green]✓[/green] Token captured and saved.")
            console.print("  Run [bold]cligoo whoami[/bold] to verify.")
        except AuthError as e:
            _err(e)
            raise SystemExit(1)
        return

    # Email / password flow
    from .auth import login as do_login

    saved_email, saved_password = get_saved_credentials()

    if not email:
        if saved_email:
            email = click.prompt("Email", default=saved_email)
        else:
            email = click.prompt("Email")
    else:
        # Email was passed on the command line — let the user know which account
        console.print(f"  Signing in as [bold]{email}[/bold]…")

    if not password:
        # Use stored password when email matches (or when no email was stored yet)
        if saved_password and (saved_email is None or saved_email == email):
            password = saved_password
            console.print("  [dim]Using stored password.[/dim]")
        else:
            password = click.prompt("Password", hide_input=True)

    try:
        do_login(email, password)
        console.print(f"[green]✓[/green] Logged in as [bold]{email}[/bold]. Token saved.")
    except AuthError as e:
        _err(e)
        raise SystemExit(1)


@main.command()
def logout():
    """Clear stored credentials and tokens."""
    from .auth import logout as do_logout

    do_logout()
    console.print("[green]✓[/green] Logged out. Credentials removed.")


@main.command(name="config")
def config_cmd():
    """Configure cligoo settings interactively.

    \b
    Currently configures:
      • Default login method (browser or password)
      • Chrome profile to seed for browser-based login

    \b
    When a Chrome profile is configured, cligoo login --browser copies that
    profile's session cookies into a temporary directory and launches Chrome
    from there.  If you are already signed into Google and Degoo in that
    profile the token is captured in seconds with no interaction required.
    Chrome can be open or closed — there is no conflict either way.
    """
    from .chrome import chrome_is_installed, list_profiles
    from .config import get_chrome_profile, get_login_method, save_config

    console.print()
    console.print("[bold]cligoo Configuration[/bold]")
    console.print()

    # ── Section 1: Login method ────────────────────────────────────────────
    console.print("[bold underline]Login Method[/bold underline]")
    console.print()
    console.print("  How should [bold]cligoo login[/bold] (no flags) authenticate by default?\n")

    current_method = get_login_method()
    b_marker = (
        "  [green]← current[/green]"
        if current_method == "browser"
        else ("  [green]← current[/green]" if current_method is None else "")
    )
    p_marker = "  [green]← current[/green]" if current_method == "password" else ""

    # When nothing is configured the default is password
    if current_method is None:
        p_marker = "  [green]← current[/green]"

    console.print(f"  [bold cyan]1)[/bold cyan]  Browser  [dim](Google OAuth — opens Chrome)[/dim]{b_marker}")
    console.print(f"  [bold cyan]2)[/bold cyan]  Password  [dim](email + password prompt)[/dim]{p_marker}")
    console.print()

    raw = click.prompt("  Select", default="1" if current_method == "browser" else "2", show_default=True).strip()
    try:
        method_idx = int(raw)
    except ValueError:
        _err(f"Invalid choice: {raw!r}")
        raise SystemExit(1)

    if method_idx == 1:
        save_config({"login_method": "browser"})
        console.print()
        console.print("[green]✓[/green] Default login method: [bold]browser[/bold].")
        console.print("  [dim]Running [bold]cligoo login[/bold] will open Chrome automatically.[/dim]")
    elif method_idx == 2:
        save_config({"login_method": "password"})
        console.print()
        console.print("[green]✓[/green] Default login method: [bold]password[/bold].")
    else:
        _err(f"Invalid choice: {method_idx}")
        raise SystemExit(1)

    console.print()

    # ── Section 2: Chrome profile (only relevant for browser method) ───────
    console.print("[bold underline]Browser Login — Chrome Profile[/bold underline]")
    console.print()

    if not chrome_is_installed():
        console.print(
            "[yellow]⚠[/yellow]  System Chrome not found.\n"
            "   Browser login will use a fresh temporary Playwright Chromium profile.\n"
            "   Install Google Chrome to enable profile selection."
        )
        console.print()
        return

    profiles = list_profiles()
    if not profiles:
        console.print("[yellow]⚠[/yellow]  No Chrome profiles found in Local State.")
        console.print()
        return

    current = get_chrome_profile()

    console.print(
        "  When you run [bold]cligoo login --browser[/bold], cligoo can seed\n"
        "  the browser with a copy of your real Chrome profile so your existing\n"
        "  Google and Degoo sessions carry over automatically.\n"
    )

    # Option 0 — fresh temporary profile
    current_marker = "  [green]← current[/green]" if current is None else ""
    label = "  [bold cyan]0)[/bold cyan]  Fresh temporary profile  [dim](no sign-in session)[/dim]"
    console.print(f"{label}{current_marker}")

    # Options 1…N — real profiles
    for i, p in enumerate(profiles, start=1):
        current_marker = "  [green]← current[/green]" if p["dir"] == current else ""
        email_str = f"  [dim]{p['email']}[/dim]" if p.get("email") else ""
        console.print(f"  [bold cyan]{i})[/bold cyan]  {p['name']}{email_str}{current_marker}")

    console.print()
    raw = click.prompt("  Select", default="0", show_default=True).strip()

    try:
        idx = int(raw)
    except ValueError:
        _err(f"Invalid choice: {raw!r}")
        raise SystemExit(1)

    if idx == 0:
        save_config({"chrome_profile": None})
        console.print()
        console.print("[green]✓[/green] Configured: fresh temporary profile.")
    elif 1 <= idx <= len(profiles):
        chosen = profiles[idx - 1]
        save_config({"chrome_profile": chosen["dir"]})
        console.print()
        name_str = chosen["name"]
        email_str = f"  ({chosen['email']})" if chosen.get("email") else ""
        console.print(f"[green]✓[/green] Configured: Chrome profile [bold]{name_str}[/bold]{email_str}.")
        console.print("  Run [bold]cligoo login --browser[/bold] — your existing session will be used.")
    else:
        _err(f"Invalid choice: {idx}")
        raise SystemExit(1)

    console.print()


@main.command(name="token")
@click.argument("access_token")
@click.option(
    "--refresh",
    "refresh_token",
    default="",
    help="Refresh token (extends session life, optional)",
)
def token_cmd(access_token: str, refresh_token: str):
    """Store a JWT token directly (for Google OAuth / SSO accounts).

    \b
    Paste a token extracted from Chrome DevTools:
      cligoo token <TOKEN>

    \b
    For automatic browser-based sign-in use:
      cligoo login --browser

    \b
    How to extract manually (Chrome DevTools):
      1. Open app.degoo.com and sign in with Google
      2. Open DevTools → Network tab  (F12)
      3. Reload the page or browse your files
      4. Click any request to production-appsync.degoo.com
      5. Open the Payload / Request Body tab
      6. Copy the value of "Token" inside "variables"
      7. Run: cligoo token <paste-here>
    """
    try:
        save_token_direct(access_token, refresh_token)
        console.print("[green]✓[/green] Token saved. Run [bold]cligoo whoami[/bold] to verify.")
    except AuthError as e:
        _err(e)
        raise SystemExit(1)


@main.command()
def whoami():
    """Show the authenticated user's profile and quota."""
    client = _client()
    try:
        info = client.get_user_info()
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    table = Table(title="Degoo Account", box=box.ROUNDED)
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    table.add_row("Name", f"{info.get('FirstName', '')} {info.get('LastName', '')}".strip())
    table.add_row("Email", info.get("Email", "—"))
    table.add_row("Account Type", str(info.get("AccountType", "—")))
    table.add_row("Used", _humanize_size(info.get("UsedQuota")))
    table.add_row("Total", _humanize_size(info.get("TotalQuota")))

    used = int(info.get("UsedQuota", 0))
    total = int(info.get("TotalQuota", 1))
    pct = (used / total * 100) if total else 0
    table.add_row("Usage", f"{pct:.1f}%")
    table.add_row("File Size Limit", _humanize_size(info.get("FileSizeLimit")))

    console.print(table)


@main.command()
def quota():
    """Show storage quota usage."""
    client = _client()
    try:
        info = client.get_user_info()
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)
    used = int(info.get("UsedQuota", 0))
    total = int(info.get("TotalQuota", 1))
    pct = (used / total * 100) if total else 0

    console.print(f"  Used:  {_humanize_size(used)}")
    console.print(f"  Total: {_humanize_size(total)}")
    console.print(f"  Free:  {_humanize_size(total - used)}")
    console.print(f"  Usage: {pct:.1f}%")


# ── Navigation ────────────────────────────────────────────────────────────────

_NAV_DISABLED_MSG = (
    "'{cmd}' is only available inside the interactive shell.\n"
    "  Run: cligoo shell\n\n"
    "  To enable standalone use, add to ~/.config/cligoo/config.toml:\n"
    "    [advanced]\n"
    "    standalone_nav = true"
)


@main.command()
def pwd():
    """Print the current working directory (shell only by default).

    \b
    Enable standalone use in config.toml:
      [advanced]
      standalone_nav = true
    """
    if not get_standalone_nav_enabled():
        _err(_NAV_DISABLED_MSG.format(cmd="pwd"))
        raise SystemExit(1)
    console.print(_load_cwd())


@main.command()
@click.argument("path", default="/")
def cd(path: str):
    """Change the current working directory (shell only by default).

    \b
    Enable standalone use in config.toml:
      [advanced]
      standalone_nav = true

    \b
    Examples:
      cligoo cd /iPhone
      cligoo cd /iPhone/Camera
      cligoo cd /          (back to root)
    """
    if not get_standalone_nav_enabled():
        _err(_NAV_DISABLED_MSG.format(cmd="cd"))
        raise SystemExit(1)

    client = _client()

    if path == "/" or path == "0":
        _save_cwd("/")
        console.print("[green]✓[/green] Now at  /")
        return

    try:
        item = client.resolve_path(path)
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)
    if item is None:
        _err(f"Path not found: {path}")
        raise SystemExit(1)
    # Allow cd into any item — items created via setUploadFile3 may have
    # Category=0 but can still act as parent directories.
    if item.get("URL"):
        # Items with a download URL are real files, not directories.
        _err(f"Not a directory: {path}")
        raise SystemExit(1)

    # Normalise the stored path
    norm = "/" + path.strip("/")
    _save_cwd(norm)
    console.print(f"[green]✓[/green] Now at  {norm}")


# ── Listing ───────────────────────────────────────────────────────────────────
@main.command(name="ls")
@click.argument("path", default=None, required=False)
@click.option("-l", "--long", is_flag=True, help="Show detailed listing")
@click.option("-S", "sort_by", flag_value="size", help="Sort by size (largest first)")
@click.option("-t", "sort_by", flag_value="time", help="Sort by modification time (newest first)")
@click.option("-r", "--reverse", is_flag=True, help="Reverse sort order")
@click.option("-R", "--recursive", is_flag=True, help="List subdirectories recursively (same as -d 99)")
@click.option(
    "-d",
    "--depth",
    default=0,
    help="Show subtree up to this many levels deep (0 = flat list)",
)
@click.option("-n", "--limit", default=100, help="Max items per directory")
def ls(path: Optional[str], long: bool, sort_by: Optional[str], reverse: bool, recursive: bool, depth: int, limit: int):
    """List files and folders.

    If PATH is omitted the current working directory is used (see 'cligoo cd').

    \b
    Sort options:
      -S          sort by size (largest first)
      -t          sort by modification time (newest first)
      -r          reverse the sort order

    \b
    Recursion:
      -R          list all subdirectories recursively (equivalent to -d 99)
      -d N        show subtree N levels deep

    \b
    Examples:
      cligoo ls                  # list cwd
      cligoo ls /iPhone
      cligoo ls -l               # long listing with IDs, sizes, dates
      cligoo ls -lS              # long + sort by size
      cligoo ls -lt              # long + sort by newest first
      cligoo ls -R               # recursive tree
      cligoo ls -d 2             # show 2 levels of subtree
      cligoo ls /iPhone -d 3 -l  # long + subtree
    """
    client = _client()

    # Use cwd if no path given
    if path is None:
        path = _load_cwd()

    # Resolve path to ID
    if path == "/" or path == "0":
        parent_id = "0"
        display_path = "/"
    else:
        item = client.resolve_path(path)
        if item is None:
            _err(f"Path not found: {path}")
            raise SystemExit(1)
        if item.get("URL"):
            # Item has a download URL — it's a real file, not a directory.
            _print_item_detail(item)
            return
        parent_id = str(item["ID"])
        display_path = "/" + path.strip("/")

    console.print(f"[dim]{display_path}[/dim]")

    try:
        items = client.list_dir(parent_id, limit=limit)
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    if not items:
        console.print("[dim]  (empty)[/dim]")
        return

    # ── Sorting ──────────────────────────────────────────────────────────────
    if sort_by == "size":
        items.sort(key=lambda x: int(x.get("Size") or 0), reverse=not reverse)
    elif sort_by == "time":
        items.sort(key=lambda x: x.get("LastModificationTime") or "", reverse=not reverse)
    else:
        # Default: alphabetical by name
        items.sort(key=lambda x: (x.get("Name") or "").lower(), reverse=reverse)

    # ── Effective depth (--recursive overrides depth default of 0) ───────────
    effective_depth = 99 if (recursive and depth == 0) else depth

    if effective_depth > 0:
        # Tree mode integrated into ls
        tree = RichTree(f"📁 [bold]{display_path}[/bold]")
        _build_tree(client, tree, parent_id, effective_depth, current_depth=0, long=long, limit=limit)
        console.print(tree)
    elif long:
        table = Table(box=box.SIMPLE_HEAVY)
        table.add_column("ID", style="dim")
        table.add_column("Type", justify="center")
        table.add_column("Name")
        table.add_column("Size", justify="right")
        table.add_column("Modified")
        table.add_column("Category")

        for it in items:
            cat = it.get("Category", 0)
            icon = _category_icon(cat)
            name_style = "bold cyan" if cat in FOLDER_CATEGORIES else ""
            table.add_row(
                str(it.get("ID", "")),
                icon,
                f"[{name_style}]{it.get('Name', '?')}[/{name_style}]" if name_style else it.get("Name", "?"),
                _humanize_size(it.get("Size")) if cat not in FOLDER_CATEGORIES else "—",
                _format_time(it.get("LastModificationTime")),
                CATEGORY_NAMES.get(cat, str(cat)),
            )
        console.print(table)
    else:
        for it in items:
            cat = it.get("Category", 0)
            icon = _category_icon(cat)
            name = it.get("Name", "?")
            if cat in FOLDER_CATEGORIES:
                console.print(f"  {icon} [bold cyan]{name}/[/bold cyan]")
            else:
                console.print(f"  {icon} {name}")

    if effective_depth == 0:
        console.print(f"\n[dim]  {len(items)} item(s)[/dim]")


@main.command()
@click.argument("path", default=None, required=False)
@click.option("-d", "--depth", default=2, help="Max depth to traverse")
@click.option("-n", "--limit", default=200, help="Max items per directory")
def tree(path: Optional[str], depth: int, limit: int):
    """Show a recursive tree view of files and folders.

    If PATH is omitted the current working directory is used.
    """
    client = _client()

    if path is None:
        path = _load_cwd()

    if path == "/" or path == "0":
        parent_id = "0"
        root_name = "/"
    else:
        item = client.resolve_path(path)
        if item is None:
            _err(f"Path not found: {path}")
            raise SystemExit(1)
        parent_id = str(item["ID"])
        root_name = item.get("Name", path)

    rich_tree = RichTree(f"📁 [bold]{root_name}[/bold]")
    _build_tree(client, rich_tree, parent_id, depth, current_depth=0, long=False, limit=limit)
    console.print(rich_tree)


def _build_tree(
    client: DegooClient,
    node: RichTree,
    parent_id: str,
    max_depth: int,
    current_depth: int,
    long: bool = False,
    limit: int = 200,
):
    if current_depth >= max_depth:
        return
    try:
        items = client.list_dir(parent_id, limit=limit)
    except DegooAPIError:
        return

    for it in items:
        cat = it.get("Category", 0)
        icon = _category_icon(cat)
        name = it.get("Name", "?")
        item_id = str(it.get("ID", ""))

        if long:
            size_str = _humanize_size(it.get("Size")) if cat not in FOLDER_CATEGORIES else ""
            mtime = _format_time(it.get("LastModificationTime"))
            id_hint = f"[dim]{item_id}[/dim]  "
        else:
            size_str = _humanize_size(it.get("Size")) if cat not in FOLDER_CATEGORIES else ""
            id_hint = ""

        if cat in FOLDER_CATEGORIES:
            label = f"{icon} [bold cyan]{name}/[/bold cyan]"
            if long:
                label = f"{id_hint}{label}  [dim]{mtime}[/dim]"
        else:
            label = f"{icon} {name}"
            if long:
                label = f"{id_hint}{label}  [dim]{size_str}  {mtime}[/dim]"
            elif size_str:
                label = f"{label}  [dim]{size_str}[/dim]"

        child = node.add(label)

        if cat in FOLDER_CATEGORIES:
            _build_tree(client, child, item_id, max_depth, current_depth + 1, long=long, limit=limit)


# ── Info ──────────────────────────────────────────────────────────────────────

def _compute_folder_size(client: "DegooClient", folder_id: str) -> tuple[int, int, int]:
    """Recursively walk *folder_id* and return ``(total_bytes, file_count, folder_count)``.

    Uses BFS with a visited set to guard against API cycles.
    """
    total_bytes = 0
    file_count = 0
    folder_count = 0
    visited: set[str] = {folder_id}
    queue = [folder_id]
    while queue:
        current_id = queue.pop()
        try:
            children = client.list_dir(current_id, limit=None)
        except DegooAPIError:
            continue
        for child in children:
            child_id = child.get("ID", "")
            if child.get("Category", 0) in FOLDER_CATEGORIES:
                folder_count += 1
                if child_id and child_id not in visited:
                    visited.add(child_id)
                    queue.append(child_id)
            else:
                file_count += 1
                try:
                    total_bytes += int(child.get("Size") or 0)
                except (ValueError, TypeError):
                    pass
    return total_bytes, file_count, folder_count


@main.command()
@click.argument("item_path")
@click.option(
    "--no-size",
    is_flag=True,
    default=False,
    help="Skip recursive content-size calculation for folders (fast for large trees).",
)
def info(item_path: str, no_size: bool):
    """Show detailed metadata for an item (path or numeric ID).

    \b
    For folders the total content size is calculated by walking the full tree.
    Use --no-size to skip this for very large folders.
    """
    client = _client()
    try:
        _item_id, item = _resolve_item(client, item_path)
    except SystemExit:
        raise
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    # For folders compute recursive content stats unless the user opts out.
    folder_stats: Optional[tuple[int, int, int]] = None
    if item.get("Category", 0) in FOLDER_CATEGORIES and not no_size:
        with console.status("[dim]Calculating folder size…[/dim]", spinner="dots"):
            try:
                folder_stats = _compute_folder_size(client, item["ID"])
            except Exception:
                folder_stats = None

    _print_item_detail(item, folder_stats=folder_stats, size_skipped=no_size)


def _print_item_detail(
    item: dict,
    *,
    folder_stats: Optional[tuple[int, int, int]] = None,
    size_skipped: bool = False,
):
    table = Table(title=item.get("Name", "Item"), box=box.ROUNDED)
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    is_folder = item.get("Category", 0) in FOLDER_CATEGORIES

    fields = [
        ("ID", "ID"),
        ("Name", "Name"),
        ("Category", None),
        ("Size", None),
        ("Parent ID", "ParentID"),
        ("File Path", "FilePath"),
        ("Created", "CreationTime"),
        ("Modified", "LastModificationTime"),
        ("Uploaded", "LastUploadTime"),
        ("In Recycle Bin", "IsInRecycleBin"),
        ("Description", "Description"),
        ("URL", "URL"),
        ("Thumbnail", "ThumbnailURL"),
    ]
    for label, key in fields:
        if key is None:
            if label == "Category":
                cat = item.get("Category", 0)
                table.add_row(label, f"{_category_icon(cat)} {CATEGORY_NAMES.get(cat, str(cat))}")
            elif label == "Size":
                if not is_folder:
                    table.add_row(label, _humanize_size(item.get("Size")))
        else:
            val = item.get(key)
            if val is not None:
                if "Time" in key:
                    table.add_row(label, _format_time(val))
                else:
                    table.add_row(label, str(val))

    # Folder-specific content stats
    if is_folder:
        if size_skipped:
            table.add_row("Content Size", "[dim]skipped (--no-size)[/dim]")
        elif folder_stats is not None:
            total_bytes, file_count, folder_count = folder_stats
            table.add_row("Content Size", _humanize_size(total_bytes))
            table.add_row("Files", str(file_count))
            table.add_row("Sub-folders", str(folder_count))
        else:
            table.add_row("Content Size", "[dim]unavailable[/dim]")

    console.print(table)


# ── Search ────────────────────────────────────────────────────────────────────
@main.command()
@click.argument("term")
@click.option("-n", "--limit", default=50, help="Max results")
def search(term: str, limit: int):
    """Search for files by name or content."""
    client = _client()
    try:
        items = client.search(term, limit=limit)
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    if not items:
        console.print("[dim]  No results found.[/dim]")
        return

    table = Table(title=f"Search: {term}", box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="dim")
    table.add_column("Type", justify="center")
    table.add_column("Name")
    table.add_column("Size", justify="right")
    table.add_column("Path")

    for it in items:
        cat = it.get("Category", 0)
        table.add_row(
            str(it.get("ID", "")),
            _category_icon(cat),
            it.get("Name", "?"),
            _humanize_size(it.get("Size")) if cat not in FOLDER_CATEGORIES else "—",
            it.get("FilePath", "—"),
        )
    console.print(table)
    console.print(f"\n[dim]  {len(items)} result(s)[/dim]")


# ── Create folder ─────────────────────────────────────────────────────────────
@main.command()
@click.argument("path")
def mkdir(path: str):
    """Create a new folder.

    PATH can be an absolute path (/Device/NewFolder) or just a name,
    in which case the folder is created inside the current working directory.
    """
    client = _client()

    # If the path has no slashes, treat it as a bare name relative to CWD.
    is_bare_name = "/" not in path
    if is_bare_name:
        cwd = _load_cwd()
        if cwd and cwd != "/":
            path = cwd.rstrip("/") + "/" + path
        else:
            _err("Cannot create a folder at the root — specify a full path like /Device/FolderName")
            raise SystemExit(1)

    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        _err("Invalid path")
        raise SystemExit(1)

    # Resolve parent
    parent_id = "0"
    if len(parts) > 1:
        parent_path = "/".join(parts[:-1])
        parent = client.resolve_path(parent_path)
        if parent is None:
            _err(f"Parent path not found: {parent_path}")
            raise SystemExit(1)
        parent_id = str(parent["ID"])

    folder_name = parts[-1]
    try:
        client.mkdir(folder_name, parent_id)
        console.print(f"[green]✓[/green] Created folder: {folder_name}")
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)


# ── Transfer helpers ──────────────────────────────────────────────────────────

def _resolve_parent_id(client: "DegooClient", parent_path: Optional[str]) -> str:
    """Return the Degoo folder ID for *parent_path* (or the saved cwd)."""
    if parent_path is None:
        parent_path = _load_cwd()
    if parent_path == "/" or parent_path == "0":
        return "0"
    parent = client.resolve_path(parent_path)
    if parent is None:
        _err(f"Parent path not found: {parent_path}")
        raise SystemExit(1)
    return str(parent["ID"])


def _get_workers(override: Optional[int]) -> int:
    """Return the worker count: command option → config file → built-in default (20)."""
    if override is not None:
        return max(1, override)
    return get_transfer_workers()


def _transfer_summary(ok: int, skipped: int = 0, failed: int = 0, action: str = "uploaded") -> str:
    """Build a human-readable transfer summary string, e.g. '5 uploaded, 2 skipped'."""
    parts = []
    if ok:
        parts.append(f"{ok} {action}")
    if skipped:
        parts.append(f"{skipped} skipped")
    if failed:
        parts.append(f"{failed} failed")
    return ", ".join(parts) if parts else f"0 {action}"


def _make_progress() -> Any:
    """Create a shared Rich Progress display for file transfers."""
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    )


# ── Upload ────────────────────────────────────────────────────────────────────

def _upload_one(
    client: "DegooClient",
    filepath: Path,
    parent_id: str,
    name: Optional[str],
    progress: Any,
    overall_advance: Optional[Any] = None,
) -> None:
    """Upload one local file, adding its row to the shared *progress* display.

    ``overall_advance(delta_bytes)`` is called on each progress tick so the
    caller can keep a byte-accurate overall progress bar.

    Raises ``RuntimeError`` on failure (so the ThreadPoolExecutor caller can
    catch it without the thread swallowing a ``SystemExit``).
    """
    file_size = filepath.stat().st_size
    display = name or filepath.name
    task_id = progress.add_task(f"↑ {display}", total=file_size)
    last: list[int] = [0]

    def cb(uploaded: int, _total: int) -> None:
        progress.update(task_id, completed=uploaded)
        if overall_advance is not None:
            delta = uploaded - last[0]
            if delta > 0:
                overall_advance(delta)
                last[0] = uploaded

    try:
        client.upload(filepath, parent_id, name=name, progress_callback=cb)
        # Ensure per-file bar and overall both reach 100 %
        remaining = file_size - last[0]
        progress.update(task_id, completed=file_size)
        if overall_advance is not None and remaining > 0:
            overall_advance(remaining)
        progress.remove_task(task_id)
    except DegooAlreadyExistsError:
        # File is already present in the target folder (dedup check in api.py).
        if overall_advance is not None:
            overall_advance(file_size - last[0])
        progress.remove_task(task_id)
        raise  # re-raise so caller counts it as skipped
    except DegooAPIError as e:
        msg = str(e)
        # HTTP 4xx from GCS (403 AccessDenied, 400 InvalidPolicyDocument, etc.)
        # means the storage policy rejected this file type — skip rather than fail.
        if any(s in msg for s in ("HTTP 4", "AccessDenied", "Policy Condition", "InvalidPolicy")):
            if overall_advance is not None:
                overall_advance(file_size - last[0])
            progress.remove_task(task_id)
            raise DegooAlreadyExistsError(msg) from e  # sentinel: count as skipped
        progress.remove_task(task_id)
        raise RuntimeError(str(e)) from e


def _collect_upload_tasks(
    client: "DegooClient",
    local_dir: Path,
    parent_id: str,
    exclude: tuple[str, ...] = (),
    *,
    _cat2_resolver: "Optional[Any]" = None,
) -> list[tuple[Path, str]]:
    """Recursively create remote dirs and return ``[(local_file, remote_parent_id)]``.

    Degoo's setUploadFile3 with Size=0/Checksum="" creates a Category=6 ghost
    item immediately.  A proper Category=2 Folder only appears in the *grandparent*
    listing after the first child is written inside the ghost.  list_dir() on a
    Category=6 ghost always returns empty, so resolve_path_under() cannot find
    children there.

    We handle this with a lazy resolver: ``_cat2_resolver`` is a zero-argument
    callable that returns the real (Category=2) ID of the *current* folder by
    querying the grandparent listing.  It is supplied by the caller when the
    caller's folder is itself a ghost.  At the top level (parent is a real
    Category=2 folder) no resolver is needed.

    Directory creation is intentionally sequential; file uploads are deferred
    and run in parallel by the caller.
    """
    import fnmatch
    import os as _os

    # If the folder already exists Degoo returns "Invalid input!" from mkdir.
    # In that case resolve the existing folder and reuse its ID.
    folder_existed = False
    mkdir_result: str = "OK"
    try:
        mkdir_result = client.mkdir(local_dir.name, parent_id)
        console.print(f"  [blue]mkdir[/blue] {local_dir.name}")
    except DegooAPIError as e:
        msg = str(e).lower()
        if "invalid input" in msg or "already exist" in msg:
            folder_existed = True
            console.print(f"  [dim]mkdir[/dim] {local_dir.name} [dim](already exists)[/dim]")
        else:
            raise RuntimeError(f"mkdir {local_dir.name}: {e}") from e

    # Resolve the newly created (or pre-existing) folder.
    #
    # Degoo's setUploadFile3 returns the new ghost folder's ID when a folder is
    # freshly created.  We prefer that direct ID over resolve_path_under because
    # list_dir() on a Cat=6 ghost always returns empty — so resolve_path_under
    # would fail at any nesting depth where the parent is still a ghost
    # (e.g. GoPro/HERO11 Black/100GOPRO before any file has been uploaded).
    #
    # When the folder pre-existed (folder_existed=True) mkdir raised before we
    # got a result, so we must use resolve_path_under to find the existing entry.
    if _cat2_resolver is not None:
        actual_parent_id = _cat2_resolver()
    else:
        actual_parent_id = parent_id

    new_folder = None
    # Try direct ID lookup first (works even when parent is a ghost)
    _mkdir_str = str(mkdir_result) if mkdir_result is not None else ""
    if not folder_existed and _mkdir_str.strip().isdigit():
        try:
            new_folder = client.get_item(_mkdir_str.strip())
        except Exception:  # noqa: BLE001
            new_folder = None  # fall through to resolve_path_under

    # Fall back to listing the parent (works when parent is a real Cat=2 folder)
    if new_folder is None:
        new_folder = client.resolve_path_under(actual_parent_id, local_dir.name)

    if new_folder is None:
        if folder_existed:
            raise RuntimeError(
                f"Folder '{local_dir.name}' already exists in Degoo but could not be resolved. "
                "Try listing the destination to confirm it is there."
            )
        raise RuntimeError(f"Could not resolve newly created folder: {local_dir.name}")
    new_id = str(new_folder["ID"])

    # Build a resolver for our children: after a child mkdir inside new_id
    # (which may be a Cat=6 ghost), Degoo creates a Cat=2 version of new_id
    # visible inside actual_parent_id.  The resolver captures both.
    _folder_name = local_dir.name

    def _child_resolver() -> str:
        refreshed = client.resolve_path_under(actual_parent_id, _folder_name)
        if refreshed is not None and client.is_folder(refreshed):
            return str(refreshed["ID"])
        return new_id

    tasks: list[tuple[Path, str]] = []
    for entry in sorted(_os.scandir(local_dir), key=lambda e: (e.is_dir(), e.name)):
        if exclude and any(fnmatch.fnmatch(entry.name, pat) for pat in exclude):
            console.print(f"  [dim]skip[/dim] {entry.name}")
            continue
        if entry.is_dir(follow_symlinks=False):
            tasks.extend(
                _collect_upload_tasks(
                    client, Path(entry.path), new_id, exclude,
                    _cat2_resolver=_child_resolver,
                )
            )
            # After the child mkdir triggered Cat=2 creation, refresh new_id
            # so any subsequent files in this folder use the real Cat=2 ID.
            refreshed = client.resolve_path_under(actual_parent_id, _folder_name)
            if refreshed is not None and client.is_folder(refreshed):
                new_id = str(refreshed["ID"])
        elif entry.is_file(follow_symlinks=False):
            tasks.append((Path(entry.path), new_id))
    return tasks


@main.command()
@click.argument("files", nargs=-1, required=True)
@click.option("--dest", "-t", default=None, help="Remote destination folder (default: default_upload_dir or /Web).")
@click.option("--name", help="Override the uploaded filename (single file only)")
@click.option("-r", "--recursive", is_flag=True, help="Upload directories recursively")
@click.option(
    "--exclude",
    multiple=True,
    metavar="PATTERN",
    help="Exclude files matching PATTERN, e.g. '*.tmp' (repeatable)",
)
@click.option("--workers", default=None, type=int, help="Concurrent workers (overrides config)")
def upload(
    files: tuple[str, ...],
    dest: Optional[str],
    name: Optional[str],
    recursive: bool,
    exclude: tuple[str, ...],
    workers: Optional[int],
) -> None:
    """Upload one or more local files (or directories with -r) to Degoo.

    \b
    Destination is set with --dest / -t.  When omitted, the value of
    default_upload_dir from the config file is used (factory default: /Web).
    Multiple sources may be given in one command.

    \b
    Examples:
      cligoo upload report.pdf
      cligoo upload report.pdf -t /Work/Reports
      cligoo upload report.pdf -t /Work/Reports --name final.pdf
      cligoo upload a.jpg b.jpg c.jpg -t /Web/Photos
      cligoo upload ~/Pictures/holiday -t /Web/Photos -r
      cligoo upload ~/Pictures/holiday -t /Web/Photos -r --exclude '*.tmp' --exclude '.DS_Store'
    """
    if name and len(files) > 1:
        _err("--name can only be used when uploading a single file")
        raise SystemExit(1)

    client = _client()
    effective_dest = dest if dest is not None else get_default_upload_dir()
    parent_id = _resolve_parent_id(client, effective_dest)
    num_workers = _get_workers(workers)

    # ── Collect all file tasks (dir creation happens here, sequentially) ──────
    all_tasks: list[tuple[Path, str, Optional[str]]] = []
    for file_arg in files:
        p = Path(file_arg)
        if not p.exists():
            _err(f"Not found: {p}")
            raise SystemExit(1)
        if p.is_dir():
            if not recursive:
                _err(f"{p} is a directory — use -r / --recursive to upload it")
                raise SystemExit(1)
            try:
                sub = _collect_upload_tasks(client, p, parent_id, exclude)
            except RuntimeError as e:
                _err(str(e))
                raise SystemExit(1)
            all_tasks.extend((fp, pid, None) for fp, pid in sub)
        else:
            all_tasks.append((p, parent_id, name if len(files) == 1 else None))

    if not all_tasks:
        console.print("[yellow]⚠[/yellow]  No files to upload.")
        return

    ok = failed = skipped = 0
    errors: list[str] = []

    total_bytes = sum(fp.stat().st_size for fp, _, _ in all_tasks)

    with _make_progress() as progress:
        overall = progress.add_task(
            f"[bold]Total ({len(all_tasks)} file(s))[/bold]",
            total=total_bytes,
        )

        def _advance_overall(delta: int) -> None:
            progress.advance(overall, delta)

        def _do_upload(args: tuple[Path, str, Optional[str]]) -> None:
            fp, pid, fname = args
            _upload_one(client, fp, pid, fname, progress, overall_advance=_advance_overall)

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {executor.submit(_do_upload, task): task for task in all_tasks}
            for fut in as_completed(futures):
                try:
                    fut.result()
                    ok += 1
                except DegooAlreadyExistsError:
                    skipped += 1
                except Exception as exc:
                    failed += 1
                    fp, _pid, _fname = futures[fut]
                    errors.append(f"{fp.name}: {exc}")

    if errors:
        for msg in errors:
            err_console.print(f"  [red]✗[/red] {_esc(msg)}")

    summary = _transfer_summary(ok, skipped, failed)

    if failed:
        console.print(f"[yellow]⚠[/yellow]  {summary}")
        raise SystemExit(1)
    elif skipped and not ok:
        console.print(f"[yellow]⚠[/yellow]  {summary}")
    else:
        console.print(f"[green]✓[/green]  {summary}")


# ── Download ──────────────────────────────────────────────────────────────────

def _download_one(
    client: "DegooClient",
    item_id: str,
    item_name: str,
    local_dest: Path,
    progress: Any,
    *,
    name_override: Optional[str] = None,
    overall_advance: Optional[Any] = None,
    overwrite: bool = False,
) -> Path:
    """Download one Degoo file, adding its row to the shared *progress* display.

    ``overall_advance(delta_bytes)`` is called on each progress tick so the
    caller can keep a byte-accurate overall progress bar.

    Raises ``RuntimeError`` on failure so the ThreadPoolExecutor caller can
    collect errors without the thread swallowing a ``SystemExit``.
    """
    display = name_override or item_name
    task_id = progress.add_task(f"↓ {display}", total=None)
    last: list[int] = [0]
    total_seen: list[int] = [0]  # track content-length reported by server

    def cb(downloaded: int, total: int) -> None:
        if total:
            progress.update(task_id, total=total, completed=downloaded)
            total_seen[0] = total
        if overall_advance is not None:
            delta = downloaded - last[0]
            if delta > 0:
                overall_advance(delta)
                last[0] = downloaded

    try:
        out = client.download(item_id, local_dest, name=name_override, progress_callback=cb, overwrite=overwrite)
        # Mark per-file bar complete; total may still be None if no Content-Length
        if total_seen[0]:
            remaining = total_seen[0] - last[0]
            progress.update(task_id, completed=total_seen[0])
            if overall_advance is not None and remaining > 0:
                overall_advance(remaining)
        progress.remove_task(task_id)
        return out
    except (DegooAPIError, FileNotFoundError) as e:
        progress.remove_task(task_id)
        raise RuntimeError(str(e)) from e


def _collect_download_tasks(
    client: "DegooClient",
    folder_id: str,
    folder_name: str,
    local_dest: Path,
    skip_existing: bool = False,
) -> list[tuple[str, str, Path, int]]:
    """Recursively create local dirs and return ``[(item_id, item_name, local_dest_dir, size)]``.

    If *skip_existing* is True, files already present on disk are omitted.
    """
    local_folder = local_dest / folder_name
    local_folder.mkdir(parents=True, exist_ok=True)
    console.print(f"  [blue]mkdir[/blue] {local_folder}")

    tasks: list[tuple[str, str, Path, int]] = []
    try:
        children = client.list_dir(folder_id)
    except DegooAPIError as e:
        _err(f"Could not list {folder_name}: {e}")
        return tasks

    for item in children:
        if client.is_folder(item):
            tasks.extend(
                _collect_download_tasks(
                    client, str(item["ID"]), item["Name"], local_folder, skip_existing
                )
            )
        else:
            if skip_existing and (local_folder / item["Name"]).exists():
                console.print(f"  [dim]skip[/dim] {item['Name']} (already exists)")
                continue
            size = int(item.get("Size") or 0)
            tasks.append((str(item["ID"]), item["Name"], local_folder, size))
    return tasks


@main.command()
@click.argument("items", nargs=-1, required=True)
@click.option(
    "--dest", "-t", default=".", type=click.Path(), help="Local destination directory (default: .)"
)
@click.option("--name", help="Override the downloaded filename (single file only)")
@click.option("-r", "--recursive", is_flag=True, help="Download a folder recursively")
@click.option("--skip-existing", is_flag=True, help="Skip files already present locally")
@click.option("--overwrite", is_flag=True, help="Overwrite existing local files (default: save as numbered copy)")
@click.option("--workers", default=None, type=int, help="Concurrent workers (overrides config)")
def download(
    items: tuple[str, ...],
    dest: str,
    name: Optional[str],
    recursive: bool,
    skip_existing: bool,
    overwrite: bool,
    workers: Optional[int],
) -> None:
    """Download one or more files or folders from Degoo (path or numeric ID).

    \b
    Destination is set with --dest / -t (defaults to the current local
    directory).  Multiple items may be downloaded in one command.

    \b
    By default, if the destination file already exists it is saved as a
    numbered copy (report (1).pdf) instead of overwriting.  Use --overwrite
    to replace existing files, or --skip-existing to skip them entirely.

    \b
    Examples:
      cligoo download /Web/report.pdf
      cligoo download /Web/report.pdf -t ~/Downloads
      cligoo download /Web/Photos/2024 -t ~/Pictures -r
      cligoo download 123456789 -t .
      cligoo download /Web/a.jpg /Web/b.jpg -t ~/Downloads
      cligoo download /Web/Photos -t ~/Pictures -r --skip-existing
    """
    if name and len(items) > 1:
        _err("--name can only be used when downloading a single item")
        raise SystemExit(1)

    client = _client()
    dest_path = Path(dest)
    num_workers = _get_workers(workers)

    # ── Collect all file tasks (folder walk + local mkdir happens here) ────────
    # all_tasks: (item_id, item_name, local_dest_dir, name_override, size)
    all_tasks: list[tuple[str, str, Path, Optional[str], int]] = []
    for item_arg in items:
        item_id, item = _resolve_item(client, item_arg)
        # Treat as folder if: Category is a folder type, OR item has no download
        # URL (Degoo sometimes returns Category=Document for folders created via
        # the API when the inferred type doesn't match FOLDER_CATEGORIES).
        looks_like_folder = client.is_folder(item) or not item.get("URL")
        if looks_like_folder:
            if not recursive:
                _err(f"{item_arg} is a folder — use -r / --recursive to download it")
                raise SystemExit(1)
            sub = _collect_download_tasks(
                client, item_id, item["Name"], dest_path, skip_existing
            )
            all_tasks.extend((iid, iname, idest, None, sz) for iid, iname, idest, sz in sub)
        else:
            fname = name if len(items) == 1 else None
            size = int(item.get("Size") or 0)
            all_tasks.append((item_id, item["Name"], dest_path, fname, size))

    if not all_tasks:
        console.print("[yellow]⚠[/yellow]  No files to download.")
        return

    ok = failed = 0
    errors: list[str] = []

    total_bytes = sum(sz for _, _, _, _, sz in all_tasks)

    with _make_progress() as progress:
        overall = progress.add_task(
            f"[bold]Total ({len(all_tasks)} file(s))[/bold]",
            total=total_bytes or len(all_tasks),
        )

        def _advance_overall(delta: int) -> None:
            progress.advance(overall, delta)

        def _do_download(args: tuple[str, str, Path, Optional[str], int]) -> None:
            iid, iname, idest, fname, _sz = args
            _download_one(client, iid, iname, idest, progress, name_override=fname,
                          overall_advance=_advance_overall, overwrite=overwrite)

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {executor.submit(_do_download, task): task for task in all_tasks}
            for fut in as_completed(futures):
                try:
                    fut.result()
                    ok += 1
                except Exception as exc:
                    failed += 1
                    _iid, iname, _idest, _fname, _sz = futures[fut]
                    errors.append(f"{iname}: {exc}")

    if errors:
        for msg in errors:
            err_console.print(f"  [red]✗[/red] {_esc(msg)}")
    if failed:
        console.print(f"[yellow]⚠[/yellow]  {_transfer_summary(ok, failed=failed, action='downloaded')}")
        raise SystemExit(1)
    console.print(f"[green]✓[/green] {_transfer_summary(ok, action='downloaded')}")


# ── Move / Copy ───────────────────────────────────────────────────────────────
def _resolve_item(client: "DegooClient", path_or_id: str) -> tuple[str, dict]:
    """Resolve a path (e.g. /Web/foo) or bare numeric ID to (id_str, item_dict).

    When the argument is already a numeric ID we call get_item() so the caller
    always gets the full metadata dict.

    Bare names without a leading ``/`` (e.g. ``photo.jpg`` or ``2024``) are
    treated as relative to the stored CWD, just like a real shell would.
    Raises SystemExit(1) if not found.
    """
    if path_or_id.lstrip("-").isdigit():
        item = client.get_item(path_or_id)
        if item is None:
            _err(f"Not found: {path_or_id}")
            raise SystemExit(1)
        return path_or_id, item
    # Relative paths → prefix with CWD so resolve_path always gets an absolute path
    item = client.resolve_path(_to_absolute(path_or_id))
    if item is None:
        _err(f"Not found: {path_or_id}")
        raise SystemExit(1)
    return str(item["ID"]), item


def _resolve_id(client: "DegooClient", path_or_id: str) -> str:
    """Resolve a path or numeric ID to a numeric ID string. SystemExit(1) on miss."""
    return _resolve_item(client, path_or_id)[0]


@main.command()
@click.argument("src")
@click.argument("dest")
def mv(src: str, dest: str) -> None:
    """Move or rename SRC to DEST.

    \b
    DEST ends with '/'      → move SRC inside that folder (must already exist).
    DEST does not exist     → rename/move SRC to DEST (parent must exist).
    DEST already exists     → error; append '/' to explicitly move inside it.

    \b
    Examples:
      cligoo mv /Web/OldProject /Web/Archive/
          → /Web/Archive must exist; result: /Web/Archive/OldProject/

      cligoo mv /Web/OldProject /Web/NewProject
          → /Web/NewProject must NOT exist; result: rename to NewProject

      cligoo mv /Web/OldProject /Web/Archive/NewProject
          → /Web/Archive must exist, NewProject must not; result: move + rename
    """
    client = _client()
    dest_is_id = dest.lstrip("-").rstrip("/").isdigit()
    force_inside = dest_is_id or dest.endswith("/")
    dest_stripped = dest.rstrip("/")

    try:
        src_id, src_item = _resolve_item(client, src)

        # Resolve dest: numeric IDs use get_item, paths use resolve_path
        if dest_is_id:
            dest_item = client.get_item(dest_stripped)
        else:
            dest_item = client.resolve_path(_to_absolute(dest_stripped))

        if dest_item is not None:
            # Destination already exists
            if force_inside:
                if not client.is_folder(dest_item):
                    _err(f"{dest_stripped} is not a folder")
                    raise SystemExit(1)
                client.move([src_id], str(dest_item["ID"]))
                console.print(f"[green]✓[/green] Moved {src} → {dest_stripped}/{src_item['Name']}")
            else:
                _err(
                    f"{dest_stripped} already exists — "
                    f"append '/' to move inside it, or choose a different name"
                )
                raise SystemExit(1)
            return

        # Destination does not exist
        if force_inside:
            _err(f"Not found: {dest_stripped}")
            raise SystemExit(1)

        abs_dest = _to_absolute(dest_stripped)
        dest_parts = [p for p in abs_dest.strip("/").split("/") if p]
        if not dest_parts:
            _err(f"Invalid destination: {dest}")
            raise SystemExit(1)

        new_name = dest_parts[-1]
        dest_parent_path = "/" + "/".join(dest_parts[:-1]) if len(dest_parts) > 1 else "/"

        dest_parent = client.resolve_path(dest_parent_path)
        if dest_parent is None:
            _err(f"Parent not found: {dest_parent_path}")
            raise SystemExit(1)

        dest_parent_id = str(dest_parent["ID"])
        src_parent_id = str(src_item.get("ParentID", ""))

        if dest_parent_id != src_parent_id:
            client.move([src_id], dest_parent_id)
        client.rename(src_id, new_name)
        console.print(f"[green]✓[/green] Moved {src} → {dest_stripped}")

    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)


@main.command()
@click.argument("src")
@click.argument("dest_folder")
def cp(src: str, dest_folder: str):
    """Copy SRC into DEST_FOLDER (DEST_FOLDER must already exist).

    Same placement semantics as mv — SRC is placed inside DEST_FOLDER.
    """
    client = _client()
    try:
        src_id = _resolve_id(client, src)
        dest_id = _resolve_id(client, dest_folder)
        client.move([src_id], dest_id, copy=True)
        console.print(f"[green]✓[/green] Copied {src} → {dest_folder}")
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)


# ── Rename ────────────────────────────────────────────────────────────────────
@main.command()
@click.argument("src")
@click.argument("new_name")
def rename(src: str, new_name: str):
    """Rename SRC to NEW_NAME (path or numeric ID)."""
    client = _client()
    try:
        src_id = _resolve_id(client, src)
        client.rename(src_id, new_name)
        console.print(f"[green]✓[/green] Renamed {src} → {new_name}")
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)


# ── Delete ────────────────────────────────────────────────────────────────────
@main.command()
@click.argument("items", nargs=-1, required=True)
@click.option("--permanent", is_flag=True, help="Permanently delete (skip recycle bin)")
@click.option("-r", "--recursive", is_flag=True, help="Allow deleting directories and their contents")
def rm(items: tuple[str, ...], permanent: bool, recursive: bool):
    """Delete items — moves to recycle bin by default.

    ITEMS can be paths (/Web/foo) or numeric IDs.

    Directories require -r.  The entire folder tree is deleted in one
    server-side call; there is no partial deletion.
    """
    client = _client()
    ids: list[str] = []
    try:
        for p in items:
            iid, meta = _resolve_item(client, p)
            cat = meta.get("Category", 0)
            if cat in FOLDER_CATEGORIES and not recursive:
                _err(f"{p}: is a directory — use -r to delete directories")
                raise SystemExit(1)
            ids.append(iid)
    except SystemExit:
        raise
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)
    try:
        client.delete(ids, permanent=permanent)
        action = "Permanently deleted" if permanent else "Moved to recycle bin"
        console.print(f"[green]✓[/green] {action}: {', '.join(items)}")
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)


# ── Trash ─────────────────────────────────────────────────────────────────────
@main.command()
@click.option("-n", "--limit", default=50, help="Max items")
def trash(limit: int):
    """List items in the recycle bin."""
    client = _client()
    try:
        items = client.list_trash(limit=limit)
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    if not items:
        console.print("[dim]  Recycle bin is empty.[/dim]")
        return

    table = Table(title="🗑️  Recycle Bin", box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="dim")
    table.add_column("Type", justify="center")
    table.add_column("Name")
    table.add_column("Size", justify="right")

    total_bytes = sum(int(it.get("Size") or 0) for it in items)
    for it in items:
        cat = it.get("Category", 0)
        table.add_row(
            str(it.get("ID", "")),
            _category_icon(cat),
            it.get("Name", "?"),
            _humanize_size(it.get("Size")),
        )
    console.print(table)
    total_human = _humanize_size(str(total_bytes)) if total_bytes else "unknown size"
    console.print(
        f"  [dim]{len(items)} item{'s' if len(items) != 1 else ''} · {total_human} total[/dim]"
    )


@main.command("empty-trash")
@click.option("--yes", is_flag=True, default=False, help="Skip interactive confirmation (for scripts)")
def empty_trash(yes: bool) -> None:
    """Permanently delete every item in the recycle bin.

    \b
    You will be asked to confirm TWICE before anything is deleted.
    Pass --yes only in non-interactive scripts where you are certain.
    This action is irreversible — items cannot be recovered afterwards.
    """
    client = _client()
    try:
        items = client.list_trash(limit=10_000)
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    if not items:
        console.print("[dim]  Recycle bin is already empty.[/dim]")
        return

    count = len(items)
    total_bytes = sum(int(it.get("Size") or 0) for it in items)
    total_human = _humanize_size(str(total_bytes)) if total_bytes else "unknown size"

    # ── First confirmation ─────────────────────────────────────────────────
    console.print()
    console.print(
        f"[bold yellow]⚠️  WARNING[/bold yellow]  You are about to "
        f"[bold red]permanently delete[/bold red] "
        f"[bold]{count} item{'s' if count != 1 else ''}[/bold] "
        f"({total_human}) from the recycle bin."
    )
    console.print("[red]  This action is irreversible. Items cannot be recovered.[/red]")
    console.print()

    if not yes:
        first = console.input("  Type [bold yellow]YES[/bold yellow] to continue: ", markup=True)
        if first.strip() != "YES":
            console.print("[dim]  Aborted.[/dim]")
            return

        # ── Second confirmation ────────────────────────────────────────────
        console.print()
        console.print(
            f"[bold red]⛔  FINAL WARNING[/bold red]  "
            f"[bold]{count} item{'s' if count != 1 else ''}[/bold] will be gone forever."
        )
        second = console.input(
            "  Type [bold red]EMPTY TRASH[/bold red] to permanently delete everything: ",
            markup=True,
        )
        if second.strip() != "EMPTY TRASH":
            console.print("[dim]  Aborted.[/dim]")
            return

    # ── Delete all items ───────────────────────────────────────────────────
    ids = [str(it["ID"]) for it in items]
    try:
        client.delete(ids, permanent=True)
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    console.print()
    console.print(
        f"[green]✓[/green] Permanently deleted "
        f"[bold]{count} item{'s' if count != 1 else ''}[/bold] "
        f"({total_human}). Recycle bin is now empty."
    )


# ── Sharing ───────────────────────────────────────────────────────────────────
@main.command()
@click.option("-n", "--limit", default=50, help="Max items")
@click.option("-l", "--long", is_flag=True, help="Show who each item is shared with")
def shared(limit: int, long: bool):
    """List shared items.

    Without -l shows a compact table.  With -l an extra 'Shared with' column
    is fetched from the permissions API (one extra request per item).
    """
    client = _client()
    try:
        items = client.list_shared(limit=limit)
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    if not items:
        console.print("[dim]  No shared items.[/dim]")
        return

    table = Table(title="Shared Items", box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="dim")
    table.add_column("Type", justify="center")
    table.add_column("Name")
    table.add_column("Size", justify="right")
    if long:
        table.add_column("Shared with")

    for it in items:
        cat = it.get("Category", 0)
        row = [
            str(it.get("ID", "")),
            _category_icon(cat),
            it.get("Name", "?"),
            _humanize_size(it.get("Size")) if cat not in FOLDER_CATEGORIES else "—",
        ]
        if long:
            try:
                perms = client.get_permissions(str(it["ID"]))
                users = perms.get("Users") or []
                shared_with = ", ".join(u.get("Email") or u.get("Name", "?") for u in users) or "—"
            except DegooAPIError:
                shared_with = "?"
            row.append(shared_with)
        table.add_row(*row)
    console.print(table)


@main.command()
@click.argument("item_id")
@click.argument("usernames", nargs=-1)
def share(item_id: str, usernames: tuple[str, ...]):
    """Share an item (optionally with specific users)."""
    client = _client()
    try:
        client.share(item_id, list(usernames) if usernames else None)
        console.print(f"[green]✓[/green] Shared item {item_id}")
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)


@main.command()
@click.argument("item_id")
def unshare(item_id: str):
    """Remove sharing from an item."""
    client = _client()
    try:
        client.unshare(item_id)
        console.print(f"[green]✓[/green] Unshared item {item_id}")
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)


# ── Feed ──────────────────────────────────────────────────────────────────────
@main.command()
@click.option("-n", "--limit", default=30, help="Max items")
def feed(limit: int):
    """Show the moments/feed timeline."""
    client = _client()
    try:
        items = client.get_feed(limit=limit)
    except DegooAPIError as e:
        _err(e)
        raise SystemExit(1)

    if not items:
        console.print("[dim]  Feed is empty.[/dim]")
        return

    table = Table(title="📸 Feed / Moments", box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="dim")
    table.add_column("Type", justify="center")
    table.add_column("Name")
    table.add_column("Date")

    for it in items:
        cat = it.get("Category", 0)
        table.add_row(
            str(it.get("ID", "")),
            _category_icon(cat),
            it.get("Name", "?"),
            _format_time(it.get("CreationTime")),
        )
    console.print(table)


# ── Interactive shell ─────────────────────────────────────────────────────────
@main.command(name="shell")
def shell_cmd() -> None:
    """Start an interactive Degoo filesystem shell.

    \b
    Inside the shell:
      ls / ll / cd / pwd / tree   — navigate the Degoo filesystem
      upload <local_path>         — upload to the current Degoo directory
      download <degoo_path> [dest]— download to a local path (default: ./)
      lcd / lpwd / lls            — manage your local working directory
      <anything else>             — forwarded to your system shell
      exit / quit / Ctrl-D        — leave the shell

    \b
    The shell starts in the Degoo directory saved by 'cligoo cd'.
    Your local working directory defaults to wherever you launched the shell.

    \b
    Example session:
      cligoo:/Web ❯ cd Photos
      cligoo:/Web/Photos ❯ ls
      cligoo:/Web/Photos ❯ upload ~/Desktop/holiday.jpg
      cligoo:/Web/Photos ❯ lcd ~/Downloads
      cligoo:/Web/Photos ❯ download holiday.jpg ./
    """
    from .shell import DegooShell

    client = _client()
    DegooShell(client, start_path="/").cmdloop()


if __name__ == "__main__":
    main()
