#!/usr/bin/env python3
"""End-to-end check of the admin API. Standard library only - no pip, no jq.

    python3 admin/smoke_test.py --email you@example.com

Covers the paths that are easy to get subtly wrong and hard to notice: refresh
rotation, tenant scoping, and unauthenticated access. Tokens are never printed.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
failures = 0


def _json_or_none(raw: bytes) -> dict | None:
    """Bodies here are usually JSON, but not always - the recording endpoint
    returns audio. Decoding blindly crashed the whole run on the first one."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"_bytes": len(raw)}


def fetch_headers(base: str, path: str, *, token: str,
                  extra: dict[str, str] | None = None
                  ) -> tuple[int, dict[str, str], int]:
    """-> (status, headers, body length). For endpoints whose body is not JSON.

    Header names are lowercased. They are case-insensitive on the wire and
    Starlette emits them lowercase, so looking up "Content-Range" finds nothing
    and reports a missing header that was in fact sent.
    """
    req = urllib.request.Request(base + path)
    req.add_header("Authorization", f"Bearer {token}")
    for k, v in (extra or {}).items():
        req.add_header(k, v)

    def lower(headers) -> dict[str, str]:
        return {k.lower(): v for k, v in headers.items()}

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, lower(r.headers), len(r.read())
    except urllib.error.HTTPError as e:
        return e.code, lower(e.headers), len(e.read())


def call(base: str, path: str, *, method: str = "GET",
         body: dict | None = None, token: str | None = None
         ) -> tuple[int, dict | None]:
    req = urllib.request.Request(base + path, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, _json_or_none(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _json_or_none(e.read())
    except urllib.error.URLError as e:
        print(f"  cannot reach {base}: {e.reason}", file=sys.stderr)
        raise SystemExit(2)


def _last_json(raw: bytes) -> dict | None:
    """Parse a body that may be one JSON object or a stream of them.

    A successful upload returns newline-delimited progress events; an error
    before the stream opens returns a single JSON object. The final line is the
    outcome in both cases.
    """
    if not raw:
        return None
    text = raw.decode(errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.strip().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return {"detail": text[:200]}


def post_file(base: str, path: str, *, filename: str, content: bytes,
              token: str) -> tuple[int, dict | None]:
    """Multipart upload, hand-rolled - stdlib has no helper for it."""
    boundary = "----aivoice-smoke-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(base + path, method="POST", data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        # ingestion is synchronous; a long PDF takes a while
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, _last_json(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _last_json(e.read())


def redact(body) -> str:
    """Response bodies get printed on failure - strip anything token-shaped first."""
    if isinstance(body, dict):
        return json.dumps({k: ("***" if "token" in k.lower() else v)
                           for k, v in body.items()})
    return str(body)[:200]


def check(label: str, ok: bool, note: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{PASS if ok else FAIL}] {label}{'  ' + note if note else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8090/api")
    ap.add_argument("--email", required=True)
    args = ap.parse_args()
    pw = getpass.getpass("Password: ")

    print("\nhealth")
    st, body = call(args.base, "/health")
    check("GET /health -> 200 ok", st == 200 and body == {"status": "ok"},
          f"got {st}")

    print("\nauth")
    st, body = call(args.base, "/auth/login", method="POST",
                    body={"email": args.email, "password": pw})
    check("login -> 200", st == 200, "" if st == 200 else f"got {st} {redact(body)}")
    if st != 200:
        return 1
    access, refresh = body["access_token"], body["refresh_token"]
    check("returns access + refresh", bool(access and refresh))

    st, body = call(args.base, "/auth/login", method="POST",
                    body={"email": args.email, "password": pw + "x"})
    check("wrong password -> 401", st == 401, f"got {st}")

    # Must be a syntactically VALID address that simply does not exist. A reserved
    # TLD like .invalid is rejected by email-validator before the handler runs and
    # returns 422, which tests pydantic rather than the enumeration defence.
    st, _ = call(args.base, "/auth/login", method="POST",
                 body={"email": "no-such-user@example.com",
                       "password": "whatever12345"})
    check("unknown email -> 401 (same as wrong password)", st == 401, f"got {st}")

    st, body = call(args.base, "/auth/me", token=access)
    check("me -> 200", st == 200, f"got {st}")
    me_id = body["id"] if st == 200 else 0
    if st == 200:
        check("role is superadmin", body["role"] == "superadmin", body.get("role", ""))
        check("superadmin has no tenant", body["tenant_id"] is None)
        check("no pending password change", body.get("must_change_password") is False)

    st, _ = call(args.base, "/auth/me")
    check("me without token -> 401", st == 401, f"got {st}")

    st, _ = call(args.base, "/auth/me", token=access + "tampered")
    check("me with tampered token -> 401", st == 401, f"got {st}")

    print("\nrefresh rotation")
    st, body = call(args.base, "/auth/refresh", method="POST",
                    body={"refresh_token": refresh})
    check("refresh -> 200", st == 200, f"got {st}")
    new_refresh = body["refresh_token"] if st == 200 else None
    if new_refresh:
        check("issues a different refresh token", new_refresh != refresh)
    st, _ = call(args.base, "/auth/refresh", method="POST",
                 body={"refresh_token": refresh})
    check("old refresh token is dead -> 401", st == 401, f"got {st}")

    print("\ncalls")
    st, body = call(args.base, "/calls?page_size=3", token=access)
    check("list -> 200", st == 200, f"got {st}")
    if st != 200:
        return 1
    total, items = body["total"], body["items"]
    check("total is populated", total > 0, f"total={total}")
    check("page_size honoured", len(items) <= 3, f"got {len(items)}")
    check("campaign is joined", all(i["campaign_id"] for i in items),
          "every call should be linked by migration 001")

    st, body2 = call(args.base, "/calls?page_size=3&transferred=true", token=access)
    check("filter transferred=true -> 200", st == 200, f"got {st}")
    if st == 200:
        check("filter narrows the result", body2["total"] <= total,
              f"{body2['total']} of {total}")

    st, _ = call(args.base, "/calls", token=None)
    check("list without token -> 401", st == 401, f"got {st}")

    if items:
        cid = items[0]["id"]
        st, detail = call(args.base, f"/calls/{cid}", token=access)
        check(f"detail /calls/{cid} -> 200", st == 200, f"got {st}")
        if st == 200:
            check("has usage block", "usage" in detail)
            check("has turns", isinstance(detail["turns"], list),
                  f"{len(detail.get('turns', []))} turns")
        st, _ = call(args.base, f"/calls/{cid}/kb-chunks", token=access)
        check("kb-chunks -> 200", st == 200, f"got {st}")

        # Asserted across the page, not on the newest call. A caller who hangs
        # up during the greeting leaves real turns and no completed response
        # cycle, so demanding latency from whichever call happens to be last
        # fails on a perfectly normal call.
        timed = 0
        for it in items:
            _, d = call(args.base, f"/calls/{it['id']}", token=access)
            timed += sum(1 for t in (d or {}).get("turns", []) if t.get("total_ms"))
        check("latency is recorded somewhere in the recent set", timed > 0,
              f"{timed} timed turns across {len(items)} calls")

    st, _ = call(args.base, "/calls/99999999", token=access)
    check("missing call -> 404", st == 404, f"got {st}")

    print("\nrecordings")
    if items:
        newest = items[0]["id"]
        _, detail = call(args.base, f"/calls/{newest}", token=access)
        available = bool((detail or {}).get("recording_available"))
        print(f"  call {newest}: recording "
              f"{'present' if available else 'absent (not recorded, or expired)'}")
        st, headers, size = fetch_headers(args.base, f"/calls/{newest}/recording",
                                          token=access)
        # The flag is resolved from disk, so it must agree with the endpoint.
        # A mismatch means the console offers a player for audio that is gone.
        check("endpoint agrees with recording_available",
              (st == 200) == available, f"got {st}, flag={available}")

        if st == 200:
            check("served as audio", headers.get("content-type") == "audio/ogg",
                  headers.get("content-type", "missing"))
            check("advertises byte ranges",
                  headers.get("accept-ranges") == "bytes",
                  headers.get("accept-ranges", "missing"))

            # Seeking in a browser's audio element is entirely a Range feature.
            # Without 206 the scrubber moves and the audio does not.
            st_r, h_r, n_r = fetch_headers(
                args.base, f"/calls/{newest}/recording", token=access,
                extra={"Range": "bytes=0-99"})
            check("Range request -> 206", st_r == 206, f"got {st_r}")
            check("returns exactly the requested bytes", n_r == 100, f"got {n_r}")
            check("Content-Range is correct",
                  h_r.get("content-range", "").startswith(f"bytes 0-99/{size}"),
                  h_r.get("content-range", "missing"))

            # "bytes=-64" is the LAST 64 bytes, not the first 64 - the easiest
            # part of the spec to implement backwards.
            st_s, h_s, n_s = fetch_headers(
                args.base, f"/calls/{newest}/recording", token=access,
                extra={"Range": "bytes=-64"})
            check("suffix range returns the tail", st_s == 206 and n_s == 64,
                  f"got {st_s}, {n_s} bytes")
            check("suffix range points at the end",
                  h_s.get("content-range", "").startswith(
                      f"bytes {size - 64}-{size - 1}/"),
                  h_s.get("content-range", "missing"))

            st_b, h_b, _ = fetch_headers(
                args.base, f"/calls/{newest}/recording", token=access,
                extra={"Range": f"bytes={size + 10}-"})
            check("range past the end -> 416", st_b == 416, f"got {st_b}")

        st, _ = call(args.base, f"/calls/{newest}/recording", token=None)
        check("recording without a token -> 401", st == 401, f"got {st}")

    st, _ = call(args.base, "/calls/99999999/recording", token=access)
    check("recording for a missing call -> 404", st == 404, f"got {st}")

    print("\ntenants / campaigns")
    st, tenants = call(args.base, "/tenants", token=access)
    check("tenants -> 200", st == 200, f"got {st}")
    check("default tenant is seeded", any(t["slug"] == "default" for t in tenants or []))

    st, camps = call(args.base, "/campaigns", token=access)
    check("campaigns -> 200", st == 200, f"got {st}")
    check("default campaign is seeded", any(c["slug"] == "default" for c in camps or []))
    check("campaign reports its agent config",
          all(c.get("config_name") for c in camps or []),
          "a campaign with no agent_config cannot take a call")

    st, body = call(args.base, "/tenants", method="POST", token=access,
                    body={"slug": "Not A Slug!", "name": "x"})
    check("invalid slug -> 422", st == 422, f"got {st}")

    st, _ = call(args.base, "/tenants", method="POST", token=access,
                 body={"slug": "default", "name": "duplicate"})
    check("duplicate tenant slug -> 409", st == 409, f"got {st}")

    print("\nagent config")
    default_campaign = next((c for c in camps or [] if c["slug"] == "default"), None)
    if default_campaign:
        cmp_id = default_campaign["id"]
        st, cfg = call(args.base, f"/campaigns/{cmp_id}/config", token=access)
        check("config -> 200", st == 200, f"got {st}")
        if st == 200:
            check("instructions are present", bool(cfg.get("instructions")))
            check("transfer target is a SIP URI",
                  str(cfg.get("transfer_to", "")).startswith("sip:"),
                  cfg.get("transfer_to", ""))
            # The workers ignore the *_provider columns, so the API must not
            # present them as if changing them would do anything.
            check("providers are not exposed",
                  not any(k.endswith("_provider") for k in cfg))
            # A campaign created from the panel used to inherit
            # llm_model='gemini-flash-latest' from a stale column default and
            # die with 404 on its first call.
            check("no campaign is left on a model we do not use",
                  not str(cfg.get("llm_model", "")).startswith("gemini"),
                  cfg.get("llm_model", ""))
            check("agent_config.enabled is not exposed", "enabled" not in cfg,
                  "disabling it makes load_config raise and calls ring forever")

            st, _ = call(args.base, f"/campaigns/{cmp_id}/config", method="PATCH",
                         token=access, body={"llm_temperature": 5})
            check("temperature out of range -> 422", st == 422, f"got {st}")

            st, _ = call(args.base, f"/campaigns/{cmp_id}/config", method="PATCH",
                         token=access, body={"transfer_to": "800@example.com"})
            check("non-SIP transfer target -> 422", st == 422, f"got {st}")

            # round-trip a real edit, then put it back
            original = cfg["kb_top_k"]
            st, updated = call(args.base, f"/campaigns/{cmp_id}/config", method="PATCH",
                               token=access, body={"kb_top_k": original + 1})
            check("valid edit -> 200", st == 200, f"got {st}")
            if st == 200:
                check("edit is persisted", updated["kb_top_k"] == original + 1)
            call(args.base, f"/campaigns/{cmp_id}/config", method="PATCH",
                 token=access, body={"kb_top_k": original})

            st, entries = call(args.base, f"/campaigns/{cmp_id}/audit", token=access)
            check("audit -> 200", st == 200, f"got {st}")
            check("the edit was logged",
                  any(e["entity"] == "agent_config" for e in entries or []))

    st, _ = call(args.base, "/campaigns/99999999/config", token=access)
    check("config for a missing campaign -> 404", st == 404, f"got {st}")

    print("\nanalytics")
    st, sm = call(args.base, "/analytics/summary?days=365", token=access)
    check("summary -> 200", st == 200, f"got {st}")
    if st == 200:
        check("call count matches the calls list", sm["calls"] == total,
              f"{sm['calls']} vs {total}")
        check("latency percentiles are ordered",
              (sm["latency"]["p50"] or 0) <= (sm["latency"]["p95"] or 0)
              <= (sm["latency"]["worst"] or 0),
              f"p50={sm['latency']['p50']} p95={sm['latency']['p95']} "
              f"worst={sm['latency']['worst']}")
        check("cached tokens do not exceed prompt tokens",
              sm["cached_tokens"] <= sm["prompt_tokens"],
              f"{sm['cached_tokens']} of {sm['prompt_tokens']}")
        check("end reasons account for every call",
              sum(sm["end_reasons"].values()) == sm["calls"],
              f"{sum(sm['end_reasons'].values())} vs {sm['calls']}")
        # stt_ms lives inside eou_ms; a fourth slice would double-count
        check("latency split has exactly three stages",
              set(sm["split"]) == {"eou_ms", "llm_ttft_ms", "tts_ttfb_ms"},
              str(sorted(sm["split"])))

    st, ts = call(args.base, "/analytics/timeseries?days=365", token=access)
    check("timeseries -> 200", st == 200, f"got {st}")
    if st == 200 and ts:
        check("buckets sum to the summary total",
              sum(b["calls"] for b in ts) == sm["calls"],
              f"{sum(b['calls'] for b in ts)} vs {sm['calls']}")
        # a join against turns would multiply these by the turn count
        check("token totals are not inflated by the turn join",
              sum(b["prompt_tokens"] for b in ts) == sm["prompt_tokens"],
              f"{sum(b['prompt_tokens'] for b in ts)} vs {sm['prompt_tokens']}")

    st, _ = call(args.base, "/analytics/summary?days=0", token=access)
    check("days out of range -> 422", st == 422, f"got {st}")

    print("\nknowledge base")
    if default_campaign:
        cmp_id = default_campaign["id"]
        st, docs = call(args.base, f"/campaigns/{cmp_id}/kb", token=access)
        check("documents -> 200", st == 200, f"got {st}")

        st, body = post_file(args.base, f"/campaigns/{cmp_id}/kb", token=access,
                             filename="notes.txt", content=b"hello")
        check("non-PDF filename -> 400", st == 400, f"got {st}")

        # A .pdf name with the wrong magic bytes is the case a filename check
        # alone would wave through.
        st, body = post_file(args.base, f"/campaigns/{cmp_id}/kb", token=access,
                             filename="fake.pdf", content=b"not really a pdf")
        check("PDF name but wrong magic bytes -> 400", st == 400, f"got {st}")

        # This one passes the magic-byte check, so the stream opens and the
        # failure arrives as an event rather than a status code. The point of
        # the check is that the traversal was neutralised, not that it parsed.
        st, body = post_file(args.base, f"/campaigns/{cmp_id}/kb", token=access,
                             filename="../../escape.pdf", content=b"%PDF-1.4 x")
        check("path traversal in the filename is not a 5xx", st in (200, 400),
              f"got {st}")
        check("an unreadable PDF is reported, not silently accepted",
              st == 400 or (body or {}).get("stage") == "error",
              str((body or {}).get("stage") or (body or {}).get("detail"))[:80])

        # Real ingestion is exercised from the console, where the chunk viewer
        # shows whether extraction actually worked - a pass/fail line here would
        # say the upload succeeded without saying whether it produced anything
        # usable.

    st, _ = call(args.base, "/kb/documents/99999999/chunks", token=access)
    check("chunks for a missing document -> 404", st == 404, f"got {st}")

    st, rules = call(args.base, "/alert-rules", token=access)
    check("alert rules -> 200", st == 200, f"got {st}")
    check("default rules were seeded", bool(rules), f"{len(rules or [])} rules")

    st, body = call(args.base, "/alerts?unacknowledged=true", token=access)
    check("alerts -> 200", st == 200, f"got {st}")

    st, body = call(args.base, "/alerts/unread-count", token=access)
    check("unread count -> 200", st == 200, f"got {st}")

    # The webhook belongs to a client, and a superadmin is in none of them, so
    # an unscoped request is genuinely ambiguous and must be refused.
    st, _ = call(args.base, "/alert-webhook", token=access)
    check("webhook without a client -> 400 for a superadmin", st == 400, f"got {st}")

    t_id = (tenants or [{}])[0].get("id")
    if t_id:
        st, body = call(args.base, f"/alert-webhook?tenant_id={t_id}", token=access)
        # The URL is a credential: anyone holding it can post into the channel.
        check("webhook is never returned in full",
              st == 200 and "webhook_url" not in (body or {}),
              str(sorted((body or {}).keys())))

        st, _ = call(args.base, f"/alert-webhook?tenant_id={t_id}", method="PUT",
                     token=access, body={"webhook_url": "not-a-url"})
        check("non-http webhook -> 422", st == 422, f"got {st}")

    if rules:
        rid = rules[0]["id"]
        st, _ = call(args.base, f"/alert-rules/{rid}", method="PATCH",
                     token=access, body={"window_minutes": 2})
        check("window below the floor -> 422", st == 422, f"got {st}")

    st, body = call(args.base, "/alert-rules/evaluate", method="POST", token=access)
    check("manual evaluation -> 200", st == 200, f"got {st}")

    st, body = call(args.base, "/live/calls", token=access)
    check("live calls -> 200", st == 200, f"got {st}")
    if st == 200:
        check("active + stale accounts for every row",
              body["active"] + body["stale"] == len(body["calls"]),
              f"{body['active']} + {body['stale']} vs {len(body['calls'])}")
        check("no finished call is listed as live",
              all(c["elapsed_sec"] >= 0 for c in body["calls"]),
              f"{len(body['calls'])} in progress")

    print("\nrbac")
    st, _ = call(args.base, "/users", method="POST", token=access,
                 body={"email": "weak@example.com", "role": "viewer",
                       "tenant_id": 1, "password": "short"})
    check("password under 12 chars -> 422", st == 422, f"got {st}")

    st, _ = call(args.base, "/users", method="POST", token=access,
                 body={"email": args.email, "role": "viewer", "tenant_id": 1,
                       "password": "a-perfectly-long-password"})
    check("duplicate email -> 409", st == 409, f"got {st}")

    st, _ = call(args.base, f"/users/{me_id}", method="PATCH", token=access,
                 body={"active": False})
    check("cannot deactivate yourself -> 400", st == 400, f"got {st}")

    st, _ = call(args.base, f"/users/{me_id}", method="DELETE", token=access)
    check("cannot delete yourself -> 400", st == 400, f"got {st}")

    print("\nlogout")
    if new_refresh:
        st, _ = call(args.base, "/auth/logout", method="POST",
                     body={"refresh_token": new_refresh}, token=access)
        check("logout -> 204", st == 204, f"got {st}")
        st, _ = call(args.base, "/auth/refresh", method="POST",
                     body={"refresh_token": new_refresh})
        check("refresh after logout -> 401", st == 401, f"got {st}")

    print(f"\n{'all checks passed' if not failures else f'{failures} check(s) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
