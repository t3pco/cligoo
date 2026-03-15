# cligoo — Backlog & Propositions

Tracks implementation-ready work and scoped feature proposals.
Pending items that are already partially implemented are noted in `AGENTS.md`.

---

## In Progress

- [ ] **`--verify` / `--max-retries` CLI flags for `upload`**
  `api.py` already implements `verify` and `max_retries` on `DegooClient.upload()`.
  The CLI does not yet expose them. See `AGENTS.md § Pending: --verify / --max-retries`.

---

## Propositions

### P-SYNC — Folder synchronisation (`cligoo sync`)

> Modelled on `rsync`/`unison` semantics: compare a local directory tree against a
> remote Degoo path and transfer only the delta, with configurable conflict and
> deletion policies.

**Value / risk:** High value for users with large libraries; significant complexity
around conflict detection and deletion safety.

**Acceptance framing:** A user can run a single command to bring a local folder and
a remote Degoo folder into the desired sync state, with no silent data loss.

- [ ] Define a `[sync]` config block supporting multiple named profiles:
  ```toml
  [[sync.profile]]
  name       = "gopro-backup"
  local      = "~/Videos/GoPro"
  remote     = "/Web/GoPro"
  direction  = "push"          # "push" | "pull" | "bidirectional"
  delete     = "trash"         # "trash" | "delete" | "keep" | "ask"
  ```
- [ ] Implement `cligoo sync [profile-name]` command that reads profiles from config.
- [ ] Build a delta engine: walk local tree + remote tree, compute
  `{only_local, only_remote, modified, identical}` sets using size + mtime heuristics
  (fall back to SHA-256 when size matches but mtime differs).
- [ ] Implement **push** direction: upload `only_local`, overwrite `modified` (local wins),
  handle `only_remote` per `delete` policy.
- [ ] Implement **pull** direction: download `only_remote`, overwrite `modified` (remote wins),
  handle `only_local` per `delete` policy.
- [ ] Implement **bidirectional** mode: detect true conflicts (both sides modified since
  last sync anchor), surface them to the user; apply non-conflicting changes automatically.
- [ ] Store a sync anchor (last-sync timestamp + file manifest) at
  `~/.local/share/cligoo/sync/<profile-name>.anchor.json` for reliable change detection.
- [ ] Honour `--dry-run` / `-n` flag: print the delta plan without transferring anything.
- [ ] Add `--workers N` to reuse the parallel upload/download pool.
- [ ] Expose `cligoo sync --list` to show configured profiles and last-sync timestamps.
- [ ] Write unit tests for the delta engine (no network required).
- [ ] Write integration tests for push/pull/bidirectional with a mock Degoo client.

---

### P-DEDUP — Smart download deduplication

> Maintain a global content-hash database so files already present locally are
> never re-downloaded, even if stored under different names or paths.

- [ ] Build an opt-in local hash DB at `~/.local/share/cligoo/hash.db` (SQLite).
- [ ] On download, look up the remote file's Degoo checksum before pulling bytes.
- [ ] If found locally, offer hardlink / copy instead of network transfer.
- [ ] Add `cligoo dedup --scan <local-dir>` to pre-populate the DB from an existing library.

---

### P-LAZY-TOKEN — Lazy token validation

> Skip `get_token()` for commands that never hit the API (`--version`, `--help`, `pwd`).
> Reduces cold-start latency from ~250 ms to ~50 ms.

- [ ] Audit every `@main.command()` entry point — mark those that call `_client()`.
- [ ] Move `_client()` call from module-level init to the first API call site in each command.
- [ ] Benchmark before/after on `cligoo --version` and `cligoo --help`.

---

### P-AUDIT — Audit log for destructive operations

> Append-only log of every `rm`, `empty-trash`, `mv` with timestamp and item ID.

- [ ] Add `[advanced] audit_log = "~/.local/share/cligoo/audit.log"` config key (off by default).
- [ ] Write a structured log line (JSON) on every destructive operation.
- [ ] Add `cligoo audit [--tail N]` command to view recent entries.
- [ ] Rotate log when it exceeds a configurable size limit (default 10 MB).
