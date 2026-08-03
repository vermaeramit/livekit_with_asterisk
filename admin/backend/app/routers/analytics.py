"""Everything the Grafana dashboard showed, tenant-scoped and in one place.

Latency percentiles come from `turns`, not `calls`: a call has no single
latency, and averaging per-call averages weights a two-turn call the same as a
thirty-turn one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from .. import db
from ..deps import CurrentUser, active_user, tenant_scope
from ..schemas import AnalyticsSummary, LatencySplit, Percentiles, TimeBucket

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _window(days: int) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days), now


def _filters(user: CurrentUser, tenant_id: int | None, campaign_id: int | None,
             date_from: datetime, date_to: datetime) -> tuple[str, list]:
    scope = tenant_scope(user, tenant_id)
    where = ["c.started_at >= $1", "c.started_at < $2"]
    args: list = [date_from, date_to]
    if scope is not None:
        args.append(scope)
        where.append(f"c.tenant_id = ${len(args)}")
    if campaign_id is not None:
        args.append(campaign_id)
        where.append(f"c.campaign_id = ${len(args)}")
    return " AND ".join(where), args


@router.get("/summary", response_model=AnalyticsSummary)
async def summary(
    user: CurrentUser = Depends(active_user),
    days: int = Query(7, ge=1, le=365),
    tenant_id: int | None = None,
    campaign_id: int | None = None,
):
    date_from, date_to = _window(days)
    clause, args = _filters(user, tenant_id, campaign_id, date_from, date_to)

    totals = await db.pool().fetchrow(f"""
        SELECT count(*)                                                   AS calls,
               count(*) FILTER (WHERE c.transferred_to IS NOT NULL)       AS transferred,
               count(*) FILTER (WHERE c.limit_hit IS NOT NULL)            AS limit_hit,
               count(*) FILTER (WHERE c.end_reason = 'error')             AS errors,
               COALESCE(sum(c.duration_ms), 0)                            AS total_duration_ms,
               COALESCE(sum(c.turn_count), 0)                             AS total_turns,
               COALESCE(sum(c.llm_prompt_tokens), 0)                      AS prompt_tokens,
               COALESCE(sum(c.llm_prompt_cached_tokens), 0)               AS cached_tokens,
               COALESCE(sum(c.llm_completion_tokens), 0)                  AS completion_tokens,
               COALESCE(sum(c.tts_characters), 0)                         AS tts_characters
          FROM calls c WHERE {clause}""", *args)

    # percentile_cont interpolates, which is what "p95" is normally taken to
    # mean; percentile_disc would snap to an actual observation instead.
    lat = await db.pool().fetchrow(f"""
        SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY t.total_ms) AS p50,
               percentile_cont(0.90) WITHIN GROUP (ORDER BY t.total_ms) AS p90,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY t.total_ms) AS p95,
               max(t.total_ms)                                          AS worst,
               count(*)                                                 AS turns,
               percentile_cont(0.50) WITHIN GROUP (ORDER BY t.eou_ms)      AS eou,
               percentile_cont(0.50) WITHIN GROUP (ORDER BY t.llm_ttft_ms) AS llm,
               percentile_cont(0.50) WITHIN GROUP (ORDER BY t.tts_ttfb_ms) AS tts
          FROM turns t JOIN calls c ON c.id = t.call_id
         WHERE {clause} AND t.total_ms IS NOT NULL""", *args)

    reasons = await db.pool().fetch(f"""
        SELECT COALESCE(
                   CASE WHEN c.limit_hit IS NOT NULL THEN 'limit'
                        WHEN c.transferred_to IS NOT NULL THEN 'transferred'
                        ELSE c.end_reason END, 'unknown') AS reason,
               count(*) AS n
          FROM calls c WHERE {clause}
         GROUP BY 1 ORDER BY 2 DESC""", *args)

    d = dict(totals)
    return AnalyticsSummary(
        **d,
        avg_duration_ms=round(d["total_duration_ms"] / d["calls"]) if d["calls"] else None,
        latency=Percentiles(
            p50=lat["p50"], p90=lat["p90"], p95=lat["p95"],
            worst=lat["worst"], turns=lat["turns"]),
        # stt_ms is NOT a fourth slice: it is already inside eou_ms, and adding
        # it double-counts. That mistake shipped once in this project.
        split=LatencySplit(eou_ms=lat["eou"], llm_ttft_ms=lat["llm"],
                           tts_ttfb_ms=lat["tts"]),
        end_reasons={r["reason"]: r["n"] for r in reasons},
    )


@router.get("/timeseries", response_model=list[TimeBucket])
async def timeseries(
    user: CurrentUser = Depends(active_user),
    days: int = Query(7, ge=1, le=365),
    tenant_id: int | None = None,
    campaign_id: int | None = None,
):
    date_from, date_to = _window(days)
    clause, args = _filters(user, tenant_id, campaign_id, date_from, date_to)

    # Hourly buckets stop being readable past a couple of days, and daily ones
    # hide the intraday shape below that.
    bucket = "hour" if days <= 2 else "day"

    # Call-level and turn-level aggregates are computed separately and joined on
    # the bucket. Doing both over one joined set would multiply each call row by
    # its turn count and inflate every call-level sum.
    rows = await db.pool().fetch(f"""
        WITH scoped AS (
            SELECT c.id, c.started_at, c.transferred_to, c.limit_hit,
                   c.llm_prompt_tokens, c.llm_prompt_cached_tokens
              FROM calls c WHERE {clause}
        ),
        call_agg AS (
            SELECT date_trunc('{bucket}', started_at)                     AS bucket,
                   count(*)                                               AS calls,
                   count(*) FILTER (WHERE transferred_to IS NOT NULL)     AS transferred,
                   count(*) FILTER (WHERE limit_hit IS NOT NULL)          AS limit_hit,
                   COALESCE(sum(llm_prompt_tokens), 0)                    AS prompt_tokens,
                   COALESCE(sum(llm_prompt_cached_tokens), 0)             AS cached_tokens
              FROM scoped GROUP BY 1
        ),
        turn_agg AS (
            -- bucketed by the CALL's start, not the turn's, so the two series
            -- line up on the same x axis
            SELECT date_trunc('{bucket}', s.started_at)                   AS bucket,
                   percentile_cont(0.50) WITHIN GROUP (ORDER BY t.total_ms)    AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY t.total_ms)    AS p95,
                   percentile_cont(0.50) WITHIN GROUP (ORDER BY t.eou_ms)      AS eou_ms,
                   percentile_cont(0.50) WITHIN GROUP (ORDER BY t.llm_ttft_ms) AS llm_ttft_ms,
                   percentile_cont(0.50) WITHIN GROUP (ORDER BY t.tts_ttfb_ms) AS tts_ttfb_ms
              FROM turns t JOIN scoped s ON s.id = t.call_id
             WHERE t.total_ms IS NOT NULL
             GROUP BY 1
        )
        SELECT ca.bucket, ca.calls, ca.transferred, ca.limit_hit,
               ca.prompt_tokens, ca.cached_tokens,
               ta.p50, ta.p95, ta.eou_ms, ta.llm_ttft_ms, ta.tts_ttfb_ms
          FROM call_agg ca LEFT JOIN turn_agg ta USING (bucket)
         ORDER BY ca.bucket""", *args)

    return [TimeBucket(**dict(r)) for r in rows]
