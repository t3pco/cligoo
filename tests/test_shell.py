"""Tests for cligoo.shell.DegooShell.

Covers:
- _complete_local(): glob.escape() prevents metacharacters from expanding
  into unintended directories.
- do_ls() / do_upload() / do_download() / do_lls(): subprocess return-code
  failures print a ✗ error message on stdout.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cligoo.shell import DegooShell

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def shell(tmp_path):
    """A DegooShell instance with a mock client and host_cwd = tmp_path."""
    client = MagicMock()
    sh = DegooShell(client=client, start_path="/Web")
    sh.host_cwd = str(tmp_path)
    return sh, tmp_path


# ── _complete_local: glob.escape ──────────────────────────────────────────────


def test_complete_local_literal_text_matches_prefix(shell):
    """Normal text returns matching files under host_cwd."""
    sh, tmp_path = shell
    (tmp_path / "notes.txt").touch()
    (tmp_path / "node_modules").mkdir()

    results = sh._complete_local("no")
    names = {r.rstrip("/") for r in results}
    assert "notes.txt" in names
    assert "node_modules" in names


def test_complete_local_metachar_star_not_expanded(shell, tmp_path):
    """A literal '*' in the input must NOT glob-expand to everything."""
    sh, base = shell
    # Create a decoy directory that a raw * would match
    (base / "secret_dir").mkdir()

    results = sh._complete_local("*")
    # glob.escape turns '*' into '[*]'; '[*]*' matches only files literally
    # named '*', which don't exist — so results should be empty.
    assert results == [], f"Expected no matches for literal '*', got {results}"


def test_complete_local_metachar_bracket_not_expanded(shell, tmp_path):
    """A '[' in the input must not be treated as a character class."""
    sh, base = shell
    (base / "abc").mkdir()  # would match [abc] if unescaped

    results = sh._complete_local("[abc")
    assert results == [], f"Expected no matches for '[abc', got {results}"


def test_complete_local_double_star_not_expanded(shell, tmp_path):
    """'**' must not traverse into subdirectories."""
    sh, base = shell
    (base / "subdir").mkdir()
    (base / "subdir" / "deep.txt").touch()

    results = sh._complete_local("**")
    # No file is literally named '**' so results must be empty
    assert results == [], f"'**' should not traverse, got {results}"


def test_complete_local_empty_text_lists_cwd_contents(shell, tmp_path):
    """Empty text returns all entries under host_cwd."""
    sh, base = shell
    (base / "alpha.txt").touch()
    (base / "beta").mkdir()

    results = sh._complete_local("")
    # Paths are returned relative when text is relative (empty = relative)
    names = {r.rstrip("/") for r in results}
    assert "alpha.txt" in names
    assert "beta" in names


def test_complete_local_dirs_have_trailing_slash(shell, tmp_path):
    """Directory entries must end with '/' to signal further completion."""
    sh, base = shell
    (base / "mydir").mkdir()

    results = sh._complete_local("mydir")
    assert any(r.endswith("/") for r in results), f"Expected trailing '/' on dir, got {results}"


# ── Subprocess return-code error reporting ────────────────────────────────────


def _failing_run(*_args, **_kwargs):
    """Simulates a subprocess that exits with code 1."""
    result = MagicMock()
    result.returncode = 1
    return result


def _ok_run(*_args, **_kwargs):
    """Simulates a subprocess that exits cleanly."""
    result = MagicMock()
    result.returncode = 0
    return result


def test_do_ls_prints_error_on_failure(shell, capsys):
    sh, _ = shell
    with patch("cligoo.shell.subprocess.run", side_effect=_failing_run):
        sh.do_ls("/Web")
    captured = capsys.readouterr()
    assert "✗" in captured.out
    assert "1" in captured.out  # exit code appears in the message


def test_do_ls_no_error_on_success(shell, capsys):
    sh, _ = shell
    with patch("cligoo.shell.subprocess.run", side_effect=_ok_run):
        sh.do_ls("/Web")
    captured = capsys.readouterr()
    assert "✗" not in captured.out


def test_do_ls_glob_star_expands_all_items(shell, capsys):
    """ls * expands the glob and invokes degoo ls once per matching item."""
    sh, _ = shell
    sh.client.list_dir.return_value = [
        {"Name": "Photos", "Category": 1, "ID": "10"},
        {"Name": "Videos", "Category": 1, "ID": "11"},
        {"Name": "notes.txt", "Category": 5, "ID": "12"},
    ]
    sh.client.resolve_path.return_value = {"ID": "5", "Category": 1}

    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_ls("*")

    # Should have called degoo ls once per expanded match (3 items)
    assert len(calls) == 3
    joined_calls = [" ".join(c) for c in calls]
    assert any("Photos" in c for c in joined_calls)
    assert any("Videos" in c for c in joined_calls)
    assert any("notes.txt" in c for c in joined_calls)


def test_do_ls_glob_filtered_pattern(shell, capsys):
    """ls *.txt expands only matching items."""
    sh, _ = shell
    sh.client.list_dir.return_value = [
        {"Name": "notes.txt", "Category": 5, "ID": "12"},
        {"Name": "readme.txt", "Category": 5, "ID": "13"},
        {"Name": "photo.jpg", "Category": 6, "ID": "14"},
    ]
    sh.client.resolve_path.return_value = {"ID": "5", "Category": 1}

    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_ls("*.txt")

    assert len(calls) == 2
    joined_calls = [" ".join(c) for c in calls]
    assert any("notes.txt" in c for c in joined_calls)
    assert any("readme.txt" in c for c in joined_calls)
    assert not any("photo.jpg" in c for c in joined_calls)


def test_do_ls_glob_no_match_passes_literal_to_cli(shell, capsys):
    """ls *.xyz with no matches forwards the literal (unresolved) path to
    degoo ls, which will report 'Not found'.  _expand_degoo_globs keeps
    unmatched patterns verbatim so the CLI produces a meaningful error."""
    sh, _ = shell
    sh.client.list_dir.return_value = [
        {"Name": "notes.txt", "Category": 5, "ID": "12"},
    ]
    sh.client.resolve_path.return_value = {"ID": "5", "Category": 1}

    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 1  # degoo ls reports "Not found"
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_ls("*.xyz")

    # degoo ls must have been called (with the verbatim/resolved pattern)
    assert len(calls) == 1
    # The ✗ error from the non-zero exit code must be printed
    out = capsys.readouterr().out
    assert "✗" in out


def test_do_upload_prints_error_on_failure(shell, tmp_path, capsys):
    sh, base = shell
    local_file = base / "file.txt"
    local_file.write_text("data")

    with patch("cligoo.shell.subprocess.run", side_effect=_failing_run):
        sh.do_upload(str(local_file))
    captured = capsys.readouterr()
    assert "✗" in captured.out


def test_do_upload_no_error_on_success(shell, tmp_path, capsys):
    sh, base = shell
    local_file = base / "file.txt"
    local_file.write_text("data")

    with patch("cligoo.shell.subprocess.run", side_effect=_ok_run):
        sh.do_upload(str(local_file))
    captured = capsys.readouterr()
    assert "✗" not in captured.out


def _capture_run():
    """Return a capture_run helper and the calls list it populates."""
    calls: list[list[str]] = []

    def capture_run(cmd, **kw):
        calls.append(list(cmd))
        r = MagicMock()
        r.returncode = 0
        return r

    return capture_run, calls


# ── upload option-parsing regression tests ────────────────────────────────────


def test_do_upload_workers_not_treated_as_path(shell, tmp_path, capsys):
    """--workers 5 must be passed as an option, not resolved as a local path."""
    sh, base = shell
    local_dir = base / "mydir"
    local_dir.mkdir()

    capture_run, calls = _capture_run()
    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_upload(f"--workers 5 -r {local_dir}")

    out = capsys.readouterr().out
    assert "Local path not found" not in out, f"--workers treated as path: {out}"
    assert calls, "subprocess.run was not called"
    joined = " ".join(calls[0])
    assert "--workers" in joined
    assert "5" in joined
    assert str(local_dir) in joined


def test_do_upload_exclude_not_treated_as_path(shell, tmp_path, capsys):
    """--exclude *.tmp must be passed as an option, not resolved as a local path."""
    sh, base = shell
    local_dir = base / "mydir"
    local_dir.mkdir()

    capture_run, calls = _capture_run()
    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_upload(f"--exclude '*.tmp' -r {local_dir}")

    out = capsys.readouterr().out
    assert "Local path not found" not in out, f"--exclude treated as path: {out}"
    assert calls
    joined = " ".join(calls[0])
    assert "--exclude" in joined


def test_do_upload_workers_eq_form(shell, tmp_path, capsys):
    """--workers=5 (equals form) must also work without treating '5' as a path."""
    sh, base = shell
    local_file = base / "file.txt"
    local_file.write_text("data")

    capture_run, calls = _capture_run()
    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_upload(f"--workers=5 {local_file}")

    out = capsys.readouterr().out
    assert "Local path not found" not in out
    assert calls
    joined = " ".join(calls[0])
    assert "--workers=5" in joined


def test_do_upload_dest_not_added_when_user_provides_it(shell, tmp_path, capsys):
    """If user passes --dest explicitly, do_upload must not append another --dest."""
    sh, base = shell
    local_file = base / "file.txt"
    local_file.write_text("data")

    capture_run, calls = _capture_run()
    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_upload(f"--dest /Web/Custom {local_file}")

    assert calls
    joined = " ".join(calls[0])
    assert joined.count("--dest") == 1, f"--dest appeared more than once: {joined}"
    assert "/Web/Custom" in joined


# ── download option-parsing regression tests ─────────────────────────────────


def test_do_download_workers_not_treated_as_positional(shell, capsys):
    """--workers 5 must not end up as a positional arg (degoo path or local dest)."""
    sh, _ = shell

    capture_run, calls = _capture_run()
    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_download("--workers 5 /Web/photo.jpg")

    assert calls
    joined = " ".join(calls[0])
    assert "--workers" in joined
    assert "5" in joined
    assert "/Web/photo.jpg" in joined


def test_do_download_dest_not_duplicated(shell, capsys):
    """If user passes --dest explicitly, do_download must not append another one."""
    sh, _ = shell

    capture_run, calls = _capture_run()
    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_download("--dest /tmp /Web/photo.jpg")

    assert calls
    joined = " ".join(calls[0])
    assert joined.count("--dest") == 1, f"--dest appeared more than once: {joined}"


def test_do_download_prints_error_on_failure(shell, capsys):
    sh, _ = shell
    with patch("cligoo.shell.subprocess.run", side_effect=_failing_run):
        sh.do_download("/Web/photo.jpg")
    captured = capsys.readouterr()
    assert "✗" in captured.out


def test_do_download_no_error_on_success(shell, capsys):
    sh, _ = shell
    with patch("cligoo.shell.subprocess.run", side_effect=_ok_run):
        sh.do_download("/Web/photo.jpg")
    captured = capsys.readouterr()
    assert "✗" not in captured.out


def test_do_lls_prints_error_on_failure(shell, tmp_path, capsys):
    sh, base = shell
    with patch("cligoo.shell.subprocess.run", side_effect=_failing_run):
        sh.do_lls("")
    captured = capsys.readouterr()
    assert "✗" in captured.out


# ── New file-management shell commands ────────────────────────────────────────


def test_do_info_calls_degoo_info(shell, capsys):
    """do_info resolves the path and calls degoo info."""
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_info("photo.jpg")

    assert any("info" in c for c in calls), f"expected 'degoo info', got {calls}"
    assert any("photo.jpg" in " ".join(c) for c in calls)


def test_do_info_no_arg_prints_usage(shell, capsys):
    sh, _ = shell
    sh.do_info("")
    out = capsys.readouterr().out
    assert "Usage" in out


def test_do_search_calls_degoo_search(shell, capsys):
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_search("holiday")

    assert any("search" in c for c in calls)
    assert any("holiday" in " ".join(c) for c in calls)


def test_do_mkdir_calls_degoo_mkdir(shell, capsys):
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_mkdir("NewFolder")

    assert any("mkdir" in c for c in calls)
    assert any("NewFolder" in " ".join(c) for c in calls)


def test_do_mv_calls_degoo_mv(shell, capsys):
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_mv("OldName NewName")

    assert any("mv" in c for c in calls)


def test_do_cp_calls_degoo_cp(shell, capsys):
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_cp("file.jpg /Web/Archive")

    assert any("cp" in c for c in calls)


def test_do_rename_calls_degoo_rename(shell, capsys):
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_rename("old.jpg new.jpg")

    assert any("rename" in c for c in calls)


def test_do_rm_calls_degoo_rm(shell, capsys):
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_rm("file.jpg")

    assert any("rm" in c for c in calls)


def test_do_rm_glob_expands_matches(shell, capsys):
    """do_rm expands glob patterns against the live Degoo listing."""
    sh, _ = shell

    # Arrange: list_dir returns two matching folders + one non-matching
    sh.client.list_dir.return_value = [
        {"Name": "cligoo-regression-1", "Category": 1, "ID": "10"},
        {"Name": "cligoo-regression-2", "Category": 1, "ID": "11"},
        {"Name": "other-folder", "Category": 1, "ID": "12"},
    ]
    # resolve_path returns something for the cwd
    sh.client.resolve_path.return_value = {"ID": "5", "Category": 1}

    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_rm("-r cligoo-regression-*")

    assert calls, "subprocess.run should have been called"
    rm_cmd = calls[0]
    # Both matching paths must be in the rm command; the non-matching one must not
    joined = " ".join(rm_cmd)
    assert "cligoo-regression-1" in joined
    assert "cligoo-regression-2" in joined
    assert "other-folder" not in joined


# ── trash / empty-trash shell commands ────────────────────────────────────────


def test_do_trash_calls_degoo_trash(shell, capsys):
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_trash("")

    assert any("trash" in c for c in calls)


def test_do_empty_trash_calls_degoo_empty_trash(shell, capsys):
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.do_empty_trash("")

    assert any("empty-trash" in " ".join(c) for c in calls)


def test_default_routes_empty_trash_command(shell, capsys):
    """'empty-trash' typed at the shell prompt must route to do_empty_trash."""
    sh, _ = shell
    calls = []

    def capture_run(cmd, **kw):
        calls.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=capture_run):
        sh.default("empty-trash")

    assert any("empty-trash" in " ".join(c) for c in calls)


# ── Completion cache ───────────────────────────────────────────────────────────


def test_list_dir_cached_returns_cached_result(shell):
    """Second _list_dir_cached call within TTL must not hit the API again."""
    sh, _ = shell
    sh.client.list_dir.return_value = [{"Name": "Photos", "Category": 1, "ID": "5"}]

    first = sh._list_dir_cached("111")
    second = sh._list_dir_cached("111")

    assert first == second
    sh.client.list_dir.assert_called_once()  # only one API call


def test_list_dir_cached_refetches_after_expiry(shell):
    """Expired cache entry must trigger a new API call."""
    sh, _ = shell
    sh.client.list_dir.return_value = [{"Name": "Photos", "Category": 1, "ID": "5"}]

    sh._list_dir_cached("111")
    # Expire by back-dating the timestamp
    ts, data = sh._dir_cache["111"]
    sh._dir_cache["111"] = (ts - 100, data)  # older than TTL

    sh._list_dir_cached("111")
    assert sh.client.list_dir.call_count == 2


def test_invalidate_cache_clears_both_caches(shell):
    """_invalidate_cache must empty both _dir_cache and _path_cache."""
    sh, _ = shell
    sh._dir_cache["0"] = (0.0, [])
    sh._path_cache["/Web"] = (0.0, {"ID": "1"})

    sh._invalidate_cache()

    assert sh._dir_cache == {}
    assert sh._path_cache == {}


def test_mutating_command_invalidates_cache(shell):
    """A successful rm must clear the cache so the next Tab reflects reality."""
    sh, _ = shell
    sh._dir_cache["0"] = (999999.0, [{"Name": "old", "Category": 1, "ID": "9"}])

    def ok_run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("cligoo.shell.subprocess.run", side_effect=ok_run):
        sh.do_rm("old")

    assert sh._dir_cache == {}, "cache should be cleared after successful rm"
