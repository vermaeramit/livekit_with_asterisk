"""Change log for everything the panel mutates.

"Who changed the prompt, and when" is a real question on a client-facing product,
and it is asked most often right after call quality drops.
"""
from __future__ import annotations

import json
from typing import Any

from . import db
from .deps import CurrentUser


def diff(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any]:
    """{field: {from, to}} for the fields that actually changed."""
    if before is None:
        return {k: {"from": None, "to": v} for k, v in after.items()}
    out: dict[str, Any] = {}
    for k, new in after.items():
        old = before.get(k)
        if old != new:
            out[k] = {"from": old, "to": new}
    return out


async def record(
    user: CurrentUser,
    *,
    entity: str,
    entity_id: str | int | None,
    action: str,
    changes: dict[str, Any] | None = None,
    tenant_id: int | None = None,
    campaign_id: int | None = None,
) -> None:
    # Never let an audit write break the operation it is describing - the change
    # has already been committed by the time we get here.
    try:
        await db.pool().execute(
            """INSERT INTO config_audit
                   (tenant_id, campaign_id, user_id, entity, entity_id, action, changes)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            tenant_id if tenant_id is not None else user.tenant_id,
            campaign_id,
            user.id,
            entity,
            str(entity_id) if entity_id is not None else None,
            action,
            json.dumps(changes) if changes else None,
        )
    except Exception:  # pragma: no cover - diagnostics only
        import logging

        logging.getLogger("admin-api").exception("audit write failed")


# Secrets and hashes must never reach the audit table - it is readable by every
# tenant admin for their own tenant.
REDACTED = {"password", "password_hash", "refresh_hash"}


def safe(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in REDACTED}
