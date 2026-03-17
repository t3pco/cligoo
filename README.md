# cligoo

A command-line interface for [Degoo](https://degoo.com) cloud storage, built on top of Degoo's GraphQL API.

> **Disclaimer:** cligoo is an independent, community-developed project and is **not** affiliated
> with, endorsed by, or in any way officially connected with Degoo AB or the
> [Degoo](https://degoo.com) service. "Degoo" is a trademark of its respective owner. This tool uses
> the same public GraphQL API that the official Degoo web application uses — no private or
> undisclosed interface is accessed. It is provided as a free, open-source convenience utility for
> existing Degoo users who prefer a terminal-based workflow.
>
> **Full command reference:** see [`docs/CLI_USAGE.md`](docs/CLI_USAGE.md)

---

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Common Workflows](#common-workflows)
  - [Browsing your cloud storage](#browsing-your-cloud-storage)
  - [Uploading files and folders](#uploading-files-and-folders)
  - [Downloading files and folders](#downloading-files-and-folders)
  - [Organising files](#organising-files)
  - [Using the interactive shell](#using-the-interactive-shell)
- [Authentication](#authentication)
  - [Email/password accounts](#emailpassword-accounts)
  - [Google OAuth accounts](#google-oauth-accounts)
  - [Store a token directly](#store-a-token-directly)
- [Commands](#commands)
  - [Account](#account)
  - [Browsing](#browsing)
  - [File management](#file-management)
  - [Sharing & activity](#sharing--activity)
- [Interactive Shell](#interactive-shell)
- [Project Structure](#project-structure)
- [Documentation](#documentation)
- [API Endpoints](#api-endpoints)
- [Disclaimer](#disclaimer)
- [Credits](#credits)
- [License](#license)

---

## Installation

```bash
git clone https://github.com/your-username/cligoo.git
cd cligoo
make install          # standalone install, no project folder needed after
```

This installs into `~/.local/share/cligoo/venv/` and symlinks `cligoo` into `~/.local/bin/`.

**Optional extras:**

```bash
make install-browser  # add Playwright for Google OAuth login
make install-dev      # editable install for development
```

**Alternative — install directly with pip** (uses your active Python / pyenv version):

```bash
pip install git+https://github.com/marcomc/cligoo.git
```

**Uninstalling:**

| Install method | Uninstall command |
| --- | --- |
| `make install` | `make uninstall` |
| `make install-dev` | `make uninstall-dev` |
| `pip install` | `pip uninstall cligoo` |

**Requirements:** Python 3.9+

---

## Quick Start

```bash
# First-time setup: choose login method and optional Chrome profile
cligoo config

# Log in (prompts once; credentials are saved to keyring for all future use)
cligoo login

# See account info and quota
cligoo whoami

# List your files
cligoo ls /

# Upload a file
cligoo upload photo.jpg -t /Web/Photos

# Download a file
cligoo download /Web/Photos/photo.jpg -t ~/Downloads/
```

---

## Common Workflows

### Browsing your cloud storage

```bash
# List the root of your cloud storage
cligoo ls /

# Long listing (size, date, type) inside a folder
cligoo ls -l /Web/Photos

# Sort by size, largest first
cligoo ls -lS /Web/Videos

# Recursive tree — handy for a quick overview of a folder hierarchy
cligoo tree /Web

# Limit depth and number of entries
cligoo tree -d 2 -n 50 /Web

# Search for files by name anywhere in your storage
cligoo search "holiday"

# Show full metadata for a single item
cligoo info /Web/Photos/holiday.jpg

# Remember where you are between sessions
cligoo cd /Web/Photos
cligoo pwd         # prints /Web/Photos
cligoo ls          # lists /Web/Photos (cwd used automatically)
```

---

### Uploading files and folders

Multiple files and directories can be transferred in a single command.
Transfers run in parallel (default 20 workers, configurable via `transfer_workers` in `~/.config/cligoo/config.toml`).

```bash
# Upload a single file to a specific folder
cligoo upload report.pdf -t /Work/Reports

# Upload and give it a different name in the cloud
cligoo upload report_v3_final.pdf -t /Work/Reports --name report.pdf

# Upload to the current working directory (set with cligoo cd)
cligoo upload photo.jpg

# Upload multiple files at once
cligoo upload a.jpg b.jpg c.jpg -t /Web/Photos

# Upload an entire local folder recursively
# Creates matching sub-folders on Degoo; all files upload in parallel
cligoo upload ~/Pictures/holiday -t /Web/Photos -r

# Recursive upload, excluding unwanted files
cligoo upload ~/myproject -t /Web/Code -r --exclude "*.tmp" --exclude ".DS_Store"
```

---

### Downloading files and folders

```bash
# Download a file to a local directory
cligoo download /Web/Photos/holiday.jpg -t ~/Downloads/

# Download and rename locally
cligoo download /Web/Photos/holiday.jpg -t ~/Downloads/ --name beach.jpg

# Download multiple files at once
cligoo download /Web/a.jpg /Web/b.jpg -t ~/Downloads/

# Download an entire Degoo folder recursively (recreates the folder tree locally)
cligoo download /Web/Photos/2024 -t ~/Pictures -r

# Resume an interrupted download — skip files already present locally
cligoo download /Web/Photos/2024 -t ~/Pictures -r --skip-existing

# Find the numeric ID of a file (shown in the info table)
cligoo info /Web/Photos/holiday.jpg

# Download by numeric item ID
cligoo download 12345678 -t ~/Downloads/

# Download to the current local directory
cligoo download /Web/Photos/holiday.jpg -t .
```

---

### Organising files

```bash
# Create a folder
cligoo mkdir /Web/Photos/2024

# Move a file into an existing folder — dest must end with '/'
cligoo mv /Web/Photos/holiday.jpg /Web/Photos/2024/
# → /Web/Photos/2024/holiday.jpg

# Rename a folder in-place — dest must NOT exist
cligoo mv /Web/OldProject /Web/NewProject
# → /Web/NewProject/

# Move AND rename in one command — /Web/Archive must exist, NewProject must not
cligoo mv /Web/OldProject /Web/Archive/NewProject
# → /Web/Archive/NewProject/

# Move inside using a numeric ID — trailing slash not required for IDs
cligoo mv 12345678 111

# Rename a file in-place
cligoo rename /Web/Photos/2024/holiday.jpg summer_holiday.jpg

# Copy a file into a folder
cligoo cp /Web/Photos/2024/holiday.jpg /Web/Archive

# Copy a folder into another folder (same — no extra flag needed)
cligoo cp /Web/Photos/2024 /Web/Backup

# Move a file to the recycle bin
cligoo rm /Web/Photos/old.jpg

# Permanently delete (skip the recycle bin)
cligoo rm --permanent /Web/Photos/old.jpg

# Remove a folder and all its contents
cligoo rm -r /Web/OldProject
```

---

### Using the interactive shell

The shell is the most efficient way to work with multiple files in one session.
It keeps a single authenticated connection open, tracks your position in both
the cloud and local filesystem, and offers tab completion on both sides.

```bash
cligoo shell
```

```text
[~/Downloads] degoo:/Web/Photos
❯ ls                          # list current Degoo folder
❯ cd 2024                     # navigate into a subfolder
❯ upload ~/Desktop/trip.jpg   # upload a local file here
❯ download holiday.jpg .      # download to current local dir
❯ lcd ~/Pictures              # change local directory
❯ lls                         # list local directory
❯ tree                        # tree view of current Degoo folder
❯ exit
```

Tab completion works on Degoo paths (`cd`, `ls`, `download` source) and on
local paths (`upload`, `download` destination, `lcd`, `lls`).

---

## Authentication

Run `cligoo config` once to choose and save your preferred login method. Bare
`cligoo login` then uses that choice automatically.

### Email/password accounts

```bash
cligoo config   # choose "Password" when prompted
cligoo login    # first time: prompts for email and password; thereafter: silent
```

After a successful login, both the **access token** and your **email + password** are stored in the system keyring (macOS Keychain, GNOME Keyring, Windows Credential Manager) — never in `config.toml`. A plaintext-file fallback (`~/.config/cligoo/credentials.json`, mode 600) is used only when the keyring is unavailable.

Stored credentials enable fully transparent operation:

- **No re-login needed**: once credentials are stored, `cligoo login` with no flags runs silently — no prompts. Only pass `--email` or `--password` explicitly when you want to change accounts or override the stored value.
- **Auto-relogin on any command**: when the short-lived access token expires (~1 hour), any `cligoo` command (not just `cligoo login`) silently re-authenticates using the stored password and carries on. You will never be asked to log in mid-workflow.
- **Rate-limit protection**: Degoo limits how often you can call the login endpoint. If a `429 Too Many Requests` response is received, cligoo automatically backs off locally for 15 minutes — no further login attempts are made to Degoo during that window, preventing the rate limit from compounding. You will see a countdown (`please wait 14m 32s`) instead of a network error.

Run `cligoo logout` to remove all stored tokens and credentials from the keyring.

> **Upgrading from degoo-cli?** Credentials and tokens previously saved under the `degoo-cli` keyring service are detected and migrated to `cligoo` automatically on first use — no manual steps needed.
>
> **Account created via Google / Apple / Facebook?** Your account has no
> password set. You can create one (or reset a forgotten one) at
> <https://degoo.com/forgotpassword> — enter your Degoo email and follow the
> link in the confirmation email. After setting a password, `cligoo login` with
> the password method works normally.

### Google OAuth accounts

```bash
make install-browser   # one-time — installs Playwright (~130 MB Chromium)
cligoo config           # choose "Browser" when prompted
cligoo login            # opens Chrome, captures token automatically
```

Chrome closes once the token is captured. The refresh token is saved so
subsequent renewals happen silently. If system Chrome is not installed,
Playwright-managed Chromium is used as a fallback.

Configure a Chrome profile with `cligoo config` to carry over an existing
Google/Degoo session, making login fully automatic with no user interaction.

### Store a token directly

```bash
cligoo token <ACCESS_JWT> [--refresh <REFRESH_JWT>]
```

Paste a token extracted manually from Chrome DevTools:

1. Open [app.degoo.com](https://app.degoo.com) and sign in.
2. Open DevTools → Network tab (`F12` / `⌥⌘I`).
3. Click any request to `production-appsync.degoo.com`.
4. Open the **Payload** tab and copy the `"Token"` value.
5. Run `cligoo token <paste-token-here>`.

> Access tokens expire in ~1 hour. Save a refresh token (`--refresh`) to
> enable silent renewal. Run `cligoo whoami` to verify your session.

---

## Commands

All path arguments accept Degoo paths (e.g. `/Web/folder`) or numeric item IDs unless noted.

### Account

| Command | Description |
| --- | --- |
| `cligoo login` | Log in (auto-uses configured method) |
| `cligoo login --browser` | Google OAuth via Chrome |
| `cligoo logout` | Clear stored credentials |
| `cligoo token <JWT> [--refresh <JWT>]` | Store tokens directly |
| `cligoo whoami` | Account info and quota table |
| `cligoo quota` | Compact usage summary |
| `cligoo config` | Interactive setup wizard |

### Browsing

| Command | Description |
| --- | --- |
| `cligoo ls [PATH]` | List directory (`-l` long, `-S` size sort, `-t` time sort, `-r` reverse, `-R` recursive, `-d N` depth) |
| `cligoo tree [PATH]` | Tree view (`-d N` depth, `-n N` limit) |
| `cligoo info <PATH\|ID>` | Full metadata for a file or folder (ID, size, dates, URL) |
| `cligoo search <TERM> [-n N]` | Search by name |
| `cligoo cd [PATH]` | Set working directory (persisted across sessions) |
| `cligoo pwd` | Show saved working directory |

### File management

| Command | Description |
| --- | --- |
| `cligoo mkdir <PATH>` | Create folder |
| `cligoo upload <FILE...> [-t DEST] [--name NAME] [-r] [--exclude PAT] [--workers N]` | Upload one or more files/dirs; parallel transfers; `-r` for directories |
| `cligoo download <ITEM...> [-t DEST] [--name NAME] [-r] [--skip-existing] [--workers N]` | Download one or more files/folders; parallel transfers; `-r` for folders |
| `cligoo mv <SRC> <DEST>` | Move/rename (Unix semantics: dest exists → move inside; dest absent → rename) |
| `cligoo cp <SRC> <DEST_FOLDER>` | Copy item into an existing folder |
| `cligoo rename <PATH\|ID> <NEW_NAME>` | Rename item |
| `cligoo rm <PATH\|ID>...` | Move to recycle bin (`-r` required for directories; `--permanent` to skip bin) |

### Sharing & activity

| Command | Description |
| --- | --- |
| `cligoo trash [-n N]` | List recycle bin |
| `cligoo empty-trash` | Permanently delete every item in the bin (double confirmation) |
| `cligoo shared [-l]` | List shared items (`-l` shows who each item is shared with) |
| `cligoo share <PATH\|ID> [USER...]` | Share an item |
| `cligoo unshare <PATH\|ID>` | Remove sharing |
| `cligoo feed [-n N]` | Show the upload feed (last N items, default 30) |
| `cligoo feed --watch [-i SEC]` | Live-monitor new uploads from any client (polls every SEC seconds, default 30) |

---

## Interactive Shell

```bash
cligoo shell
```

Drops into a REPL with a dual-context prompt:

```text
[~/local/dir] cligoo:/degoo/path
❯
```

**Degoo commands:** `ls`, `ll` (alias for `ls -l`), `cd`, `pwd`, `tree`, `upload <local>`, `download <degoo> [local]`

**Local commands:** `lcd`, `lpwd`, `lls`

**Everything else** is forwarded to the system shell. Tab completion works for Degoo paths on `cd`, `ls`, `ll`, `tree`, and the first argument of `download`; local paths complete on `upload`, the second argument of `download`, `lcd`, and `lls`. Exit with `exit`, `quit`, or Ctrl-D.

---

## Project Structure

```text
src/cligoo/
  api.py         GraphQL API client
  auth.py        Authentication & token management
  chrome.py      Chrome installation & profile detection
  cli.py         Click CLI commands
  config.py      Read/write ~/.config/cligoo/config.toml
  constants.py   Endpoints, categories, config paths
  queries.py     GraphQL query/mutation strings
  shell.py       Interactive Degoo shell REPL
```

---

## Documentation

| Document | Description |
| --- | --- |
| [`docs/CLI_USAGE.md`](docs/CLI_USAGE.md) | Full command reference, flag descriptions, config schema, and shell usage |
| [`docs/API_INTERNALS.md`](docs/API_INTERNALS.md) | GraphQL schema, authentication flow, client internals, and implementation pitfalls |
| [`docs/TESTING.md`](docs/TESTING.md) | Test suite overview, how to run tests, and guide for adding new tests |
| [`docs/PLAYWRIGHT_COMPARISON.md`](docs/PLAYWRIGHT_COMPARISON.md) | Comparison of Playwright browser-login approaches and trade-offs |

---

## API Endpoints

| Purpose | URL |
| --- | --- |
| GraphQL | `https://production-appsync.degoo.com/graphql` |
| REST login | `https://rest-api.degoo.com/login` |
| Token refresh | `https://rest-api.degoo.com/access-token/v2` |

---

## Disclaimer

cligoo is an **independent, community-maintained project** and is not affiliated with,
associated with, authorized by, endorsed by, or in any way officially connected with
**Degoo AB** or the [Degoo](https://degoo.com) cloud storage service.

The name "Degoo" and all related logos, product names, and trademarks are the exclusive
property of their respective owners. Any reference to Degoo in this project is solely for the
purpose of identifying the third-party service that this tool is designed to work with.

This tool interacts with the same public GraphQL API that the official Degoo web application
uses. No private, internal, or undisclosed interface is accessed. Use of this tool is subject
to [Degoo's Terms of Service](https://degoo.com/terms-of-service).

This software is provided "as is", without warranty of any kind. The author(s) accept no
liability for any consequences arising from its use.

---

## Credits

API structure discovered by reverse-engineering the Degoo web application. Informed by community work at [bernd-wechner/Degoo](https://github.com/bernd-wechner/Degoo).

## License

MIT
