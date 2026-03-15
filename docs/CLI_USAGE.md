# Degoo CLI — Command Reference

> **Note:** This is an independent, community-developed tool and is not affiliated with or endorsed
> by [Degoo](https://degoo.com). See the project README for the full disclaimer.

## Table of Contents

- [Authentication](#authentication)
  - [`cligoo login`](#cligoo-login)
  - [`cligoo login --browser`](#cligoo-login---browser)
  - [`cligoo logout`](#cligoo-logout)
  - [`cligoo token`](#cligoo-token)
  - [`cligoo whoami`](#cligoo-whoami)
  - [`cligoo quota`](#cligoo-quota)
- [Browsing](#browsing)
  - [`cligoo ls`](#cligoo-ls-path)
  - [`cligoo ll`](#cligoo-ll-path)
  - [`cligoo tree`](#cligoo-tree-path)
  - [`cligoo info`](#cligoo-info-pathid)
  - [`cligoo search`](#cligoo-search-term)
  - [`cligoo cd`](#cligoo-cd-path)
  - [`cligoo pwd`](#cligoo-pwd)
- [File Operations](#file-operations)
  - [`cligoo mkdir`](#cligoo-mkdir-path)
  - [`cligoo upload`](#cligoo-upload-file--t-dest---name-name--r---exclude-pattern---workers-n)
  - [`cligoo download`](#cligoo-download-item--t-dest---name-name--r---skip-existing---workers-n)
  - [`cligoo mv`](#cligoo-mv-src-dest)
  - [`cligoo cp`](#cligoo-cp-src-dest_folder)
  - [`cligoo rename`](#cligoo-rename-pathid-new_name)
  - [`cligoo rm`](#cligoo-rm-pathid)
- [Recycle Bin](#recycle-bin)
  - [`cligoo trash`](#cligoo-trash--n-n)
  - [`cligoo empty-trash`](#cligoo-empty-trash)
- [Sharing](#sharing)
  - [`cligoo shared`](#cligoo-shared)
  - [`cligoo share`](#cligoo-share-pathid-user)
  - [`cligoo unshare`](#cligoo-unshare-pathid)
- [Feed](#feed)
  - [`cligoo feed`](#cligoo-feed--n-n)
- [Configuration](#configuration)
  - [`cligoo config`](#cligoo-config)
  - [Storage locations](#storage-locations)
  - [`config.json` schema](#configjson-schema)
- [Interactive Shell](#interactive-shell)
  - [`cligoo shell`](#cligoo-shell)
- [Exit Codes](#exit-codes)
- [Global Options](#global-options)

---

## Authentication

### `cligoo login`

Prompts for email and password interactively. Tokens are stored in the system
keyring (macOS Keychain, GNOME Keyring, Windows Credential Manager) with a
plaintext fallback.

```bash
cligoo login
cligoo login --email user@example.com --password secret
```

Bare `cligoo login` uses the method configured via `cligoo config`. If no method
is configured it falls back to the email/password prompt.

### `cligoo login --browser`

Opens system Chrome (or a Playwright-managed Chromium fallback) at
`app.degoo.com`, captures the access token and refresh token from cookies, and
saves them.

```bash
# One-time dependency install
make install-browser
# or
pip install 'cligoo[browser]'

# Authenticate
cligoo login --browser
```

If a Chrome profile is configured via `cligoo config`, the temporary browser is
seeded with that profile's cookies before launch. When both an existing Google
session and a Degoo session are still active, the token is captured
automatically with no user interaction.

> If Chrome is not installed, `make install-browser` downloads a
> Playwright-managed Chromium (~130 MB) as a fallback.

### `cligoo logout`

Clears all stored tokens and credentials.

```bash
cligoo logout
```

### `cligoo token`

Store tokens manually — useful when extracting them from Chrome DevTools.

```bash
cligoo token <ACCESS_TOKEN>
cligoo token <ACCESS_TOKEN> --refresh <REFRESH_TOKEN>
```

**Steps to extract tokens manually:**

1. Open [app.degoo.com](https://app.degoo.com) and sign in.
2. Open DevTools → Network tab (`F12` or `⌥⌘I`).
3. Click any request to `production-appsync.degoo.com`.
4. Open the **Payload** tab.
5. Copy the value of `"Token"` from the request variables.
6. Run `cligoo token <paste-token-here>`.

> Access tokens expire in ~1 hour. If a refresh token is saved (automatically
> via `cligoo login --browser`), the CLI renews tokens silently. Run
> `cligoo whoami` to verify your session.

### `cligoo whoami`

Shows name, email, account type, storage usage, and per-file size limit.

```bash
cligoo whoami
```

### `cligoo quota`

Compact storage summary: used, total, free, and usage percentage.

```bash
cligoo quota
```

---

## Browsing

### `cligoo ls [PATH]`

List a Degoo directory. PATH defaults to the saved working directory.

```bash
cligoo ls
cligoo ls /Web/Photos
cligoo ls -l /Web
cligoo ls -S -r /Web/Videos
cligoo ls -d 3 /Web
```

| Flag | Description |
| --- | --- |
| `-l`, `--long` | Detailed table — ID, type, name, size, modified, category |
| `-S` | Sort by size, largest first |
| `-t` | Sort by modification time, newest first |
| `-r`, `--reverse` | Reverse sort order (applies to `-S`, `-t`, or default name sort) |
| `-R`, `--recursive` | List all levels (equivalent to `-d 99`) |
| `-d N`, `--depth N` | Show subtree up to N levels deep |
| `-n N`, `--limit N` | Max items per directory (default 100) |

### `cligoo ll [PATH]`

Alias for `ls -l`. Available inside `cligoo shell` only.

### `cligoo tree [PATH]`

Print a tree view of the Degoo filesystem.

```bash
cligoo tree
cligoo tree /Web --depth 4
cligoo tree /Web/Photos -n 50
```

| Flag | Description |
| --- | --- |
| `-d N`, `--depth N` | Max depth (default 2) |
| `-n N`, `--limit N` | Max items per directory (default 200) |

### `cligoo info <PATH|ID>`

Full metadata for a single item — works for both files and folders: ID, name,
category, size, parent ID, file path, timestamps, download URL, and more.

```bash
# By absolute path (file or folder)
cligoo info /Web/report.pdf
cligoo info /Web/Photos/2024

# By bare name — resolved against the current working directory (degoo cd)
cligoo info report.pdf        # → /Web/report.pdf  when CWD is /Web
cligoo info Photos/2024       # → /Web/Photos/2024 when CWD is /Web

# By numeric ID
cligoo info 123456789
```

All commands that accept a `PATH|ID` argument resolve bare names relative to
the CWD set by `cligoo cd`. This means you can `cligoo cd /Web/Photos` and then
run `cligoo info holiday.jpg` without typing the full path.

The **ID** field in the output can be used with any command that accepts a
numeric ID (e.g. `cligoo download 123456789 .`).

### `cligoo search <TERM>`

Search for items by name across the whole account.

```bash
cligoo search "vacation photos"
cligoo search "report.pdf" -n 10
```

| Flag | Description |
| --- | --- |
| `-n N`, `--limit N` | Maximum results to return |

### `cligoo cd [PATH]`

Set the working directory, persisted to `~/.config/cligoo/cwd.json`. All
commands use this as the default PATH when none is given.

```bash
cligoo cd /Web/Photos
cligoo cd ..
```

### `cligoo pwd`

Print the saved working directory.

```bash
cligoo pwd
```

---

## File Operations

### `cligoo mkdir <PATH>`

Create a folder. Intermediate directories are created automatically. Relative
paths are resolved against the working directory.

```bash
cligoo mkdir /Web/NewFolder
cligoo mkdir Projects/2024/Q1
```

### `cligoo upload <FILE...> [-t DEST] [--name NAME] [-r] [--exclude PATTERN] [--workers N]`

Upload one or more local files or directories to Degoo.  All transfers run in
parallel (default 20 concurrent workers) with a single shared live progress
display showing byte-accurate overall and per-file progress.  Directory
creation on Degoo is sequential before parallel file uploads begin.

```bash
# Upload a single file to the working directory
cligoo upload report.pdf

# Upload to a specific remote folder
cligoo upload report.pdf -t /Web/Documents

# Upload with a custom remote filename (single file only)
cligoo upload photo.jpg -t /Web/Photos --name "holiday_2024.jpg"

# Upload multiple files to the same destination in one command
cligoo upload a.jpg b.jpg c.jpg -t /Web/Photos

# Upload an entire directory recursively
cligoo upload ~/Pictures/holiday -t /Web/Photos -r
# result: /Web/Photos/holiday/<original tree recreated>

# Recursive upload, excluding macOS metadata and temp files
cligoo upload ~/myproject -t /Web/Code -r \
  --exclude ".DS_Store" --exclude "*.tmp" --exclude "__pycache__"

# Override the concurrency level for this command
cligoo upload bigfile.iso -t /Web/Backups --workers 4
```

| Argument / Flag | Description |
| --- | --- |
| `FILE...` | One or more local file or directory paths |
| `-t`, `--dest PATH` | Degoo destination folder (default: current working directory) |
| `--name NAME` | Override the remote filename — single file only |
| `-r`, `--recursive` | Required when any source is a directory; walks the tree and uploads each file |
| `--exclude PATTERN` | Exclude files matching a glob pattern (repeatable, e.g. `--exclude "*.tmp"`) |
| `--workers N` | Number of concurrent upload threads for this invocation (overrides `config.json`) |

#### Upload summary and exit codes

After all transfers complete, the CLI prints a one-line summary:

```text
✓  3 uploaded                    ← all succeeded, exit 0
✓  2 uploaded, 1 skipped         ← mix of new + duplicate, exit 0
⚠  3 skipped                     ← all already existed, exit 0
⚠  2 uploaded, 1 failed          ← at least one hard failure, exit 1
```

#### Duplicate detection (deduplication)

Degoo performs server-side deduplication by checksum.  If a file with the same
content already exists anywhere in your account, the upload auth call returns
`"Already exist!"`.  The CLI treats this as a **skip** (yellow `~` indicator),
not a failure:

```text
⠦ ~ report.csv (already exists)   0% 0/328 bytes
⚠  1 skipped
```

Exit code is **0** when all failures are skips.

#### Unsupported file types (storage rejected)

Certain file types are blocked by Degoo's GCS upload policy (e.g. `.DS_Store`,
`.exe` on some plans). The CLI catches HTTP 4xx responses from storage and
treats them as skips rather than hard failures:

```text
~ .DS_Store (skipped: storage rejected)
```

**Recommended**: always exclude `.DS_Store` on macOS:

```bash
cligoo upload ~/mydir -r -t /Web --exclude ".DS_Store"
```

> **Breaking change from 1.0.x:** the destination is now the `--dest / -t`
> option instead of a positional argument.  Replace `upload file.jpg /Web`
> with `upload file.jpg -t /Web`.
>
> Passing a directory without `-r` exits with code 1.
>
> Sub-folder creation on Degoo is done via `mkdir` sequentially before the
> files in each folder are uploaded in parallel.  Upload progress shows
> byte-accurate incremental progress per file and a shared overall bar.

### `cligoo download <ITEM...> [-t DEST] [--name NAME] [-r] [--skip-existing] [--workers N]`

Download one or more files or folders from Degoo (path or numeric ID).  All
file transfers run in parallel with a single shared live progress display that
shows real incremental byte progress (streaming CDN download with
`Content-Length`).

```bash
# Download a single file to the current local directory
cligoo download /Web/report.pdf

# Download to a specific local directory
cligoo download /Web/report.pdf -t ~/Downloads

# Download by numeric ID (find IDs with cligoo info)
cligoo download 123456789 -t ~/Downloads

# Download with a custom local filename (single file only)
cligoo download 123456789 -t ~/Downloads --name "my_report.pdf"

# Download multiple files in one command
cligoo download /Web/a.jpg /Web/b.jpg -t ~/Downloads

# Download an entire Degoo folder recursively
cligoo download /Web/Photos/2024 -t ~/Pictures -r
# result: ~/Pictures/2024/<original tree recreated>

# Resume an interrupted recursive download — already-present files are skipped
cligoo download /Web/Photos -t ~/Pictures -r --skip-existing

# Override the concurrency level for this command
cligoo download /Web/BigFolder -t . -r --workers 8
```

| Argument / Flag | Description |
| --- | --- |
| `ITEM...` | One or more Degoo paths or numeric item IDs (files or folders) |
| `-t`, `--dest PATH` | Local destination directory (default: current directory `.`) |
| `--name NAME` | Override the local filename — single file only |
| `-r`, `--recursive` | Required when any item is a folder; walks the Degoo tree and downloads each file |
| `--skip-existing` | Skip files whose local path already exists — useful to resume interrupted downloads |
| `--workers N` | Number of concurrent download threads for this invocation (overrides `config.json`) |

#### Folder detection

The CLI detects folders by checking the item's `Category` field **or** the
absence of a download URL.  Some folders created via the API may be returned
with `Category=Document` and no URL; `download -r` handles these correctly.

#### Resume an interrupted download

Use `--skip-existing` to safely re-run a partial download:

```bash
cligoo download /Web/Photos -t ~/Pictures -r --skip-existing
```

Files already present on disk are silently skipped; only missing files are
transferred.

> **Breaking change from 1.0.x:** the destination is now the `--dest / -t`
> option instead of a positional argument.  Replace `download 999 ~/Downloads`
> with `download 999 -t ~/Downloads`.
>
> Passing a folder without `-r` exits with code 1.
>
> The local destination directory is created automatically if it does not exist.
>
> Use `cligoo info <PATH>` to find the numeric ID of any item.

### `cligoo mv <SRC> <DEST>`

Move or rename an item. Follows explicit-intent semantics to prevent accidental
overwrites.

| DEST form | DEST exists? | Behaviour |
| --- | --- | --- |
| `path/` (trailing slash) | Yes (folder) | Move SRC **inside** DEST |
| `path/` (trailing slash) | No | **Error** — destination not found |
| `path` (no slash) | No | **Rename/move** SRC to DEST (parent must exist) |
| `path` (no slash) | Yes | **Error** — append `/` to move inside, or choose a different name |
| numeric ID | — | Always move SRC **inside** the folder with that ID |

```bash
# Move a file INTO a folder — trailing slash required
cligoo mv /Web/Photos/img.jpg /Web/Archive/
# result: /Web/Archive/img.jpg

# Rename a folder in-place — dest must not exist
cligoo mv /Web/OldProject /Web/NewProject
# result: /Web/NewProject/

# Move and rename in one step — parent must exist, new name must not
cligoo mv /Web/OldProject /Web/Archive/NewProject
# result: /Web/Archive/NewProject/

# Move using numeric IDs — no trailing slash needed
cligoo mv 123456789 987654321

# These error — add '/' to resolve
cligoo mv /Web/Photos /Web/Archive   # /Web/Archive already exists → error
```

### `cligoo cp <SRC> <DEST_FOLDER>`

Copy an item to a different folder. Same argument format as `mv`.

```bash
cligoo cp /Web/report.pdf /Web/Archive
cligoo cp 123456789 987654321
```

### `cligoo rename <PATH|ID> <NEW_NAME>`

Rename an item in place.

```bash
cligoo rename /Web/old_name.txt "new_name.txt"
cligoo rename 123456789 "final_report.pdf"
```

### `cligoo rm <PATH|ID>...`

Delete one or more items.

```bash
# Move to recycle bin
cligoo rm /Web/file.txt

# Delete multiple items
cligoo rm file1.txt /Web/folder

# Delete a directory (required flag for any directory)
cligoo rm -r /Web/OldFolder

# Permanently delete, skipping the recycle bin
cligoo rm --permanent /Web/file.txt
cligoo rm -r --permanent /Web/OldFolder
```

| Flag | Description |
| --- | --- |
| `-r`, `--recursive` | Required for directories (empty or not); the server deletes the entire tree in one call |
| `--permanent` | Skip the recycle bin and delete immediately |

Multiple paths and IDs can be mixed in a single call:
`cligoo rm file1.txt 987654321 /Web/other.pdf`

---

## Recycle Bin

The recycle bin is a virtual location — it has no path in the Degoo folder
tree. You access it exclusively through the `trash`, `rm --permanent`, and
`empty-trash` commands.

### `cligoo trash [-n N]`

List the contents of the recycle bin (default limit 50). The table shows the
numeric **ID**, type icon, name, and size of each item.

```bash
cligoo trash
cligoo trash -n 100
```

Use the **ID** shown in the table to permanently delete individual items:

```bash
# Permanently delete one item from the bin by its ID
cligoo rm --permanent 12345678

# Permanently delete several items at once
cligoo rm --permanent 12345678 23456789
```

### `cligoo empty-trash`

Permanently delete **every item** in the recycle bin in one shot. Because
this is irreversible, the command asks for two confirmations:

```text
$ cligoo empty-trash

⚠️  WARNING  You are about to permanently delete 7 items (142.3 MB) from the recycle bin.
  This action is irreversible. Items cannot be recovered.

  Type YES to continue: YES

⛔  FINAL WARNING  7 items will be gone forever.
  Type EMPTY TRASH to permanently delete everything: EMPTY TRASH

✓ Permanently deleted 7 items (142.3 MB). Recycle bin is now empty.
```

| Flag | Description |
| --- | --- |
| `--yes` | Skip the interactive prompts (for non-interactive scripts where you are certain) |

---

## Sharing

### `cligoo shared`

List all items that are currently shared.

```bash
cligoo shared
cligoo shared -l
```

| Flag | Description |
| --- | --- |
| `-l`, `--long` | Add a "Shared with" column showing the email addresses of all users the item is shared with |
| `-n N`, `--limit N` | Maximum results to return (default 50) |

Without `-l` the output is a compact table that renders instantly. With `-l`
the CLI fetches permissions for each item individually, so the response is
slightly slower but shows exactly who can access each file.

### `cligoo share <PATH|ID> [USER...]`

Share an item. Optionally specify one or more usernames to share with directly.

```bash
cligoo share /Web/report.pdf
cligoo share 123456789 alice bob
```

### `cligoo unshare <PATH|ID>`

Remove sharing from an item.

```bash
cligoo unshare /Web/report.pdf
cligoo unshare 123456789
```

---

## Feed

### `cligoo feed [-n N]`

Show the moments / photo timeline (default limit 30).

```bash
cligoo feed
cligoo feed -n 50
```

---

## Configuration

### `cligoo config`

Interactive wizard. Re-run at any time to change settings. `make reconfigure`
also re-runs the wizard.

The wizard covers two sections:

#### 1. Login method

```text
1) Browser   (degoo login --browser)
2) Password  (degoo login — email + password)
```

Sets `login_method` in `config.json`. Bare `cligoo login` uses this choice
automatically.

#### 2. Chrome profile (shown when browser method is selected)

```text
0)  Fresh temporary profile  (no session)
1)  Default  (user@gmail.com)
2)  Work     (user@company.com)
```

Sets `chrome_profile` in `config.json`. When set, `cligoo login --browser`
copies that profile's cookies into a temporary browser so existing Google and
Degoo sessions carry over automatically.

### Storage locations

| What | Where |
| --- | --- |
| User preferences | `~/.config/cligoo/config.json` |
| Auth tokens (primary) | macOS Keychain / GNOME Keyring / Windows Credential Manager |
| Auth tokens (fallback) | `~/.config/cligoo/tokens.json` |
| Credentials (fallback) | `~/.config/cligoo/credentials.json` |
| Working directory | `~/.config/cligoo/cwd.json` |

### `config.json` schema

```json
{
  "login_method": "browser",
  "chrome_profile": "Default",
  "api_key": "da2-vs6twz5vnjdavpqndtbzg3prra",
  "transfer_workers": 20
}
```

| Key | Values | Meaning |
| --- | --- | --- |
| `login_method` | `"browser"` \| `"password"` \| `null` | Method used by bare `cligoo login`. `null` = prompt each time |
| `chrome_profile` | `"Default"` \| `"Profile 1"` \| … \| `null` | Chrome profile to seed for `--browser` login. `null` = fresh temporary profile |
| `api_key` | `"da2-…"` \| `null` | AWS AppSync client key override. Only needed if Degoo rotates the key. The `DEGOO_API_KEY` environment variable takes precedence |
| `transfer_workers` | integer ≥ 1 (default `20`) | Concurrent threads used for parallel upload/download. Override per-command with `--workers N` |

#### Overriding the API key

The AppSync key embedded in the package is the same one visible in any
browser's network inspector when visiting `app.degoo.com` — it is **not** a
secret. If Degoo rotates it you can update without reinstalling:

```bash
# Option A — environment variable (takes highest priority)
export DEGOO_API_KEY=da2-newkeyhere

# Option B — config file (persists across shells)
echo '{"api_key": "da2-newkeyhere"}' | jq -s '.[0] * .[1]' \
  ~/.config/cligoo/config.json - > /tmp/cfg.json \
  && mv /tmp/cfg.json ~/.config/cligoo/config.json
```

---

## Interactive Shell

### `cligoo shell`

Drops into a REPL for navigating the Degoo filesystem without re-authenticating
on every command. The prompt shows both the local and Degoo working directories:

```text
[~/Desktop] cligoo:/Web/Photos
❯ _
```

The shell starts in the Degoo directory last set by `cligoo cd`.

#### Degoo commands

| Command | Description |
| --- | --- |
| `ls [opts] [path]` | List Degoo directory — all `ls` flags work (`-l`, `-S`, `-t`, `-r`, `-R`, `-d N`) |
| `ll [path]` | Long listing — alias for `ls -l` |
| `cd [path]` | Change Degoo directory — supports `..`, relative paths, absolute paths, `~` for root |
| `pwd` | Print current Degoo directory |
| `tree [opts] [path]` | Tree view |
| `info <path\|id>` | Show full metadata for a single item |
| `search <term>` | Search by filename |
| `mkdir <path>` | Create a folder (absolute or relative path) |
| `mv <src> <dest>` | Move or rename — same semantics as `cligoo mv` (trailing `/` = move inside) |
| `cp <src> <dest>` | Copy item into a folder |
| `rename <path\|id> <name>` | Rename item |
| `rm [-r] [--permanent] <path\|id> [...]` | Remove item(s); supports glob patterns (e.g. `rm -r 2024-*`) |
| `trash [-n N]` | List the recycle bin |
| `empty-trash` | Permanently delete everything in the bin (double confirmation) |
| `upload [-r] [--exclude P] <local>` | Upload a local file or folder to the current Degoo directory; `-r` required for directories |
| `download [-r] [--skip-existing] <degoo> [local]` | Download a Degoo file or folder; `-r` required for folders; local defaults to current local dir |

All path arguments accept absolute paths, paths relative to the current Degoo
directory, or numeric IDs. `upload` and `download` resolve local paths against
the current local directory shown in the prompt.

`upload` and `download` obey the same duplicate / skip rules as the standalone
commands: already-existing files are shown in yellow as `~ name (already
exists)` and counted as skipped, not failures.

#### Glob patterns in `rm`

The `rm` command expands shell-style glob patterns (`*`, `?`, `[…]`) against
the current Degoo directory. For example:

```text
❯ rm -r cligoo-regression-*
```

expands to all items in the current folder whose name starts with
`cligoo-regression-` and deletes them all in one call.

#### Local helpers

| Command | Description |
| --- | --- |
| `lcd [path]` | Change local working directory (empty = home) |
| `lpwd` | Print local working directory |
| `lls [FLAGS] [path]` | Run `ls` on a local directory; all `ls` flags are forwarded (e.g. `lls -1`, `lls -lah`) |

#### Tab completion

Tab completion is available for all path arguments:

| Command | Completion source |
| --- | --- |
| `cd` | Degoo filesystem (live API) |
| `ls` | Degoo filesystem (live API) |
| `ll` | Degoo filesystem (live API) |
| `tree` | Degoo filesystem (live API) |
| `info` | Degoo filesystem (live API) |
| `mkdir` | Degoo filesystem (live API) |
| `mv` | Degoo filesystem (live API) |
| `cp` | Degoo filesystem (live API) |
| `rename` | Degoo filesystem (live API) |
| `rm` | Degoo filesystem (live API) |
| `trash` | No completion needed (no path argument) |
| `empty-trash` | No completion needed (no path argument) |
| `download` first arg | Degoo filesystem (live API) |
| `download` second arg | Local filesystem |
| `upload` | Local filesystem |
| `lcd` | Local filesystem |
| `lls` | Local filesystem |

Degoo completion works like fish/zsh: it lists the children of the deepest
resolvable path component and filters by whatever has been typed so far.
Typing `cd /Web/Ph<TAB>` on a folder named `Photos` expands to `cd /Web/Photos/`.
Completion is case-insensitive and appends `/` to directories automatically.

#### System shell pass-through

Any command not listed above is forwarded verbatim to your system shell
(`bash -c …`) and runs inside the current local directory. Standard tools such
as `cat`, `grep`, `cp`, `open`, and `python3` all work normally. Prefix any
line with `!` to force pass-through for names that clash with built-in shell
commands.

#### Ctrl-C behaviour

Pressing Ctrl-C while a command is running (e.g. during a long upload or
download) **does not exit the shell**.  The current command is interrupted and
the shell returns to the prompt, ready for the next command.  Press Ctrl-D or
type `exit` to leave the shell.

#### Exiting

Type `exit`, `quit`, or press Ctrl-D.

#### Example session

```text
$ cligoo shell

  Degoo Shell
  ──────────────────────────────────────────────────────────
  Degoo:  ls  ll  cd  pwd  tree  upload  download
          info  mv  cp  rm  rename  mkdir  search
  Local:  lcd  lpwd  lls
  Other:  any command is forwarded to your system shell
  Quit:   exit  /  quit  /  Ctrl-D

[~] cligoo:/Web
❯ cd Photos/2024

[~] cligoo:/Web/Photos/2024
❯ ls -l

[~] cligoo:/Web/Photos/2024
❯ info holiday.jpg

[~] cligoo:/Web/Photos/2024
❯ mv holiday.jpg summer_holiday.jpg

[~] cligoo:/Web/Photos/2024
❯ rm -r cligoo-regression-*

[~] cligoo:/Web/Photos/2024
❯ lcd ~/Desktop

[~/Desktop] cligoo:/Web/Photos/2024
❯ upload holiday.jpg

[~/Desktop] cligoo:/Web/Photos/2024
❯ download ../budget.pdf ./

[~/Desktop] cligoo:/Web/Photos/2024
❯ exit
```

---

## Exit Codes

| Code | Meaning |
| --- | --- |
| 0 | Success — includes cases where all files were skipped (already exist or storage rejected) |
| 1 | At least one hard failure (auth error, API error, path not found, network error) |

For `upload` and `download`, skipped files (duplicates or unsupported types) do **not** cause a
non-zero exit code.  Only unrecoverable errors do.

---

## Global Options

```bash
cligoo --version
cligoo --help
degoo <command> --help
```
