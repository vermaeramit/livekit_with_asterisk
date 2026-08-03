from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import db
from ..deps import CurrentUser, active_user, tenant_scope
from ..schemas import (CallDetail, CallListItem, CallListResponse, CallUsage,
                       TurnOut)

router = APIRouter(prefix="/calls", tags=["calls"])

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
                   c.tts_characters, c.tts_audio_seconds, c.stt_audio_seconds
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

    d = dict(row)
    usage = CallUsage(**{k: d.pop(k) for k in (
        "llm_prompt_tokens", "llm_prompt_cached_tokens", "llm_completion_tokens",
        "tts_characters", "tts_audio_seconds", "stt_audio_seconds")})

    return CallDetail(**d, usage=usage,
                      turns=[TurnOut(**dict(t)) for t in turns])


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
