"""Infrastructure the console can see but does not manage.

Backups today. The question this answers - "are backups actually running?" -
was previously only answerable by SSHing in and running `ls`, which is why
nobody asked until the day it would have mattered.

Deliberately read-only. Nothing here starts, stops or deletes anything: a page
that can trigger a restore is a page that can trigger a restore by accident.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db
from ..deps import CurrentUser, require_perm
from ..schemas import (BackupFile, BackupStatus, CampaignSlots, DiskUsage,
                       ServiceCheck, SystemAck, SystemHealth)

log = logging.getLogger("admin-api")

router = APIRouter(prefix="/system", tags=["system"])

# Mounted read-only from the host - see admin/docker-compose.yml.
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/data/backups"))

# The one writable path this service has outside its own app directory, and it
# holds a single empty file. Creating it makes a systemd .path unit run the
# backup as root - see server-configs/systemd/aivoice-backup-trigger.path.
#
# The alternatives were the docker socket (root on the host, handed to a web
# API) or write access to the dumps themselves (so anyone who got in could
# delete the backups before doing anything else). Neither is worth a button.
TRIGGER = Path(os.getenv("BACKUP_TRIGGER", "/data/backup-trigger")) / "request"

# Backups run nightly. Past this, something is wrong and the page should say so
# rather than showing a stale timestamp and leaving the reader to do the sum.
STALE_AFTER_HOURS = float(os.getenv("BACKUP_STALE_HOURS", "36"))

# Infrastructure, so superadmin only. A tenant admin has no way to act on it and
# the disk figures are about the platform, not their campaigns.
superadmin = require_perm("system.manage")

# What was acknowledged, identified without being stored. A truncated SHA-256 of
# SECRETS_KEY: not the key, not reversible, and different the moment the key is
# rotated - which is exactly when a stale "yes, it is backed up" becomes
# dangerous rather than merely wrong.
SECRETS_KEY_ACK = "secrets_key_stored_offsite"


def _secrets_fingerprint() -> str | None:
    raw = (os.getenv("SECRETS_KEY") or "").strip()
    if not raw:
        return None
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _load_ack(key: str, fingerprint: str | None) -> SystemAck | None:
    row = await db.pool().fetchrow(
        "SELECT key, fingerprint, acked_name, acked_at FROM system_acks "
        "WHERE key = $1", key)
    if row is None:
        return None
    return SystemAck(
        key=row["key"],
        acked_by=row["acked_name"],
        acked_at=row["acked_at"],
        # Both known and different: the key has been rotated since. If either is
        # unknown we cannot claim staleness, and claiming it wrongly would nag
        # somebody who has done nothing wrong.
        stale=bool(fingerprint and row["fingerprint"]
                   and fingerprint != row["fingerprint"]),
    )


# Where the six agent workers listen. They are native systemd processes, so
# they are reached through the host gateway rather than by container name - see
# extra_hosts in admin/docker-compose.yml.
AGENT_HOST = os.getenv("AGENT_HOST", "host.docker.internal")
AGENT_PORTS = tuple(range(int(os.getenv("AGENT_PORT_BASE", "8081")),
                          int(os.getenv("AGENT_PORT_BASE", "8081"))
                          + int(os.getenv("AGENT_WORKERS", "6"))))

# Reached through the HOST, on the ports they publish - not by container name.
#
# admin-api is on aivoice_default and only `postgres` is exposed to it there;
# livekit, sip and redis do not resolve from here at all. That is deliberate and
# worth keeping: this service's reach is part of its blast radius, and widening
# it to make a status page green would be the wrong trade.
#
# Both of these publish on all interfaces (*:7880, *:5080), so the host gateway
# reaches them.
#
# REDIS IS NOT IN THIS LIST. It publishes on 127.0.0.1:6379 only, on purpose, so
# nothing outside the host can reach it - including this container. There is no
# check to make, and a red cross would say "broken" where the truth is "cannot
# see". It is named in the note beside Asterisk instead.
SERVICES = (
    ("LiveKit", AGENT_HOST, 7880, "carries the audio between Asterisk and the agent"),
    ("LiveKit SIP", AGENT_HOST, 5080, "turns the call from Asterisk into a room"),
)

# Half a second. These run at once, so the page waits for the slowest, and a
# service that has not answered in 500 ms on a local network is not answering.
_PROBE_TIMEOUT = 0.5

# Below this, recordings stop being written and nobody finds out until a call
# is missing one.
_DISK_WARN_PCT = 15


async def _reachable(host: str, port: int) -> bool:
    """Can something be connected to here. Not "is it healthy" - said so on the page."""
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port),
                                      _PROBE_TIMEOUT)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


# The router already carries prefix="/system" - see the top of the file. This
# said "/system/health" and served /api/system/system/health, which 404s in a
# way that looks like the page is broken rather than the path.
@router.get("/health", response_model=SystemHealth)
async def system_health(user: CurrentUser = Depends(superadmin)):
    """Is everything running. Answerable in the console rather than over SSH.

    WHAT THIS CANNOT SEE, and says so rather than showing a green tick that
    means nothing: Asterisk is native and listens only on UDP - 5060 for SIP and
    4569 for the trunk - so there is no socket here to connect to. It gets a
    "last call received" line instead, which is a fact rather than a verdict: on
    a quiet afternoon a long gap means nothing, and at 3pm on a weekday it means
    everything. The reader knows which it is; this endpoint does not.
    """
    workers, services = await asyncio.gather(
        asyncio.gather(*(_reachable(AGENT_HOST, p) for p in AGENT_PORTS)),
        asyncio.gather(*(_reachable(h, p) for _, h, p, _ in SERVICES)),
    )

    checks = [
        ServiceCheck(name=f"Agent worker {i + 1}", detail=f"port {port}",
                     ok=ok, kind="worker")
        for i, (port, ok) in enumerate(zip(AGENT_PORTS, workers))
    ] + [
        ServiceCheck(name=name, detail=why, ok=ok, kind="service")
        for (name, _, _, why), ok in zip(SERVICES, services)
    ]

    # Postgres answers or this request would not be running, but saying so
    # explicitly keeps the list complete rather than mysteriously missing one.
    checks.append(ServiceCheck(name="Postgres", detail="configuration and history",
                               ok=True, kind="service"))

    disks = []
    for label, path in (("Recordings", "/data/recordings"),
                        ("Backups", str(BACKUP_DIR)),
                        ("Hold messages", os.getenv("HOLD_CACHE_DIR", "/data/hold"))):
        try:
            u = shutil.disk_usage(path)
        except Exception:
            # A mount that is not there is worth showing as missing rather than
            # omitting - an absent recordings mount is why a recording is absent.
            disks.append(DiskUsage(name=label, path=path, ok=False,
                                   total_gb=0, free_gb=0, free_pct=0))
            continue
        pct = round(u.free / u.total * 100, 1) if u.total else 0
        disks.append(DiskUsage(
            name=label, path=path, ok=pct >= _DISK_WARN_PCT,
            total_gb=round(u.total / 1e9, 1), free_gb=round(u.free / 1e9, 1),
            free_pct=pct))

    last_call = await db.pool().fetchval("SELECT max(started_at) FROM calls")
    queued = await db.pool().fetchrow(
        """SELECT count(*) FILTER (WHERE status = 'pending')  AS pending,
                  count(*) FILTER (WHERE status = 'failed')   AS failed
             FROM call_postbacks""")

    # What the concurrency limits built yesterday are actually doing right now.
    slots = await db.pool().fetch(
        """SELECT cam.name, ac.max_parallel_calls AS limit_n,
                  count(c.id) FILTER (WHERE c.ended_at IS NULL) AS in_use
             FROM agent_config ac
             JOIN campaigns cam ON cam.id = ac.campaign_id
             LEFT JOIN calls c ON c.campaign_id = ac.campaign_id
                              AND c.ended_at IS NULL
            WHERE ac.max_parallel_calls IS NOT NULL
            GROUP BY cam.name, ac.max_parallel_calls
            ORDER BY cam.name""")

    return SystemHealth(
        checks=checks, disks=disks,
        last_call_at=last_call,
        postbacks_pending=queued["pending"] or 0,
        postbacks_failed=queued["failed"] or 0,
        slots=[CampaignSlots(campaign=r["name"], limit=r["limit_n"],
                             in_use=r["in_use"]) for r in slots])


@router.get("/backups", response_model=BackupStatus)
async def backups(user: CurrentUser = Depends(superadmin)):
    if not BACKUP_DIR.is_dir():
        return BackupStatus(
            configured=False,
            problem=f"{BACKUP_DIR} is not mounted - add "
                    "'/opt/aivoice/backups:/data/backups:ro' to the admin-api "
                    "volumes",
        )

    # A 500 here would have read, from the console, as "no backups exist" - the
    # page cannot tell an empty directory from one it may not open, and the
    # difference is the entire point of looking.
    try:
        files = sorted(
            (p for p in BACKUP_DIR.glob("aivoice-*.dump") if p.is_file()),
            key=lambda p: p.stat().st_mtime, reverse=True)
    except PermissionError:
        return BackupStatus(
            configured=False,
            problem=f"{BACKUP_DIR} cannot be read by this service. The backup "
                    "script sets 755 on the directory and 600 on the dumps - an "
                    "older run may have left it at 700. Re-run the backup, or "
                    "chmod 755 /opt/aivoice/backups",
        )

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
        # Wrapped, because an unreadable status file is a permissions detail and
        # not a reason to fail the whole page - the dumps above are the answer
        # people came for.
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
        secrets_key_ack=await _load_ack(SECRETS_KEY_ACK, _secrets_fingerprint()),
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


@router.post("/acks/secrets-key", response_model=SystemAck)
async def ack_secrets_key(actor: CurrentUser = Depends(superadmin)):
    """Record that SECRETS_KEY is stored somewhere other than this server.

    A statement by a person, not a check. Nothing here can look inside a
    password manager - and pretending to would be worse than admitting it,
    because a green tick nobody earned is read as proof.

    Re-acknowledging after a rotation simply overwrites the row: the fingerprint
    moves with it, which is the point.
    """
    fp = _secrets_fingerprint()
    if not fp:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "SECRETS_KEY is not set on this service, so there is nothing to "
            "acknowledge. Provider keys cannot be decrypted either - check "
            "admin/.env")

    # CurrentUser carries email, not name - it is built from the token, and the
    # token holds what is needed to authorise a request rather than what is
    # pleasant to display. Stored as text so it survives the user row being
    # deleted: "who confirmed this" has to outlive them leaving.
    name = actor.email
    await db.pool().execute(
        """INSERT INTO system_acks (key, fingerprint, acked_by, acked_name)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (key) DO UPDATE
              SET fingerprint = EXCLUDED.fingerprint,
                  acked_by    = EXCLUDED.acked_by,
                  acked_name  = EXCLUDED.acked_name,
                  acked_at    = now()""",
        SECRETS_KEY_ACK, fp, actor.id, name)

    # In the audit trail like any other decision. "Who said the key was safe,
    # and when" is a question that gets asked exactly once, in a bad week.
    await audit.record(actor, entity="system", entity_id=SECRETS_KEY_ACK,
                       action="acknowledge", tenant_id=None, campaign_id=None,
                       changes={"fingerprint": fp})

    return await _load_ack(SECRETS_KEY_ACK, fp)


@router.post("/backups/run", status_code=status.HTTP_202_ACCEPTED)
async def run_backup(actor: CurrentUser = Depends(superadmin)):
    """Ask for a backup now. Returns once ASKED, not once done.

    202 rather than 200, and deliberately: this service does not run the dump
    and cannot say whether it worked. The page finds out the way anyone else
    would - a new file appears, or the last run says it failed.

    Additive, which is why it is the one thing on this page that writes at all.
    An extra backup cannot destroy anything; a restore button could, which is
    why there isn't one.
    """
    try:
        TRIGGER.parent.mkdir(parents=True, exist_ok=True)
        TRIGGER.touch()
    except Exception as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"could not ask for a backup: {type(e).__name__}. The trigger "
            f"directory {TRIGGER.parent} has to be mounted writable, and "
            "aivoice-backup-trigger.path has to be enabled on the host")

    await audit.record(actor, entity="system", entity_id="backup",
                       action="run", tenant_id=None, campaign_id=None)
    log.info("backup requested by %s", actor.email)
    return {"requested": True}
