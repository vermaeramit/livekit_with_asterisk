"""Knowledge base pages pulled from a URL.

A source is one address. Importing it produces one kb_document per page, so
everything that already works per document keeps working: enable and disable,
the chunk viewer, and citations that name where an answer came from.

Refresh is a button rather than a nightly job. That was chosen knowingly - a
scheduled fetch means the agent can start quoting a new price before anyone has
read it - and the cost is that a source goes stale quietly, so the list shows
how long ago each one was fetched rather than only the date.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from .. import audit, db, kblib
from .. import provider_keys as pk
from ..deps import (CurrentUser, active_user, assert_campaign_visible,
                    require_perm)
from ..schemas import KbSourceIn, KbSourceOut

log = logging.getLogger("admin-api")
router = APIRouter(tags=["knowledge base"])

editor = require_perm("campaign.write")

_SELECT = """
    SELECT s.id, s.campaign_id, s.url, s.title, s.last_fetched_at,
           s.last_status, s.last_error, s.page_count, s.skipped,
           s.created_at, s.updated_at,
           (SELECT count(*) FROM kb_documents d
             WHERE d.source_id = s.id) AS document_count,
           (SELECT count(*) FROM kb_documents d
             WHERE d.source_id = s.id AND d.enabled) AS enabled_count
      FROM kb_sources s
"""


def _row(r) -> KbSourceOut:
    d = dict(r)
    # asyncpg returns JSONB as text and no codec is registered - see the note
    # in agent_config.py. A column added here without this arrives as a string
    # and pydantic rejects the whole row.
    if isinstance(d.get("skipped"), str):
        d["skipped"] = json.loads(d["skipped"])
    return KbSourceOut(**d)


@router.get("/campaigns/{campaign_id}/kb/sources",
            response_model=list[KbSourceOut])
async def list_sources(campaign_id: int,
                       user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        _SELECT + " WHERE s.campaign_id = $1 ORDER BY s.created_at", campaign_id)
    return [_row(r) for r in rows]


@router.post("/campaigns/{campaign_id}/kb/sources")
async def add_source(campaign_id: int, body: KbSourceIn,
                     actor: CurrentUser = Depends(editor)):
    """Create a source and import it, streaming progress as the upload does."""
    tenant_id, cfg_name, api_key = await _prepare(actor, campaign_id)

    existing = await db.pool().fetchrow(
        "SELECT id FROM kb_sources WHERE campaign_id = $1 AND url = $2",
        campaign_id, body.url)
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this campaign already imports that URL - use "
                            "Refresh on it instead")

    source_id = await db.pool().fetchval(
        """INSERT INTO kb_sources (campaign_id, tenant_id, config_name, url, title)
           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
        campaign_id, tenant_id, cfg_name, body.url, body.title)

    await audit.record(actor, entity="kb_source", entity_id=body.url,
                       action="create", tenant_id=tenant_id,
                       campaign_id=campaign_id)
    return _stream(source_id, body.url, cfg_name, campaign_id, tenant_id,
                   api_key, actor, force=False)


@router.post("/kb/sources/{source_id}/refresh")
async def refresh_source(source_id: int,
                         force: bool = Query(False, description="re-embed pages that have not changed"),
                         actor: CurrentUser = Depends(editor)):
    src = await _source_or_404(actor, source_id)
    _, cfg_name, api_key = await _prepare(actor, src["campaign_id"])
    return _stream(source_id, src["url"], cfg_name, src["campaign_id"],
                   src["tenant_id"], api_key, actor, force=force)


@router.delete("/kb/sources/{source_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: int, actor: CurrentUser = Depends(editor)):
    """Removes the source and, by cascade, every document it produced."""
    src = await _source_or_404(actor, source_id)
    await db.pool().execute("DELETE FROM kb_sources WHERE id = $1", source_id)
    await audit.record(actor, entity="kb_source", entity_id=src["url"],
                       action="delete", tenant_id=src["tenant_id"],
                       campaign_id=src["campaign_id"])


# ─────────────────────────────── plumbing ──────────────────────────────────

async def _source_or_404(user: CurrentUser, source_id: int):
    row = await db.pool().fetchrow(
        "SELECT id, campaign_id, tenant_id, url, config_name "
        "  FROM kb_sources WHERE id = $1", source_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such source")
    await assert_campaign_visible(user, row["campaign_id"])
    return row


async def _prepare(actor: CurrentUser, campaign_id: int):
    """The three things an import needs, all refused up front.

    Up front and not part way through: a half-finished import has already
    embedded some pages, and somebody has already paid for them.
    """
    if not kblib.available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"ingestion is not configured: {kblib.why_unavailable()}")

    tenant_id = await assert_campaign_visible(actor, campaign_id)
    cfg = await db.pool().fetchrow(
        "SELECT name FROM agent_config WHERE campaign_id = $1 ORDER BY id LIMIT 1",
        campaign_id)
    if cfg is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this campaign has no agent config to attach documents to")

    keys = await pk.resolve(tenant_id=tenant_id, campaign_id=campaign_id)
    if not keys.get("openai"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this campaign has no OpenAI key, and embedding is billed to it")
    return tenant_id, cfg["name"], keys["openai"]


def _stream(source_id, url, cfg_name, campaign_id, tenant_id, api_key, actor,
            force: bool):
    async def gen():
        result = None
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def on_progress(**event) -> None:
            await queue.put(event)

        task = asyncio.create_task(kblib.kb().ingest_web_source(
            url, config_name=cfg_name, campaign_id=campaign_id,
            source_id=source_id, api_key=api_key, emit=on_progress,
            force=force))

        try:
            # Waiting on the queue with a timeout rather than on the task
            # alone: a workbook of 47 sheets spends minutes embedding, and a
            # response that produces nothing for that long is one some proxies
            # and browsers give up on.
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    event = {"stage": "working"}
                yield json.dumps(event) + "\n"
            result = await task
        except Exception as e:
            log.exception("web ingest failed for %s", url)
            await db.pool().execute(
                "UPDATE kb_sources SET last_fetched_at = now(), "
                "  last_status = 'error', last_error = $2, updated_at = now() "
                " WHERE id = $1", source_id, f"{type(e).__name__}: {e}"[:500])
            yield json.dumps({"stage": "error",
                              "message": f"{type(e).__name__}: {e}"}) + "\n"
            return

        await db.pool().execute(
            """UPDATE kb_sources
                  SET last_fetched_at = now(), last_status = $2,
                      last_error = $3, page_count = $4,
                      skipped = $5::jsonb, updated_at = now()
                WHERE id = $1""",
            source_id, result.get("status"), result.get("error"),
            result.get("pages") or 0,
            json.dumps(result.get("skipped") or []))

        await audit.record(
            actor, entity="kb_source", entity_id=url,
            action="refresh" if force else "import",
            tenant_id=tenant_id, campaign_id=campaign_id,
            changes={"pages": {"from": None, "to": result.get("pages")}})
        yield json.dumps({"stage": "done", **result}) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        # nginx buffers proxied responses by default, which would hold every
        # progress line back until the end and defeat the point.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )
