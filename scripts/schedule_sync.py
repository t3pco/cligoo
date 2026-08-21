#!/usr/bin/env python3
"""Simple, self-contained 12-hour scheduler for Degoo sync.

Runs synchronization automatically twice a day at fixed times:
  - 01:00 (1:00 AM)
  - 13:00 (1:00 PM)

Outputs clean logfmt logs for Loki & Grafana monitoring.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
import time
from pathlib import Path

# Fixed daily execution hours: 01:00 and 13:00 (1am & 1pm)
SCHEDULE_HOURS = [1, 13]


def log(level: str, msg: str, **kwargs: object) -> None:
    """Print structured logfmt log line for Loki & Grafana."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    extras = " ".join(f'{k}="{v}"' if isinstance(v, str) and (" " in v or not v) else f"{k}={v}" for k, v in kwargs.items())
    log_line = f"[{timestamp}] level={level.upper()} msg={msg!r} {extras}".rstrip()
    if level.upper() in ("ERROR", "FATAL"):
        print(log_line, file=sys.stderr, flush=True)
    else:
        print(log_line, flush=True)


def get_seconds_until_next_run() -> tuple[float, datetime.datetime]:
    """Calculate the exact timestamp and seconds until the next 01:00 or 13:00."""
    now = datetime.datetime.now()
    candidates = []

    for day_offset in (0, 1):
        target_date = now.date() + datetime.timedelta(days=day_offset)
        for hour in SCHEDULE_HOURS:
            run_time = datetime.datetime.combine(target_date, datetime.time(hour, 0, 0))
            if run_time > now:
                candidates.append(run_time)

    next_run = min(candidates)
    delay_seconds = (next_run - now).total_seconds()
    return delay_seconds, next_run


def run_sync(sync_script: Path, source_dir: str, target_dir: str) -> None:
    """Execute the synchronization script."""
    cmd = [
        sys.executable,
        str(sync_script),
        "--workers", "20",
        "--delete",
        source_dir,
        target_dir,
    ]
    log("INFO", "Scheduled sync job started", source=source_dir, target=target_dir)

    start = time.time()
    res = subprocess.run(cmd)
    elapsed = time.time() - start

    if res.returncode == 0:
        log("INFO", "Scheduled sync job completed", status="SUCCESS", duration_sec=round(elapsed, 2))
    else:
        log("ERROR", "Scheduled sync job failed", status="FAILED", exit_code=res.returncode, duration_sec=round(elapsed, 2))


def main() -> None:
    source_dir = sys.argv[1] if len(sys.argv) > 1 else "/data/kopia"
    target_dir = sys.argv[2] if len(sys.argv) > 2 else "/Backup/kopia"

    sync_script = Path(__file__).resolve().parent / "degoo_sync.py"

    log("INFO", "Degoo 12-hour scheduler initialized", schedule="01:00,13:00", source=source_dir, target=target_dir)

    # 1. Initial startup sync
    log("INFO", "Executing initial startup sync")
    run_sync(sync_script, source_dir, target_dir)

    # 2. Main schedule loop
    while True:
        delay_seconds, next_run = get_seconds_until_next_run()
        log(
            "INFO",
            "Scheduler waiting for next run",
            next_run=next_run.strftime("%Y-%m-%d %H:%M:%S"),
            wait_hours=round(delay_seconds / 3600, 2),
        )
        time.sleep(delay_seconds)
        run_sync(sync_script, source_dir, target_dir)


if __name__ == "__main__":
    main()
