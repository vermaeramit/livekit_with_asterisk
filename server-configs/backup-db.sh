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

mkdir -p "$DIR"
chmod 700 "$DIR"

# Written to a temp name and moved only once it verifies, so a dump interrupted
# half way never sits in the directory looking like a good one.
TMP="$OUT.partial"
trap 'rm -f "$TMP"' EXIT

docker exec postgres pg_dump -U aivoice -Fc -d aivoice > "$TMP"

if ! docker exec -i postgres pg_restore --list < "$TMP" > /dev/null 2>&1; then
    echo "backup verification FAILED - $TMP is not a readable dump" >&2
    exit 1
fi

SIZE=$(stat -c %s "$TMP")
if [ "$SIZE" -lt 10240 ]; then
    # A dump this small means the database answered but had nothing in it.
    echo "backup is only ${SIZE} bytes - refusing to keep it" >&2
    exit 1
fi

mv "$TMP" "$OUT"
trap - EXIT
chmod 600 "$OUT"

find "$DIR" -name 'aivoice-*.dump' -mtime "+$KEEP_DAYS" -delete

echo "backup ok: $OUT ($(numfmt --to=iec "$SIZE" 2>/dev/null || echo "$SIZE bytes"))"
echo "kept: $(find "$DIR" -name 'aivoice-*.dump' | wc -l) dumps, $KEEP_DAYS day retention"
