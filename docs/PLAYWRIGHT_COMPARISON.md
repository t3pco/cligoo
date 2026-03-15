# OAuth Token Capture: Python `playwright` vs `@playwright/cli` (Node.js)

> **Audience**: developers evaluating whether to replace or supplement the current
> `degoo login --browser` implementation with a Node.js-based approach.
>
> **Research note**: `@playwright/cli` v0.1.1 was installed and all subcommands
> were exercised to determine actual capabilities. Findings below are based on
> direct empirical testing, not documentation assumptions.

---

## Table of Contents

- [Background](#background)
- [1. What each tool is](#1-what-each-tool-is)
  - [Current implementation — Python `playwright`](#current-implementation--python-playwright)
  - [Alternative — `@playwright/cli` v0.1.1](#alternative--playwrightcli-v011)
- [2. Network interception capabilities (empirically verified)](#2-network-interception-capabilities-empirically-verified)
  - [2a. `playwright-cli network` subcommand](#2a-playwright-cli-network-subcommand)
  - [2b. `playwright-cli run-code` — arbitrary Playwright scripts](#2b-playwright-cli-run-code--arbitrary-playwright-scripts)
  - [2c. `playwright-cli route` / `route-list` / `unroute` — request mocking](#2c-playwright-cli-route--route-list--unroute--request-mocking)
  - [2d. Tracing](#2d-tracing-tracing-start--tracing-stop)
  - [2e. HAR recording](#2e-har-recording)
  - [2f. Cookie access](#2f-cookie-access)
- [3. Capability matrix for OAuth token capture](#3-capability-matrix-for-oauth-token-capture)
- [4. End-user workflow comparison](#4-end-user-workflow-comparison)
  - [4a. Current: Python `playwright`](#4a-current-python-playwright)
  - [4b. Alternative: `@playwright/cli` + shell orchestration](#4b-alternative-playwrightcli--shell-orchestration)
- [5. Why `playwright codegen` is not relevant here](#5-why-playwright-codegen-is-not-relevant-here)
- [6. Architectural verdict](#6-architectural-verdict)
  - [When `@playwright/cli` would be the right choice](#when-playwrightcli-would-be-the-right-choice)
- [7. Installation reference](#7-installation-reference)
- [8. References](#8-references)

---

## Background

`degoo login --browser` uses the Python Playwright library to launch a headed
Chrome window, wait for the user to complete the Google OAuth dance, and capture
the resulting JWT from the first authenticated GraphQL request body. This document
compares that approach to using `@playwright/cli` (a separate CLI-first browser
control tool, distinct from the standard `playwright` npm package) for the same
purpose.

---

## 1. What each tool is

### Current implementation — Python `playwright`

The `playwright` PyPI package is the official Python binding to the Playwright
browser automation framework. It runs inside the same Python process as the CLI.

**Key facts:**

- Package: [`playwright`](https://pypi.org/project/playwright/) on PyPI
- Install: `pip install playwright` (+ `playwright install chromium` for managed browser)
- Runtime: CPython 3.8+; **no Node.js dependency**
- API: `sync_playwright()` / `async_playwright()` context managers, in-process
- Browser launch: `chromium.launch_persistent_context()` with headless/headed mode
- Network interception: `page.on("request", handler)` — full access to POST bodies
- Cookie access: `page.context.cookies()` via Chrome DevTools Protocol (CDP)

### Alternative — `@playwright/cli` v0.1.1

`@playwright/cli` is a **session-oriented CLI tool** that controls a persistent
browser session from the command line. It is architecturally distinct from the
`playwright` npm package used in test suites (`npx playwright test`).

**Key facts:**

- Available via Homebrew (`brew install playwright-cli`) or npm
- Version confirmed available: **0.1.1**
- Runtime: Node.js + a running browser session managed by the tool
- Model: stateful session (open → interact → close), unlike the Python library's
  in-process model
- Subcommands: 50+ covering navigation, clicks, keyboard, mouse, tabs, storage,
  network routing, tracing, screenshots, PDF, cookie management, and more
- Scripts: `run-code` subcommand executes arbitrary `async (page) => { ... }` JS
  and returns JSON-serialised output to stdout

---

## 2. Network interception capabilities (empirically verified)

This is the critical capability for JWT/token capture.

### 2a. `playwright-cli network` subcommand

The `network` command **does exist** but is very shallow. It captures and
displays only three fields per request:

```json
[GET] https://example.com/api/data => [200] OK
[POST] https://production-appsync.degoo.com/graphql => [200] OK
```

It logs `[METHOD] URL => [STATUS] statusText`.

**It does NOT capture request bodies, POST payloads, response bodies, or
request headers.** The implementation literally calls `request.method()`,
`request.url()`, and `response.status()` — nothing more.

**Verdict for token capture**: ❌ Cannot extract the JWT from a GraphQL POST body.

### 2b. `playwright-cli run-code` — arbitrary Playwright scripts

`run-code` accepts a JavaScript string containing an `async (page) => { ... }`
function, executes it in the context of the current page, and JSON-serialises
the return value to stdout. This is the only mechanism to access full POST bodies:

```bash
# Hypothetical token capture via run-code
playwright-cli run-code "async page => {
  return new Promise(resolve => {
    page.on('request', request => {
      if (!request.url().includes('appsync')) return;
      try {
        const body = JSON.parse(request.postData() || '{}');
        const tok = body?.variables?.Token;
        if (tok) resolve({ token: tok });
      } catch {}
    });
  });
}"
```

This *could* work, but:

- It requires a **pre-existing open session** — the browser must already be
  running and connected to `playwright-cli`
- The listener is installed *after* the page is open; a race condition exists
  if the token request fires during initial page load before the listener attaches
- It requires the caller to parse the JSON stdout and handle error cases
- The session management (open → navigate → run-code → close) requires a multi-step
  shell script or orchestration layer

### 2c. `playwright-cli route` / `route-list` / `unroute` — request mocking

These commands intercept requests at a URL pattern level and can substitute
responses (for mocking). They are designed for **response injection**, not for
passively reading request bodies. Capturing the JWT from outgoing requests is
not a natural fit.

### 2d. Tracing (`tracing-start` / `tracing-stop`)

Tracing captures **full network activity** including request/response headers
and bodies, stored in a trace bundle (zip archive). The trace can be viewed via
`npx playwright show-trace`, but it is a **binary format** — not usable for
real-time token extraction during an interactive login session.

### 2e. HAR recording

`--save-har` is **not exposed** as a CLI flag in `playwright-cli` v0.1.1.
The underlying `playwright-core` library supports HAR recording via its
Node.js API, but this is inaccessible from the CLI interface.

### 2f. Cookie access

The `cookie-list` / `cookie-get` subcommands provide cookie read access.
However, **HttpOnly cookies** — the kind Degoo uses for the refresh token —
are only accessible via the CDP layer that Playwright exposes through
`page.context.cookies()`. Whether `cookie-list` exposes HttpOnly cookies
through the CLI is untested; the Python `context.cookies()` CDP call reliably
does.

---

## 3. Capability matrix for OAuth token capture

| Feature | Python `playwright` | `@playwright/cli` v0.1.1 |
| --- | --- | --- |
| **Install** | `pip install playwright` | `brew install playwright-cli` |
| **Runtime dependency** | Python 3.8+ (already needed) | **Node.js + persistent browser session** |
| **In-process execution** | ✅ Same Python process as CLI | ❌ External process; shell orchestration needed |
| **System Chrome support** | ✅ `channel="chrome"` | ✅ via `open --channel chrome` |
| **Request body access** | ✅ `page.on("request")` → `request.post_data()` | ⚠️ Only via `run-code` with JS script |
| **`network` command** | N/A (library, not CLI) | ❌ Method+URL+status only — no bodies |
| **HttpOnly cookie access** | ✅ `context.cookies()` via CDP | ⚠️ `cookie-list` — CDP access unconfirmed |
| **HAR recording** | ❌ Not built-in (library call) | ❌ Not exposed as CLI flag |
| **Tracing** | ✅ Via library API | ✅ `tracing-start` / `tracing-stop` (binary format) |
| **JWT extraction logic** | ✅ Implemented in `auth.py` | ❌ Requires writing + passing a JS string |
| **Token saved to keyring** | ✅ Done inside `auth.py` | ❌ Must parse stdout + call Python keyring |
| **Single-command login** | ✅ `degoo login --browser` | ❌ 4-5 sequential CLI commands + JS string |
| **Anti-detection flags** | ✅ `ignore_default_args`, `args` | ✅ `--ignore-default-args`, `--args` flags |
| **5-min login timeout** | ✅ Handled in `auth.py` | ❌ Would need shell timeout + cleanup |

---

## 4. End-user workflow comparison

### 4a. Current: Python `playwright`

**Installation (first-time):**

```bash
pip install 'degoo-cli[browser]'
# System Chrome → nothing else needed.
# No Chrome? → playwright install chromium
```

**Login:**

```bash
degoo login --browser
# Chrome opens → user signs in → window closes → token saved. Done.
```

**What happens internally** (`degoo_cli/auth.py`, ~80 lines):

1. `sync_playwright()` opens system Chrome (or Playwright Chromium fallback)
2. `page.on("request", handler)` begins intercepting all outgoing requests
3. User completes Google OAuth at `app.degoo.com`
4. Handler extracts `variables.Token` from the first authenticated GraphQL POST
5. `context.cookies()` harvests the HttpOnly refresh token via CDP
6. Both tokens are saved to the system keyring
7. Browser closes automatically; `auth.py` returns

---

### 4b. Alternative: `@playwright/cli` + shell orchestration

**Installation (first-time):**

```bash
brew install playwright-cli
# Or: npm install -g @playwright/cli
playwright-cli install-browser chromium   # if no system Chrome
```

**Login would require a multi-step shell script** (no single command):

```bash
#!/usr/bin/env bash
# Hypothetical degoo-browser-login.sh

# 1. Open browser and navigate to Degoo
playwright-cli open --channel chrome  &
BROWSER_PID=$!
playwright-cli goto https://app.degoo.com

# 2. Install network interceptor via run-code (but user must be logged in first)
#    PROBLEM: this is a race — must attach BEFORE the user logs in
RESULT=$(playwright-cli run-code "async page => {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject('timeout'), 300000);
    page.on('request', request => {
      if (!request.url().includes('appsync')) return;
      try {
        const body = JSON.parse(request.postData() || '{}');
        const tok = body?.variables?.Token;
        if (tok) { clearTimeout(deadline); resolve({ token: tok }); }
      } catch {}
    });
  });
}")

# 3. Parse stdout, save token
TOKEN=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
# ... write to keyring via separate Python call ...

# 4. Close browser
playwright-cli close-all
```

**Problems with this approach:**

1. **Race condition**: `run-code` attaches the listener *after* the page has
   already loaded; the first GraphQL request may have already fired.
2. **Session coordination**: `open` and `run-code` are separate processes — session
   identity management is fragile across multiple CLI invocations.
3. **No atomic operation**: an interrupted flow can leave a browser session open
   with no cleanup.
4. **Token persistence**: requires a separate Python call to write to the keyring;
   the shell cannot call `keyring` without a Python helper.
5. **Error handling**: `playwright-cli` exits 0 on most errors; the shell script
   cannot distinguish timeout from success without parsing JSON output.

---

## 5. Why `playwright codegen` is not relevant here

`playwright codegen` (from the standard `npx playwright` tooling) records user
interactions and generates Playwright test code. It:

- Does not expose request POST bodies in its output
- Does not extract JWT tokens
- Does not save cookies to a keyring
- Is designed for generating test scripts, not for production auth flows

It is a developer convenience tool and is not a viable replacement for any part
of the token capture flow.

---

## 6. Architectural verdict

The current Python implementation (`auth.py: fetch_token_via_browser()`) is the
correct architecture for `degoo-cli`. The specific reasons, in priority order:

1. **No new runtime dependency.** Python 3.9 is already required. `@playwright/cli`
   requires Node.js 18+ to be installed by the user — a ~60–80 MB runtime with
   no other use in this project.

2. **`@playwright/cli` cannot capture POST bodies via a simple command.** The
   `network` subcommand only captures method/URL/status. JWT extraction requires
   `run-code` with an injected JS script, which still requires writing and
   maintaining JavaScript code embedded in a Python string or shell script.

3. **Race condition risk.** The `run-code` model attaches listeners to an
   already-open page. The Python library attaches the `page.on("request")`
   handler *before* navigation, ensuring no requests are missed.

4. **Single-process simplicity.** Python Playwright runs inside the CLI process.
   `@playwright/cli` requires at minimum two external processes (the browser
   session daemon and the CLI command) plus stdout parsing.

5. **Token persistence.** `auth.py` saves directly to the system keyring in the
   same process. The `@playwright/cli` approach requires piping stdout back to
   a Python invocation.

6. **Identical browser API with no capability gap.** The Python and Node.js
   Playwright APIs offer feature-identical capabilities for this use case.
   `@playwright/cli` adds no new capabilities that would improve the
   implementation.

### When `@playwright/cli` would be the right choice

- You need an interactive, scriptable browser session from a shell (not from Python)
- You are debugging a web application and want to issue browser commands interactively
- You want to mock network responses (`route` command) during manual testing
- Your codebase is already Node.js / TypeScript and you are writing test automation

---

## 7. Installation reference

### Python `playwright` (current — recommended)

```bash
# Option A: system Chrome (macOS/Windows/Linux with Chrome installed)
pip install 'degoo-cli[browser]'
degoo login --browser    # Chrome opens, closes automatically

# Option B: Playwright-managed Chromium (~130 MB download)
pip install 'degoo-cli[browser]'
playwright install chromium
degoo login --browser
```

### `@playwright/cli` (for reference)

```bash
# macOS via Homebrew
brew install playwright-cli

# Or via npm (Node.js 18+ required)
npm install -g @playwright/cli   # version 0.1.1

# Install a browser if no system Chrome
playwright-cli install-browser chromium

# Available subcommands (50+):
playwright-cli --help
```

---

## 8. References

- [Playwright Python docs](https://playwright.dev/python/docs/intro)
- [Playwright Node.js docs](https://playwright.dev/docs/intro)
- Current implementation: `src/degoo_cli/auth.py` — `fetch_token_via_browser()`
- `@playwright/cli` v0.1.1 subcommands verified empirically (2026-03)
