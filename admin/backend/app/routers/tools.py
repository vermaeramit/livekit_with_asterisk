"""Campaign tools — the HTTP calls the agent can make mid-conversation.

The auth value is handled exactly like a provider key: encrypted with the same
Fernet key, never returned, only ever a four-character hint.

There is a /test endpoint on purpose. A tool is written once and then fires in
front of a caller; a wrong URL, a wrong header name or an auth value with a
trailing space should be found here, by someone looking at a screen, and not by
a customer hearing "I could not check that right now".
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db, secretlib
from ..deps import CurrentUser, active_user, assert_campaign_visible, require_roles
from ..schemas import ToolCreate, ToolOut, ToolTestResult, ToolUpdate

router = APIRouter(prefix="/campaigns/{campaign_id}/tools", tags=["tools"])

editor = require_roles("tenant_admin")

COLUMNS = """id, name, description, parameters, method, url, headers,
             auth_header, auth_value_hint, body_template, timeout_ms,
             max_response_bytes, response_path, enabled, updated_at"""


def _row(r) -> ToolOut:
    d = dict(r)
    # asyncpg hands JSONB back as text unless a codec is registered.
    for k in ("parameters", "headers"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return ToolOut(**d)


@router.get("", response_model=list[ToolOut])
async def list_tools(campaign_id: int, user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        f"SELECT {COLUMNS} FROM campaign_tools WHERE campaign_id = $1 ORDER BY name",
        campaign_id)
    return [_row(r) for r in rows]


@router.post("", response_model=ToolOut, status_code=status.HTTP_201_CREATED)
async def create_tool(campaign_id: int, body: ToolCreate,
                      actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    enc = hint = None
    if body.auth_value:
        c = secretlib.crypto()
        enc, hint = c.encrypt(body.auth_value), c.hint(body.auth_value)

    try:
        row = await db.pool().fetchrow(
            f"""INSERT INTO campaign_tools
                    (campaign_id, tenant_id, name, description, parameters,
                     method, url, headers, auth_header, auth_value_enc,
                     auth_value_hint, body_template, timeout_ms,
                     max_response_bytes, response_path, enabled)
                VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8::jsonb,$9,$10,$11,$12,
                        $13,$14,$15,$16)
                RETURNING {COLUMNS}""",
            campaign_id, tenant_id, body.name, body.description,
            json.dumps(body.parameters), body.method, body.url,
            json.dumps(body.headers) if body.headers else None,
            body.auth_header, enc, hint, body.body_template, body.timeout_ms,
            body.max_response_bytes, body.response_path, body.enabled)
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"this campaign already has a tool called '{body.name}' - "
            "two tools with one name makes the model's call ambiguous")

    # The URL and header NAMES are useful history; the auth value never is.
    await audit.record(actor, entity="tool", entity_id=body.name, action="create",
                       tenant_id=tenant_id, campaign_id=campaign_id,
                       changes={"url": body.url, "method": body.method})
    return _row(row)


@router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(campaign_id: int, tool_id: int, body: ToolUpdate,
                      actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)

    # One ordered list of (column, value), so the placeholder numbers can never
    # drift out of step with the values. The first version of this built two
    # fragments and renumbered one against the other, which is a silent
    # wrong-column update waiting to happen.
    JSON_COLS = ("parameters", "headers")
    pairs: list[tuple[str, object]] = []
    for col, val in body.model_dump(exclude={"auth_value"}).items():
        pairs.append((col, json.dumps(val) if col in JSON_COLS and val is not None
                      else val))

    # None = leave the stored secret alone. "" = clear it. Conflating the two
    # means editing a description silently wipes the credential.
    if body.auth_value is not None:
        if body.auth_value == "":
            pairs += [("auth_value_enc", None), ("auth_value_hint", None)]
        else:
            c = secretlib.crypto()
            pairs += [("auth_value_enc", c.encrypt(body.auth_value)),
                      ("auth_value_hint", c.hint(body.auth_value))]

    sets = ", ".join(
        f"{col} = ${i}" + ("::jsonb" if col in JSON_COLS else "")
        for i, (col, _) in enumerate(pairs, start=3))

    row = await db.pool().fetchrow(
        f"""UPDATE campaign_tools SET {sets}, updated_at = now()
             WHERE id = $1 AND campaign_id = $2 RETURNING {COLUMNS}""",
        tool_id, campaign_id, *[v for _, v in pairs])
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tool not found")

    await audit.record(actor, entity="tool", entity_id=body.name, action="update",
                       tenant_id=tenant_id, campaign_id=campaign_id,
                       changes={"url": body.url, "method": body.method,
                                "secret_changed": body.auth_value is not None})
    return _row(row)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(campaign_id: int, tool_id: int,
                      actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    row = await db.pool().fetchrow(
        "DELETE FROM campaign_tools WHERE id = $1 AND campaign_id = $2 "
        "RETURNING name", tool_id, campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tool not found")
    await audit.record(actor, entity="tool", entity_id=row["name"],
                       action="delete", tenant_id=tenant_id,
                       campaign_id=campaign_id)


# ---------------------------------------------------------------------------

def _run_once(method: str, url: str, headers: dict, data: str | None,
              timeout_s: float, max_bytes: int) -> tuple[int | None, str, str | None]:
    """A deliberately separate implementation from agent/tools.py.

    Sharing it is not possible - that module imports livekit.agents, which is
    not in this image - and not desirable either: the agent's version raises
    ToolError so a model can speak, this one reports so a human can read.
    """
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=data.encode() if data else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return r.status, r.read(max_bytes).decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, e.read(max_bytes).decode("utf-8", "replace"), None
    except Exception as e:
        return None, "", f"{type(e).__name__}: {e}"


@router.post("/{tool_id}/test", response_model=ToolTestResult)
async def test_tool(campaign_id: int, tool_id: int, arguments: dict,
                    actor: CurrentUser = Depends(editor)):
    """Run the tool now, with arguments you supply.

    ⚠️ This makes the REAL request. For a GET that is harmless; for a POST that
    books something, it books it. There is no dry-run mode because there is no
    way to have one that proves anything - a request that is not sent tells you
    nothing about whether the endpoint works.
    """
    await assert_campaign_visible(actor, campaign_id)
    row = await db.pool().fetchrow(
        """SELECT name, method, url, headers, auth_header, auth_value_enc,
                  body_template, timeout_ms, max_response_bytes, response_path
             FROM campaign_tools WHERE id = $1 AND campaign_id = $2""",
        tool_id, campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tool not found")

    # The SAME substitution and extraction the agent uses, imported rather than
    # reimplemented - see agent/toolfmt.py. The first version of this endpoint
    # had its own copy of one and none of the other, so the test showed a whole
    # document where the model would have seen a single field.
    fmt = secretlib.toolfmt()

    def fill(tpl):
        return fmt.fill(tpl, arguments)

    url = fill(row["url"])
    # Same User-Agent the agent sends, so the test cannot pass where the real
    # call would fail. urllib's default is a WAF magnet - see agent/tools.py.
    headers = {"User-Agent": os.getenv("TOOL_USER_AGENT", "AIVoice-Agent/1.0")}
    headers.update(json.loads(row["headers"]) if isinstance(row["headers"], str)
                   else (row["headers"] or {}))
    if row["auth_header"] and row["auth_value_enc"]:
        headers[row["auth_header"]] = secretlib.crypto().decrypt(row["auth_value_enc"])
    data = fill(row["body_template"]) if row["method"] != "GET" else None
    if data:
        headers.setdefault("Content-Type", "application/json")

    t0 = time.perf_counter()
    code, body, err = await asyncio.to_thread(
        _run_once, row["method"], url, headers, data,
        row["timeout_ms"] / 1000, row["max_response_bytes"])
    ms = int((time.perf_counter() - t0) * 1000)

    if ms > row["timeout_ms"] * 0.8 and err is None:
        # Not a failure, but the caller would have been listening to it.
        err = (f"took {ms}ms against a {row['timeout_ms']}ms timeout - a real "
               "caller hears this as silence")

    # Apply response_path exactly as the agent does, or the promise under this
    # result ("same truncation, same response path") is not true.
    shown = body
    if body and row["response_path"]:
        try:
            picked = fmt.extract(json.loads(body), row["response_path"])
            shown = (json.dumps(picked, ensure_ascii=False)
                     if picked is not None
                     else f"(response_path '{row['response_path']}' matched "
                          f"nothing - the model would get the whole body)")
        except json.JSONDecodeError:
            shown = body  # not JSON; the path cannot apply

    return ToolTestResult(ok=bool(code and code < 400), status_code=code,
                          duration_ms=ms, body=shown or None, error=err, url=url)
