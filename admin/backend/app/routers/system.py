"""Infrastructure the console can see but does not manage.

Backups today. The question this answers - "are backups actually running?" -
was previously only answerable by SSHing in and running `ls`, which is why
nobody asked until the day it would have mattered.

Deliberately read-only. Nothing here starts, stops or deletes anything: a page
that can trigger a restore is a page that can trigger a restore by accident.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from ..deps import CurrentUser, require_roles
from ..schemas import BackupStatus, BackupFile

log = logging.getLogger("admin-api")

router = APIRouter(prefix="/system", tags=["system"])

# Mounted read-only from the host - see admin/docker-compose.yml.
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/data/backups"))

# Backups run nightly. Past this, something is wrong and the page should say so
# rather than showing a stale timestamp and leaving the reader to do the sum.
STALE_AFTER_HOURS = float(os.getenv("BACKUP_STALE_HOURS", "36"))

# Infrastructure, so superadmin only. A tenant admin has no way to act on it and
# the disk figures are about the platform, not their campaigns.
superadmin = require_roles()


@router.get("/backups", response_model=BackupStatus)
async def backups(user: CurrentUser = Depends(superadmin)):
    if not BACKUP_DIR.is_dir():
        return BackupStatus(
            configured=False,
            problem=f"{BACKUP_DIR} is not mounted - add "
                    "'/opt/aivoice/backups:/data/backups:ro' to the admin-api "
                    "volumes",
        )

    files = sorted(
        (p for p in BACKUP_DIR.glob("aivoice-*.dump") if p.is_file()),
        key=lambda p: p.stat().st_mtime, reverse=True)

    dumps = [
        BackupFile(
            name=p.name,
            bytes=p.stat().st_size,
            at=datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc),
        )
        for p in files[:30]
    ]

    # Written by backup-db.sh on every run. Its absence means the script has
    # never completed here - not that it succeeded quietly.
    last_result = last_detail = None
    last_run = None
    status_file = BACKUP_DIR / "last-run.json"
    if status_file.is_file():
        try:
            d = json.loads(status_file.read_text())
            last_result = d.get("result")
            last_detail = (d.get("detail") or "").strip() or None
            when = d.get("when")
            if when:
                last_run = datetime.fromisoformat(when)
        except Exception:
            log.exception("could not read %s", status_file)
            last_detail = "the status file could not be read"

    newest = dumps[0].at if dumps else None
    age_hours = ((datetime.now(timezone.utc) - newest).total_seconds() / 3600
                 if newest else None)

    # The newest dump is the honest health check. A timer can be armed and
    # failing every night; a dump from last night cannot lie about having been
    # written.
    problem = None
    if not dumps:
        problem = ("no backups have been taken. Install the timer - see "
                   "docs/DATABASE.md")
    elif age_hours is not None and age_hours > STALE_AFTER_HOURS:
        problem = (f"the newest backup is {age_hours:.0f} hours old. Backups "
                   "run nightly, so something has stopped")
    elif last_result == "failed":
        problem = f"the last run failed: {last_detail or 'no reason recorded'}"

    try:
        usage = shutil.disk_usage(BACKUP_DIR)
        disk_free, disk_total = usage.free, usage.total
    except Exception:
        disk_free = disk_total = None

    return BackupStatus(
        configured=True,
        problem=problem,
        last_run=last_run,
        last_result=last_result,
        last_detail=last_detail,
        newest_at=newest,
        age_hours=age_hours,
        total_bytes=sum(d.bytes for d in dumps),
        disk_free_bytes=disk_free,
        disk_total_bytes=disk_total,
        files=dumps,
    )
