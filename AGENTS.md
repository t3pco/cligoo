# AGENTS.md — Guide for AI Agents and Automation

This file is the **single entry point** for any AI agent, bot, or automated
tool that needs to understand, use, or contribute to `cligoo`.

Read this file **first**, then follow the document chain it points to.

---

## Table of Contents

- [Repository orientation](#repository-orientation)
- [Document chain](#document-chain)
- [Project structure](#project-structure)
- [Architecture patterns every contributor must know](#architecture-patterns-every-contributor-must-know)
- [How to add a new CLI command](#how-to-add-a-new-cli-command)
- [How to add a new shell command](#how-to-add-a-new-shell-command)
- [Development workflow (mandatory checklist)](#development-workflow-mandatory-checklist)
- [Running commands](#running-commands)
- [Versioning and releasing](#versioning-and-releasing)

---

## Repository orientation

`cligoo` is a Python command-line interface for the
[Degoo](https://degoo.com) cloud-storage service. It wraps Degoo's public
GraphQL API (the same one the official web app uses — no private interface
is accessed).

The project has three user-facing entry points:

1. **`cligoo <command>`** — one-shot CLI (e.g. `cligoo ls /Web`)
2. **`cligoo shell`** — interactive REPL with tab completion and Degoo/local
   dual-context prompt
3. **Python API** — `DegooClient` in `src/cligoo/api.py` can be imported
   directly

All three share the same underlying `DegooClient`. The CLI commands live in
`cli.py`, the REPL in `shell.py`.

---

## Document chain

Read these in order when picking up the project from scratch:

| Order | File | What it explains |
| --- | --- | --- |
| 1 | `README.md` | Quick start, installation, feature overview, command table |
| 2 | `docs/CLI_USAGE.md` | Full command reference: every flag, every option, config schema, shell commands, tab-completion table |
| 3 | `docs/API_INTERNALS.md` | GraphQL schema, auth flow, known queries/mutations, item data model, upload/download internals, pitfalls |
| 4 | `docs/TESTING.md` | Test suite layout, how to run unit and integration tests, design principles, guide for adding new tests |
| 5 | `CHANGELOG.md` | Full history of every feature, fix, and breaking change (newest first) |
| 6 | `docs/PLAYWRIGHT_COMPARISON.md` | Browser-login trade-offs (only relevant when working on auth) |

---

## Project structure

```text
src/cligoo/
  __init__.py     Version string (single source of truth, also in pyproject.toml)
  api.py          DegooClient — all GraphQL calls; _ProgressFile for upload progress
  auth.py         Token fetch/store/refresh; browser OAuth via Playwright
  chrome.py       Cross-platform Chrome installation and profile detection
  cli.py          Click command group; all `cligoo <cmd>` commands
  config.py       Read/write ~/.config/cligoo/config.json; get_* helpers
  constants.py    API endpoints, category IDs, default headers, config paths
  queries.py      GraphQL query and mutation strings (no logic — strings only)
  shell.py        DegooShell (cmd.Cmd REPL); all `cligoo shell` built-in commands

tests/
  test_api_client.py   DegooClient header isolation, token passthrough
  test_auth.py         Token expiry helpers
  test_cli_unit.py     All CLI commands via Click CliRunner (offline, mocked)
  test_config.py       config.py read/write, get_* helpers
  test_constants.py    API key format
  test_queries.py      Query string smoke tests
  test_shell.py        DegooShell: _complete_local, do_* commands, glob expansion
  integration/         Live-API tests (require valid Degoo credentials)
```

---

## Architecture patterns every contributor must know

### 1 — Path-or-ID duality

Every command that operates on a Degoo item accepts **either** a path string
(`/Web/Photos/holiday.jpg`) **or** a bare numeric ID (`12345678`). The
canonical way to resolve either form is:

```python
item_id, item = _resolve_item(client, path_or_id)
```

`_resolve_item` (in `cli.py`) handles three cases:

- **Numeric ID** → `client.get_item(id)` → returns full metadata dict
- **Absolute path** (`/…`) → `client.resolve_path(path)` → walks the tree
- **Relative path / bare name** → prefixes with CWD via `_to_absolute()` →
  then `client.resolve_path(absolute_path)`

Never call `client.resolve_path()` directly from a Click command. Always go
through `_resolve_item` (or `_resolve_id` when only the ID string is needed).

### 2 — CWD resolution

Bare filenames and relative paths are made absolute by `_to_absolute()`:

```python
def _to_absolute(path: str) -> str:
    if path.startswith("/"):
        return path
    cwd = _load_cwd()
    if cwd == "/":
        return "/" + path
    return cwd.rstrip("/") + "/" + path
```

Any new code that resolves a user-supplied path must call `_to_absolute()`
before passing it to `client.resolve_path()`.

### 3 — DegooClient API surface

The key methods used by CLI commands:

| Method | Description |
| --- | --- |
| `client.list_dir(parent_id, limit)` | Returns list of child item dicts |
| `client.resolve_path(abs_path)` | Walks `/A/B/C` from root; returns item or `None` |
| `client.resolve_path_under(parent_id, name)` | Finds one child by name (used after `mkdir`) |
| `client.get_item(id)` | Full metadata for a numeric ID |
| `client.search(term, limit)` | Name search across the account |
| `client.mkdir(path)` | Create a folder; returns `True` |
| `client.upload(filepath, parent_id, …, progress_callback)` | Upload; uses `_ProgressFile` for incremental progress |
| `client.download(item_id, dest_dir, …, progress_callback)` | Download; streams with Content-Length progress |
| `client.move(ids, dest_folder_id, copy)` | Move or copy items |
| `client.rename(id, new_name)` | Rename an item |
| `client.delete(ids, permanent)` | Trash or permanently delete |
| `client.is_folder(item)` | Static method; returns `True` if `Category` is in `FOLDER_CATEGORIES` |

Item dicts always contain at minimum:
`ID`, `Name`, `Category`, `Size`, `ParentID`, `FilePath`, `URL` (empty string
for folders), `LastModificationTime`, `LastUploadTime`.

### 4 — Error handling convention

- API errors raise `DegooAPIError`.
- CLI commands catch `DegooAPIError`, call `_err(e)`, then `raise SystemExit(1)`.
- Worker threads in `ThreadPoolExecutor` must raise `RuntimeError` (never
  `SystemExit`) so errors surface through futures.
- `_err(msg)` prints `✗ <msg>` to stderr, safely escaping Rich markup.

### 5 — Parallel transfers

`upload` and `download` use `ThreadPoolExecutor` + `as_completed`:

1. **Serial phase** (main thread): create all remote directories with `mkdir` +
   `resolve_path_under` to recover the new folder ID.
2. **Parallel phase**: submit one worker per file; share a single `_make_progress()`
   Rich `Progress` display; collect `RuntimeError`s from futures.

Worker helpers: `_upload_one`, `_collect_upload_tasks`, `_download_one`,
`_collect_download_tasks` (all in `cli.py`).

### 6 — Upload progress

`_ProgressFile` (in `api.py`) wraps a file path, intercepts every `read()`
call made by httpx, and invokes a `progress_callback(bytes_read, total)`.
This is necessary because the GCS multipart POST is a single atomic request —
there is no per-chunk callback at the HTTP layer.

### 7 — Shell command dispatch

`DegooShell` (in `shell.py`) is a `cmd.Cmd` subclass. Every Degoo command
is a `do_<name>` method that:

1. Parses `args` with `shlex.split`.
2. Resolves Degoo paths via `self._resolve_degoo(path)` (CWD-aware).
3. Expands glob patterns via `self._expand_degoo_globs(raw_paths)` for
   commands that accept multiple paths (e.g. `rm`).
4. Calls `subprocess.run(["cligoo", "<cmd>", …])` — the shell always delegates
   to the installed `cligoo` binary.
5. Checks `result.returncode` and prints `✗ …` on failure.

Tab completion is provided by a matching `complete_<name>` method that calls
`self._complete_degoo_path(text)` (Degoo paths) or `self._complete_local(text)`
(local paths).

---

## How to add a new CLI command

1. **Add the GraphQL query/mutation string** to `queries.py` if needed.
2. **Add the `DegooClient` method** to `api.py` that calls `_gql()`.
3. **Add the Click command** to `cli.py`:
   - Decorate with `@main.command()` and `@click.argument` / `@click.option`.
   - Accept paths as `str`; resolve them with `_resolve_item(client, path_or_id)`
     or `_to_absolute(path)` + `client.resolve_path(…)`.
   - Wrap the body in `try: … except DegooAPIError as e: _err(e); raise SystemExit(1)`.
4. **Add the shell command** to `shell.py` (see next section).
5. **Update `docs/CLI_USAGE.md`**: add entry to the appropriate section table
   and document all flags.
6. **Update `README.md`**: add a row to the relevant commands table.
7. **Add tests** to `tests/test_cli_unit.py`: at minimum a success path and an
   error path (mocked via `_patch_client`).
8. **Follow the development workflow checklist** below.

---

## How to add a new shell command

1. Add `do_<name>(self, args: str) -> None` to `DegooShell` in `shell.py`.
   - Parse with `shlex.split(args)`.
   - Resolve Degoo paths with `self._resolve_degoo(path)`.
   - For commands that accept multiple paths, expand globs with
     `self._expand_degoo_globs(raw_paths)`.
   - Delegate to `subprocess.run(["cligoo", "<name>", …])`.
   - Print `✗ …` on non-zero return code.
2. Add `complete_<name>(self, text, line, begidx, endidx)` that returns
   `self._complete_degoo_path(text)` (or `_complete_local` for local paths).
3. Update the `intro` banner string to list the new command.
4. Update the **Degoo commands** table and **Tab completion** table in
   `docs/CLI_USAGE.md`.
5. Add tests to `tests/test_shell.py`.

---

## Development workflow (mandatory checklist)

Run these steps **in order** after every code change:

```bash
# 1. Lint
.venv/bin/ruff check src/ tests/

# 2. Format check
.venv/bin/ruff format --check src/ tests/

# 3. Unit tests (fast, no credentials needed)
.venv/bin/pytest tests/ --ignore=tests/integration -q

# 4. Markdown lint (after any .md edit)
markdownlint <changed-file.md>
```

Fix all errors before moving on. **Do not skip any step.**

Integration tests (live API, require Degoo credentials):

```bash
.venv/bin/pytest tests/integration/ -q
```

---

## Running commands

The project uses a `.venv` at the root for development. Always use it
explicitly:

```bash
.venv/bin/python   # Python interpreter
.venv/bin/pytest   # Test runner
.venv/bin/ruff     # Linter / formatter
```

The standalone install (for end users) lives at
`~/.local/share/cligoo/venv/` and is symlinked to `~/.local/bin/cligoo`.
Do **not** use or modify that environment during development.

---

## Versioning and releasing

The version string is maintained in **two places** that must always match:

- `src/cligoo/__init__.py` → `__version__ = "X.Y.Z"`
- `pyproject.toml` → `version = "X.Y.Z"`

Version format follows [Semantic Versioning](https://semver.org/):

- **patch** (`X.Y.Z+1`): bug fixes, doc updates, new tests, no API change
- **minor** (`X.Y+1.0`): new commands or features, backwards-compatible
- **major** (`X+1.0.0`): breaking changes (e.g. renamed flags, removed commands)

After bumping the version, always add a new section to `CHANGELOG.md` with
today's date, following the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format
(`Added`, `Changed`, `Fixed`, `Removed`, `Security`, `Tests`,
`Documentation`).

---

## Roadmap and pending work

### Pending: `--verify` / `--max-retries` CLI flags for `upload`

`api.py` `DegooClient.upload()` already accepts `verify: bool = False` and
`max_retries: int = 3`. When `verify=True` the upload is checked via
`upload_verifier.verify_and_retry()` (exponential backoff, detects Size=0 or
missing download URL as a GCS linkage failure).

The CLI (`cli.py`) does **not** yet expose these as flags. Next step:

- Add `--verify` / `-V` flag to the `upload` command.
- Add `--max-retries N` option (default 3).
- Pass both through to `client.upload(..., verify=verify, max_retries=max_retries)`.
- On `UploadVerificationError`, print the file ID and a Degoo support note but
  do not abort the whole batch — mark the file as failed and continue.

### Feature propositions (not yet scoped)

See [`TODO.md`](TODO.md) for fully scoped propositions with action lists.

| ID | Feature | TODO.md section |
| -- | ------- | --------------- |
| P-SYNC | `cligoo sync` — folder synchronisation with push/pull/bidirectional and configurable delete policy | `§ P-SYNC` |
| P-DEDUP | Smart download deduplication via local content-hash DB | `§ P-DEDUP` |
| P-LAZY-TOKEN | Lazy token validation — skip `get_token()` for non-API commands | `§ P-LAZY-TOKEN` |
| P-AUDIT | Audit log for destructive ops (`rm`, `mv`, `empty-trash`) | `§ P-AUDIT` |
