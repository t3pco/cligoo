# Testing Guide

## Table of Contents

- [Overview](#overview)
- [Running the tests](#running-the-tests)
- [Test suites](#test-suites)
  - [test_auth.py](#test_authpy)
  - [test_cli_unit.py](#test_cli_unitpy)
  - [test_config.py](#test_configpy)
  - [test_api_client.py](#test_api_clientpy)
  - [test_shell.py](#test_shellpy)
  - [test_constants.py](#test_constantspy)
  - [test_queries.py](#test_queriespy)
  - [integration/](#integration)
- [Design principles](#design-principles)
- [Adding new tests](#adding-new-tests)

---

## Overview

The test suite is split into **unit tests** (fast, fully offline, no credentials
needed) and **integration tests** (require valid Degoo credentials and hit the
live API).

| Suite | Files | Tests | Requires credentials |
| --- | --- | --- | --- |
| Unit | `tests/test_*.py` | 152 | No |
| Integration | `tests/integration/` | ~30 | Yes |

All unit tests run in under 60 seconds on a typical laptop.

---

## Running the tests

```bash
# All unit tests (default)
make test
# or directly:
.venv/bin/pytest tests/ -q

# A single test file
.venv/bin/pytest tests/test_config.py -v

# A single test by name
.venv/bin/pytest tests/test_config.py::test_save_config_atomic_rename -v

# Integration tests (live API — requires stored token)
.venv/bin/pytest -m integration -v

# All tests including integration
.venv/bin/pytest --run-integration -v
```

---

## Test suites

### test_auth.py

Tests for `cligoo.auth` — token validation, expiry detection, and the
`TokenStore` file-based fallback path.

| Test | What it covers |
| --- | --- |
| `test_token_not_expired` | JWT with a future expiry passes `is_token_expired()` |
| `test_token_expired` | JWT with a past expiry is detected |
| `test_token_expiring_within_margin` | Token within the 5-minute renewal margin is treated as expired |
| `test_token_invalid_string` | A non-JWT string is treated as expired |
| `test_token_empty` | Empty string is treated as expired |
| `test_token_store_save_load` | Round-trips a token through the file-based store |
| `test_token_store_clear` | `clear()` removes the stored token |
| `test_token_store_load_missing` | Loading from a non-existent file returns `(None, None)` |

---

### test_cli_unit.py

Tests for `cligoo.cli` — all CLI commands exercised via Click's
`CliRunner`. All external I/O (API, filesystem) is mocked.

Key areas covered:

- Auth commands: `token`, `logout`, `login` (password flow, browser flow, auto-select)
- Navigation: `pwd`, `cd`, `ls` (with all flags), `tree`, `info`, `search`
- Transfers: `upload`, `download`
- File operations: `mv`, `cp`, `rename`, `rm` (trash + permanent)
- Listing: `trash`, `shared`, `feed`
- Error handling: `AuthError` produces clean `✗` output; missing args show usage
- **`_client()` token passthrough** (regression guard): verifies `get_token()`
  is called exactly once and the fetched token is forwarded to
  `DegooClient(token=...)`, preventing the double-fetch bug.

---

### test_config.py

Tests for `cligoo.config` — configuration persistence and key resolution.

All tests use the `isolated_config` fixture, which redirects
`CONFIG_DIR` / `TOML_FILE` / `CONFIG_FILE` to a `tmp_path` so the developer's own
`~/.config/cligoo/config.toml` is never touched.

| Test | What it covers |
| --- | --- |
| `test_save_config_writes_correct_content` | Written JSON matches the dict passed in |
| `test_save_config_merges_without_clobbering` | A second `save_config()` call preserves keys from the first |
| `test_save_config_leaves_no_temp_files` | No `.config-*` orphan files after a successful write |
| `test_save_config_atomic_rename` | `os.replace()` is called (not `write_text`); temp file is in CONFIG_DIR |
| `test_get_api_key_from_env_var` | `DEGOO_API_KEY` env var is returned |
| `test_get_api_key_from_config_file` | Config file value returned when env var absent |
| `test_get_api_key_env_overrides_config` | Env var wins over config file |
| `test_get_api_key_returns_none_when_absent` | Returns `None` when neither is set |
| `test_get_api_key_returns_none_for_empty_string_in_config` | Empty string treated as absent |
| `test_get_login_method_browser` | `"browser"` is returned |
| `test_get_login_method_password` | `"password"` is returned |
| `test_get_login_method_invalid_returns_none` | Unknown value returns `None` |
| `test_get_login_method_absent_returns_none` | Absent key returns `None` |
| `test_get_chrome_profile_returns_value` | Profile name string is returned |
| `test_get_chrome_profile_null_returns_none` | JSON `null` returns `None` |
| `test_get_chrome_profile_absent_returns_none` | Absent key returns `None` |

---

### test_api_client.py

Tests for `cligoo.api.DegooClient` construction behaviour.

| Test | What it covers |
| --- | --- |
| `test_client_headers_are_independent_of_module_dict` | Mutating a client's HTTP headers does not change `DEFAULT_HEADERS` |
| `test_two_clients_have_independent_headers` | Mutating one client's headers does not affect a second client |
| `test_default_headers_unchanged_after_client_creation` | `DEFAULT_HEADERS` is bitwise-identical before and after construction |
| `test_token_passthrough_does_not_call_get_token` | `DegooClient(token=x).token == x` without calling `get_token()` |
| `test_token_fetched_lazily_when_not_supplied` | `get_token()` is called on the first `.token` access when no token is given |
| `test_token_fetched_only_once_across_multiple_accesses` | A second `.token` access reuses the cached value |

---

### test_shell.py

Tests for `cligoo.shell.DegooShell`.

**`_complete_local()` — glob metacharacter safety**

Verifies that `glob.escape(text)` is applied before constructing the glob
pattern, so user-typed metacharacters cannot traverse unintended directories.

| Test | Input | Expected |
| --- | --- | --- |
| `test_complete_local_literal_text_matches_prefix` | `"no"` | Matches `notes.txt`, `node_modules/` |
| `test_complete_local_metachar_star_not_expanded` | `"*"` | No matches (literal `*` matches nothing) |
| `test_complete_local_metachar_bracket_not_expanded` | `"[abc"` | No matches |
| `test_complete_local_double_star_not_expanded` | `"**"` | No matches (does not recurse) |
| `test_complete_local_empty_text_lists_cwd_contents` | `""` | All entries in `host_cwd` |
| `test_complete_local_dirs_have_trailing_slash` | `"mydir"` | Result ends with `/` |

#### Subprocess return-code error reporting

Verifies that a non-zero exit from a child `degoo` process causes the shell
to print a `✗` error message, and that a zero exit produces no error output.

| Test | Command | Scenario |
| --- | --- | --- |
| `test_do_ls_prints_error_on_failure` | `do_ls` | subprocess exits 1 → `✗` printed |
| `test_do_ls_no_error_on_success` | `do_ls` | subprocess exits 0 → no `✗` |
| `test_do_upload_prints_error_on_failure` | `do_upload` | subprocess exits 1 → `✗` printed |
| `test_do_upload_no_error_on_success` | `do_upload` | subprocess exits 0 → no `✗` |
| `test_do_download_prints_error_on_failure` | `do_download` | subprocess exits 1 → `✗` printed |
| `test_do_download_no_error_on_success` | `do_download` | subprocess exits 0 → no `✗` |
| `test_do_lls_prints_error_on_failure` | `do_lls` | subprocess exits 1 → `✗` printed |

---

### test_constants.py

Smoke tests for `cligoo.constants` — verifies that the bundled values have
the expected shape without requiring a live API connection.

| Test | What it covers |
| --- | --- |
| `test_graphql_url` | URL starts with `https://` and contains `graphql` |
| `test_login_url` | URL starts with `https://` and contains `login` |
| `test_token_refresh_url` | URL starts with `https://` and contains `access-token` |
| `test_api_key_format` | Bundled key starts with `da2-`; env-var override accepted |
| `test_default_headers` | `x-api-key` and `Content-Type` present |
| `test_category_names` | Spot-checks: `0 = File`, `2 = Folder`, `10 = Recycle Bin` |
| `test_folder_categories` | `1` and `2` are folder types; `0` is not |
| `test_checksum_seed` | 16-byte seed of integers |

---

### test_queries.py

Structural tests for `cligoo.queries` — verifies that every query/mutation
string is non-empty and contains the expected operation name.

Covers: `getUserInfo3`, `getFileChildren5`, `getOverlay4`, `getFeed`,
`getSearchContent3`, `getDeletedFiles`, `getBucketWriteAuth4`,
`getCollections5`, `getShared`, `getPermissions3`, `setUploadFile3`,
`setDeleteFile5`, `setMoveFile`, `setRenameFile`, `setShareFile`,
`setDeleteShareFile`, `setCollection2`, `setDescription`.

---

### integration/

Integration tests require a valid stored token (run `cligoo login` first).
They are excluded from the default test run and must be opted into explicitly:

```bash
.venv/bin/pytest -m integration -v
```

These tests exercise the full request/response cycle against the live Degoo
GraphQL API and are intended for validating API compatibility after a Degoo
backend update.

---

## Design principles

1. **No network in unit tests.** All HTTP calls are mocked via
   `unittest.mock.patch`. Tests that need a `DegooClient` receive a
   `MagicMock` via the `_mock_client()` / `_patch_client()` helpers in
   `test_cli_unit.py`.

2. **No touching the developer's home directory.** Config and token paths are
   redirected to `tmp_path` in every test that exercises file I/O.

3. **Isolation over cleverness.** Each test sets up its own state; shared
   state between tests is avoided. The `isolated_config` fixture in
   `test_config.py` and the `shell` fixture in `test_shell.py` are the
   primary examples.

4. **Regression guards document the bug.** Each test added for a specific bug
   fix (e.g. the `_client()` double-fetch, `glob.escape` omission) includes a
   docstring that names the original defect so future readers understand why
   the test exists.

---

## Adding new tests

- **For a new CLI command**: add a test in `test_cli_unit.py` following the
  `_mock_client` / `_patch_client` pattern.
- **For a new config key**: add tests in `test_config.py` using the
  `isolated_config` fixture to cover the happy path, absent key, and invalid
  value.
- **For a new API operation**: add a structural test in `test_queries.py`
  (checks the query string) and a unit test in `test_cli_unit.py` (checks the
  CLI command's output).
- **For a new shell command**: add tests in `test_shell.py` using the `shell`
  fixture; mock `subprocess.run` to test success and failure paths.
- After adding tests, always run `make lint` and `make test` to confirm
  everything passes.
