"""Everything the Grafana dashboard showed, tenant-scoped and in one place.

Latency percentiles come from `turns`, not `calls`: a call has no single
latency, and averaging per-call averages weights a two-turn call the same as a
thirty-turn one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from .. import costing, db
from ..deps import CurrentUser, active_user, tenant_scope
from . import rates as rates_router
from ..schemas import (AnalyticsCost, AnalyticsSummary, LatencySplit,
                       Percentiles, TimeBucket)

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


# A call shorter than this is not a call. Without a floor, "the worst cost per
# minute" reliably finds whichever one dropped fastest: the greeting is paid for
# in full and then divided by almost nothing.
PER_MINUTE_FLOOR_SEC = 30


async def _window_cost(clause: str, args: list) -> AnalyticsCost | None:
    """Price every call in the window and blend it.

    Priced in Python rather than SQL because the rules - cached tokens excluded
    from the input leg, per-provider currency, a model-specific rate beating a
    general one - live in costing.py, and a second copy of them in SQL would
    drift from the per-call figures on the very first change.

    Cheap enough: one narrow query and arithmetic. If a window ever gets big
    enough for that to hurt, the answer is a nightly roll-up, not a second
    implementation of the pricing.
    """
    rows = await db.pool().fetch(f"""
        SELECT c.id, c.duration_ms,
               c.llm_prompt_tokens, c.llm_prompt_cached_tokens,
               c.llm_completion_tokens, c.tts_characters,
               c.tts_audio_seconds, c.stt_audio_seconds,
               c.llm_provider_used, c.tts_provider_used, c.stt_provider_used,
               c.llm_model_used, c.tts_model_used, c.stt_model_used
          FROM calls c WHERE {clause}""", *args)
    if not rows:
        return None

    rates = await rates_router.load_rates()
    fx = await rates_router.usd_to_inr()
    if not rates:
        return None

    # One currency for the whole panel. Rupees when there is a rate to get
    # there, dollars otherwise - never a column with both in it.
    currency = "INR" if fx else "USD"
    total = Decimal(0)
    minutes = Decimal(0)
    priced = 0
    unpriced = 0
    worst: tuple[Decimal, int] | None = None

    for r in rows:
        call = dict(r)
        c = costing.price_call(call, rates, fx)
        if not c["priced"]:
            unpriced += 1
            continue
        priced += 1
        amount = Decimal(str(c["inr_total"] if fx else c["usd_total"]))
        total += amount
        mins = Decimal(str(call.get("duration_ms") or 0)) / Decimal(60_000)
        minutes += mins
        if mins > 0 and (call.get("duration_ms") or 0) >= PER_MINUTE_FLOOR_SEC * 1000:
            rate = amount / mins
            if worst is None or rate > worst[0]:
                worst = (rate, call["id"])

    if not priced:
        return None

    return AnalyticsCost(
        currency=currency,
        total=float(round(total, 4)),
        per_call=float(round(total / priced, 4)),
        per_minute_avg=float(round(total / minutes, 4)) if minutes > 0 else 0.0,
        per_minute_max=float(round(worst[0], 4)) if worst else None,
        per_minute_max_call_id=worst[1] if worst else None,
        per_minute_max_floor_sec=PER_MINUTE_FLOOR_SEC,
        priced_calls=priced,
        unpriced_calls=unpriced,
    )


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
               -- AHT over calls that HAVE a duration. Dividing the sum by
               -- every call counts the ones that never connected as zero-length
               -- and drags the average down.
               avg(c.duration_ms) FILTER (WHERE c.duration_ms IS NOT NULL)  AS aht_ms,
               max(c.duration_ms)                                          AS max_duration_ms,
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

    longest = await db.pool().fetchval(f"""
        SELECT c.id FROM calls c
         WHERE {clause} AND c.duration_ms IS NOT NULL
         ORDER BY c.duration_ms DESC LIMIT 1""", *args)

    # Same rule as the per-call figure: not computed unless it may be seen.
    cost = await _window_cost(clause, args) if user.can("cost.read") else None

    d = dict(totals)
    aht = d.pop("aht_ms", None)
    # Same disclosure as the Usage panel: with the rates in hand these are the
    # cost. Nulled rather than zeroed - zero would be a claim about the window.
    if not user.can("usage.read"):
        for k in ("prompt_tokens", "cached_tokens", "completion_tokens",
                  "tts_characters"):
            d[k] = None
    return AnalyticsSummary(
        **d,
        avg_duration_ms=round(aht) if aht is not None else None,
        longest_call_id=longest,
        cost=cost,
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

    # Same rule as the summary and the Usage panel. The chart these feed is
    # titled "prompt tokens", so leaving them in would have made the permission
    # cosmetic - hidden in one place and plotted in another.
    show_usage = user.can("usage.read")
    out = []
    for r in rows:
        b = dict(r)
        if not show_usage:
            b["prompt_tokens"] = None
            b["cached_tokens"] = None
        out.append(TimeBucket(**b))
    return out
