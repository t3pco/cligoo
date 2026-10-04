"""Delta backup from a local directory tree to a Degoo folder."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
import uuid
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from curl_cffi.requests.errors import RequestsError

from .api import DegooAPIError, DegooClient


@dataclass(frozen=True)
class LocalFile:
    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class RemoteFile:
    item_id: str
    size: int


@dataclass(frozen=True)
class SyncSummary:
    local_files: int
    remote_files: int
    unchanged_files: int
    upload_files: int
    update_files: int
    transfer_bytes: int
    completed_files: int
    failed_files: int
    dry_run: bool


class SyncError(RuntimeError):
    """Raised when a sync cannot safely complete."""


def _resolve_created_item(client: DegooClient, parent_id: str, name: str) -> dict | None:
    """Retry a lookup after mkdir because Degoo listings are eventually consistent."""
    for attempt in range(6):
        item = client.resolve_path_under(parent_id, name)
        if item is not None:
            return item
        if attempt < 5:
            delay = 5
            _log(
                "INFO",
                "New remote folder is not visible yet; retrying lookup",
                folder=name,
                parent_id=parent_id,
                attempt=attempt + 1,
                retry_in_sec=delay,
            )
            time.sleep(delay)
    return None


def _is_created_folder(client: DegooClient, item: dict) -> bool:
    """Accept Degoo's temporary Category=6 folder ghost returned after mkdir."""
    return client.is_folder(item) or _is_folder_ghost(item)


def _is_folder_ghost(item: dict) -> bool:
    """Identify Degoo's empty Category=6 placeholder created by mkdir."""
    try:
        size = int(item.get("Size") or 0)
    except (TypeError, ValueError):
        return False
    return str(item.get("Category")) == "6" and size == 0 and not item.get("URL")


class SyncState:
    """Persist source fingerprints between runs to detect same-size edits."""

    def __init__(self, database: Path, target: str) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database)
        self._target = target
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                target TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                PRIMARY KEY (target, relative_path)
            )
            """
        )
        self._connection.commit()

    def load(self) -> dict[str, tuple[int, int]]:
        rows = self._connection.execute(
            "SELECT relative_path, size, mtime_ns FROM sync_state WHERE target = ?",
            (self._target,),
        )
        return {relative_path: (size, mtime_ns) for relative_path, size, mtime_ns in rows}

    def save_many(self, files: Iterator[tuple[str, int, int]]) -> None:
        self._connection.executemany(
            """
            INSERT INTO sync_state (target, relative_path, size, mtime_ns)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(target, relative_path) DO UPDATE SET
                size = excluded.size,
                mtime_ns = excluded.mtime_ns
            """,
            ((self._target, relative_path, size, mtime_ns) for relative_path, size, mtime_ns in files),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


def _log(level: str, message: str, **fields: object) -> None:
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    suffix = " ".join(f"{key}={json.dumps(value, ensure_ascii=True)}" for key, value in fields.items())
    line = f"ts={json.dumps(timestamp)} level={level.upper()} msg={json.dumps(message)}"
    print(f"{line} {suffix}".rstrip(), file=sys.stderr if level.upper() == "ERROR" else sys.stdout, flush=True)


def scan_local_tree(root: Path) -> tuple[dict[str, LocalFile], set[str]]:
    """Scan regular files without following symlinks; fail on incomplete scans."""
    if not root.is_dir():
        raise SyncError(f"Local source is not a directory: {root}")

    files: dict[str, LocalFile] = {}
    directories: set[str] = set()
    pending = [root]

    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    try:
                        if entry.is_symlink():
                            _log("WARN", "Skipping symbolic link", path=str(entry_path))
                        elif entry.is_dir(follow_symlinks=False):
                            relative = entry_path.relative_to(root).as_posix()
                            directories.add(relative)
                            pending.append(entry_path)
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            relative = entry_path.relative_to(root).as_posix()
                            files[relative] = LocalFile(entry_path, stat.st_size, stat.st_mtime_ns)
                        else:
                            _log("WARN", "Skipping non-regular filesystem entry", path=str(entry_path))
                    except OSError as exc:
                        raise SyncError(f"Could not inspect local path {entry_path}: {exc}") from exc
        except OSError as exc:
            raise SyncError(f"Could not scan local directory {directory}: {exc}") from exc

    return files, directories


def ensure_remote_root(client: DegooClient, remote_path: str, *, create: bool) -> str | None:
    """Resolve or create every folder in a remote absolute path."""
    parts = [part for part in remote_path.strip("/").split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise SyncError(f"Remote target path cannot contain '.' or '..': {remote_path}")

    parent_id = "0"
    for part in parts:
        item = client.resolve_path_under(parent_id, part)
        if item is None:
            if not create:
                return None
            try:
                created_id = client.mkdir(part, parent_id)
            except DegooAPIError as exc:
                # An existing folder can be reported as an API error if another
                # process created it after our lookup; resolve it below.
                item = _resolve_created_item(client, parent_id, part)
                if item is None:
                    raise SyncError(f"Could not create remote folder {part!r}: {exc}") from exc
            else:
                if isinstance(created_id, str) and created_id.isdigit():
                    parent_id = created_id
                    continue
            if item is None:
                item = _resolve_created_item(client, parent_id, part)
        if item is None or not _is_created_folder(client, item):
            raise SyncError(f"Remote target component is not a folder: {part}")
        parent_id = str(item["ID"])

    return parent_id


def scan_remote_tree(client: DegooClient, root_folder_id: str) -> tuple[dict[str, RemoteFile], dict[str, str]]:
    """Walk the target folder recursively, streaming each folder's paginated items."""
    files: dict[str, RemoteFile] = {}
    folders = {"": root_folder_id}
    pending: deque[tuple[str, str]] = deque([(root_folder_id, "")])

    while pending:
        parent_id, parent_relative = pending.popleft()
        try:
            for item in client.iter_dir(parent_id):
                name = item.get("Name")
                if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
                    raise SyncError(f"Remote item has an invalid name in folder {parent_relative!r}")
                relative = f"{parent_relative}/{name}".lstrip("/")
                if relative in files or relative in folders:
                    raise SyncError(f"Multiple remote items map to the same path: {relative}")
                item_id = str(item["ID"])
                if client.is_folder(item) or _is_folder_ghost(item):
                    folders[relative] = item_id
                    pending.append((item_id, relative))
                else:
                    try:
                        size = int(item.get("Size") or 0)
                    except (TypeError, ValueError) as exc:
                        raise SyncError(f"Remote item has an invalid size: {relative}") from exc
                    files[relative] = RemoteFile(item_id, size)
        except DegooAPIError as exc:
            raise SyncError(f"Could not list remote folder {parent_relative or '/'}: {exc}") from exc

    return files, folders


def _fingerprint_matches(local: LocalFile, cached: tuple[int, int] | None) -> bool:
    return cached == (local.size, local.mtime_ns)


def _validate_workers(workers: int) -> None:
    if workers < 1:
        raise ValueError("workers must be at least 1")


def run_sync(
    client: DegooClient,
    local_root: Path,
    remote_path: str,
    *,
    workers: int,
    state_path: Path,
    dry_run: bool = False,
) -> SyncSummary:
    """Compare source and target, then upload new or changed files."""
    _validate_workers(workers)
    local_root = local_root.resolve()
    remote_path = "/" + remote_path.strip("/")
    if remote_path == "":
        remote_path = "/"

    started = time.monotonic()
    local_files, local_directories = scan_local_tree(local_root)
    _log("INFO", "Local scan complete", files=len(local_files), directories=len(local_directories))

    root_id = ensure_remote_root(client, remote_path, create=not dry_run)
    if root_id is None:
        remote_files: dict[str, RemoteFile] = {}
        remote_folders = {"": None}
    else:
        remote_files, remote_folders = scan_remote_tree(client, root_id)
    _log("INFO", "Remote scan complete", files=len(remote_files), folders=len(remote_folders))

    state_scope = json.dumps([str(local_root), remote_path], separators=(",", ":"))
    state = SyncState(state_path, state_scope)
    try:
        saved_fingerprints = state.load()
        unchanged: list[tuple[str, int, int]] = []
        upload_count = 0
        update_count = 0
        transfer_bytes = 0

        for relative, local in local_files.items():
            remote = remote_files.get(relative)
            cached = saved_fingerprints.get(relative)
            if remote is not None and remote.size == local.size:
                if cached is None or _fingerprint_matches(local, cached):
                    unchanged.append((relative, local.size, local.mtime_ns))
                    continue
            if remote is None:
                upload_count += 1
            else:
                update_count += 1
            transfer_bytes += local.size

        _log(
            "INFO",
            "Delta plan computed",
            unchanged_files=len(unchanged),
            upload_files=upload_count,
            update_files=update_count,
            transfer_bytes=transfer_bytes,
            dry_run=dry_run,
        )

        if dry_run:
            return SyncSummary(
                len(local_files),
                len(remote_files),
                len(unchanged),
                upload_count,
                update_count,
                transfer_bytes,
                0,
                0,
                True,
            )

        state.save_many(iter(unchanged))
        if root_id is None:
            root_id = ensure_remote_root(client, remote_path, create=True)
            if root_id is None:
                raise SyncError(f"Could not create remote target folder: {remote_path}")
            remote_files, remote_folders = scan_remote_tree(client, root_id)

        folder_map = dict(remote_folders)
        for relative in sorted(local_directories, key=lambda item: (item.count("/"), item)):
            if relative in folder_map:
                continue
            conflicting_file = remote_files.get(relative)
            if conflicting_file is not None:
                raise SyncError(
                    f"Remote file conflicts with local directory {relative!r} "
                    f"(item ID {conflicting_file.item_id}); refusing to replace it"
                )
            parent_relative, _, folder_name = relative.rpartition("/")
            parent_id = folder_map.get(parent_relative)
            if parent_id is None:
                raise SyncError(f"Remote parent folder is missing for {relative}")
            try:
                created_id = client.mkdir(folder_name, parent_id)
            except DegooAPIError as exc:
                existing = _resolve_created_item(client, parent_id, folder_name)
                if existing is None or not _is_created_folder(client, existing):
                    raise SyncError(f"Could not create remote folder {relative}: {exc}") from exc
            else:
                if isinstance(created_id, str) and created_id.isdigit():
                    folder_map[relative] = created_id
                    continue
            folder = _resolve_created_item(client, parent_id, folder_name)
            if folder is None or not _is_created_folder(client, folder):
                raise SyncError(f"Could not resolve newly created remote folder: {relative}")
            folder_map[relative] = str(folder["ID"])

        def pending_uploads() -> Iterator[tuple[str, LocalFile, RemoteFile | None, str]]:
            for relative, local in local_files.items():
                remote = remote_files.get(relative)
                cached = saved_fingerprints.get(relative)
                if remote is not None and remote.size == local.size:
                    if cached is None or _fingerprint_matches(local, cached):
                        continue
                parent_relative, _, _ = relative.rpartition("/")
                parent_id = folder_map.get(parent_relative)
                if parent_id is None:
                    raise SyncError(f"Remote parent folder is missing for {relative}")
                yield relative, local, remote, parent_id

        def upload_one(local: LocalFile, remote: RemoteFile | None, parent_id: str) -> None:
            upload_started = time.monotonic()
            _log(
                "INFO",
                "File upload started",
                path=str(local.path),
                size_bytes=local.size,
                action="replace" if remote is not None else "upload",
            )
            try:
                current = local.path.stat()
                if current.st_size != local.size or current.st_mtime_ns != local.mtime_ns:
                    raise SyncError(f"Local file changed after scanning; it will be retried next run: {local.path}")

                if remote is None:
                    client.upload(local.path, parent_id)
                else:
                    transaction_id = uuid.uuid4().hex
                    staging_name = f".cligoo-sync-new-{transaction_id}{local.path.suffix}"
                    backup_name = f".cligoo-sync-old-{transaction_id}"
                    staged_id = client.upload(local.path, parent_id, name=staging_name)
                    if not staged_id or staged_id == "OK" or not staged_id.isdigit():
                        staged_item = client.resolve_path_under(parent_id, staging_name)
                        if staged_item is None:
                            raise SyncError(f"Upload did not return an ID for staged file {local.path}")
                        staged_id = str(staged_item["ID"])

                    try:
                        client.rename(remote.item_id, backup_name)
                    except Exception as exc:
                        try:
                            client.delete([staged_id], permanent=False)
                        except Exception as cleanup_exc:
                            raise SyncError(
                                f"Could not stage replacement for {local.path}: {exc}; "
                                f"could not clean staged upload: {cleanup_exc}"
                            ) from exc
                        raise SyncError(f"Could not prepare existing remote file for replacement: {exc}") from exc

                    try:
                        client.rename(staged_id, local.path.name)
                    except Exception as exc:
                        try:
                            client.rename(remote.item_id, local.path.name)
                        except Exception as rollback_exc:
                            raise SyncError(
                                f"Could not promote replacement for {local.path}: {exc}; "
                                f"could not restore prior remote name {backup_name!r}: {rollback_exc}"
                            ) from exc
                        try:
                            client.delete([staged_id], permanent=False)
                        except Exception as cleanup_exc:
                            raise SyncError(
                                f"Could not promote replacement for {local.path}: {exc}; "
                                f"could not clean staged upload: {cleanup_exc}"
                            ) from exc
                        raise SyncError(f"Could not promote replacement for {local.path}: {exc}") from exc

                    try:
                        client.delete([remote.item_id], permanent=False)
                    except Exception as exc:
                        _log(
                            "WARN",
                            "Replacement uploaded but previous version remains under its temporary name",
                            path=str(local.path),
                            temporary_name=backup_name,
                            error=str(exc),
                        )

                current = local.path.stat()
                if current.st_size != local.size or current.st_mtime_ns != local.mtime_ns:
                    raise SyncError(f"Local file changed during upload; it will be retried next run: {local.path}")
            except Exception as exc:
                _log(
                    "ERROR",
                    "File upload finished",
                    path=str(local.path),
                    size_bytes=local.size,
                    status="FAILED",
                    runtime_sec=round(time.monotonic() - upload_started, 3),
                    error=str(exc),
                )
                raise
            _log(
                "INFO",
                "File upload finished",
                path=str(local.path),
                size_bytes=local.size,
                status="SUCCESS",
                runtime_sec=round(time.monotonic() - upload_started, 3),
            )

        completed = 0
        failures: list[str] = []
        state_updates: list[tuple[str, int, int]] = []
        iterator = iter(pending_uploads())
        max_pending = max(workers, workers * 2)
        pending: dict[Future[None], tuple[str, LocalFile]] = {}

        with ThreadPoolExecutor(max_workers=workers) as executor:
            while len(pending) < max_pending:
                try:
                    relative, local, remote, parent_id = next(iterator)
                except StopIteration:
                    break
                pending[executor.submit(upload_one, local, remote, parent_id)] = (relative, local)

            while pending:
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    relative, local = pending.pop(future)
                    try:
                        future.result()
                    except Exception:
                        failures.append(relative)
                        continue
                    state_updates.append((relative, local.size, local.mtime_ns))
                    completed += 1
                    if len(state_updates) >= 500:
                        state.save_many(iter(state_updates))
                        state_updates.clear()

                    try:
                        next_relative, next_local, next_remote, next_parent = next(iterator)
                    except StopIteration:
                        continue
                    pending[executor.submit(upload_one, next_local, next_remote, next_parent)] = (
                        next_relative,
                        next_local,
                    )

        state.save_many(iter(state_updates))
        summary = SyncSummary(
            len(local_files),
            len(remote_files),
            len(unchanged),
            upload_count,
            update_count,
            transfer_bytes,
            completed,
            len(failures),
            False,
        )
        _log(
            "INFO" if not failures else "ERROR",
            "Sync completed" if not failures else "Sync completed with failures",
            completed_files=completed,
            failed_files=len(failures),
            duration_sec=round(time.monotonic() - started, 2),
        )
        if failures:
            raise SyncError(f"{len(failures)} file(s) failed to upload; see preceding log entries")
        return summary
    finally:
        state.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delta-back up a local folder to Degoo.")
    parser.add_argument("local_path", nargs="?", default=os.environ.get("SYNC_SOURCE", "/data"))
    parser.add_argument("remote_path", nargs="?", default=os.environ.get("SYNC_TARGET", "/Backup"))
    parser.add_argument(
        "--workers",
        type=int,
        default=os.environ.get("SYNC_WORKERS", "4"),
        help="Maximum concurrent file uploads (default: SYNC_WORKERS or 4)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the delta plan without changing Degoo")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.environ.get("SYNC_STATE_FILE", "/home/cligoo/.config/cligoo/sync-state.sqlite3")),
    )
    args = parser.parse_args(argv)

    try:
        with DegooClient() as client:
            run_sync(
                client,
                Path(args.local_path),
                args.remote_path,
                workers=args.workers,
                state_path=args.state_file,
                dry_run=args.dry_run,
            )
    except (DegooAPIError, OSError, RequestsError, sqlite3.Error, SyncError, ValueError) as exc:
        _log("ERROR", "Sync failed", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
