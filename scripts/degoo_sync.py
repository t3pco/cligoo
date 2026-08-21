#!/usr/bin/env python3
"""High-performance Two-Phase Delta Synchronization for Degoo.

Designed for synchronizing large directory trees (such as Kopia backup repositories
with tens of thousands of files) between a local server and Degoo cloud storage.

Workflow:
  1. Fast Local Scan: Collects local file paths, sizes, and timestamps.
  2. Fast Remote Scan: Recursively paginates Degoo folder metadata (1000 items/page).
  3. Compare (Delta Plan): Identifies Unchanged, New, Modified, and Orphan files.
  4. Parallel Execution: Uploads new/modified files via a concurrent worker pool.

Logging:
  Outputs clean logfmt-compatible logs with metrics for easy Grafana + Loki
  parsing, querying, and alerting.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import humanize
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

# Import cligoo API client
try:
    from cligoo.api import DegooAPIError, DegooClient
    from cligoo.constants import FOLDER_CATEGORIES
except ImportError:
    # Allow running directly from repository root
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from cligoo.api import DegooAPIError, DegooClient
    from cligoo.constants import FOLDER_CATEGORIES

is_tty = sys.stdout.isatty()
console = Console(force_terminal=is_tty, no_color=not is_tty)
err_console = Console(stderr=True, force_terminal=is_tty, no_color=not is_tty)


def log(level: str, msg: str, **kwargs: Any) -> None:
    """Print structured logfmt-compatible log line for Loki & Grafana."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    extras = " ".join(f'{k}="{v}"' if isinstance(v, str) and (" " in v or not v) else f"{k}={v}" for k, v in kwargs.items())
    log_line = f"[{timestamp}] level={level.upper()} msg={msg!r} {extras}".rstrip()
    if level.upper() in ("ERROR", "FATAL"):
        print(log_line, file=sys.stderr, flush=True)
    else:
        print(log_line, flush=True)


# ── Step 1: Local Filesystem Scan ────────────────────────────────────────────


def scan_local_tree(local_root: Path) -> Tuple[Dict[str, Tuple[Path, int, float]], Set[str]]:
    """Scan local directory recursively."""
    files: Dict[str, Tuple[Path, int, float]] = {}
    dirs: Set[str] = set()

    if not local_root.is_dir():
        raise FileNotFoundError(f"Local path is not a directory: {local_root}")

    def _walk(current_dir: Path, rel_prefix: str = "") -> None:
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    entry_rel = f"{rel_prefix}/{entry.name}".lstrip("/")
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs.add(entry_rel)
                            _walk(Path(entry.path), entry_rel)
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat()
                            files[entry_rel] = (Path(entry.path), stat.st_size, stat.st_mtime)
                    except (PermissionError, FileNotFoundError) as e:
                        log("WARN", "Skipping inaccessible local file", file=entry.path, error=str(e))
        except PermissionError as e:
            log("WARN", "Skipping inaccessible local directory", dir=str(current_dir), error=str(e))

    _walk(local_root)
    return files, dirs


# ── Step 2: Remote Degoo Scan ────────────────────────────────────────────────


def ensure_remote_root(client: DegooClient, remote_path: str) -> str:
    """Ensure remote path exists, creating intermediate folders if necessary."""
    clean_path = remote_path.strip("/")
    parts = [p for p in clean_path.split("/") if p]
    if not parts:
        return "0"

    current_parent_id = "0"
    current_path_acc = ""

    for part in parts:
        current_path_acc = f"{current_path_acc}/{part}"
        item = client.resolve_path_under(current_parent_id, part)
        if item is None:
            client.mkdir(part, current_parent_id)
            item = client.resolve_path_under(current_parent_id, part)
            if item is None:
                item = client.resolve_path(current_path_acc)
            if item is None:
                raise RuntimeError(f"Failed to create remote directory: {current_path_acc}")

        current_parent_id = str(item["ID"])

    return current_parent_id


def scan_remote_tree(
    client: DegooClient, root_folder_id: str
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Scan remote Degoo tree recursively using paginated queries."""
    files: Dict[str, Dict[str, Any]] = {}
    folders: Dict[str, str] = {"": root_folder_id}
    queue: List[Tuple[str, str]] = [(root_folder_id, "")]

    if is_tty:
        with Progress(
            SpinnerColumn(),
            TextColumn("[cyan]Scanning Degoo remote folders...[/cyan]"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("remote_scan", total=None)
            _execute_remote_scan(client, queue, files, folders)
    else:
        _execute_remote_scan(client, queue, files, folders)

    return files, folders


def _execute_remote_scan(
    client: DegooClient,
    queue: List[Tuple[str, str]],
    files: Dict[str, Dict[str, Any]],
    folders: Dict[str, str],
) -> None:
    while queue:
        parent_id, parent_rel = queue.pop(0)
        try:
            items = client.list_dir(parent_id, limit=None)
        except DegooAPIError as e:
            log("ERROR", "Error listing remote folder", folder_id=parent_id, error=str(e))
            continue

        for item in items:
            name = item.get("Name") or ""
            category = int(item.get("Category", 0))
            item_rel = f"{parent_rel}/{name}".lstrip("/")

            if category in FOLDER_CATEGORIES:
                folder_id = str(item["ID"])
                folders[item_rel] = folder_id
                queue.append((folder_id, item_rel))
            else:
                files[item_rel] = {
                    "ID": str(item["ID"]),
                    "Name": name,
                    "Size": int(item.get("Size") or 0),
                    "ParentID": str(item.get("ParentID") or parent_id),
                    "Category": category,
                }


# ── Step 3: Create Missing Remote Folders ────────────────────────────────────


def ensure_remote_folders(
    client: DegooClient,
    local_dirs: Set[str],
    remote_folders: Dict[str, str],
    root_folder_id: str,
    dry_run: bool = False,
) -> Dict[str, str]:
    """Ensure all required local directory paths exist remotely."""
    folder_map = dict(remote_folders)
    sorted_dirs = sorted(local_dirs, key=lambda d: (d.count("/"), d))

    for rel_dir in sorted_dirs:
        if rel_dir in folder_map:
            continue

        parent_rel = "/".join(rel_dir.split("/")[:-1]) if "/" in rel_dir else ""
        dir_name = rel_dir.split("/")[-1]
        parent_id = folder_map.get(parent_rel, root_folder_id)

        if dry_run:
            folder_map[rel_dir] = f"dry-run-{rel_dir}"
            continue

        try:
            client.mkdir(dir_name, parent_id)
            item = client.resolve_path_under(parent_id, dir_name)
            if item is not None:
                folder_map[rel_dir] = str(item["ID"])
            else:
                folder_map[rel_dir] = parent_id
        except DegooAPIError as e:
            msg = str(e).lower()
            if "already exist" in msg or "invalid input" in msg:
                item = client.resolve_path_under(parent_id, dir_name)
                if item:
                    folder_map[rel_dir] = str(item["ID"])
            else:
                log("WARN", "Failed to create remote directory", dir=rel_dir, error=str(e))

    return folder_map


# ── Step 4: Parallel Transfer Engine ─────────────────────────────────────────


def _upload_single_file(
    client: DegooClient,
    local_path: Path,
    target_folder_id: str,
    target_name: str,
    file_size: int,
    progress: Optional[Progress],
    overall_task_id: Any,
) -> bool:
    """Worker task to upload one file."""
    last_uploaded = [0]
    task_id = progress.add_task(f"↑ {target_name}", total=file_size) if progress else None

    def progress_cb(uploaded: int, _total: int) -> None:
        delta = uploaded - last_uploaded[0]
        if delta > 0:
            if progress and overall_task_id is not None:
                progress.advance(overall_task_id, delta)
            last_uploaded[0] = uploaded
        if progress and task_id is not None:
            progress.update(task_id, completed=uploaded)

    try:
        client.upload(
            local_path,
            target_folder_id,
            name=target_name,
            progress_callback=progress_cb,
            upload_retries=5,
        )
        remaining = file_size - last_uploaded[0]
        if progress and overall_task_id is not None and remaining > 0:
            progress.advance(overall_task_id, remaining)
        if progress and task_id is not None:
            progress.remove_task(task_id)
        return True
    except Exception as e:
        if progress and task_id is not None:
            progress.remove_task(task_id)
        raise RuntimeError(f"Upload failed for {target_name}: {e}") from e


# ── Main Entrypoint ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Two-phase delta sync between local filesystem and Degoo cloud storage."
    )
    parser.add_argument("local_path", help="Local directory path (source)")
    parser.add_argument("remote_path", help="Remote Degoo folder path, e.g. /Backup/kopia (destination)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview sync plan without transferring data")
    parser.add_argument("-w", "--workers", type=int, default=20, help="Number of concurrent transfer workers (default: 20)")
    parser.add_argument("--delete", action="store_true", help="Delete remote files that no longer exist locally")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    local_root = Path(args.local_path).resolve()
    remote_path = "/" + args.remote_path.strip("/")
    sync_start_time = time.time()

    log(
        "INFO",
        "Sync started",
        source=str(local_root),
        target=remote_path,
        workers=args.workers,
        delete_mode=bool(args.delete),
        dry_run=bool(args.dry_run),
    )

    client = DegooClient()

    # 1. Scan Local
    t0 = time.time()
    try:
        local_files, local_dirs = scan_local_tree(local_root)
    except Exception as e:
        log("ERROR", "Failed to scan local directory", error=str(e))
        sys.exit(1)

    t_local = time.time() - t0
    total_local_bytes = sum(size for _, size, _ in local_files.values())
    log(
        "INFO",
        "Local scan completed",
        files=len(local_files),
        dirs=len(local_dirs),
        size_bytes=total_local_bytes,
        duration_sec=round(t_local, 2),
    )

    # 2. Scan Remote
    t0 = time.time()
    try:
        root_folder_id = ensure_remote_root(client, remote_path)
        remote_files, remote_folders = scan_remote_tree(client, root_folder_id)
    except Exception as e:
        log("ERROR", "Failed to scan remote Degoo folder", error=str(e))
        sys.exit(1)

    t_remote = time.time() - t0
    total_remote_bytes = sum(item["Size"] for item in remote_files.values())
    log(
        "INFO",
        "Remote scan completed",
        files=len(remote_files),
        folders=len(remote_folders),
        size_bytes=total_remote_bytes,
        duration_sec=round(t_remote, 2),
    )

    # 3. Delta Comparison
    to_upload: List[Tuple[str, Path, int]] = []
    to_update: List[Tuple[str, Path, int, str]] = []
    to_delete: List[Tuple[str, str, int]] = []
    unchanged_count = 0
    unchanged_bytes = 0

    for rel_path, (abs_path, local_size, _) in local_files.items():
        if rel_path in remote_files:
            remote_info = remote_files[rel_path]
            if remote_info["Size"] == local_size:
                unchanged_count += 1
                unchanged_bytes += local_size
            else:
                to_update.append((rel_path, abs_path, local_size, remote_info["ID"]))
        else:
            to_upload.append((rel_path, abs_path, local_size))

    if args.delete:
        for rel_path, remote_info in remote_files.items():
            if rel_path not in local_files:
                to_delete.append((rel_path, remote_info["ID"], remote_info["Size"]))

    bytes_to_transfer = sum(s for _, _, s in to_upload) + sum(s for _, _, s, _ in to_update)
    total_tasks = len(to_upload) + len(to_update)

    log(
        "INFO",
        "Delta plan computed",
        unchanged_files=unchanged_count,
        to_upload_files=len(to_upload),
        to_update_files=len(to_update),
        to_delete_files=len(to_delete),
        transfer_bytes=bytes_to_transfer,
    )

    if total_tasks == 0 and len(to_delete) == 0:
        total_duration = time.time() - sync_start_time
        log(
            "INFO",
            "Sync completed - all files up to date",
            status="SUCCESS",
            files_transferred=0,
            bytes_transferred=0,
            failed_files=0,
            duration_sec=round(total_duration, 2),
        )
        return

    if args.dry_run:
        log("INFO", "Dry run finished. No changes made", status="DRY_RUN")
        return

    # 4. Create missing remote folders
    remote_folders = ensure_remote_folders(client, local_dirs, remote_folders, root_folder_id)

    # 5. Handle Deletions
    deleted_count = 0
    if args.delete and to_delete:
        delete_ids = [d_id for _, d_id, _ in to_delete]
        BATCH_SIZE = 100
        for i in range(0, len(delete_ids), BATCH_SIZE):
            chunk = delete_ids[i : i + BATCH_SIZE]
            try:
                client.delete(chunk, permanent=True)
                deleted_count += len(chunk)
            except DegooAPIError as e:
                log("WARN", "Warning during batch deletion", error=str(e))

    # 6. Delete old items that need to be updated
    if to_update:
        replace_ids = [r_id for _, _, _, r_id in to_update]
        for i in range(0, len(replace_ids), 100):
            chunk = replace_ids[i : i + 100]
            try:
                client.delete(chunk, permanent=True)
            except DegooAPIError:
                pass

    # 7. Execute Parallel Transfers
    all_upload_tasks: List[Tuple[Path, str, str, int]] = []

    for rel_path, abs_path, size in to_upload:
        parent_rel = "/".join(rel_path.split("/")[:-1]) if "/" in rel_path else ""
        target_folder_id = remote_folders.get(parent_rel, root_folder_id)
        target_name = rel_path.split("/")[-1]
        all_upload_tasks.append((abs_path, target_folder_id, target_name, size))

    for rel_path, abs_path, size, _ in to_update:
        parent_rel = "/".join(rel_path.split("/")[:-1]) if "/" in rel_path else ""
        target_folder_id = remote_folders.get(parent_rel, root_folder_id)
        target_name = rel_path.split("/")[-1]
        all_upload_tasks.append((abs_path, target_folder_id, target_name, size))

    progress: Optional[Progress] = None
    overall_task_id = None

    if is_tty:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        overall_task_id = progress.add_task("[bold green]Overall Sync Progress", total=bytes_to_transfer)

    failed_files: List[Tuple[str, str]] = []
    completed_count = 0

    def _run_pool() -> None:
        nonlocal completed_count
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    _upload_single_file,
                    client,
                    abs_path,
                    target_fid,
                    target_name,
                    size,
                    progress,
                    overall_task_id,
                ): (target_name, abs_path)
                for abs_path, target_fid, target_name, size in all_upload_tasks
            }

            for future in concurrent.futures.as_completed(futures):
                name, path = futures[future]
                try:
                    future.result()
                    completed_count += 1
                except Exception as e:
                    failed_files.append((str(path), str(e)))
                    log("ERROR", "File upload failed", file=str(path), error=str(e))

    if progress:
        with progress:
            _run_pool()
    else:
        _run_pool()

    total_duration = time.time() - sync_start_time

    if failed_files:
        log(
            "ERROR",
            "Sync finished with errors",
            status="FAILED",
            files_transferred=completed_count,
            bytes_transferred=bytes_to_transfer,
            deleted_files=deleted_count,
            failed_files=len(failed_files),
            duration_sec=round(total_duration, 2),
        )
        sys.exit(1)
    else:
        log(
            "INFO",
            "Sync completed successfully",
            status="SUCCESS",
            files_transferred=completed_count,
            bytes_transferred=bytes_to_transfer,
            deleted_files=deleted_count,
            failed_files=0,
            duration_sec=round(total_duration, 2),
        )


if __name__ == "__main__":
    main()
