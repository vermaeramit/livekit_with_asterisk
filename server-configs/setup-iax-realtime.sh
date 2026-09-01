#!/bin/bash
# Let Asterisk read dialler peers out of the database, so adding one is a form.
#
#   bash /srv/aivoice/server-configs/setup-iax-realtime.sh
#
# Run migration 034 FIRST - this grants on the view it creates.
# Safe to re-run; every file is backed up and nothing is appended twice.
#
#
# WHAT CHANGES, AND WHAT IT COSTS
#
# Until now a dialler was a block in iax.conf plus a row naming it. Adding one
# meant an ssh session. After this, chan_iax2 looks a peer up in the database
# when it is dialled, so a row saved in the console is dialable on the next
# transfer - no reload, no restart, no server access.
#
# The price is that the password is in the database in clear text, because IAX2
# is MD5 challenge-response and Asterisk needs the secret itself to answer a
# challenge. It cannot be encrypted at rest when the thing that reads it has no
# key. Concretely:
#
#   * every pg_dump and every backup contains dialler passwords. Backup access
#     and backup retention are now trunk access.
#   * asterisk_ro could read three columns of routing. It can now read trunk
#     credentials as well. Its own password is in res_odbc.conf, 0640.
#
# That trade was made deliberately, for not having to edit files. It is written
# here so that whoever finds this later knows it was a decision.
#
#
# ONE THING THAT WILL LOOK WRONG AND IS NOT
#
# `iax2 show peers` will NOT list diallers configured this way. Realtime peers
# are built when they are dialled and freed afterwards, so there is nothing to
# list. An empty output is not a broken trunk. The check further down reads the
# database instead, and a real transfer is the only complete answer.

set -euo pipefail

DSN=aivoice
DBUSER=asterisk_ro
PGDB=aivoice
IAX=/etc/asterisk/iax.conf
EXTCONFIG=/etc/asterisk/extconfig.conf

say() { printf '\n== %s\n' "$1"; }
bak() { [ -f "$1" ] && cp -a "$1" "$1.bak-$(date +%Y%m%d-%H%M%S)"; }
psql_() { docker exec -i postgres psql -qtAX -U aivoice -d "$PGDB" "$@"; }

# ── 1. migration 034 ─────────────────────────────────────────────────────────
say "checking migration 034"
if [ "$(psql_ -c "SELECT to_regclass('public.iax_peers') IS NOT NULL")" != "t" ]; then
    echo "!! iax_peers does not exist. Run migrations/034_dialler_credentials.sql"
    echo "!! first - granting on a view that is not there would leave Asterisk"
    echo "!! connected and unable to read anything, which looks like a broken query."
    exit 1
fi
echo "iax_peers is there"

# ── 2. the driver module ─────────────────────────────────────────────────────
say "res_config_odbc"
if asterisk -rx "module show like res_config_odbc" | grep -q "1 modules loaded"; then
    echo "already loaded"
else
    asterisk -rx "module load res_config_odbc.so" >/dev/null 2>&1 || true
    sleep 1
    if ! asterisk -rx "module show like res_config_odbc" | grep -q "1 modules loaded"; then
        echo "!! res_config_odbc will not load. Without it Asterisk cannot read"
        echo "!! any realtime family and every dialler added in the console will"
        echo "!! simply not exist. Try: dnf install asterisk-odbc"
        exit 1
    fi
    echo "loaded"
fi

# ── 3. the grant ─────────────────────────────────────────────────────────────
say "letting $DBUSER read the peers"
psql_ >/dev/null <<SQL
GRANT SELECT ON iax_peers TO $DBUSER;
SQL
echo "granted - note this role can now read trunk passwords, see the header"

# ── 4. the mapping ───────────────────────────────────────────────────────────
say "extconfig.conf"
bak "$EXTCONFIG"
python3 - "$EXTCONFIG" "$DSN" <<'PY'
import os, re, sys
path, dsn = sys.argv[1], sys.argv[2]
s = open(path).read() if os.path.exists(path) else "[settings]\n"

line = f"iaxpeers => odbc,{dsn},iax_peers"
if line in s:
    print("already mapped"); raise SystemExit

# Replace any existing iaxpeers mapping rather than adding a second one - the
# first match wins and the other becomes a line that looks configured and is
# not.
s = re.sub(r"(?m)^\s*iaxpeers\s*=>.*$\n?", "", s)

if not re.search(r"(?m)^\[settings\]", s):
    s = s.rstrip() + "\n\n[settings]\n"

# Only peers. iaxusers is the family for calls coming IN, and nothing here
# should be able to authenticate an inbound call - those are still in iax.conf
# where a person put them on purpose.
s = re.sub(r"(?m)^\[settings\]\s*$",
           "[settings]\n; Dialler trunks, added from the console. See migration 034.\n"
           + line, s, count=1)
open(path, "w").write(s)
print("mapped iaxpeers -> " + dsn + ",iax_peers")
PY

chown root:asterisk "$EXTCONFIG" 2>/dev/null || true
chmod 0640 "$EXTCONFIG"

# ── 5. static sections that would shadow the database ────────────────────────
say "checking for static peers with the same name"
# A peer written into iax.conf is found before the database is consulted, so a
# leftover section silently wins and every edit in the console does nothing
# visible. That is a bad afternoon, so it is removed rather than warned about.
MANAGED=$(psql_ -c "SELECT name FROM iax_peers")
if [ -z "$MANAGED" ]; then
    echo "no managed diallers yet - nothing can be shadowed"
else
    REMOVED=0
    for name in $MANAGED; do
        if grep -q "^\[$name\]" "$IAX"; then
            [ "$REMOVED" -eq 0 ] && bak "$IAX"
            python3 - "$IAX" "$name" <<'PY'
import re, sys
path, name = sys.argv[1], sys.argv[2]
s = open(path).read()
s = re.sub(r"(?:\n;[^\n]*)*\n\[%s\]\n(?:[^\[]*)" % re.escape(name), "\n", s)
open(path, "w").write(s)
PY
            echo "removed static [$name] - it is in the database now"
            REMOVED=1
        fi
    done
    [ "$REMOVED" -eq 0 ] && echo "none - nothing shadows the database"
fi

# ── 6. does it work ──────────────────────────────────────────────────────────
say "reloading"
asterisk -rx "module reload res_config_odbc.so" >/dev/null
asterisk -rx "iax2 reload" >/dev/null
sleep 1

say "what Asterisk can dial"
# Read from the database with the password masked. `realtime load iaxpeers`
# would answer the same question and print the secret to the console, which is
# why it is not used here.
psql_ -c "SELECT name, username, host, port, length(secret) || ' chars' AS secret
            FROM iax_peers ORDER BY name" | sed 's/^/  /'

cat <<'EOF'

Diallers are added on the console's Diallers page now - host, port, username
and password - and take effect on the next transfer.

`iax2 show peers` will not list them. That is expected: a realtime peer exists
only while it is being dialled. The list above is the real answer, and a test
transfer is the complete one.
EOF
