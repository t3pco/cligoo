from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

import pytest

from cligoo.constants import FOLDER_CATEGORIES
from cligoo.scheduler import _validate_cron_schedule, next_run
from cligoo.sync import SyncError, run_sync, scan_local_tree


class FakeDegooClient:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.children: dict[str, list[dict]] = {"0": []}
        self.next_id = 1
        self.events: list[tuple[str, str]] = []
        self.active_uploads = 0
        self.max_active_uploads = 0
        self._lock = threading.Lock()
        self.fail_upload = False
        self.fail_rename_on_call: int | None = None
        self.rename_calls = 0

    @staticmethod
    def is_folder(item: dict) -> bool:
        return item.get("Category", 0) in FOLDER_CATEGORIES

    def iter_dir(self, parent_id: str):
        yield from self.children.get(parent_id, [])

    def resolve_path_under(self, parent_id: str, name: str) -> dict | None:
        candidates = [item for item in self.children.get(parent_id, []) if item["Name"] == name]
        folders = [item for item in candidates if self.is_folder(item)]
        return folders[0] if folders else (candidates[0] if candidates else None)

    def mkdir(self, name: str, parent_id: str) -> str:
        item_id = str(self.next_id)
        self.next_id += 1
        item = {"ID": item_id, "Name": name, "Category": 2, "ParentID": parent_id, "Size": 0}
        self.items[item_id] = item
        self.children.setdefault(parent_id, []).append(item)
        self.children[item_id] = []
        self.events.append(("mkdir", name))
        return item_id

    def delete(self, item_ids: list[str], *, permanent: bool = False) -> str:
        del permanent
        for item_id in item_ids:
            item = self.items.pop(item_id)
            self.children[item["ParentID"]].remove(item)
            self.events.append(("delete", item["Name"]))
        return "OK"

    def rename(self, item_id: str, new_name: str) -> str:
        self.rename_calls += 1
        if self.rename_calls == self.fail_rename_on_call:
            raise RuntimeError("rename failed")
        item = self.items[item_id]
        item["Name"] = new_name
        self.events.append(("rename", new_name))
        return "OK"

    def upload(self, filepath: Path, parent_id: str, *, name: str | None = None) -> str:
        if self.fail_upload:
            raise RuntimeError("upload failed")
        with self._lock:
            self.active_uploads += 1
            self.max_active_uploads = max(self.max_active_uploads, self.active_uploads)
        try:
            time.sleep(0.02)
            item_id = str(self.next_id)
            self.next_id += 1
            item = {
                "ID": item_id,
                "Name": name or filepath.name,
                "Category": 0,
                "ParentID": parent_id,
                "Size": filepath.stat().st_size,
            }
            self.items[item_id] = item
            self.children.setdefault(parent_id, []).append(item)
            self.events.append(("upload", filepath.name))
            return item_id
        finally:
            with self._lock:
                self.active_uploads -= 1


def test_scan_local_tree_returns_relative_paths_and_fingerprints(tmp_path: Path):
    source = tmp_path / "source"
    nested = source / "one" / "two"
    nested.mkdir(parents=True)
    file_path = nested / "item.bin"
    file_path.write_bytes(b"content")

    files, directories = scan_local_tree(source)

    assert files["one/two/item.bin"].size == len(b"content")
    assert files["one/two/item.bin"].mtime_ns == file_path.stat().st_mtime_ns
    assert directories == {"one", "one/two"}


def test_run_sync_creates_remote_folders_and_uploads_with_delta_state(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "one" / "backup.bin"
    file_path.parent.mkdir()
    file_path.write_bytes(b"first")
    state_path = tmp_path / "state.sqlite3"
    client = FakeDegooClient()

    initial = run_sync(client, source, "/Backup", workers=2, state_path=state_path)
    assert initial.upload_files == 1
    assert initial.completed_files == 1
    assert client.events.count(("upload", "backup.bin")) == 1
    assert ("mkdir", "Backup") in client.events
    assert ("mkdir", "one") in client.events

    file_path.write_bytes(b"other")
    file_path.touch()
    changed = run_sync(client, source, "/Backup", workers=2, state_path=state_path)

    assert changed.update_files == 1
    assert changed.completed_files == 1
    assert [event[0] for event in client.events[-4:]] == ["upload", "rename", "rename", "delete"]
    assert sum(item["Name"] == "backup.bin" for item in client.items.values()) == 1

    unchanged = run_sync(client, source, "/Backup", workers=2, state_path=state_path)
    assert unchanged.unchanged_files == 1
    assert unchanged.completed_files == 0


def test_run_sync_dry_run_does_not_create_remote_items(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("content")
    client = FakeDegooClient()

    result = run_sync(
        client,
        source,
        "/Backup",
        workers=1,
        state_path=tmp_path / "state.sqlite3",
        dry_run=True,
    )

    assert result.dry_run
    assert result.upload_files == 1
    assert client.events == []


def test_run_sync_limits_upload_parallelism_to_workers(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    for index in range(8):
        (source / f"{index}.txt").write_text(str(index))
    client = FakeDegooClient()

    result = run_sync(
        client,
        source,
        "/Backup",
        workers=2,
        state_path=tmp_path / "state.sqlite3",
    )

    assert result.completed_files == 8
    assert client.max_active_uploads <= 2
    assert client.max_active_uploads > 1


def test_upload_logs_start_finish_and_runtime(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "file.txt"
    file_path.write_text("content")

    result = run_sync(
        FakeDegooClient(),
        source,
        "/Backup",
        workers=1,
        state_path=tmp_path / "state.sqlite3",
    )

    captured = capsys.readouterr()
    logs = (captured.out + captured.err).splitlines()
    start_line = next(line for line in logs if '"File upload started"' in line)
    finish_line = next(line for line in logs if '"File upload finished"' in line)
    assert json.dumps(str(file_path)) in start_line
    assert json.dumps(str(file_path)) in finish_line
    assert 'status="SUCCESS"' in finish_line
    assert "runtime_sec=" in finish_line
    assert result.completed_files == 1


def test_failed_upload_logs_failure_runtime(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "file.txt"
    file_path.write_text("content")
    client = FakeDegooClient()
    client.fail_upload = True

    with pytest.raises(SyncError, match="failed to upload"):
        run_sync(client, source, "/Backup", workers=1, state_path=tmp_path / "state.sqlite3")

    captured = capsys.readouterr()
    logs = (captured.out + captured.err).splitlines()
    start_line = next(line for line in logs if '"File upload started"' in line)
    failure_line = next(line for line in logs if '"File upload finished"' in line)
    assert json.dumps(str(file_path)) in start_line
    assert json.dumps(str(file_path)) in failure_line
    assert 'status="FAILED"' in failure_line
    assert "runtime_sec=" in failure_line
    assert "upload failed" in failure_line


def test_run_sync_rejects_nonpositive_worker_count(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ValueError, match="at least 1"):
        run_sync(
            FakeDegooClient(),
            source,
            "/Backup",
            workers=0,
            state_path=tmp_path / "state.sqlite3",
        )


def test_cron_schedule_validation():
    assert _validate_cron_schedule("0 1,13 * * *") == "0 1,13 * * *"
    with pytest.raises(ValueError, match="five-field"):
        _validate_cron_schedule("0 1 * *")
    with pytest.raises(ValueError, match="Invalid CRON_SCHEDULE"):
        _validate_cron_schedule("60 1 * * *")


def test_next_run_uses_cron_expression_and_timezone():
    now = dt.datetime(2026, 10, 4, 12, 30).astimezone()

    assert next_run(now, "0 1,13 * * *") == now.replace(hour=13, minute=0, second=0, microsecond=0)
    assert next_run(now.replace(hour=14), "0 1,13 * * *") == now.replace(
        day=5, hour=1, minute=0, second=0, microsecond=0
    )


def test_update_uploads_before_replacing_existing_remote_version(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "file.txt"
    file_path.write_text("old")
    client = FakeDegooClient()
    state_path = tmp_path / "state.sqlite3"

    run_sync(client, source, "/Backup", workers=1, state_path=state_path)
    file_path.write_text("newer")
    file_path.touch()
    original_id = next(item_id for item_id, item in client.items.items() if item["Name"] == "file.txt")
    client.events.clear()

    result = run_sync(client, source, "/Backup", workers=1, state_path=state_path)

    assert result.completed_files == 1
    assert client.events[0][0] == "upload"
    assert client.events[1][0] == "rename"
    assert client.events[2][0] == "rename"
    assert client.events[3] == ("delete", client.events[1][1])
    assert original_id not in client.items
    assert sum(item["Name"] == "file.txt" for item in client.items.values()) == 1


def test_failed_replacement_upload_keeps_existing_remote_file(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "file.txt"
    file_path.write_text("old")
    client = FakeDegooClient()
    state_path = tmp_path / "state.sqlite3"

    run_sync(client, source, "/Backup", workers=1, state_path=state_path)
    original = next(item for item in client.items.values() if item["Name"] == "file.txt")
    file_path.write_text("new")
    file_path.touch()
    client.fail_upload = True

    with pytest.raises(SyncError, match="failed to upload"):
        run_sync(client, source, "/Backup", workers=1, state_path=state_path)

    assert client.items[original["ID"]]["Name"] == "file.txt"


def test_failed_promotion_restores_existing_name_and_retains_old_content(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "file.txt"
    file_path.write_text("old")
    client = FakeDegooClient()
    state_path = tmp_path / "state.sqlite3"

    run_sync(client, source, "/Backup", workers=1, state_path=state_path)
    original = next(item for item in client.items.values() if item["Name"] == "file.txt")
    file_path.write_text("new")
    file_path.touch()
    client.fail_rename_on_call = client.rename_calls + 2

    with pytest.raises(SyncError, match="failed to upload"):
        run_sync(client, source, "/Backup", workers=1, state_path=state_path)

    assert client.items[original["ID"]]["Name"] == "file.txt"
