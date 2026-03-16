"""Comprehensive tests for upload and download behaviour.

All tests run offline: DegooClient is mocked at the _client() level so no
network I/O occurs.  Where api-level helpers are tested directly a plain
MagicMock is used.

Scenarios covered
-----------------
Upload
  1.  Single file → CWD                            (success)
  2.  Single file → explicit --dest                 (success)
  3.  Single file with --name override              (success)
  4.  Single file → hard API error                  (exit 1, failed)
  5.  Directory without -r                          (exit 1, helpful message)
  6.  Recursive dir → mkdir called, upload per file (success)
  7.  Multi-file                                    (upload called N times)
  8.  --exclude glob skips matching files           (excluded not uploaded)
  9.  Storage-rejected file (HTTP 4xx)              (skipped, exit 0)
  10. Dedup: file already in folder                 (skipped, exit 0)
  11. Dedup: file NOT yet in folder                 (upload raises DegooAlreadyExistsError →
                                                     still counted as skipped in this scenario
                                                     because api.upload re-raises)
  12. Folder already exists (mkdir → Invalid input!) → reuse ID (success)
  13. Folder exists + all files exist               (0 uploaded, N skipped, exit 0)
  14. Nested recursive dirs                         (mkdir called for each sub-dir)
  15. Mixed success + skip                          (exit 0, summary "X uploaded, Y skipped")
  16. Mixed success + failure                       (exit 1)
  17. Non-existent source file                      (exit 1)
  18. --workers accepted                            (no error)

Download
  1.  By numeric ID                                 (success)
  2.  By path                                       (success)
  3.  With --name override                          (success)
  4.  API error                                     (exit 1)
  5.  Folder without -r                             (exit 1, helpful message)
  6.  Folder recursive                              (list_dir + download per file)
  7.  Multi-item                                    (download called N times)
  8.  --skip-existing omits existing local files    (only new files downloaded)
  9.  --overwrite passed to client.download         (overwrite=True in call)
  10. Numbered copy (default)                       (file saved as "name (1).ext")
  11. Cat=6 / no-URL item treated as folder         (requires -r)
  12. --workers accepted                            (no error)
  13. Non-existent remote path                      (exit 1)

api.py helpers
  _unique_local_path:  no existing file, single conflict, chained conflicts
  resolve_path_under:  returns Cat=2 folder when both Cat=6 ghost and Cat=2
                       folder share the same name
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cligoo.api import DegooAlreadyExistsError, DegooAPIError, DegooClient
from cligoo.cli import main

# ── Helpers ───────────────────────────────────────────────────────────────────


def _runner():
    return CliRunner()


def _mock_client(**kwargs):
    """MagicMock that looks like a DegooClient with sane defaults."""
    m = MagicMock(spec=DegooClient)
    m.is_folder.side_effect = DegooClient.is_folder
    m.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    m.resolve_path_under.return_value = {"ID": "222", "Name": "folder", "Category": 2}
    m.list_dir.return_value = []
    m.mkdir.return_value = "OK"
    m.upload.return_value = "OK"
    m.download.return_value = Path("/tmp/file.jpg")
    m.get_item.return_value = {
        "ID": "999",
        "Name": "photo.jpg",
        "Category": 3,
        "Size": "1000",
        "URL": "https://cdn.example.com/photo.jpg",
        "ParentID": "111",
    }
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def _patch_client(mock):
    return patch("cligoo.cli._client", return_value=mock)


def _cwd_patch(tmp_path: Path, path: str = "/Web"):
    cwd_file = tmp_path / "cwd.json"
    cwd_file.write_text(json.dumps({"path": path}))
    return patch("cligoo.cli._CWD_FILE", cwd_file)


# ═══════════════════════════════════════════════════════════════════════════════
# UPLOAD TESTS
# ═══════════════════════════════════════════════════════════════════════════════


# ── 1. Single file → CWD ──────────────────────────────────────────────────────


def test_upload_single_file_to_cwd(tmp_path):
    """Upload a single file; destination defaults to stored CWD."""
    f = tmp_path / "report.txt"
    f.write_text("hello")
    mock = _mock_client()
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(f)])
    assert result.exit_code == 0, result.output
    mock.upload.assert_called_once()
    assert "1 uploaded" in result.output


# ── 2. Single file → explicit --dest ──────────────────────────────────────────


def test_upload_single_file_explicit_dest(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00" * 64)
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(f), "--dest", "/Web"])
    assert result.exit_code == 0, result.output
    mock.upload.assert_called_once()


# ── 3. --name override ────────────────────────────────────────────────────────


def test_upload_name_override(tmp_path):
    """--name renames the uploaded file."""
    f = tmp_path / "original.txt"
    f.write_text("content")
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(f), "--dest", "/Web", "--name", "renamed.txt"])
    assert result.exit_code == 0, result.output
    # The name argument must have been forwarded to upload()
    _call = mock.upload.call_args
    assert _call.kwargs.get("name") == "renamed.txt" or (len(_call.args) > 2 and _call.args[2] == "renamed.txt")


# ── 4. Hard API error → exit 1 ────────────────────────────────────────────────


def test_upload_hard_api_error_exits_1(tmp_path):
    f = tmp_path / "fail.txt"
    f.write_text("x")
    mock = _mock_client()
    mock.upload.side_effect = DegooAPIError("server exploded")
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(f)])
    assert result.exit_code != 0


# ── 5. Directory without -r ───────────────────────────────────────────────────


def test_upload_directory_without_r_errors(tmp_path):
    d = tmp_path / "mydir"
    d.mkdir()
    (d / "file.txt").write_text("x")
    mock = _mock_client()
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(d)])
    assert result.exit_code != 0
    assert "directory" in result.output.lower() or "recursive" in result.output.lower()


# ── 6. Recursive dir: mkdir + upload per file ─────────────────────────────────


def test_upload_recursive_dir_calls_mkdir_and_upload(tmp_path):
    d = tmp_path / "holiday"
    d.mkdir()
    (d / "beach.jpg").write_bytes(b"\xff" * 100)
    (d / "sunset.jpg").write_bytes(b"\xfe" * 200)
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.resolve_path_under.return_value = {"ID": "222", "Name": "holiday", "Category": 2}
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(d), "--dest", "/Web", "-r"])
    assert result.exit_code == 0, result.output
    mock.mkdir.assert_called_once_with("holiday", "111")
    assert mock.upload.call_count == 2
    assert "2 uploaded" in result.output


# ── 7. Multi-file ──────────────────────────────────────────────────────────────


def test_upload_multi_file(tmp_path):
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.jpg"
    f3 = tmp_path / "c.jpg"
    for f in (f1, f2, f3):
        f.write_bytes(b"\xff" * 10)
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(f1), str(f2), str(f3), "--dest", "/Web"])
    assert result.exit_code == 0, result.output
    assert mock.upload.call_count == 3
    assert "3 uploaded" in result.output


# ── 8. --exclude glob skips matching files ────────────────────────────────────


def test_upload_exclude_pattern_skips_matching_files(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "main.py").write_text("code")
    (d / "temp.tmp").write_text("temp")
    (d / ".DS_Store").write_bytes(b"\x00")
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.resolve_path_under.return_value = {"ID": "222", "Name": "proj", "Category": 2}
    with _patch_client(mock):
        result = _runner().invoke(
            main,
            ["upload", str(d), "--dest", "/Web", "-r", "--exclude", "*.tmp", "--exclude", ".DS_Store"],
        )
    assert result.exit_code == 0, result.output
    assert mock.upload.call_count == 1  # only main.py


# ── 9. Storage-rejected file (HTTP 4xx) → skipped, exit 0 ────────────────────


def test_upload_storage_rejected_counted_as_skipped(tmp_path):
    """.DS_Store-style rejections show as 'skipped' and exit 0."""
    f = tmp_path / ".DS_Store"
    f.write_bytes(b"\x00" * 10)
    mock = _mock_client()
    mock.upload.side_effect = DegooAPIError("Upload to storage failed (HTTP 400): AccessDenied")
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(f)])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output


def test_upload_storage_rejected_403_counted_as_skipped(tmp_path):
    f = tmp_path / "restricted.bin"
    f.write_bytes(b"\x01")
    mock = _mock_client()
    mock.upload.side_effect = DegooAPIError("Upload to storage failed (HTTP 403): PolicyConditionFailed")
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(f)])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output


# ── 10. Dedup: file already in folder → skipped ───────────────────────────────


def test_upload_dedup_file_already_in_folder_is_skipped(tmp_path):
    """api.upload raises DegooAlreadyExistsError when file is already linked."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff" * 512)
    mock = _mock_client()
    mock.upload.side_effect = DegooAlreadyExistsError("Upload auth failed: Already exist!")
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(f)])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "failed" not in result.output


def test_upload_dedup_already_in_folder_not_counted_as_failure(tmp_path):
    """Re-uploading a folder of already-present files → all skipped, exit 0."""
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.jpg"
    f1.write_bytes(b"\xff" * 100)
    f2.write_bytes(b"\xfe" * 100)
    mock = _mock_client()
    mock.upload.side_effect = DegooAlreadyExistsError("Already exist!")
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(f1), str(f2), "--dest", "/Web"])
    assert result.exit_code == 0, result.output
    assert "2 skipped" in result.output
    assert "failed" not in result.output


# ── 12. Folder already exists (Invalid input!) → reuse ID ────────────────────


def test_upload_recursive_folder_already_exists_is_reused(tmp_path):
    """mkdir returns 'Invalid input!' → existing folder ID reused, files uploaded."""
    d = tmp_path / "Prints"
    d.mkdir()
    (d / "photo.jpg").write_bytes(b"\xff" * 100)
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    # mkdir fails with "Invalid input!" (folder already exists in Degoo)
    mock.mkdir.side_effect = DegooAPIError("Invalid input!")
    # resolve_path_under finds the existing Cat=2 folder
    mock.resolve_path_under.return_value = {"ID": "333", "Name": "Prints", "Category": 2}
    mock.upload.return_value = "OK"
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(d), "--dest", "/Web", "-r"])
    assert result.exit_code == 0, result.output
    # mkdir was attempted, resolve_path_under found the existing folder, upload proceeded
    mock.mkdir.assert_called_once()
    mock.upload.assert_called_once()


# ── 13. Folder + files already exist → all skipped, exit 0 ───────────────────


def test_upload_recursive_all_already_exist(tmp_path):
    d = tmp_path / "Prints"
    d.mkdir()
    (d / "a.jpg").write_bytes(b"\xff" * 100)
    (d / "b.jpg").write_bytes(b"\xfe" * 100)
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.mkdir.side_effect = DegooAPIError("Invalid input!")
    mock.resolve_path_under.return_value = {"ID": "333", "Name": "Prints", "Category": 2}
    mock.upload.side_effect = DegooAlreadyExistsError("Already exist!")
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(d), "--dest", "/Web", "-r"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "failed" not in result.output


# ── 14. Nested recursive dirs: mkdir called for each ─────────────────────────


def test_upload_recursive_nested_dirs_mkdir_per_subdir(tmp_path):
    """Three levels deep: mkdir called for each directory level."""
    root = tmp_path / "project"
    sub = root / "src"
    sub.mkdir(parents=True)
    (sub / "main.py").write_text("code")

    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    # resolve_path_under is called multiple times (once per dir + lazy resolver calls).
    # Use a callable side_effect that returns the right folder by name.
    _folders = {
        "project": {"ID": "200", "Name": "project", "Category": 2},
        "src": {"ID": "201", "Name": "src", "Category": 2},
    }
    mock.resolve_path_under.side_effect = lambda _parent, name: _folders.get(name)
    mock.upload.return_value = "OK"
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(root), "--dest", "/Web", "-r"])
    assert result.exit_code == 0, result.output
    # mkdir called for "project" and "src"
    assert mock.mkdir.call_count == 2


# ── 15. Mixed success + skip → exit 0 ────────────────────────────────────────


def test_upload_mixed_success_and_skip_exits_0(tmp_path):
    f_ok = tmp_path / "new.jpg"
    f_skip = tmp_path / "dup.jpg"
    f_ok.write_bytes(b"\xaa" * 100)
    f_skip.write_bytes(b"\xbb" * 100)
    mock = _mock_client()

    def _upload_side_effect(path, parent_id, name=None, progress_callback=None, **kw):
        if Path(path).name == "dup.jpg":
            raise DegooAlreadyExistsError("already in folder")
        return "OK"

    mock.upload.side_effect = _upload_side_effect
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(f_ok), str(f_skip), "--dest", "/Web"])
    assert result.exit_code == 0, result.output
    assert "1 uploaded" in result.output
    assert "1 skipped" in result.output


# ── 16. Mixed success + failure → exit 1 ──────────────────────────────────────


def test_upload_mixed_success_and_failure_exits_1(tmp_path):
    f_ok = tmp_path / "good.jpg"
    f_bad = tmp_path / "bad.jpg"
    f_ok.write_bytes(b"\xaa" * 100)
    f_bad.write_bytes(b"\xbb" * 100)
    mock = _mock_client()

    def _upload_side_effect(path, parent_id, name=None, progress_callback=None, **kw):
        if Path(path).name == "bad.jpg":
            raise DegooAPIError("server error")
        return "OK"

    mock.upload.side_effect = _upload_side_effect
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(f_ok), str(f_bad), "--dest", "/Web"])
    assert result.exit_code != 0
    assert "1 uploaded" in result.output
    assert "1 failed" in result.output


# ── 17. Non-existent source file → exit 1 ────────────────────────────────────


def test_upload_nonexistent_source_exits_1(tmp_path):
    mock = _mock_client()
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(tmp_path / "ghost.jpg")])
    assert result.exit_code != 0


# ── 18. --workers accepted ────────────────────────────────────────────────────


def test_upload_workers_option_accepted(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("content")
    mock = _mock_client()
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(f), "--dest", "/Web", "--workers", "5"])
    assert result.exit_code == 0, result.output


# ═══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD TESTS
# ═══════════════════════════════════════════════════════════════════════════════


# ── 1. By numeric ID ──────────────────────────────────────────────────────────


def test_download_by_id(tmp_path):
    mock = _mock_client()
    mock.download.return_value = tmp_path / "photo.jpg"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "999", "--dest", str(tmp_path)])
    assert result.exit_code == 0, result.output
    mock.download.assert_called_once()


# ── 2. By path ────────────────────────────────────────────────────────────────


def test_download_by_path(tmp_path):
    mock = _mock_client()
    file_item = {
        "ID": "999",
        "Name": "report.pdf",
        "Category": 6,
        "Size": "50000",
        "URL": "https://cdn.example.com/report.pdf",
    }
    mock.resolve_path.return_value = file_item
    mock.download.return_value = tmp_path / "report.pdf"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "/Web/report.pdf", "--dest", str(tmp_path)])
    assert result.exit_code == 0, result.output
    mock.download.assert_called_once()


# ── 3. --name override ────────────────────────────────────────────────────────


def test_download_name_override(tmp_path):
    mock = _mock_client()
    mock.download.return_value = tmp_path / "final.pdf"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "999", "--dest", str(tmp_path), "--name", "final.pdf"])
    assert result.exit_code == 0, result.output
    _call = mock.download.call_args
    # name kwarg should be "final.pdf"
    assert _call.kwargs.get("name") == "final.pdf" or "final.pdf" in str(_call)


# ── 4. API error → exit 1 ─────────────────────────────────────────────────────


def test_download_api_error_exits_1(tmp_path):
    mock = _mock_client()
    mock.download.side_effect = DegooAPIError("no download URL")
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "999", "--dest", str(tmp_path)])
    assert result.exit_code != 0


# ── 5. Folder without -r → exit 1 ────────────────────────────────────────────


def test_download_folder_without_r_errors(tmp_path):
    mock = _mock_client()
    mock.resolve_path.return_value = {
        "ID": "111",
        "Name": "Photos",
        "Category": 1,
        "Size": "0",
        "URL": "",
    }
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "/Web/Photos", "--dest", str(tmp_path)])
    assert result.exit_code != 0
    out = result.output.lower()
    assert "folder" in out or "recursive" in out


# ── 6. Folder recursive ───────────────────────────────────────────────────────


def test_download_folder_recursive(tmp_path):
    mock = _mock_client()
    folder = {"ID": "111", "Name": "Photos", "Category": 1, "Size": "0", "URL": ""}
    file1 = {"ID": "998", "Name": "a.jpg", "Category": 3, "Size": "1000", "URL": "https://x"}
    file2 = {"ID": "999", "Name": "b.jpg", "Category": 3, "Size": "1000", "URL": "https://y"}
    mock.resolve_path.return_value = folder
    mock.list_dir.return_value = [file1, file2]
    mock.download.side_effect = [
        tmp_path / "Photos" / "a.jpg",
        tmp_path / "Photos" / "b.jpg",
    ]
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "/Web/Photos", "--dest", str(tmp_path), "-r"])
    assert result.exit_code == 0, result.output
    assert mock.download.call_count == 2
    assert "2" in result.output


# ── 7. Multi-item ─────────────────────────────────────────────────────────────


def test_download_multi_item(tmp_path):
    mock = _mock_client()
    mock.download.return_value = tmp_path / "photo.jpg"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "111", "222", "333", "--dest", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert mock.download.call_count == 3
    assert "3" in result.output


# ── 8. --skip-existing omits existing local files ─────────────────────────────


def test_download_skip_existing_omits_present_files(tmp_path):
    mock = _mock_client()
    folder = {"ID": "111", "Name": "Photos", "Category": 1, "Size": "0", "URL": ""}
    old_file = {"ID": "998", "Name": "old.jpg", "Category": 3, "Size": "100", "URL": "https://x"}
    new_file = {"ID": "999", "Name": "new.jpg", "Category": 3, "Size": "100", "URL": "https://y"}

    # Pre-create local folder + existing file
    local_photos = tmp_path / "Photos"
    local_photos.mkdir()
    (local_photos / "old.jpg").write_bytes(b"exists")

    mock.resolve_path.return_value = folder
    mock.list_dir.return_value = [old_file, new_file]
    mock.download.return_value = local_photos / "new.jpg"

    with _patch_client(mock):
        result = _runner().invoke(
            main,
            ["download", "/Web/Photos", "--dest", str(tmp_path), "-r", "--skip-existing"],
        )
    assert result.exit_code == 0, result.output
    # Only new.jpg should be downloaded
    assert mock.download.call_count == 1
    assert mock.download.call_args[0][0] == "999"


# ── 9. --overwrite passed to client.download ──────────────────────────────────


def test_download_overwrite_flag_passed_to_client(tmp_path):
    mock = _mock_client()
    mock.download.return_value = tmp_path / "photo.jpg"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "999", "--dest", str(tmp_path), "--overwrite"])
    assert result.exit_code == 0, result.output
    _call = mock.download.call_args
    assert _call.kwargs.get("overwrite") is True


def test_download_no_overwrite_flag_defaults_false(tmp_path):
    """Without --overwrite, overwrite=False (numbered copy behaviour)."""
    mock = _mock_client()
    mock.download.return_value = tmp_path / "photo.jpg"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "999", "--dest", str(tmp_path)])
    assert result.exit_code == 0, result.output
    _call = mock.download.call_args
    assert _call.kwargs.get("overwrite") is False


# ── 10. Numbered copy: file saved as "name (1).ext" ──────────────────────────


def test_download_numbered_copy_when_file_exists(tmp_path):
    """_unique_local_path: when dest exists, returns stem (1).suffix."""
    existing = tmp_path / "report.pdf"
    existing.write_bytes(b"original")

    result = DegooClient._unique_local_path(tmp_path / "report.pdf")
    assert result == tmp_path / "report (1).pdf"
    assert not result.exists()  # the (1) copy should NOT exist yet


def test_download_numbered_copy_increments_when_n1_exists(tmp_path):
    """When both report.pdf and report (1).pdf exist, returns report (2).pdf."""
    (tmp_path / "report.pdf").write_bytes(b"original")
    (tmp_path / "report (1).pdf").write_bytes(b"first copy")

    result = DegooClient._unique_local_path(tmp_path / "report.pdf")
    assert result == tmp_path / "report (2).pdf"


def test_download_numbered_copy_no_conflict(tmp_path):
    """When destination does not exist, returns it unchanged."""
    path = tmp_path / "new_file.pdf"
    result = DegooClient._unique_local_path(path)
    assert result == path


# ── 11. Cat=6 / no-URL item treated as folder ────────────────────────────────


def test_download_cat6_no_url_requires_r(tmp_path):
    """Category=6 items without a URL are treated as folders; -r required."""
    mock = _mock_client()
    # Degoo API returns Cat=6 (Document) with no URL for some folder types
    mock.resolve_path.return_value = {
        "ID": "111",
        "Name": "Backup",
        "Category": 6,
        "Size": "0",
        "URL": "",
    }
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "/Web/Backup", "--dest", str(tmp_path)])
    assert result.exit_code != 0
    out = result.output.lower()
    assert "folder" in out or "recursive" in out


def test_download_cat6_no_url_recursive_lists_children(tmp_path):
    """Cat=6 folder with -r: list_dir is called to enumerate children."""
    mock = _mock_client()
    folder = {"ID": "111", "Name": "Backup", "Category": 6, "Size": "0", "URL": ""}
    child = {"ID": "998", "Name": "db.sql", "Category": 6, "Size": "5000", "URL": "https://cdn/db"}
    mock.resolve_path.return_value = folder
    mock.list_dir.return_value = [child]
    mock.download.return_value = tmp_path / "Backup" / "db.sql"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "/Web/Backup", "--dest", str(tmp_path), "-r"])
    assert result.exit_code == 0, result.output
    mock.list_dir.assert_called()
    mock.download.assert_called_once()


# ── 12. --workers accepted ────────────────────────────────────────────────────


def test_download_workers_option_accepted(tmp_path):
    mock = _mock_client()
    mock.download.return_value = tmp_path / "photo.jpg"
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "999", "--dest", str(tmp_path), "--workers", "3"])
    assert result.exit_code == 0, result.output


# ── 13. Non-existent remote path → exit 1 ────────────────────────────────────


def test_download_nonexistent_path_exits_1(tmp_path):
    mock = _mock_client()
    mock.resolve_path.return_value = None
    with _patch_client(mock):
        result = _runner().invoke(main, ["download", "/Web/ghost.jpg", "--dest", str(tmp_path)])
    assert result.exit_code != 0


# ═══════════════════════════════════════════════════════════════════════════════
# api.py helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestUniquLocalPath:
    """DegooClient._unique_local_path behaviour."""

    def test_no_conflict_returns_original(self, tmp_path):
        p = tmp_path / "file.txt"
        assert DegooClient._unique_local_path(p) == p

    def test_conflict_returns_n1(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        assert DegooClient._unique_local_path(tmp_path / "file.txt") == tmp_path / "file (1).txt"

    def test_conflict_n1_already_exists_returns_n2(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        (tmp_path / "file (1).txt").write_text("x")
        assert DegooClient._unique_local_path(tmp_path / "file.txt") == tmp_path / "file (2).txt"

    def test_no_extension_file(self, tmp_path):
        (tmp_path / "README").write_text("x")
        result = DegooClient._unique_local_path(tmp_path / "README")
        assert result == tmp_path / "README (1)"

    def test_dotfile(self, tmp_path):
        (tmp_path / ".env").write_text("x")
        result = DegooClient._unique_local_path(tmp_path / ".env")
        assert result == tmp_path / ".env (1)"

    def test_chain_of_five(self, tmp_path):
        for n in ["file.mp4", "file (1).mp4", "file (2).mp4", "file (3).mp4"]:
            (tmp_path / n).write_bytes(b"x")
        assert DegooClient._unique_local_path(tmp_path / "file.mp4") == tmp_path / "file (4).mp4"


class TestResolvePathUnder:
    """DegooClient.resolve_path_under prefers Cat=2 over Cat=6 ghosts."""

    def _client_with_listing(self, items):
        """Return a DegooClient whose list_dir always returns *items*."""
        client = MagicMock(spec=DegooClient)
        client.is_folder.side_effect = DegooClient.is_folder
        client.list_dir.return_value = items
        # Delegate to the real resolve_path_under implementation
        client.resolve_path_under.side_effect = lambda pid, name: DegooClient.resolve_path_under(client, pid, name)
        return client

    def test_returns_cat2_when_both_present(self):
        ghost = {"ID": "100", "Name": "Docs", "Category": 6, "Size": "0"}
        real_folder = {"ID": "101", "Name": "Docs", "Category": 2, "Size": "0"}
        client = self._client_with_listing([ghost, real_folder])
        result = client.resolve_path_under("0", "Docs")
        assert result["ID"] == "101"
        assert result["Category"] == 2

    def test_returns_ghost_when_no_cat2_available(self):
        ghost = {"ID": "100", "Name": "Docs", "Category": 6, "Size": "0"}
        client = self._client_with_listing([ghost])
        result = client.resolve_path_under("0", "Docs")
        assert result["ID"] == "100"

    def test_returns_none_when_name_not_found(self):
        client = self._client_with_listing([{"ID": "100", "Name": "Other", "Category": 2}])
        result = client.resolve_path_under("0", "Missing")
        assert result is None

    def test_cat2_preferred_even_if_ghost_listed_first(self):
        items = [
            {"ID": "10", "Name": "X", "Category": 6},
            {"ID": "11", "Name": "X", "Category": 2},
        ]
        client = self._client_with_listing(items)
        result = client.resolve_path_under("0", "X")
        assert result["Category"] == 2


# ── upload exit-code summary edge cases ──────────────────────────────────────


def test_upload_only_skips_shows_warning_not_success(tmp_path):
    """When every file is skipped (no upload, no failure), show yellow ⚠ line."""
    f = tmp_path / "dup.jpg"
    f.write_bytes(b"\x01" * 50)
    mock = _mock_client()
    mock.upload.side_effect = DegooAlreadyExistsError("Already exist!")
    with _patch_client(mock), _cwd_patch(tmp_path):
        result = _runner().invoke(main, ["upload", str(f)])
    # Exit 0 (no failure), but output indicates skipped
    assert result.exit_code == 0
    assert "skipped" in result.output


def test_upload_no_files_to_upload(tmp_path):
    """An empty directory produces 'No files to upload.' and exit 0."""
    d = tmp_path / "empty"
    d.mkdir()
    mock = _mock_client()
    mock.resolve_path.return_value = {"ID": "111", "Name": "Web", "Category": 1, "URL": ""}
    mock.resolve_path_under.return_value = {"ID": "222", "Name": "empty", "Category": 2}
    with _patch_client(mock):
        result = _runner().invoke(main, ["upload", str(d), "--dest", "/Web", "-r"])
    assert result.exit_code == 0, result.output
    assert "no files" in result.output.lower()
