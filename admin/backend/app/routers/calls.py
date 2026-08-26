from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import db
from ..deps import CurrentUser, active_user, tenant_scope
from ..schemas import (CallDetail, CallListItem, CallListResponse, CallUsage,
                       ToolInvocationOut, TurnOut)

router = APIRouter(prefix="/calls", tags=["calls"])

# Written by Asterisk, mounted read-only. The filename is derived from
# calls.sip_call_id rather than stored, so a change of mount point cannot leave
# stale paths in the database.
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "/data/recordings"))

# sip_call_id comes from a SIP header, so it is attacker-influenced in principle.
# Anything outside this shape never reaches the filesystem.
_CALL_ID_OK = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

CHUNK = 256 * 1024


def recording_file(sip_call_id: str | None) -> Path | None:
    if not sip_call_id or not _CALL_ID_OK.match(sip_call_id):
        return None
    path = RECORDINGS_DIR / f"{sip_call_id}.opus"
    return path if path.is_file() else None

LIST_COLUMNS = """
    c.id, c.started_at, c.ended_at, c.duration_ms, c.caller, c.callee,
    c.language, c.end_reason, c.limit_hit, c.transferred_to, c.turn_count,
    c.campaign_id, c.tenant_id, cam.name AS campaign_name
"""


@router.get("", response_model=CallListResponse)
async def list_calls(
    user: CurrentUser = Depends(active_user),
    tenant_id: int | None = Query(None, description="superadmin only"),
    campaign_id: int | None = None,
    search: str | None = Query(None, description="caller / callee / room name"),
    end_reason: str | None = None,
    outcome: str | None = None,
    transferred: bool | None = Query(None, description="only transferred calls"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_duration_ms: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    scope = tenant_scope(user, tenant_id)

    # Filters are assembled as parallel lists so the WHERE clause and the
    # parameter tuple can never drift out of sync.
    where: list[str] = []
    args: list = []

    def add(sql: str, value) -> None:
        args.append(value)
        where.append(sql.format(n=len(args)))

    if scope is not None:
        add("c.tenant_id = ${n}", scope)
    if campaign_id is not None:
        add("c.campaign_id = ${n}", campaign_id)
    if end_reason:
        add("c.end_reason = ${n}", end_reason)
    if outcome:
        add("c.outcome = ${n}", outcome)
    if date_from:
        add("c.started_at >= ${n}", date_from)
    if date_to:
        add("c.started_at < ${n}", date_to)
    if min_duration_ms is not None:
        add("c.duration_ms >= ${n}", min_duration_ms)
    if transferred is True:
        where.append("c.transferred_to IS NOT NULL")
    elif transferred is False:
        where.append("c.transferred_to IS NULL")
    if search:
        add("(c.caller ILIKE '%' || ${n} || '%' OR c.callee ILIKE '%' || ${n} "
            "|| '%' OR c.room_name ILIKE '%' || ${n} || '%')", search)

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    total = await db.pool().fetchval(
        f"SELECT count(*) FROM calls c {clause}", *args)

    args.extend([page_size, (page - 1) * page_size])
    rows = await db.pool().fetch(
        f"""SELECT {LIST_COLUMNS}
              FROM calls c LEFT JOIN campaigns cam ON cam.id = c.campaign_id
              {clause}
             ORDER BY c.started_at DESC
             LIMIT ${len(args) - 1} OFFSET ${len(args)}""",
        *args)

    return CallListResponse(
        items=[CallListItem(**dict(r)) for r in rows],
        total=total, page=page, page_size=page_size)


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(call_id: int, user: CurrentUser = Depends(active_user)):
    row = await db.pool().fetchrow(
        f"""SELECT {LIST_COLUMNS},
                   c.room_name, c.sip_call_id, c.outcome, c.transfer_reason,
                   c.recording_path, c.llm_prompt_tokens,
                   c.llm_prompt_cached_tokens, c.llm_completion_tokens,
                   c.tts_characters, c.tts_audio_seconds, c.stt_audio_seconds,
                   c.stt_provider_used, c.llm_provider_used, c.tts_provider_used,
                   c.dialer_context
              FROM calls c LEFT JOIN campaigns cam ON cam.id = c.campaign_id
             WHERE c.id = $1""", call_id)

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    # Same 404 for "does not exist" and "not yours" - a distinct 403 would let a
    # tenant probe which call ids belong to other tenants.
    if not user.is_superadmin and row["tenant_id"] != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")

    turns = await db.pool().fetch(
        """SELECT seq, role, text, ts, eou_ms, stt_ms, llm_ttft_ms,
                  tts_ttfb_ms, total_ms, interrupted, kb_chunk_ids, kb_scores
             FROM turns WHERE call_id = $1 ORDER BY seq""", call_id)

    # Ordered by time so the console can interleave them with the transcript:
    # the useful view is "caller asked → this tool fired → agent answered", and
    # a tool call read on its own says nothing about what prompted it.
    #
    # Note created_at is when the call FINISHED, not when it started - that is
    # what the agent records - so a slow tool sits slightly late against the
    # turn that triggered it. Deliberate: it is the finish that the caller
    # waited for.
    invocations = await db.pool().fetch(
        """SELECT id, name, arguments, url, request, response, status_code,
                  duration_ms, error, created_at
             FROM tool_invocations WHERE call_id = $1 ORDER BY created_at, id""",
        call_id)

    d = dict(row)
    # asyncpg returns JSONB as text unless a codec is registered, and none is.
    if isinstance(d.get("dialer_context"), str):
        d["dialer_context"] = json.loads(d["dialer_context"])

    usage = CallUsage(**{k: d.pop(k) for k in (
        "llm_prompt_tokens", "llm_prompt_cached_tokens", "llm_completion_tokens",
        "tts_characters", "tts_audio_seconds", "stt_audio_seconds")})

    # Checked here rather than trusted from a column: retention deletes files
    # without touching the database, so a stored flag would go stale.
    audio = recording_file(d["sip_call_id"])

    def _inv(r) -> ToolInvocationOut:
        t = dict(r)
        if isinstance(t.get("arguments"), str):
            t["arguments"] = json.loads(t["arguments"])
        return ToolInvocationOut(**t)

    return CallDetail(**d, usage=usage,
                      recording_available=audio is not None,
                      recording_bytes=audio.stat().st_size if audio else None,
                      turns=[TurnOut(**dict(t)) for t in turns],
                      tools=[_inv(r) for r in invocations])


@router.get("/{call_id}/recording")
async def get_recording(call_id: int, request: Request,
                        user: CurrentUser = Depends(active_user)):
    """Stream a call recording, with HTTP Range support.

    Range is not optional: without it a browser's audio element cannot seek, and
    on a long call the scrubber simply does nothing.
    """
    row = await db.pool().fetchrow(
        "SELECT tenant_id, sip_call_id, started_at FROM calls WHERE id = $1", call_id)
    if row is None or (not user.is_superadmin
                       and row["tenant_id"] != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")

    path = recording_file(row["sip_call_id"])
    if path is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no recording for this call - it may have passed the retention "
            "window, or the call carried no audio")

    size = path.stat().st_size
    media = "audio/ogg"
    filename = f"call-{call_id}-{row['started_at']:%Y%m%d-%H%M}.opus"
    common = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{filename}"',
        # no-store, not max-age. A cached recording sounds like a good idea -
        # they never change - but one poisoned entry then sticks for the full
        # hour with no way for anyone to clear it. That happened: a zero-length
        # entry was served from cache with no request reaching the server at
        # all, the audio decoded as corrupt, and a hard reload did not help
        # because a JS fetch() uses the default cache mode regardless.
        #
        # These are half a megabyte on a LAN, opened one at a time by a human.
        # Re-fetching costs nothing worth having.
        "Cache-Control": "private, no-store",
    }

    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type=media, headers=common)

    m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return Response(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                        headers={"Content-Range": f"bytes */{size}"})

    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:
        # "bytes=-500" means the LAST 500 bytes, not "from 0 to 500"
        start = max(0, size - int(m.group(2)))
        end = size - 1

    if start >= size or end < start:
        return Response(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                        headers={"Content-Range": f"bytes */{size}"})
    end = min(end, size - 1)

    def chunks():
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = fh.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        chunks(), status_code=status.HTTP_206_PARTIAL_CONTENT, media_type=media,
        headers={**common,
                 "Content-Range": f"bytes {start}-{end}/{size}",
                 "Content-Length": str(end - start + 1)},
    )


@router.get("/{call_id}/kb-chunks")
async def get_call_kb_chunks(call_id: int,
                             user: CurrentUser = Depends(active_user)):
    """Text of every KB chunk cited during a call, keyed by chunk id.

    The transcript stores only chunk ids; this resolves them in one query so the
    UI can show what the agent actually retrieved next to what it said.
    """
    row = await db.pool().fetchrow(
        "SELECT tenant_id FROM calls WHERE id = $1", call_id)
    if row is None or (not user.is_superadmin
                       and row["tenant_id"] != user.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")

    chunks = await db.pool().fetch(
        """SELECT k.id, k.seq, k.page, k.heading, k.content, d.filename, d.title
             FROM kb_chunks k JOIN kb_documents d ON d.id = k.doc_id
            WHERE k.id = ANY(
                SELECT DISTINCT unnest(kb_chunk_ids) FROM turns
                 WHERE call_id = $1 AND kb_chunk_ids IS NOT NULL)""", call_id)

    return {str(c["id"]): dict(c) for c in chunks}
