#!/usr/bin/env python3
"""Generate admin/.env from the media stack's existing /opt/aivoice/.env.

    python3 admin/bootstrap_env.py

Reuses the postgres credentials already in use and mints a fresh JWT secret.
Nothing is printed except a masked summary - the whole point is that the DB
password never reaches the terminal, the shell history, or this chat.

Re-running regenerates JWT_SECRET, which logs everyone out (issued access tokens
stop verifying). Pass --keep-secret to leave an existing one alone.
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

SOURCE = Path("/opt/aivoice/.env")
TARGET = Path(__file__).resolve().parent / ".env"


def read_env(path: Path) -> dict[str, str]:
    text = path.read_text()
    out: dict[str, str] = {}
    for key, raw in re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", text, re.M):
        out[key] = raw.strip().strip('"').strip("'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--cors", default="http://localhost:5173")
    ap.add_argument("--keep-secret", action="store_true")
    args = ap.parse_args()

    if not args.source.exists():
        print(f"{args.source} not found", file=sys.stderr)
        return 1

    src = read_env(args.source)
    missing = [k for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
               if not src.get(k)]
    if missing:
        print(f"missing in {args.source}: {', '.join(missing)}", file=sys.stderr)
        return 1

    secret = ""
    if args.keep_secret and TARGET.exists():
        secret = read_env(TARGET).get("JWT_SECRET", "")
    if not secret:
        secret = secrets.token_urlsafe(48)

    # quote() the credentials: a password containing @ : / or # would otherwise
    # silently corrupt the DSN and produce a baffling connection error
    dsn = (f"postgresql://{quote(src['POSTGRES_USER'], safe='')}:"
           f"{quote(src['POSTGRES_PASSWORD'], safe='')}"
           f"@postgres:5432/{src['POSTGRES_DB']}")

    # trailing newline is not cosmetic - without it a later `echo >> .env` glues
    # itself onto the last value (we have been bitten by exactly this before)
    TARGET.write_text(
        f"DATABASE_URL={dsn}\n"
        f"JWT_SECRET={secret}\n"
        f"ACCESS_TOKEN_MINUTES=15\n"
        f"REFRESH_TOKEN_DAYS=7\n"
        f"CORS_ORIGINS={args.cors}\n"
    )
    TARGET.chmod(0o600)

    print(f"wrote {TARGET} (mode 600)")
    for line in TARGET.read_text().splitlines():
        k, _, v = line.partition("=")
        print(f"  {k}={v[:6]}***" if k in ("DATABASE_URL", "JWT_SECRET")
              else f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
