#!/usr/bin/env python3
"""Create (or reset the password of) the first superadmin.

    docker compose -f admin/docker-compose.yml run --rm admin-api \
        python seed_admin.py --email you@example.com

The password is read with getpass so it never appears on screen, in the shell
history, or in the process list.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app import db, security


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", default="Super Admin")
    args = ap.parse_args()

    pw = getpass.getpass("Password: ")
    if len(pw) < 12:
        print("password must be at least 12 characters", file=sys.stderr)
        return 1
    if pw != getpass.getpass("Confirm : "):
        print("passwords do not match", file=sys.stderr)
        return 1

    await db.connect()
    try:
        existing = await db.pool().fetchrow(
            "SELECT id, role FROM users WHERE lower(email) = lower($1)",
            args.email)

        if existing:
            if existing["role"] != "superadmin":
                print(f"{args.email} exists with role {existing['role']!r}; "
                      "refusing to change it", file=sys.stderr)
                return 1
            await db.pool().execute(
                "UPDATE users SET password_hash = $2, active = true, "
                "updated_at = now() WHERE id = $1",
                existing["id"], security.hash_password(pw))
            print(f"password reset for superadmin {args.email}")
        else:
            uid = await db.pool().fetchval(
                """INSERT INTO users (tenant_id, email, name, password_hash, role)
                   VALUES (NULL, $1, $2, $3, 'superadmin') RETURNING id""",
                args.email, args.name, security.hash_password(pw))
            print(f"superadmin created: id={uid} email={args.email}")
        return 0
    finally:
        await db.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
