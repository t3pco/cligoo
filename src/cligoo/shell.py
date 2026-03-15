"""Interactive Degoo filesystem shell (``degoo shell``)."""

from __future__ import annotations

import cmd
import glob
import os
import shlex
import subprocess
import time
from pathlib import Path

from .constants import FOLDER_CATEGORIES

# Completion cache TTL — entries older than this are re-fetched from the API.
# 5 seconds is long enough to make rapid Tab presses instant and short enough
# that the cache never feels stale during normal use.
_CACHE_TTL: float = 5.0


class DegooShell(cmd.Cmd):
    """REPL that blends Degoo filesystem navigation with host shell access.

    Degoo commands (``ls``, ``ll``, ``cd``, ``pwd``, ``tree``,
    ``upload``, ``download``) all operate on the Degoo cloud filesystem.

    Local helpers (``lcd``, ``lpwd``, ``lls``) let you change and inspect
    your host working directory — useful for composing upload/download paths.

    Any command not listed above is forwarded verbatim to your system shell
    (``bash -c …``), run inside the current local working directory.  You can
    also prefix any line with ``!`` to force a host-shell pass-through.
    """

    intro = (
        "\n"
        "  Degoo Shell\n"
        "  ──────────────────────────────────────────────────────────\n"
        "  Degoo:  ls  ll  cd  pwd  tree  upload  download\n"
        "          info  mv  cp  rm  rename  mkdir  search\n"
        "          trash  empty-trash\n"
        "  Local:  lcd  lpwd  lls\n"
        "  Other:  any command is forwarded to your system shell\n"
        "  Quit:   exit  /  quit  /  Ctrl-D\n"
    )

    def __init__(self, client, start_path: str = "/") -> None:
        super().__init__()
        self.client = client
        self.degoo_cwd: str = start_path
        self.host_cwd: str = str(Path.cwd())
        # ── Completion cache ──────────────────────────────────────────────
        # Maps parent_id → (timestamp, children_list).  Avoids a live API
        # call on every Tab keypress when the user is still typing in the
        # same directory.  Entries expire after _CACHE_TTL seconds.
        self._dir_cache: dict[str, tuple[float, list[dict]]] = {}
        # Maps absolute_path → (timestamp, item_or_None).  Avoids one
        # resolve_path round-trip per path component on each Tab press.
        self._path_cache: dict[str, tuple[float, dict | None]] = {}
        self._update_prompt()

    # ── Loop ──────────────────────────────────────────────────────────────────

    def cmdloop(self, intro: object = None) -> None:  # type: ignore[override]
        """Run the shell loop, keeping it alive on Ctrl-C (KeyboardInterrupt).

        Python's ``cmd.Cmd.cmdloop`` does not catch ``KeyboardInterrupt``
        during command execution, so a Ctrl-C pressed while a subprocess is
        running (e.g. a long upload) would propagate out and exit the shell.
        Overriding here catches it per-command and prints a short notice
        instead.
        """
        self.preloop()
        if intro is not None:
            self.intro = intro
        if self.intro:
            self.stdout.write(str(self.intro) + "\n")
        stop = None
        while not stop:
            try:
                if self.cmdqueue:
                    line = self.cmdqueue.pop(0)
                else:
                    try:
                        line = input(self.prompt)
                    except EOFError:
                        line = "EOF"
                line = self.precmd(line)
                stop = self.onecmd(line)
                stop = self.postcmd(stop, line)
            except KeyboardInterrupt:
                print()  # newline after ^C
        self.postloop()

    # ── Readline / libedit fix ────────────────────────────────────────────────

    def preloop(self) -> None:
        """Fix tab-completion on macOS where readline is backed by libedit.

        ``cmd.Cmd.cmdloop()`` always calls
        ``readline.parse_and_bind("tab: complete")`` which is GNU readline
        syntax.  macOS ships Python with libedit instead, which silently
        ignores that binding and inserts a literal tab character.  Calling the
        correct libedit binding here (after cmdloop has done its setup)
        restores normal tab-completion behaviour.
        """
        try:
            import readline

            doc = getattr(readline, "__doc__", "") or ""
            if "libedit" in doc:
                readline.parse_and_bind("bind ^I rl_complete")
        except ImportError:
            pass

    # ── Prompt ────────────────────────────────────────────────────────────────

    def _update_prompt(self) -> None:
        host = self.host_cwd.replace(str(Path.home()), "~")
        self.prompt = f"\n[{host}] cligoo:{self.degoo_cwd}\n❯ "

    # ── Path helpers ──────────────────────────────────────────────────────────

    def _resolve_degoo(self, path: str) -> str:
        """Resolve a Degoo path (absolute or relative to ``degoo_cwd``)."""
        path = (path or "").strip()
        if not path or path == "~":
            return "/"
        if path.startswith("/"):
            parts = [p for p in path.split("/") if p]
            return ("/" + "/".join(parts)) if parts else "/"
        # Relative — walk component by component
        parts = [p for p in self.degoo_cwd.split("/") if p]
        for component in path.split("/"):
            if component == "..":
                if parts:
                    parts.pop()
            elif component and component != ".":
                parts.append(component)
        return ("/" + "/".join(parts)) if parts else "/"

    # ── Completion cache helpers ───────────────────────────────────────────────

    def _list_dir_cached(self, parent_id: str, limit: int = 500) -> list[dict]:
        """``list_dir`` with a short TTL cache.

        The first call for a given *parent_id* fetches from the API and stores
        the result.  Subsequent calls within ``_CACHE_TTL`` seconds return the
        stored list without touching the network.  This makes rapid Tab presses
        on the same directory instant.
        """
        now = time.monotonic()
        entry = self._dir_cache.get(parent_id)
        if entry and now - entry[0] < _CACHE_TTL:
            return entry[1]
        children = self.client.list_dir(parent_id, limit=limit)
        self._dir_cache[parent_id] = (now, children)
        return children

    def _resolve_path_cached(self, path: str) -> dict | None:
        """``resolve_path`` with a short TTL cache.

        Avoids one API round-trip per path component for every Tab press when
        the settled portion of the path hasn't changed.
        """
        now = time.monotonic()
        entry = self._path_cache.get(path)
        if entry and now - entry[0] < _CACHE_TTL:
            return entry[1]
        item = self.client.resolve_path(path)
        self._path_cache[path] = (now, item)
        return item

    def _invalidate_cache(self) -> None:
        """Clear all completion caches.

        Called after any mutating command (mkdir, mv, cp, rm, rename, upload)
        so that the next Tab press reflects the updated directory listing.
        """
        self._dir_cache.clear()
        self._path_cache.clear()

    def _complete_degoo_path(self, text: str) -> list[str]:
        """Tab-complete a Degoo path against the live filesystem.

        Works like fish/zsh: lists children of the deepest resolvable component
        and filters by whatever the user has typed so far.
        """
        # Split what the user typed into "settled prefix" and "incomplete suffix"
        if "/" in text:
            slash_pos = text.rfind("/")
            parent_text = text[: slash_pos + 1]  # e.g. "/Web/Phot" → "/Web/"
            prefix = text[slash_pos + 1 :]  # e.g. "Phot"
        else:
            parent_text = ""
            prefix = text

        # Resolve the settled part to a directory ID
        if parent_text:
            parent_path = self._resolve_degoo(parent_text.rstrip("/") or "/")
        else:
            parent_path = self.degoo_cwd

        try:
            if parent_path == "/":
                parent_id = "0"
            else:
                item = self._resolve_path_cached(parent_path)
                if item is None or item.get("URL"):
                    return []
                parent_id = str(item["ID"])
            children = self._list_dir_cached(parent_id)
        except Exception:  # noqa: BLE001
            # On any error during resolution/listing, return empty to allow
            # graceful degradation of tab completion rather than breaking the REPL
            return []

        completions: list[str] = []
        for child in children:
            name: str = child.get("Name", "")
            if not name.lower().startswith(prefix.lower()):
                continue
            # Return the full text the user would have typed
            is_dir = child.get("Category", 0) in FOLDER_CATEGORIES
            suffix = "/" if is_dir else ""
            completions.append(parent_text + name + suffix)
        return completions

    def _expand_degoo_globs(self, raw_paths: list[str]) -> list[str]:
        """Expand a list of Degoo paths, resolving shell-style globs (``*``, ``?``, ``[…]``).

        Each entry in *raw_paths* is first resolved relative to ``degoo_cwd``
        (same as ``_resolve_degoo``).  Paths that contain no glob metacharacters
        are returned as-is (after resolving relative components).  Patterns
        containing ``*``, ``?``, or ``[`` are matched against the children of
        the parent directory and all matches are returned; if a pattern produces
        no matches it is kept verbatim so that ``degoo rm`` can report the error.
        """
        import fnmatch

        results: list[str] = []
        for raw in raw_paths:
            if not any(c in raw for c in ("*", "?", "[")):
                # Plain path — just resolve it
                results.append(
                    raw if raw.lstrip("-").isdigit() else self._resolve_degoo(raw)
                )
                continue
            # Glob: split into parent + pattern
            if "/" in raw:
                slash = raw.rfind("/")
                parent_raw = raw[:slash] or "/"
                pattern = raw[slash + 1 :]
            else:
                parent_raw = self.degoo_cwd
                pattern = raw
            parent_path = self._resolve_degoo(parent_raw)
            try:
                if parent_path == "/":
                    parent_id = "0"
                else:
                    item = self._resolve_path_cached(parent_path)
                    if item is None:
                        results.append(self._resolve_degoo(raw))
                        continue
                    parent_id = str(item["ID"])
                children = self._list_dir_cached(parent_id)
            except Exception:  # noqa: BLE001
                # On API error during glob expansion, return the pattern verbatim
                # so the CLI command can report it as not found (rather than hiding the error)
                results.append(self._resolve_degoo(raw))
                continue
            matches = [
                parent_path.rstrip("/") + "/" + c["Name"]
                for c in children
                if fnmatch.fnmatch(c.get("Name", ""), pattern)
            ]
            if matches:
                results.extend(matches)
            else:
                # No matches — keep verbatim so the CLI can report it
                results.append(self._resolve_degoo(raw))
        return results

    def _maybe_show_help(self, degoo_cmd: str, args: str) -> bool:
        """If *args* contains ``--help`` or ``-h``, print ``degoo <cmd> --help`` and return True."""
        tokens = args.split()
        if "--help" in tokens or "-h" in tokens:
            subprocess.run(["cligoo", degoo_cmd, "--help"])
            return True
        return False

    def _safe_split(self, args: str) -> "list[str] | None":
        """shlex.split *args*, printing an error and returning None on syntax error."""
        try:
            return shlex.split(args) if args.strip() else []
        except ValueError as exc:
            print(f"  ✗ {exc}")
            return None

    def _run_degoo_cmd(self, cmd_parts: list[str], *, invalidate_cache: bool = False) -> bool:
        """Run a cligoo subprocess command.

        Prints a ✗ error on non-zero exit.  When *invalidate_cache* is True and
        the command succeeds, the listing cache is cleared.
        Returns True on success, False on failure.
        """
        result = subprocess.run(cmd_parts)
        if result.returncode:
            # Extract the subcommand name from cmd_parts for the error message
            # cmd_parts is e.g. ["cligoo", "mkdir", ...]
            subcmd = cmd_parts[1] if len(cmd_parts) > 1 else "command"
            print(f"  ✗ cligoo {subcmd} exited with code {result.returncode}")
            return False
        if invalidate_cache:
            self._invalidate_cache()
        return True

    def _resolve_host(self, path: str) -> str:
        """Resolve a host path (absolute or relative to ``host_cwd``)."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path(self.host_cwd) / p
        return str(p.resolve())

    def _complete_local(self, text: str) -> list[str]:
        """Return tab-completion candidates for a local filesystem path."""
        safe = glob.escape(text)
        if not text:
            pattern = os.path.join(self.host_cwd, "*")
        elif os.path.isabs(text):
            pattern = safe + "*"
        else:
            pattern = os.path.join(self.host_cwd, safe + "*")
        completions: list[str] = []
        for match in glob.glob(pattern):
            rel = match if os.path.isabs(text) else os.path.relpath(match, self.host_cwd)
            if os.path.isdir(match):
                rel += "/"
            completions.append(rel)
        return completions

    # ── Degoo navigation ──────────────────────────────────────────────────────

    def do_ls(self, args: str) -> None:
        """List a Degoo directory.

        \b
        Usage: ls [OPTIONS] [PATH [PATH ...]]
        Options (-l, -S, -t, -r, -R, -d N) are forwarded to ``degoo ls``.
        PATH defaults to the current Degoo directory.
        Glob patterns (*  ?  [...]) are expanded against the live Degoo listing:
          ls *          list every item in the current directory
          ls *.jpg      list only files whose names end in .jpg
          ls 2024-*/    list the contents of every folder starting with 2024-
        """
        tokens = shlex.split(args) if args.strip() else []
        opts = [t for t in tokens if t.startswith("-")]
        path_tokens = [t for t in tokens if not t.startswith("-")]

        if not path_tokens:
            # Plain `ls` — list CWD
            paths = [self.degoo_cwd]
        elif any(any(c in t for c in ("*", "?", "[")) for t in path_tokens):
            # One or more glob patterns — expand them
            paths = self._expand_degoo_globs(path_tokens)
            if not paths:
                print("  ✗ No matching items")
                return
        else:
            # Plain path(s) — resolve each relative to CWD
            paths = [
                self._resolve_degoo(t) if not t.lstrip("-").isdigit() else t
                for t in path_tokens
            ]

        for path in paths:
            self._run_degoo_cmd(["cligoo", "ls"] + opts + [path])

    def do_ll(self, args: str) -> None:
        """Long Degoo listing — alias for ``ls -l``.

        \b
        Usage: ll [PATH]
        """
        self.do_ls(("-l " + args).strip())

    def do_cd(self, args: str) -> None:
        """Change Degoo working directory.

        \b
        Usage: cd [PATH]
        Empty or ``~`` returns to the Degoo root (/).
        Supports absolute paths and relative paths including ``..``.
        """
        resolved = self._resolve_degoo(args.strip())
        if resolved == "/":
            self.degoo_cwd = "/"
            self._update_prompt()
            return
        try:
            item = self.client.resolve_path(resolved)
            if item is None:
                print(f"  ✗ Not found: {resolved}")
                return
            if item.get("URL"):
                print(f"  ✗ Not a directory: {resolved}")
                return
            self.degoo_cwd = resolved
            self._update_prompt()
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {exc}")

    def do_pwd(self, args: str) -> None:
        """Print the current Degoo working directory."""
        print(self.degoo_cwd)

    def do_tree(self, args: str) -> None:
        """Show a Degoo directory tree.

        \b
        Usage: tree [OPTIONS] [PATH [PATH ...]]
        Options are forwarded to ``degoo tree`` (e.g. -d N).
        PATH defaults to the current Degoo directory.
        Glob patterns are expanded against the live Degoo listing.
        """
        tokens = shlex.split(args) if args.strip() else []
        opts = [t for t in tokens if t.startswith("-")]
        path_tokens = [t for t in tokens if not t.startswith("-")]

        if not path_tokens:
            paths = [self.degoo_cwd]
        elif any(any(c in t for c in ("*", "?", "[")) for t in path_tokens):
            paths = self._expand_degoo_globs(path_tokens)
            if not paths:
                print("  ✗ No matching items")
                return
        else:
            paths = [
                self._resolve_degoo(t) if not t.lstrip("-").isdigit() else t
                for t in path_tokens
            ]

        for path in paths:
            self._run_degoo_cmd(["cligoo", "tree"] + opts + [path])

    # ── File-management commands ───────────────────────────────────────────────

    def do_info(self, args: str) -> None:
        """Show detailed metadata for a Degoo item.

        \b
        Usage: info <PATH|ID>
        PATH may be absolute or relative to the current Degoo directory.
        """
        if self._maybe_show_help("info", args):
            return
        arg = args.strip()
        if not arg:
            print("  Usage: info <path|id>")
            return
        resolved = self._resolve_degoo(arg) if not arg.lstrip("-").isdigit() else arg
        self._run_degoo_cmd(["cligoo", "info", resolved])

    def complete_info(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def do_search(self, args: str) -> None:
        """Search Degoo by name.

        \b
        Usage: search <TERM> [-n N]
        """
        if not args.strip():
            print("  Usage: search <term>")
            return
        self._run_degoo_cmd(["cligoo", "search"] + shlex.split(args))

    def do_mkdir(self, args: str) -> None:
        """Create a Degoo folder.

        \b
        Usage: mkdir <PATH>
        PATH may be absolute or relative to the current Degoo directory.
        """
        if self._maybe_show_help("mkdir", args):
            return
        arg = args.strip()
        if not arg:
            print("  Usage: mkdir <path>")
            return
        resolved = self._resolve_degoo(arg)
        self._run_degoo_cmd(["cligoo", "mkdir", resolved], invalidate_cache=True)

    def complete_mkdir(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def do_mv(self, args: str) -> None:
        """Move or rename a Degoo item.

        \b
        Usage: mv <SRC> <DEST>
        SRC / DEST may be absolute paths, paths relative to the current
        Degoo directory, or numeric IDs.  Trailing '/' on DEST means
        "move inside that folder".
        """
        if self._maybe_show_help("mv", args):
            return
        tokens = self._safe_split(args)
        if tokens is None:
            return
        if len(tokens) != 2:
            print("  Usage: mv <src> <dest>")
            return
        src_raw, dest_raw = tokens
        # Resolve relative paths; preserve trailing slash (move-inside signal)
        trailing = "/" if dest_raw.endswith("/") else ""
        src = self._resolve_degoo(src_raw) if not src_raw.lstrip("-").isdigit() else src_raw
        dest_base = dest_raw.rstrip("/")
        dest = (
            self._resolve_degoo(dest_base) if not dest_base.lstrip("-").isdigit() else dest_base
        ) + trailing
        self._run_degoo_cmd(["cligoo", "mv", src, dest], invalidate_cache=True)

    def complete_mv(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def do_cp(self, args: str) -> None:
        """Copy a Degoo item into a folder.

        \b
        Usage: cp <SRC> <DEST_FOLDER>
        SRC / DEST_FOLDER may be absolute, relative, or numeric IDs.
        """
        if self._maybe_show_help("cp", args):
            return
        tokens = self._safe_split(args)
        if tokens is None:
            return
        if len(tokens) != 2:
            print("  Usage: cp <src> <dest_folder>")
            return
        src_raw, dest_raw = tokens
        src = self._resolve_degoo(src_raw) if not src_raw.lstrip("-").isdigit() else src_raw
        dest = self._resolve_degoo(dest_raw) if not dest_raw.lstrip("-").isdigit() else dest_raw
        self._run_degoo_cmd(["cligoo", "cp", src, dest], invalidate_cache=True)

    def complete_cp(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def do_rename(self, args: str) -> None:
        """Rename a Degoo item.

        \b
        Usage: rename <PATH|ID> <NEW_NAME>
        """
        if self._maybe_show_help("rename", args):
            return
        tokens = self._safe_split(args)
        if tokens is None:
            return
        if len(tokens) != 2:
            print("  Usage: rename <path|id> <new_name>")
            return
        path_raw, new_name = tokens
        resolved = (
            self._resolve_degoo(path_raw) if not path_raw.lstrip("-").isdigit() else path_raw
        )
        self._run_degoo_cmd(["cligoo", "rename", resolved, new_name], invalidate_cache=True)

    def complete_rename(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def do_rm(self, args: str) -> None:
        """Move a Degoo item to the recycle bin (or permanently delete it).

        \b
        Usage: rm [-r] [--permanent] <PATH|ID> [PATH|ID ...]
        Supports glob patterns like ``rm 2024-*``.
        """
        if self._maybe_show_help("rm", args):
            return
        tokens = self._safe_split(args)
        if tokens is None:
            return
        if not tokens:
            print("  Usage: rm [-r] [--permanent] <path|id> [...]")
            return
        # Separate flags from paths
        flags = [t for t in tokens if t.startswith("-")]
        raw_paths = [t for t in tokens if not t.startswith("-")]
        resolved_paths = self._expand_degoo_globs(raw_paths)
        if not resolved_paths:
            print("  ✗ No matching items found")
            return
        self._run_degoo_cmd(["cligoo", "rm"] + flags + resolved_paths, invalidate_cache=True)

    def complete_rm(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    # ── Recycle bin ───────────────────────────────────────────────────────────

    def do_trash(self, args: str) -> None:
        """List items in the recycle bin.

        \b
        Usage: trash [-n N]
        -n N   Show at most N items (default 50).
        """
        self._run_degoo_cmd(["cligoo", "trash"] + shlex.split(args) if args.strip() else ["cligoo", "trash"])

    def do_empty_trash(self, args: str) -> None:
        """Permanently delete every item in the recycle bin.

        \b
        Usage: empty-trash
        You will be asked to confirm TWICE before anything is deleted.
        """
        self._run_degoo_cmd(["cligoo", "empty-trash"])

    # ── Transfer ──────────────────────────────────────────────────────────────

    # Options that consume the next token as their value.
    _UPLOAD_VALUE_OPTS: frozenset = frozenset({"--dest", "-t", "--name", "--workers", "--exclude"})
    _DOWNLOAD_VALUE_OPTS: frozenset = frozenset({"--dest", "-t", "--name", "--workers"})

    def do_upload(self, args: str) -> None:
        """Upload a local file or directory to the current Degoo directory.

        \b
        Usage: upload [-r] [--workers N] [--exclude PAT] <local_path> [...]
        -r / --recursive  upload a directory and all its contents
        --workers N       parallel worker count
        --exclude PAT     exclude files matching pattern (repeatable)
        <local_path> may be absolute or relative to the local working directory.
        """
        if self._maybe_show_help("upload", args):
            return
        tokens = self._safe_split(args)
        if tokens is None:
            return
        if not tokens:
            print("  Usage: upload [-r] [--workers N] [--exclude PAT] <local_path> ...")
            return

        cmd: list[str] = ["cligoo", "upload"]
        has_dest = False
        found_paths = False
        skip_next = False

        for tok in tokens:
            if skip_next:
                cmd.append(tok)
                skip_next = False
                continue
            if tok.startswith("-"):
                opt_name = tok.split("=")[0]
                if opt_name in {"--dest", "-t"}:
                    has_dest = True
                cmd.append(tok)
                # If not --opt=val form, the next token is the value
                if "=" not in tok and opt_name in self._UPLOAD_VALUE_OPTS:
                    skip_next = True
            else:
                # Positional: local file/directory path
                resolved = self._resolve_host(tok)
                if not Path(resolved).exists():
                    print(f"  ✗ Local path not found: {resolved}")
                    return
                cmd.append(resolved)
                found_paths = True

        if not found_paths:
            print("  Usage: upload [-r] [--workers N] [--exclude PAT] <local_path> ...")
            return

        if not has_dest:
            cmd.extend(["--dest", self.degoo_cwd])

        self._run_degoo_cmd(cmd, invalidate_cache=True)

    def complete_ls(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def complete_ll(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def complete_tree(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def complete_upload(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_local(text)

    def do_download(self, args: str) -> None:
        """Download a Degoo file or folder to a local path.

        \b
        Usage: download [-r] [--workers N] [--skip-existing] <degoo_path> [local_dest]

        -r / --recursive   download a folder recursively
        --workers N        parallel worker count
        --skip-existing    skip files already present locally
        <degoo_path> may be absolute or relative to the current Degoo directory.
        <local_dest> defaults to the current local working directory.
        """
        if self._maybe_show_help("download", args):
            return
        tokens = self._safe_split(args)
        if tokens is None:
            return
        if not tokens:
            print("  Usage: download [-r] [--workers N] <degoo_path> [local_dest]")
            return

        opts: list[str] = []
        positional: list[str] = []
        has_dest = False
        skip_next = False

        for tok in tokens:
            if skip_next:
                opts.append(tok)
                skip_next = False
                continue
            if tok.startswith("-"):
                opt_name = tok.split("=")[0]
                if opt_name in {"--dest", "-t"}:
                    has_dest = True
                opts.append(tok)
                if "=" not in tok and opt_name in self._DOWNLOAD_VALUE_OPTS:
                    skip_next = True
            else:
                positional.append(tok)

        if not positional:
            print("  Usage: download [-r] [--workers N] <degoo_path> [local_dest]")
            return

        degoo_src = self._resolve_degoo(positional[0])
        cmd = ["cligoo", "download"] + opts + [degoo_src]

        if has_dest:
            # User supplied --dest explicitly; honour any extra positional as-is
            if len(positional) > 1:
                cmd.append(self._resolve_host(positional[1]))
        else:
            local_dest = self._resolve_host(positional[1]) if len(positional) > 1 else self.host_cwd
            cmd.extend(["--dest", local_dest])

        self._run_degoo_cmd(cmd)

    def complete_cd(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_degoo_path(text)

    def complete_download(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        # First token → Degoo path; second token → local destination path.
        try:
            preceding = shlex.split(line[:begidx])
        except ValueError:
            preceding = []
        if len(preceding) >= 2:
            return self._complete_local(text)
        return self._complete_degoo_path(text)

    # ── Local filesystem helpers ──────────────────────────────────────────────

    def do_lcd(self, args: str) -> None:
        """Change the local (host) working directory.

        \b
        Usage: lcd [PATH]   (empty → home directory)
        """
        path = args.strip() or str(Path.home())
        try:
            resolved = self._resolve_host(path)
            os.chdir(resolved)
            self.host_cwd = str(Path.cwd())
            self._update_prompt()
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {exc}")

    def complete_lcd(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_local(text)

    def do_lpwd(self, args: str) -> None:
        """Print the current local (host) working directory."""
        print(self.host_cwd)

    def do_lls(self, args: str) -> None:
        """List local (host) directory contents.

        \b
        Usage: lls [ls-options] [PATH]
        All flags are passed directly to ls.  PATH defaults to the current
        local directory.  Examples: lls -1  lls -lh  lls -la ~/Downloads
        """
        try:
            tokens = shlex.split(args) if args.strip() else []
        except ValueError as exc:
            print(f"  ✗ {exc}")
            return

        # Separate flags (start with '-') from path tokens
        flags = [t for t in tokens if t.startswith("-")]
        paths = [t for t in tokens if not t.startswith("-")]

        if paths:
            resolved = self._resolve_host(paths[0])
        else:
            resolved = self.host_cwd

        cmd = ["ls"] + flags + ["--", resolved]
        result = subprocess.run(cmd)
        if result.returncode:
            print(f"  ✗ ls exited with code {result.returncode}")

    def complete_lls(self, text, line, begidx, endidx):  # noqa: ANN001,ANN201
        return self._complete_local(text)

    # ── Shell passthrough ─────────────────────────────────────────────────────

    def default(self, line: str) -> None:
        """Forward unrecognised commands to the system shell.

        Hyphenated Degoo commands (``empty-trash``) are routed here because
        Python identifiers cannot contain hyphens; we intercept them before
        falling through to the system shell.

        Prefix a line with ``!`` to force shell pass-through even for names
        that clash with built-in commands.
        """
        # Route hyphenated Degoo commands that cmd.Cmd can't dispatch natively
        if line.strip() == "empty-trash" or line.strip().startswith("empty-trash "):
            rest = line.strip()[len("empty-trash"):].strip()
            self.do_empty_trash(rest)
            return

        stripped = line[1:].strip() if line.startswith("!") else line
        if not stripped:
            return
        try:
            subprocess.run(stripped, shell=True, cwd=self.host_cwd)
        except KeyboardInterrupt:
            print()

    def emptyline(self) -> None:
        pass  # don't repeat the last command on bare Enter

    # ── Exit ──────────────────────────────────────────────────────────────────

    def do_exit(self, args: str) -> bool:  # noqa: ARG002
        """Exit the Degoo shell."""
        print()
        return True

    def do_quit(self, args: str) -> bool:
        """Exit the Degoo shell."""
        return self.do_exit(args)

    def do_EOF(self, args: str) -> bool:  # noqa: N802
        """Exit on Ctrl-D."""
        return self.do_exit(args)
