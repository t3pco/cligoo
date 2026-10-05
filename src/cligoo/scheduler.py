"""Timezone-local scheduler for recurring Degoo delta backups."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import time

from croniter import CroniterBadCronError, CroniterBadDateError, croniter

from . import __version__
from .sync import _log


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _validate_cron_schedule(expression: str) -> str:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("CRON_SCHEDULE must be a five-field cron expression")
    try:
        croniter(expression, dt.datetime.now().astimezone())
    except (CroniterBadCronError, CroniterBadDateError, ValueError) as exc:
        raise ValueError(f"Invalid CRON_SCHEDULE expression: {expression!r}") from exc
    return expression


def next_run(now: dt.datetime, expression: str) -> dt.datetime:
    """Return the next run time from a cron expression in the given timezone."""
    try:
        return croniter(expression, now).get_next(dt.datetime)
    except (CroniterBadCronError, CroniterBadDateError, ValueError) as exc:
        raise ValueError(f"Invalid CRON_SCHEDULE expression: {expression!r}") from exc


def run_backup(source: str, target: str, workers: int, heartbeat_seconds: float) -> int:
    command = [
        sys.executable,
        "-u",
        "-m",
        "cligoo.sync",
        "--workers",
        str(workers),
        source,
        target,
    ]
    _log("INFO", "Scheduled backup started", source=source, target=target, workers=workers)
    started = time.monotonic()
    process = subprocess.Popen(command)
    while True:
        try:
            return_code = process.wait(timeout=heartbeat_seconds)
            break
        except subprocess.TimeoutExpired:
            _log(
                "INFO",
                "Scheduled backup still running",
                duration_sec=round(time.monotonic() - started, 1),
            )
    _log(
        "INFO" if return_code == 0 else "ERROR",
        "Scheduled backup completed" if return_code == 0 else "Scheduled backup failed",
        exit_code=return_code,
        duration_sec=round(time.monotonic() - started, 1),
    )
    return return_code


def main() -> int:
    try:
        source = os.environ.get("SYNC_SOURCE", "/data")
        target = os.environ.get("SYNC_TARGET", "/Backup")
        workers = int(os.environ.get("SYNC_WORKERS", "4"))
        if workers < 1:
            raise ValueError("SYNC_WORKERS must be at least 1")
        schedule = _validate_cron_schedule(os.environ.get("CRON_SCHEDULE", "0 1,13 * * *"))
        heartbeat = max(1.0, float(os.environ.get("SYNC_HEARTBEAT_INTERVAL", "60")))
        run_on_startup = _env_bool("RUN_ON_STARTUP", True)
    except ValueError as exc:
        _log("ERROR", "Invalid scheduler configuration", error=str(exc))
        return 2

    _log(
        "INFO",
        "Backup scheduler initialized",
        cligoo_version=__version__,
        source=source,
        target=target,
        workers=workers,
        cron_schedule=schedule,
        run_on_startup=run_on_startup,
    )

    if run_on_startup:
        run_backup(source, target, workers, heartbeat)

    while True:
        now = dt.datetime.now().astimezone()
        scheduled = next_run(now, schedule)
        delay = max(0.0, (scheduled - now).total_seconds())
        _log(
            "INFO",
            "Waiting for next backup",
            next_run=scheduled.isoformat(timespec="seconds"),
            wait_seconds=round(delay),
        )
        time.sleep(delay)
        run_backup(source, target, workers, heartbeat)


if __name__ == "__main__":
    raise SystemExit(main())
