"""Reports: the same data the console already shows, grouped for a question.

ONE ROUTER FOR ALL OF THEM, and each report is a function here rather than a
page of its own. The shell - client, campaign, date range, download - is the
same for every one, and the thing that differs is a query and a list of
columns. Making that the only thing that differs is what keeps the second
report cheap.

PERMISSIONS ARE PER REPORT, not for the section. `reports.read` opens the
section; a report that shows money also requires `cost.read`, and one that
shows tokens requires `usage.read`. Without that a report becomes a way around
the rest of permissions.py - somebody refused cost on the Calls page would open
a report and read the same figure. The console hides what a role cannot open,
and these guards are what make that true rather than decorative.

DOWNLOADING IS ITS OWN PERMISSION. A figure read on screen stays here; a file
does not. `reports.export` is checked separately, and a role can hold one
without the other.

NOTHING HERE WORKS OUT A PRICE. costing.price_call does that, exactly as the
call detail page and the dashboard do, so the three cannot disagree about what
a day cost. The window and the tenant filter come from the analytics router for
the same reason: that picker already decided what "to 9 Sept" means, and it
cost most of the 9th to find out.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from .. import costing, db
from ..deps import CurrentUser, require_perm
from ..schemas import ProviderCostReport, ProviderCostRow
from . import analytics, rates as rates_router

router = APIRouter(prefix="/reports", tags=["reports"])

# Opening the section at all.
reader = require_perm("reports.read")
# This report shows money and usage, so it needs both of those as well. See the
# module docstring: the guard is the whole point.
cost_reader = require_perm("reports.read", "cost.read", "usage.read")
exporter = require_perm("reports.read", "cost.read", "usage.read", "reports.export")


async def _provider_cost_rows(user: CurrentUser, tenant_id: int | None,
                              campaign_id: int | None,
                              date_from: datetime, date_to: datetime
                              ) -> ProviderCostReport:
    clause, args = analytics._filters(user, tenant_id, campaign_id,
                                      date_from, date_to)
    calls = await db.pool().fetch(
        f"""SELECT c.duration_ms,
                   c.llm_prompt_tokens, c.llm_prompt_cached_tokens,
                   c.llm_completion_tokens,
                   c.tts_characters, c.tts_audio_seconds, c.stt_audio_seconds,
                   c.stt_provider_used, c.llm_provider_used, c.tts_provider_used,
                   c.stt_model_used, c.llm_model_used, c.tts_model_used
              FROM calls c
             WHERE {clause}""", *args)

    rate_rows = await rates_router.load_rates()
    fx = await rates_router.usd_to_inr()

    groups: dict[tuple, dict] = {}
    for row in calls:
        call = dict(row)
        key = (call["stt_provider_used"], call["llm_provider_used"],
               call["tts_provider_used"])
        g = groups.setdefault(key, {
            "calls": 0, "ms": 0, "llm_tokens": 0, "stt_seconds": Decimal(0),
            "tts_characters": 0, "priced": 0, "missing": set(),
            # Per leg as well as in total. costing already splits them, so
            # carrying the split costs nothing and answers the question the
            # total raises: which third of this is the expensive one.
            "inr": {"stt": Decimal(0), "llm": Decimal(0), "tts": Decimal(0)},
        })
        g["calls"] += 1
        g["ms"] += call.get("duration_ms") or 0
        # Prompt tokens already include the cached ones - see costing's own
        # note. Added once, not twice.
        g["llm_tokens"] += ((call.get("llm_prompt_tokens") or 0)
                            + (call.get("llm_completion_tokens") or 0))
        g["stt_seconds"] += Decimal(str(call.get("stt_audio_seconds") or 0))
        g["tts_characters"] += call.get("tts_characters") or 0

        priced = costing.price_call(call, rate_rows, fx)
        g["missing"].update(priced["missing_rates"])
        if priced["priced"]:
            g["priced"] += 1
            # inr_total is only present when an exchange rate is set. Without
            # one the report has no single currency to add up in, and says so
            # in `caveats` rather than mixing rupees into dollars.
            if "inr" in priced:
                for leg in ("stt", "llm", "tts"):
                    g["inr"][leg] += Decimal(str(priced["inr"].get(leg) or 0))

    rows: list[ProviderCostRow] = []
    for (stt, llm, tts), g in groups.items():
        minutes = Decimal(g["ms"]) / Decimal(60_000)
        have_money = fx is not None and g["priced"] > 0
        # Totalled from the legs rather than from costing's own inr_total.
        # They differ by rounding alone - costing rounds each leg to four
        # decimals - and measured over two hundred calls that came to 0.0004
        # rupees, against a figure shown to two. Summing the legs is what makes
        # the three columns add up to the fourth on screen, which is the thing
        # a reader will actually check.
        total = sum(g["inr"].values(), Decimal(0))

        # Total cost over total minutes, NOT the average of each call's own
        # per-minute figure. The second lets a five-second call weigh as much
        # as a ten-minute one, and a load test full of calls that died after
        # the greeting would decide the number.
        #
        # The legs are divided by the same minutes as the total, so the three
        # add up to it. A reader checking that by hand should find it true.
        def money(v: Decimal) -> float | None:
            return float(round(v, 2)) if have_money else None

        def per_minute(v: Decimal) -> float | None:
            if not have_money or minutes <= 0:
                return None
            return float(round(v / minutes, 4))

        rows.append(ProviderCostRow(
            stt=stt, llm=llm, tts=tts,
            calls=g["calls"], minutes=float(round(minutes, 2)),
            llm_tokens=g["llm_tokens"],
            stt_seconds=float(round(g["stt_seconds"], 1)),
            tts_characters=g["tts_characters"],
            inr_stt=money(g["inr"]["stt"]), inr_llm=money(g["inr"]["llm"]),
            inr_tts=money(g["inr"]["tts"]), inr_total=money(total),
            inr_stt_per_minute=per_minute(g["inr"]["stt"]),
            inr_llm_per_minute=per_minute(g["inr"]["llm"]),
            inr_tts_per_minute=per_minute(g["inr"]["tts"]),
            inr_per_minute=per_minute(total),
            priced_calls=g["priced"],
            missing_rates=sorted(g["missing"]),
            any_usage=bool(g["llm_tokens"] or g["tts_characters"]
                           or g["stt_seconds"] > 0),
        ))

    # Dearest per minute first - that is the row somebody opened this to find.
    # Unpriced rows last rather than sorted as though they were free.
    rows.sort(key=lambda r: (r.inr_per_minute is None, -(r.inr_per_minute or 0)))

    caveats: list[str] = []
    if fx is None:
        caveats.append(
            "No exchange rate is set, so nothing can be totalled in rupees - "
            "some providers bill in dollars and some in rupees, and the two "
            "cannot be added without one. Set it under Provider rates.")
    # On missing_rates, NOT on priced_calls. A call can be priced and still be
    # short: costing reports priced=True when ANY leg had a rate, so a call
    # whose speech-to-text and model were priced but whose voice was not comes
    # back as priced with the gap only in missing_rates. Gating this on the
    # count would have hidden exactly that call.
    if any(r.missing_rates for r in rows):
        caveats.append(
            "Some legs had no rate. Those rows are short by whatever those legs "
            "cost - the missing rates are named on each row, and a row is only "
            "complete when that column is empty.")
    # Only rows that actually used something. A row with no usage at all is
    # not an unpriced row - there is nothing in it to price - and warning about
    # it sends somebody to look for a rate that is not missing.
    if any(r.priced_calls < r.calls and r.any_usage for r in rows):
        caveats.append(
            "Some calls could not be priced at all and contribute nothing to "
            "their row's total, though their minutes are still counted.")
    if any(not r.any_usage for r in rows):
        caveats.append(
            "A row showing no usage is calls that ended before any provider "
            "was asked for anything - usually after the greeting, which plays "
            "from a cached file. They cost nothing and are shown so the call "
            "count adds up.")
    if any("," in (r.llm or "") or "," in (r.tts or "") or "," in (r.stt or "")
           for r in rows):
        caveats.append(
            "A row with two providers in a column is a call where a fallback "
            "took over mid-way. It is priced at the primary's rate, because "
            "how much of the call each one served is not recorded.")

    return ProviderCostReport(date_from=date_from, date_to=date_to,
                              usd_to_inr=float(fx) if fx else None,
                              rows=rows, caveats=caveats)


@router.get("/provider-cost", response_model=ProviderCostReport)
async def provider_cost(
    tenant_id: int | None = None,
    campaign_id: int | None = None,
    days: int = Query(7, ge=1, le=366),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    user: CurrentUser = Depends(cost_reader),
):
    """What each combination of providers cost, per minute, on real calls.

    The Price comparison page shows what providers CHARGE. This shows what they
    were actually paid, on the calls that happened - which differs, because a
    call does not spend its minutes evenly across the three legs.
    """
    start, end = analytics._window(days, date_from, date_to)
    return await _provider_cost_rows(user, tenant_id, campaign_id, start, end)


@router.get("/provider-cost.csv")
async def provider_cost_csv(
    tenant_id: int | None = None,
    campaign_id: int | None = None,
    days: int = Query(7, ge=1, le=366),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    user: CurrentUser = Depends(exporter),
):
    start, end = analytics._window(days, date_from, date_to)
    report = await _provider_cost_rows(user, tenant_id, campaign_id, start, end)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["STT", "LLM", "TTS", "Calls", "Minutes",
                "LLM tokens", "STT seconds", "TTS characters",
                "STT INR", "LLM INR", "TTS INR", "Total INR",
                "STT INR/min", "LLM INR/min", "TTS INR/min", "Total INR/min",
                "Priced calls", "Missing rates"])

    # Blank, not 0. A spreadsheet will happily sum a zero and report a total
    # that was never true.
    def cell(v):
        return "" if v is None else v

    for r in report.rows:
        w.writerow([r.stt or "", r.llm or "", r.tts or "", r.calls, r.minutes,
                    r.llm_tokens, r.stt_seconds, r.tts_characters,
                    cell(r.inr_stt), cell(r.inr_llm), cell(r.inr_tts),
                    cell(r.inr_total),
                    cell(r.inr_stt_per_minute), cell(r.inr_llm_per_minute),
                    cell(r.inr_tts_per_minute), cell(r.inr_per_minute),
                    r.priced_calls, "; ".join(r.missing_rates)])

    name = f"provider-cost-{start.date()}-to-{end.date()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})
