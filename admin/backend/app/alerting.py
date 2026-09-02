"""Rule evaluation and webhook delivery.

Runs as a background task inside the API rather than a separate service: it needs
the same database pool and nothing else, and a second process would be one more
thing to notice had died.

Every firing is written to `alerts` BEFORE the webhook is attempted. The row is
the record; delivery is best effort. A chat tool being down must not lose the
alert.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

from . import db

log = logging.getLogger("admin-api")

INTERVAL_SEC = 60
WEBHOOK_TIMEOUT = 10


# Each rule returns (value, breached, message) for a tenant/campaign scope.
# Percentage rules also honour min_calls: on a quiet afternoon two calls, one of
# which errored, is not a 50% error rate worth waking anyone for.

# What a status code means for the person reading the alert at 9pm. Naming the
# action is the whole point: 429, 402 and 401 are three different phone calls
# to three different people, and every one of them used to read as "an error".
_CODE_MEANING = {
    429: "rate limit - you are sending faster than the plan allows",
    402: "out of credit - top the account up",
    401: "the API key is being rejected",
    403: "the API key is not allowed to do this",
    500: "the provider is failing on their side",
    503: "the provider is unavailable",
}


def _describe(rows) -> str:
    """The top few failures, in words somebody can act on."""
    parts = []
    for r in rows[:3]:
        who = r["provider"] or "provider unknown"
        what = _CODE_MEANING.get(r["code"])
        if what:
            parts.append(f"{r['n']}x {who} {r['source']} {r['code']}: {what}")
        elif r["code"]:
            parts.append(f"{r['n']}x {who} {r['source']} {r['code']}")
        else:
            # No code and no provider is what a FallbackAdapter failure looks
            # like: it reports itself rather than the leg that failed. Say so,
            # rather than implying we know more than we do.
            parts.append(f"{r['n']}x {who} {r['source']}"
                         + (" (cause not reported)" if not r["provider"] else ""))
    more = len(rows) - 3
    return "; ".join(parts) + (f"; and {more} other kind(s)" if more > 0 else "")


async def _evaluate(rule: dict) -> tuple[float | None, bool, str]:
    kind = rule["kind"]
    window = f"{rule['window_minutes']} minutes"
    args: list = [rule["tenant_id"]]
    campaign_clause = ""
    if rule["campaign_id"] is not None:
        args.append(rule["campaign_id"])
        campaign_clause = f" AND c.campaign_id = ${len(args)}"

    scope = (f"c.tenant_id = $1 AND c.started_at > now() - interval '{window}'"
             + campaign_clause)

    if kind == "stale_calls":
        # Open rows past their own duration guardrail. Not a rate - one is a
        # worker that died holding a call, which is worth saying immediately.
        row = await db.pool().fetchrow(f"""
            SELECT count(*) AS n FROM calls c
              LEFT JOIN agent_config ac ON ac.campaign_id = c.campaign_id
             WHERE c.ended_at IS NULL AND c.tenant_id = $1{campaign_clause}
               AND EXTRACT(EPOCH FROM (now() - c.started_at))
                   > COALESCE(ac.max_duration_sec, 600) * 1.5""", *args)
        n = float(row["n"])
        return n, n >= rule["threshold"], (
            f"{int(n)} call(s) stuck open past their duration limit - "
            "most likely a worker that died mid-call")

    if kind == "no_calls":
        row = await db.pool().fetchrow(
            f"SELECT count(*) AS n FROM calls c WHERE {scope}", *args)
        n = float(row["n"])
        return n, n == 0, (
            f"no calls at all in the last {rule['window_minutes']} minutes - "
            "the dialer or the workers may be down")

    if kind == "latency_p95":
        row = await db.pool().fetchrow(f"""
            SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY t.total_ms) AS v,
                   count(DISTINCT c.id) AS calls
              FROM turns t JOIN calls c ON c.id = t.call_id
             WHERE {scope} AND t.total_ms IS NOT NULL""", *args)
        if row["v"] is None or row["calls"] < rule["min_calls"]:
            return None, False, ""
        v = float(row["v"])
        return v, v > rule["threshold"], (
            f"p95 response time is {v / 1000:.2f}s, above the "
            f"{rule['threshold'] / 1000:.2f}s threshold")

    if kind == "provider_errors":
        # Counted from the errors themselves, not from what share of FINISHED
        # calls carried one. During a rate limit the calls are still running:
        # error_rate reports the problem after it is over, and reports it as a
        # percentage that says nothing about the cause.
        #
        # The alert that prompted this said "25.7% of the last 35 calls ended
        # in an error". True, acknowledged, and it took the worker journal to
        # find out that OpenAI had hit its token-per-minute ceiling.
        err_scope = (f"tenant_id = $1 AND created_at > now() - interval "
                     f"'{window}'")
        if rule["campaign_id"] is not None:
            err_scope += f" AND campaign_id = ${len(args)}"
        rows = await db.pool().fetch(f"""
            SELECT source, provider, code, count(*) AS n
              FROM call_errors WHERE {err_scope}
             GROUP BY 1, 2, 3 ORDER BY n DESC""", *args)

        total = sum(r["n"] for r in rows)
        if not total:
            return 0, False, ""
        return (total, total >= rule["threshold"],
                f"{total} provider error(s) in {rule['window_minutes']} "
                f"minutes - {_describe(rows)}")

    column = {
        "error_rate": "c.end_reason = 'error'",
        "transfer_rate": "c.transferred_to IS NOT NULL",
        "limit_hits": "c.limit_hit IS NOT NULL",
    }.get(kind)
    if column is None:
        return None, False, ""

    row = await db.pool().fetchrow(f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE {column}) AS hits
          FROM calls c WHERE {scope}""", *args)
    if row["total"] < rule["min_calls"]:
        return None, False, ""

    pct = 100.0 * row["hits"] / row["total"]
    label = {"error_rate": "ended in an error",
             "transfer_rate": "were handed to a human",
             "limit_hits": "were stopped by a guardrail"}[kind]
    return pct, pct > rule["threshold"], (
        f"{pct:.1f}% of the last {row['total']} calls {label}, above the "
        f"{rule['threshold']:.0f}% threshold")


async def _deliver(alert_id: int, tenant_name: str, webhook: str | None,
                   payload: dict) -> None:
    if not webhook:
        await db.pool().execute(
            "UPDATE alerts SET delivery = 'skipped' WHERE id = $1", alert_id)
        return

    # `text` first, because Slack and Teams both render that field and ignore
    # the rest; the structured keys are for anything else.
    body = json.dumps({
        "text": f"[{payload['severity'].upper()}] {tenant_name}: {payload['message']}",
        **payload,
    }).encode()

    def post() -> None:
        req = urllib.request.Request(
            webhook, data=body, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT):
            pass

    try:
        # urllib is blocking; off the event loop or a hanging webhook stalls
        # every request the API is serving.
        await asyncio.to_thread(post)
        await db.pool().execute(
            "UPDATE alerts SET delivery = 'sent' WHERE id = $1", alert_id)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        if isinstance(e, urllib.error.HTTPError):
            detail = f"HTTP {e.code}"
        log.warning("alert %s webhook failed: %s", alert_id, detail)
        await db.pool().execute(
            "UPDATE alerts SET delivery = 'failed', delivery_error = $2 "
            "WHERE id = $1", alert_id, detail[:400])


async def evaluate_once() -> int:
    """-> number of alerts raised."""
    rules = await db.pool().fetch("""
        SELECT r.*, t.name AS tenant_name, t.webhook_url, t.status AS tenant_status
          FROM alert_rules r JOIN tenants t ON t.id = r.tenant_id
         WHERE r.enabled AND t.status = 'active'""")

    raised = 0
    for r in rules:
        rule = dict(r)
        try:
            value, breached, message = await _evaluate(rule)
        except Exception:
            log.exception("alert rule %s (%s) failed to evaluate",
                          rule["id"], rule["kind"])
            continue

        await db.pool().execute(
            "UPDATE alert_rules SET last_checked_at = now() WHERE id = $1",
            rule["id"])

        if not breached:
            # Re-arm. Without this a rule fires once and never again.
            if rule["firing"]:
                await db.pool().execute(
                    "UPDATE alert_rules SET firing = false WHERE id = $1", rule["id"])
            continue

        if rule["firing"]:
            continue        # already reported; stay quiet until it clears

        alert_id = await db.pool().fetchval("""
            INSERT INTO alerts (tenant_id, campaign_id, rule_id, kind, severity,
                                message, value, threshold)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
            rule["tenant_id"], rule["campaign_id"], rule["id"], rule["kind"],
            rule["severity"], message, value, rule["threshold"])
        await db.pool().execute(
            "UPDATE alert_rules SET firing = true, last_fired_at = now() "
            "WHERE id = $1", rule["id"])
        raised += 1

        await _deliver(alert_id, rule["tenant_name"], rule["webhook_url"], {
            "alert_id": alert_id,
            "kind": rule["kind"],
            "severity": rule["severity"],
            "message": message,
            "value": value,
            "threshold": rule["threshold"],
            "tenant": rule["tenant_name"],
        })

    return raised


async def run_forever() -> None:
    log.info("alert evaluator started (every %ss)", INTERVAL_SEC)
    while True:
        try:
            raised = await evaluate_once()
            if raised:
                log.info("raised %d alert(s)", raised)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one bad evaluation stop the loop - a monitor that dies
            # silently is worse than no monitor.
            log.exception("alert evaluation cycle failed")
        await asyncio.sleep(INTERVAL_SEC)
