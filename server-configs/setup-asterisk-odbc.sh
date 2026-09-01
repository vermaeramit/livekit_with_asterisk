#!/bin/bash
# Let Asterisk read transfer routing out of Postgres, and nothing else.
#
#   bash /srv/aivoice/server-configs/setup-asterisk-odbc.sh
#
# Run migration 033 FIRST - this grants on the view it creates, and grants on a
# view that does not exist yet would leave Asterisk connected and unable to read
# anything, which looks exactly like a broken query.
#
# Safe to re-run: every step checks before it changes anything, and each file it
# touches is backed up first.
#
#
# WHAT ASTERISK GETS, AND WHAT IT DOES NOT
#
# A role that can SELECT one view: campaign_id, peer, extension. No tables, no
# transcripts, no provider keys, no users. Asterisk's dialplan is a large and
# old surface that is edited by people who are not us, and the smallest useful
# grant is the whole point of doing this properly.
#
# The password lands in res_odbc.conf because ODBC has nowhere else to put it.
# That file is locked to asterisk and 0640 here; it was 0644.

set -euo pipefail

DSN=aivoice
DBUSER=asterisk_ro
PGHOST=127.0.0.1
PGPORT=5432
PGDB=aivoice

say() { printf '\n== %s\n' "$1"; }
bak() { [ -f "$1" ] && cp -a "$1" "$1.bak-$(date +%Y%m%d-%H%M%S)"; }

# ── 1. the driver ────────────────────────────────────────────────────────────
say "postgres odbc driver"
if ! rpm -q postgresql-odbc >/dev/null 2>&1; then
    dnf install -y postgresql-odbc
else
    echo "already installed"
fi

DRIVER=$(ls /usr/lib64/psqlodbcw.so 2>/dev/null || true)
[ -n "$DRIVER" ] || { echo "!! psqlodbcw.so not found - stopping"; exit 1; }
echo "driver: $DRIVER"

if ! odbcinst -q -d 2>/dev/null | grep -q '^\[PostgreSQL\]$'; then
    bak /etc/odbcinst.ini
    cat >> /etc/odbcinst.ini <<INI

[PostgreSQL]
Description = PostgreSQL ODBC driver
Driver      = $DRIVER
Setup       = $DRIVER
FileUsage   = 1
INI
    echo "registered [PostgreSQL] in odbcinst.ini"
else
    echo "[PostgreSQL] already registered"
fi

# ── 2. the read-only role ────────────────────────────────────────────────────
say "read-only database role"
# Generated here and never printed. It goes into res_odbc.conf and nowhere else,
# so nothing has to remember it and it is not in anybody's shell history.
PW=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)

docker exec -i postgres psql -U aivoice -d "$PGDB" >/dev/null <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DBUSER') THEN
        CREATE ROLE $DBUSER LOGIN;
    END IF;
END \$\$;
ALTER ROLE $DBUSER WITH PASSWORD '$PW';

-- Deliberately narrow. Not the tables - the view, and only the view.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM $DBUSER;
GRANT USAGE ON SCHEMA public TO $DBUSER;
GRANT SELECT ON transfer_routes TO $DBUSER;
SQL
echo "role $DBUSER can SELECT transfer_routes, and nothing else"

# ── 3. the DSN ───────────────────────────────────────────────────────────────
say "odbc.ini"
if ! grep -q "^\[$DSN\]" /etc/odbc.ini 2>/dev/null; then
    bak /etc/odbc.ini
    cat >> /etc/odbc.ini <<INI

[$DSN]
Description = aivoice transfer routing (read only)
Driver      = PostgreSQL
Servername  = $PGHOST
Port        = $PGPORT
Database    = $PGDB
INI
    echo "added [$DSN]"
else
    echo "[$DSN] already present"
fi

# ── 4. Asterisk's side ───────────────────────────────────────────────────────
say "res_odbc.conf"
bak /etc/asterisk/res_odbc.conf
# Rewritten rather than appended, so re-running does not stack a second section
# with a stale password - which would connect on whichever Asterisk read first.
python3 - "$DSN" "$DBUSER" "$PW" <<'PY'
import re, sys
dsn, user, pw = sys.argv[1:4]
p = "/etc/asterisk/res_odbc.conf"
s = open(p).read()
s = re.sub(r"\n\[%s\]\n(?:[^\[]*)" % re.escape(dsn), "\n", s)
s = s.rstrip() + f"""

[{dsn}]
enabled       => yes
dsn           => {dsn}
username      => {user}
password      => {pw}
; Kept open. A transfer is the one moment a caller is already waiting, and
; paying for a connection handshake there is the wrong place to save a socket.
pre-connect   => yes
; Asterisk must never write here. The role cannot anyway; saying so twice costs
; nothing and documents the intent where somebody editing this will see it.
forcecommit   => no
isolation     => read_committed
"""
open(p, "w").write(s)
print(f"[{dsn}] written")
PY

chown root:asterisk /etc/asterisk/res_odbc.conf
chmod 0640 /etc/asterisk/res_odbc.conf
echo "res_odbc.conf is now 0640 root:asterisk (it was 0644)"

# ── 5. does it work ──────────────────────────────────────────────────────────
say "connecting"
asterisk -rx "module reload res_odbc.so" >/dev/null
sleep 1
asterisk -rx "odbc show all"

echo
echo "Expected: '$DSN' listed with a connection. If it says Disconnected, the"
echo "reason is in /var/log/asterisk/messages - usually the driver path or that"
echo "migration 033 has not been run, so the view it grants on does not exist."
