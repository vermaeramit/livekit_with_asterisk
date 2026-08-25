#!/bin/sh
# Nightly Postgres backup. Run by aivoice-backup.timer; safe to run by hand.
#
#     /srv/aivoice/server-configs/backup-db.sh
#
# Custom format (-Fc), which is compressed and lets pg_restore pull out a single
# table later. A plain SQL dump cannot do that, and "restore just the campaigns"
# is the request that actually arrives.
#
# Every dump is verified by listing it. A backup nobody has ever read back is
# not a backup - it is a file, and the difference only becomes apparent on the
# day it matters.
#
# A status file is written on EVERY run, success or failure. Without it the
# directory cannot tell "last night failed" from "last night never happened" -
# both look like an absent dump - and that distinction is the whole reason
# anyone looks.
#
# ⚠️ This does NOT back up SECRETS_KEY, and that is deliberate. Provider keys,
# tool auth values and the postback credential are Fernet-encrypted in these
# rows; the key lives in /opt/aivoice/.env. Keeping both in one directory would
# undo the encryption at rest. See docs/DATABASE.md - the key is static, so it
# is stored once somewhere safe rather than copied every night.
set -eu

DIR="${BACKUP_DIR:-/opt/aivoice/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP=$(date +%Y-%m-%d-%H%M)
OUT="$DIR/aivoice-$STAMP.dump"
TMP="$OUT.partial"
STATUS="$DIR/last-run.json"

RESULT="failed"
DETAIL="the script exited before finishing"
BYTES=0
NAME=""

# Cleared first, not last. The .path unit fires on the file EXISTING, so
# leaving it in place until the end would re-trigger the moment this finishes -
# and a failing backup would then loop. Removing it up front means a request
# made while this is already running simply queues one more run, which is the
# behaviour anyone pressing the button twice would expect.
rm -f /opt/aivoice/backup-trigger/request 2>/dev/null || true

mkdir -p "$DIR"
# 755 on the DIRECTORY, 600 on the dumps.
#
# The console lists these files - names, sizes, timestamps - so it needs to read
# the directory. It has no business reading the dumps themselves, and does not:
# admin-api runs as a non-root user, so 600 keeps every dump unreadable to it
# while `stat` still works. 700 here made the whole page fail with a permission
# error that looked exactly like "no backups exist".
chmod 755 "$DIR"

# One EXIT trap for both jobs: clean up a partial dump, and leave a status file
# behind whichever way this ends. `set -e` means most failures land here.
finish() {
    rm -f "$TMP"
    KEPT=$(find "$DIR" -name 'aivoice-*.dump' | wc -l)
    cat > "$STATUS" <<JSON
{"when":"$(date -Is)","result":"$RESULT","detail":"$DETAIL","file":"$NAME","bytes":$BYTES,"kept":$KEPT,"retention_days":$KEEP_DAYS}
JSON
    # Readable, unlike the dumps: it holds a timestamp, a result and a byte
    # count, and the console cannot report a failed run without it.
    chmod 644 "$STATUS"
}
trap finish EXIT

if ! docker exec postgres pg_dump -U aivoice -Fc -d aivoice > "$TMP" 2>/dev/null; then
    DETAIL="pg_dump failed - is the postgres container running"
    echo "$DETAIL" >&2
    exit 1
fi

if ! docker exec -i postgres pg_restore --list < "$TMP" > /dev/null 2>&1; then
    DETAIL="the dump is not readable by pg_restore - not keeping it"
    echo "$DETAIL" >&2
    exit 1
fi

BYTES=$(stat -c %s "$TMP")
if [ "$BYTES" -lt 10240 ]; then
    # A dump this small means the database answered but had nothing in it.
    DETAIL="only $BYTES bytes - the database answered but is empty"
    echo "$DETAIL" >&2
    BYTES=0
    exit 1
fi

mv "$TMP" "$OUT"
chmod 600 "$OUT"
find "$DIR" -name 'aivoice-*.dump' -mtime "+$KEEP_DAYS" -delete

RESULT="ok"
DETAIL=""
NAME="aivoice-$STAMP.dump"

echo "backup ok: $OUT ($(numfmt --to=iec "$BYTES" 2>/dev/null || echo "$BYTES bytes"))"
echo "kept: $(find "$DIR" -name 'aivoice-*.dump' | wc -l) dumps, $KEEP_DAYS day retention"
