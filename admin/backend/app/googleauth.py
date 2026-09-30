"""An access token for Google Cloud, minted from a service-account JSON.

WHY THIS EXISTS AT ALL. Every other provider in this console authenticates with
a string in a header - Soniox, Sarvam, OpenAI, Gemini. Google Cloud does not:
the credential is a service account, and calling its APIs means signing a JWT
with that account's private key and exchanging it for an access token that
lasts an hour.

The agent never runs this. It uses livekit's Google plugin, which does the same
exchange itself. But admin-api has no livekit plugins - ttspreview.py's own
docstring says why, and the reason has not changed - so the console has to mint
its own token to preview a voice or render a hold message.

NOTHING NEW WAS INSTALLED. PyJWT and cryptography are already in this image
(auth tokens and secretlib respectively), and between them they sign RS256,
which is the whole of the hard part.

    creds  the parsed service-account JSON, as stored in provider_keys - one
           encrypted string, parsed where it is used
    ->     an access token, cached until shortly before it expires

THE CACHE IS PER CREDENTIAL, not global. A tenant's key and a campaign's key
are different accounts and must not share a token; keying on the private key's
own id makes that structural rather than remembered. It is a dict in one
process, which is the right size for the thing being cached - a token lasts an
hour and a worker restart simply mints another.

Never logs the key, the private key, or the token.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt

log = logging.getLogger("admin-api")

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Google issues hour-long tokens. Renewed early so a request that starts just
# before the edge does not finish just after it.
_SKEW = 300
_LIFETIME = 3600

# {credential id: (token, expires_at)}
_cache: dict[str, tuple[str, float]] = {}

# The fields a service-account file must have for any of this to work. Checked
# up front so a wrong paste - an API key, an OAuth client, a whole project
# export - is named as what it is rather than failing later inside a JWT
# library with a message about PEM headers.
REQUIRED = ("type", "client_email", "private_key", "private_key_id")


class AuthError(Exception):
    """The credential could not be turned into a token. Carries no secret."""


def parse(raw: str) -> dict:
    """-> the service-account JSON as a dict, or raise saying what is wrong."""
    try:
        creds = json.loads(raw)
    except json.JSONDecodeError:
        raise AuthError("this is not valid JSON - paste the whole "
                        "service-account file, not an API key")
    if not isinstance(creds, dict):
        raise AuthError("the JSON is not an object")
    missing = [f for f in REQUIRED if not creds.get(f)]
    if missing:
        raise AuthError(f"the JSON is missing {', '.join(missing)} - it does not "
                        "look like a service-account key file")
    if creds.get("type") != "service_account":
        raise AuthError(f"this is a '{creds.get('type')}' credential; Cloud "
                        "Text-to-Speech needs a service account")
    return creds


def mint(creds: dict) -> tuple[str, float]:
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": creds["client_email"],
            "scope": SCOPE,
            "aud": creds.get("token_uri") or TOKEN_URI,
            "iat": now,
            "exp": now + _LIFETIME,
        },
        creds["private_key"],
        algorithm="RS256",
        headers={"kid": creds["private_key_id"]},
    )
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    req = urllib.request.Request(
        creds.get("token_uri") or TOKEN_URI, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "AIVoice-Console/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # Google's own words. They are worth passing on: "invalid_grant"
        # usually means the clock is wrong or the key was revoked, and
        # "unauthorized_client" means the account exists but was never given
        # the scope - which need opposite answers.
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise AuthError(f"Google refused the credential ({e.code}): "
                        f"{detail or e.reason}")
    except Exception as e:
        # Never the exception text: it can quote the request, and the request
        # carried a signed assertion.
        log.warning("google token request failed: %s", type(e).__name__)
        raise AuthError(f"could not reach Google ({type(e).__name__})")

    token = data.get("access_token")
    if not token:
        raise AuthError("Google returned no access token")
    return token, time.time() + int(data.get("expires_in") or _LIFETIME)


async def access_token(creds: dict) -> str:
    """-> a bearer token for this service account, minted or cached."""
    key = creds.get("private_key_id") or creds.get("client_email") or ""
    hit = _cache.get(key)
    if hit and hit[1] - _SKEW > time.time():
        return hit[0]
    token, expires = await asyncio.to_thread(mint, creds)
    _cache[key] = (token, expires)
    return token


def forget(creds: dict) -> None:
    """Drop a cached token. For when a key is replaced and the old one is stale."""
    _cache.pop(creds.get("private_key_id") or creds.get("client_email") or "", None)
