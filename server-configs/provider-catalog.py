#!/usr/bin/env python3
"""Ask a provider what it actually offers, using the key already stored.

    docker exec -i admin-api python - < server-configs/provider-catalog.py soniox voices
    docker exec -i admin-api python - < server-configs/provider-catalog.py soniox models

Runs INSIDE admin-api because that is the only place with both SECRETS_KEY and
agent/crypto.py mounted at /app/kblib. Piped over stdin so nothing has to be
copied to the server and no shell quoting can mangle it.

⚠️ The decrypted key never leaves this process. It goes into an Authorization
header and nothing else - it is not printed, logged, or echoed on failure. That
is the whole reason this exists rather than a one-line curl: reading a stored
key means decrypting it, and decrypting it in a shell means it lands in the
terminal and the history file.

Why it is needed at all: the Soniox voice in agent/voice_agent.py is the
hardcoded string "Priya", which was never read from the provider - it was a
guess. It was then judged on quality and rejected, which is not a fair test of
a voice nobody chose.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

BASES = {
    "soniox": "https://api.soniox.com",
    "sarvam": "https://api.sarvam.ai",
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
}

# Gemini does not take a bearer token. Every other provider here does, and
# sending the wrong one gets a 401 that reads like a bad key rather than like
# the wrong header - which is a morning lost to rotating a key that was fine.
AUTH = {
    "gemini": lambda key: {"x-goog-api-key": key},
}


def auth_headers(provider: str, key: str) -> dict:
    return AUTH.get(provider, lambda k: {"Authorization": f"Bearer {k}"})(key)

ENDPOINTS = {
    "soniox": {
        # /v1/voices lists CLONED voices only - it comes back empty on an
        # account that has never made one. The built-in voices, with gender and
        # character description, are attached to the TTS models instead, and
        # /v1/models returns only the STT ones.
        "voices": "https://api.soniox.com/v1/voices",
        "models": "https://api.soniox.com/v1/models",
        "tts-models": "https://api.soniox.com/v1/tts/models",
    },
    "sarvam": {
        # Sarvam publishes no voice list; speakers are documented, not served.
        "models": "https://api.sarvam.ai/v1/models",
    },
    "openai": {
        "models": "https://api.openai.com/v1/models",
    },
    "gemini": {
        # pageSize because the default page is small and the list is long;
        # nextPageToken is followed below so "models" really means all of them.
        "models": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
        # The same call, filtered to what can speak. Added 30 Sep 2026 because
        # the model name in tts_defaults was chosen from memory, and memory has
        # a date on it - asking the API is the only way to find out what this
        # key can actually call TODAY.
        "tts-models": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
    },
}


async def fetch_key(provider: str) -> str:
    sys.path.insert(0, "/app/kblib")
    import asyncpg
    import crypto

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        # Any campaign's key will do for a read-only catalogue call. Ordered so
        # the choice is at least deterministic across runs.
        row = await conn.fetchrow(
            "SELECT key_enc, key_hint, campaign_id FROM provider_keys "
            "WHERE provider = $1 ORDER BY campaign_id NULLS FIRST, id LIMIT 1",
            provider)
    finally:
        await conn.close()

    if row is None:
        raise SystemExit(f"no {provider} key stored - add one in the console first")
    # The hint only, never the key. Enough to confirm which key answered.
    print(f"using the {provider} key ending ····{row['key_hint']} "
          f"(campaign {row['campaign_id']})\n")
    return crypto.decrypt(row["key_enc"])


def get(url: str, key: str, provider: str = "") -> str:
    req = urllib.request.Request(
        url,
        headers={**auth_headers(provider, key),
                 # The default urllib UA gets 403 from WAFs - see agent/tools.py.
                 "User-Agent": "AIVoice-Agent/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # The body, not the key that failed to authenticate with it.
        raise SystemExit(f"{url} -> HTTP {e.code}\n"
                         f"{e.read().decode('utf-8', 'replace')[:500]}")


async def main() -> None:
    provider = (sys.argv[1] if len(sys.argv) > 1 else "soniox").lower()
    what = (sys.argv[2] if len(sys.argv) > 2 else "voices").lower()

    # A raw path is allowed so a wrong guess about an endpoint costs a re-run
    # rather than a commit. Provider docs move; this tool should not have to.
    if what.startswith("/"):
        url = BASES[provider] + what
    elif provider in ENDPOINTS and what in ENDPOINTS[provider]:
        url = ENDPOINTS[provider][what]
    else:
        raise SystemExit(
            "usage: provider-catalog.py <provider> <what|/raw/path>\n  " +
            "\n  ".join(f"{p} {w}" for p, ws in ENDPOINTS.items() for w in ws))

    key = await fetch_key(provider)
    body = get(url, key, provider)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(body[:4000])
        return

    if provider == "gemini":
        # Follow the pages, then print one line per model rather than the full
        # record: each carries a description, token limits and a method list,
        # and fifty of those is a screen nobody reads.
        models = list(data.get("models") or [])
        token = data.get("nextPageToken")
        while token:
            more = json.loads(get(f"{url}&pageToken={token}", key, provider))
            models += more.get("models") or []
            token = more.get("nextPageToken")

        if what == "tts-models":
            models = [m for m in models if "tts" in (m.get("name") or "").lower()]
        print(f"{len(models)} models")
        print()
        for m in sorted(models, key=lambda m: m.get("name") or ""):
            name = (m.get("name") or "").removeprefix("models/")
            print(f"{name:<42} {m.get('displayName', '')}")
        return

    print(json.dumps(data, indent=2, ensure_ascii=False))


asyncio.run(main())
