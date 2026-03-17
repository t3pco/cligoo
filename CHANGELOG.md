# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- **`cligoo feed --watch`**: new live-monitoring mode — polls the feed every
  `--interval` seconds (default 30 s) and prints only newly appeared items as they
  arrive from any client (iOS, Android, web, cligoo, …). First poll shows a
  snapshot for context; subsequent polls emit only new rows. Works with
  `--output json` for scripted pipelines (`cligoo feed --watch -o json | jq …`).
- **`feed` table enriched**: the one-shot `cligoo feed` table now shows Size, Path,
  Platform (iOS / Android / Web / Windows / macOS / Linux), and upload timestamp
  alongside the existing ID / Type / Name columns.

### Fixed

- **`cligoo login` unnecessary password-endpoint call**: when stored credentials
  and a valid refresh token were both present, `cligoo login` (no flags) always hit
  Degoo's email/password endpoint, consuming rate-limit quota unnecessarily. It now
  tries `get_token()` first (refresh-token path); the password endpoint is only
  reached if the refresh token has expired.

---

## [0.1.1] — 2026-03-15

### Added

- **`--output json` / `-o json`** flag on all commands that benefit from structured output:
  `whoami`, `quota`, `ls`, `tree`, `info`, `search`, `mkdir`, `upload`, `download`,
  `mv`, `cp`, `rename`, `rm`, `trash`, `shared`, `share`, `unshare`, `feed`.
  All item objects use a consistent snake_case schema (`id`, `name`, `category`,
  `is_folder`, `size_bytes`, `path`, `created`, `modified`, `url`, …).
  Recursive `ls`/`tree` JSON output includes an `"incomplete": true` flag when any
  subfolder could not be listed due to an API error.
- **`info` folder content size**: `cligoo info <folder>` now recursively walks the
  tree and reports total content size, file count, and subfolder count. Use
  `--no-size` to skip the walk on large trees.
- **Streaming folder-size walk**: replaced `list_dir(limit=None)` bulk fetch with
  `iter_dir()` generator that streams one API page at a time — peak memory is now
  bounded to ~1 000 items regardless of tree depth.
- **Clickable URLs in `info` output**: `URL` and `ThumbnailURL` fields render as
  OSC 8 terminal hyperlinks (`[link=url]url[/link]`) in supporting terminals.
- **Silent `cligoo login` when credentials are saved**: running `cligoo login` with
  no flags and stored credentials no longer prompts — it logs in silently. Prompts
  only appear on first use or when `--email`/`--password` flags are passed explicitly.
- **Auto-relogin on any command**: any `cligoo` command transparently re-authenticates
  when the access token expires; `cligoo login` is only needed on first setup or after
  `cligoo logout`.
- **Login rate-limit backoff**: after receiving HTTP 429 from Degoo's login endpoint,
  all subsequent login attempts (auto and explicit) are blocked locally for 15 minutes
  with a countdown message, preventing the rate limit from compounding.
- **Legacy keyring migration**: credentials and tokens previously stored under the
  `degoo-cli` or `degoo` keyring service name are detected and migrated to `cligoo`
  automatically on first use.
- **`cd` / `pwd` disabled-by-default as standalone commands**: outside `cligoo shell`,
  `cd` and `pwd` show a helpful message pointing to the shell. Set
  `[advanced] standalone_nav = true` in `config.toml` to re-enable them.
- **Upload root guard**: attempting to upload directly to `/` (root, ID `"0"`) shows
  a clear error with a usage example instead of silently failing.
- **`_safe_int()` helper**: tolerates float-string sizes (`"1048576.0"`) from the API
  in `_item_json` — no more `ValueError` on unexpected size formats.
- **JSON output config option**: set `output.format = "json"` in `config.toml` to
  make JSON the default output format for all commands without passing `-o json` each
  time.
- **`upload_retries` config option**: set `[session] upload_retries = N` in
  `config.toml` to control how many times a failed GCS upload is retried (default 5).
  Retries use exponential backoff (1 s, 2 s, 4 s … capped at 30 s). Set to `0` to
  disable retries entirely.

### Changed

- `ls --output json` with depth > 0 now skips the initial `list_dir` call and walks
  the tree directly — eliminates a redundant API round-trip and preserves sort order
  for flat (non-recursive) JSON mode.
- `_collect_tree_flat` returns `(items, incomplete)` tuple instead of a bare list;
  all JSON callers include `"incomplete"` in the response envelope.
- `whoami` JSON: `free_bytes` is clamped to `max(0, total - used)` — never negative
  when the API omits `TotalQuota`.
- `shared --json -l`: adds `"shared_with_error": true` alongside `null` when the
  permissions API call fails, making errors distinguishable from "no shares".
- Documentation (`README.md`, `docs/CLI_USAGE.md`): updated Authentication section
  to describe silent login, auto-relogin, rate-limit protection, and degoo-cli
  migration; added full JSON Output reference section.

### Fixed

- **Token expiry during long-running uploads/downloads**: `_client()` previously
  snapshotted the access token at startup and passed it explicitly to `DegooClient`,
  causing all API calls to fail with auth errors after ~1 hour. `DegooClient` is now
  constructed without an explicit token so its `token` property calls `get_token()`
  on every request — access tokens are refreshed transparently via the refresh token
  or re-login without interrupting running transfers.
- **GCS upload transient failure retry**: GCS network errors (`ConnectError`, timeouts)
  and 5xx responses are now retried up to `upload_retries` times (default 5,
  configurable via `[session] upload_retries` in `config.toml`) with exponential
  backoff (1 s, 2 s, 4 s … capped at 30 s). 4xx responses (policy violations,
  bad content type) are not retried — they indicate a non-recoverable condition.
- **`quota` / `whoami` table output negative free space**: table path now clamps
  free space to `max(0, total - used)` matching the JSON path — no more negative
  values shown when the account is over-quota.
- **`TotalQuota` absent causes division by zero or absurd 100 % usage**: both
  `whoami` and `quota` now use `int(info.get("TotalQuota") or 0)` with an explicit
  `if total else 0` guard on the percentage — missing quota info shows 0 % instead
  of a division-by-zero traceback or a misleading 100 % reading.
- **`whoami` JSON `file_size_limit_bytes` rejects float-string sizes**: switched
  from `int(… or 0)` to `_safe_int(…)` so values like `"1048576.0"` parse
  correctly instead of raising `ValueError`.
- **`shared --output json` missing envelope**: output now wraps the item array in
  `{"items": […], "count": N}` matching every other multi-item JSON command.
- **`ls` flat JSON always reported `"incomplete": false`**: replaced the hardcoded
  `False` with `len(items) >= limit` so callers know when results may be
  truncated (heuristic — use `--limit N` to raise the cap).
- **Login 429 rate-limit error shown on first occurrence**: previously the
  rate-limit message was only shown on the *next* login attempt; now it is raised
  immediately when a 429 is received.
- **Rate-limit backoff not cleared after successful `login(save=False)`**: the
  backoff file is now unconditionally deleted on a successful HTTP 200 response,
  regardless of whether credentials are persisted.
- **Corrupted login backoff file permanently disables rate-limit protection**:
  a non-float or partially-written `.login_backoff` file is now deleted when it
  cannot be parsed, restoring normal rate-limit behaviour.
- **Legacy keyring migration returned `None` refresh token / password**: both
  migration paths now normalise `None` to `""` before returning — prevents
  `login(email, None)` in the auto-relogin path.
- **File handle leak on GCS upload retry**: `_ProgressFile` / bare file handle
  now closed via `try/finally` so the handle is released even when
  `httpx.post()` raises or a retry is triggered.
- Integration test `test_cli_cd_and_pwd`: patched `get_standalone_nav_enabled` so the
  test works now that `cd`/`pwd` are disabled by default outside the shell.
- Test `test_login_429_sets_backoff_and_next_call_is_blocked`: replaced vacuous
  `assert` inside bare `except` with `pytest.raises` so the test actually fails when
  no exception is raised.

---

## [0.1.0] — 2026-03-15 — First public beta

### Core API client (`api.py`)

- Full reverse-engineered Degoo GraphQL API client over HTTPS
- File operations: list directory, metadata, search, mkdir, upload, download,
  move/copy, rename, delete (recycle bin + permanent), list trash
- Sharing: share item (optionally with specific users), unshare, list shared items
- Account: user info (name, email, quota), moments feed
- Parallel file transfers via `ThreadPoolExecutor` — uploads and downloads run
  concurrently with a single shared Rich progress display (spinner, speed, ETA
  per file; overall count)
- Real incremental upload progress: `_ProgressFile` wrapper intercepts every
  `read()` call and feeds live byte counts to the progress bar
- Degoo deduplication handling: when `getBucketWriteAuth4` returns
  `"Already exist!"`, the file is linked to the target folder via
  `setUploadFile3` so deduplicated content still appears in the correct folder
- `DegooAlreadyExistsError`: sentinel exception that distinguishes deduplicated
  or storage-rejected files from real errors; overall exit code is 0 when only
  skips occurred
- Browser-style numbered download copies: when a local file already exists the
  download is saved as `stem (1).ext`, `stem (2).ext`, etc. — the original is
  never touched. Pass `--overwrite` to revert to overwrite behaviour.
- `DegooClient` self-configures from `config.toml`: `timeout`, `graphql_url`,
  and `debug` are resolved from config when not explicitly passed
- Verbose HTTP debug logging: when `api.debug = true` in config, every request
  and response URL is logged to stderr via httpx event hooks
- `resolve_path_under(parent_id, name)`: finds a direct child by name — used by
  recursive upload to recover the ID of a newly created sub-folder

### Authentication & token management (`auth.py`)

- Email/password login with access-token + refresh-token persistence via the
  system keyring (macOS Keychain / GNOME Keyring / Windows Credential Locker);
  `credentials.json` (mode 600) is used as a fallback when keyring is
  unavailable
- Stored password auto-fill: `cligoo login --email <addr>` uses the password
  already saved in keyring so the user is never re-prompted after the first
  login
- Browser-based OAuth login using Playwright (`cligoo login --browser`) —
  captures both access token and refresh token from cookies for silent renewal
- Chrome profile seeding: copies `Cookies` + `Login Data` from a real Chrome
  profile into the temporary browser session so existing Google/Degoo sessions
  carry over without re-authentication
- JWT-based token storage with automatic expiry detection
- Automatic re-login on token expiry (`session.auto_relogin = true` in config):
  `get_token()` calls `login()` transparently when a saved token expires and
  stored credentials are available
- Direct token injection via `cligoo token <JWT>`
- One-time transparent migration: credentials stored in a legacy `[auth]`
  section of `config.toml` are automatically moved to the keyring on first use,
  with a printed warning
- Clean error messages for rate-limit and Cloudflare proxy responses: HTML error
  bodies are stripped and replaced with a single readable sentence

### Chrome profile detection (`chrome.py`)

- Cross-platform Chrome installation and profile discovery (macOS, Linux, Windows)
- Parses `Local State` JSON to enumerate profile names, display names, and
  associated email addresses without launching Chrome

### Configuration (`config.py`)

- Primary store: `~/.config/cligoo/config.toml` (TOML, read + write)
- Legacy fallback: `~/.config/cligoo/config.json` (flat JSON, read-only;
  transparent upgrade on first `save_config()` call)
- Atomic write: `tempfile.mkstemp` + `os.replace` — crash-safe against a killed
  process leaving the config file corrupt or empty
- TOML sections and keys:

  | Section | Key | Default | Description |
  | --- | --- | --- | --- |
  | `[api]` | `graphql_url` | built-in | GraphQL endpoint override |
  | `[api]` | `api_key` | built-in | AppSync API key (`DEGOO_API_KEY` env var overrides) |
  | `[api]` | `timeout` | `60` | HTTP request timeout in seconds |
  | `[api]` | `debug` | `false` | Verbose HTTP request/response logging |
  | `[session]` | `login_method` | — | `"browser"` or `"password"` |
  | `[session]` | `chrome_profile` | — | Chrome profile directory name |
  | `[session]` | `transfer_workers` | `20` | Concurrent upload/download threads |
  | `[session]` | `auto_relogin` | `true` | Re-login automatically on token expiry |
  | `[output]` | `format` | `"table"` | Default output format: `"table"` or `"json"` |
  | `[output]` | `compact_json` | `false` | Compact (single-line) vs pretty JSON |

- Credentials (email, password) are stored exclusively in the system keyring —
  never written to `config.toml`

### CLI commands (`cli.py`)

| Command | Description |
| --- | --- |
| `cligoo login` | Authenticate — uses configured method automatically |
| `cligoo login --browser` | Browser-based Google OAuth login |
| `cligoo login --email ADDR` | Email/password login; uses stored password if available |
| `cligoo logout` | Clear stored tokens and credentials |
| `cligoo token <JWT>` | Store a token directly |
| `cligoo whoami` | Show account profile and quota |
| `cligoo quota` | Show storage usage summary |
| `cligoo ls [PATH]` | List directory contents |
| `cligoo ls -l` | Long listing with ID, size, date, category |
| `cligoo ls -S` | Sort by size (largest first) |
| `cligoo ls -t` | Sort by modification time (newest first) |
| `cligoo ls -r` | Reverse sort order |
| `cligoo ls -R` | Recursive listing (all levels) |
| `cligoo ls -d N` | Recursive listing up to N levels deep |
| `cligoo tree [PATH]` | Tree view of the folder hierarchy |
| `cligoo info <PATH\|ID>` | Detailed metadata for a single item |
| `cligoo search <TERM>` | Full-text search across all files |
| `cligoo cd <PATH>` | Set the current working directory |
| `cligoo pwd` | Show the current working directory |
| `cligoo mkdir <PATH>` | Create a folder (including nested paths) |
| `cligoo upload FILE... [-t DEST]` | Upload one or more local files with progress; `-r` for directories |
| `cligoo upload --exclude PATTERN` | Skip files matching a glob pattern during recursive upload (repeatable) |
| `cligoo upload --workers N` | Override concurrent worker count for this invocation |
| `cligoo download ITEM... [-t DEST]` | Download one or more items with progress; `-r` for folders |
| `cligoo download --skip-existing` | Skip files whose local path already exists (resume) |
| `cligoo download --overwrite` | Overwrite existing local files instead of making numbered copies |
| `cligoo download --workers N` | Override concurrent worker count for this invocation |
| `cligoo mv <SRC> <DEST>` | Move or rename item (trailing `/` = move inside; no slash + absent = rename) |
| `cligoo cp <SRC> <DEST>` | Copy item into a folder |
| `cligoo rename <PATH\|ID> <NAME>` | Rename an item |
| `cligoo rm <PATH\|ID>...` | Move item(s) to recycle bin |
| `cligoo rm -r <PATH\|ID>...` | Delete directory and all contents |
| `cligoo rm --permanent` | Permanently delete (skip recycle bin) |
| `cligoo trash` | List recycle bin contents with total size footer |
| `cligoo empty-trash` | Permanently delete every item in the bin (double confirmation) |
| `cligoo shared` | List shared items |
| `cligoo shared -l` | List shared items with recipient email column |
| `cligoo share <PATH\|ID>` | Share an item |
| `cligoo unshare <PATH\|ID>` | Remove sharing from an item |
| `cligoo feed` | Show moments timeline |
| `cligoo config` | Interactive wizard: set login method and Chrome profile |
| `cligoo shell` | Interactive Degoo filesystem shell (REPL) |

- All commands accept Degoo paths (`/Web/folder/file`) or numeric IDs
- Bare filenames and relative paths are automatically resolved against the
  current working directory set by `cligoo cd`
- `rm` requires `-r` to delete directories; refuses without it even when the
  directory is empty (POSIX behaviour)
- Eager token validation in `_client()`: expired or missing tokens are caught
  before any API call is made, with a clean `✗ <message>` error — no Python
  tracebacks exposed to the user
- JSON output via `--output json`; respects `output.format` and
  `output.compact_json` from config
- Storage-rejected uploads (e.g. `.DS_Store`) are reported as skips (yellow
  label) rather than hard failures; exit code is 0 when `failed == 0`
- Upload summary: `X uploaded, Y skipped, Z failed`

### Interactive shell (`shell.py`)

- `cligoo shell` drops into a `cmd.Cmd`-based REPL with a dual prompt showing
  both the current local and Degoo working directories
- Full set of file-management commands with tab completion for Degoo paths:

  | Command | Description |
  | --- | --- |
  | `ls [PATH...]` | List directory; supports glob patterns and multiple paths |
  | `ll` | Long listing alias |
  | `tree [PATH...]` | Tree view; supports glob patterns |
  | `cd <PATH>` | Change Degoo directory |
  | `pwd` | Show Degoo working directory |
  | `info <PATH\|ID>` | Full metadata for a single item |
  | `search <TERM>` | Search by filename |
  | `mkdir <PATH>` | Create a folder |
  | `mv <SRC> <DEST>` | Move or rename |
  | `cp <SRC> <DEST>` | Copy into a folder |
  | `rename <PATH\|ID> <NAME>` | Rename |
  | `rm [-r] [--permanent] <PATH\|ID> [...]` | Delete; supports glob patterns |
  | `trash [-n N]` | List recycle bin |
  | `empty-trash` | Permanently empty recycle bin (double confirmation) |
  | `upload <local>` | Upload to current Degoo directory |
  | `download <degoo> [local]` | Download to local directory |
  | `lcd / lpwd / lls` | Manage local working directory |

- Tab completion for Degoo paths — live API calls enumerate children,
  case-insensitive prefix match, directories gain a trailing `/`
- Tab completion for local paths (`upload`, `lcd`, `lls`)
- TTL completion cache: `list_dir` results cached for 5 seconds; cache
  invalidated automatically on any mutating command
- Glob expansion: `*`, `?`, and `[…]` patterns expanded against live Degoo
  listings in `rm`, `ls`, and `tree`
- `Ctrl-C` returns to the shell prompt instead of exiting the process
- All unrecognised commands forwarded to the system shell; `!` prefix forces
  pass-through for any name that clashes with a built-in
- Starts in the Degoo directory last set by `cligoo cd`
- macOS libedit fix: `preloop()` re-binds `^I` using libedit syntax so tab
  completion works without `gnureadline`

### Installation (`Makefile`)

- Standalone installation model: `make install` creates an isolated venv at
  `~/.local/share/cligoo/venv/` and symlinks `~/.local/bin/cligoo` into it —
  the project folder is not required after installation (mirrors `pipx`)
- `make install-dev`: editable install pointing the symlink at the project
  `.venv` for live development
- `make install-browser`: installs Playwright into the standalone venv; uses
  system Chrome/Edge if present, otherwise downloads Chromium (~130 MB)
- `make install-config`: seeds `~/.config/cligoo/config.toml` from the
  bundled template; only prompts for login method preference; credentials are
  stored in the system keyring, never in the config file
- `make reconfigure`: re-run the login-method and Chrome-profile wizard
- `make uninstall` / `make reinstall`: remove and optionally reinstall the
  standalone venv; config preserved
- `make lint` / `make fmt` / `make test`: use the project dev `.venv` only

### Documentation

- `README.md`: full Quick Start, all commands, keyring credential storage,
  `--browser` login, Chrome profile guide, multi-file/recursive transfer
  examples, `cligoo shell` overview
- `docs/CLI_USAGE.md`: comprehensive reference for every command and flag,
  transfer options, shell commands, tab-completion guide, recycle bin walkthrough
- `docs/API_INTERNALS.md`: reverse-engineered Degoo GraphQL API internals,
  upload pipeline details, token refresh flow, download flow
- `docs/TESTING.md`: testing guide, fixtures, integration test setup
- `AGENTS.md`: guide for AI agents — document chain, architecture patterns, how
  to add CLI and shell commands, mandatory dev workflow checklist, versioning rules
- `cligoo.toml.example`: annotated config template; no credentials, only
  preferences and API settings

### Project

- MIT licence
- Python 3.9–3.13 support
- Dependencies: `httpx`, `rich`, `click`, `keyring`, `humanize`, `pyjwt`,
  `tomli` (Python < 3.11), `tomli-w`
- Optional browser dependency: `playwright`
- 194+ unit tests; integration test suite for live API validation
- `ruff` linting and formatting enforced

[0.1.0]: https://github.com/marcomc/cligoo/releases/tag/v0.1.0
