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
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, {"detail": raw.decode()[:200]}
    except urllib.error.URLError as e:
        print(f"  cannot reach {base}: {e.reason}", file=sys.stderr)
        raise SystemExit(2)


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
            latency = [t for t in detail["turns"] if t.get("total_ms")]
            check("turns carry latency", bool(latency) or not detail["turns"],
                  f"{len(latency)} timed turns")
        st, _ = call(args.base, f"/calls/{cid}/kb-chunks", token=access)
        check("kb-chunks -> 200", st == 200, f"got {st}")

    st, _ = call(args.base, "/calls/99999999", token=access)
    check("missing call -> 404", st == 404, f"got {st}")

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
