#!/bin/sh
# Deploy. Knows which box it is on, and behaves differently on each.
#
#     server-configs/deploy.sh            # development: pull main
#     server-configs/deploy.sh v0.7.0     # production: check out that release
#     server-configs/deploy.sh v0.7.0 --force    # ... even with calls in progress
#
# WHICH BOX is read from APP_ENV in admin/.env - the same line the login page
# uses - and never from an IP address. A hardcoded 10.130.9.243 is what sent a
# cloned production server's calls to the development box for two days; see
# docs/REPLICA.md. A box that has not been told what it is gets asked, not
# assumed.
#
# Production is checked out AT A TAG, which is what makes the version honest:
# `git describe` there returns exactly v0.7.0, while development is always some
# commits past the last tag and says so. Cut a release with release.sh first.
#
# Order is not negotiable: migrations, then services. The other way round runs
# new code against an old schema for however long the gap is, and the failure
# lands on a live call. That has happened here.
set -eu

# ── Run from a copy of itself ───────────────────────────────────────────────
# This script pulls code that INCLUDES THIS SCRIPT, and sh reads a script in
# chunks as it executes. Rewriting the file underneath a running shell leaves it
# reading the new bytes from the old offset: a syntax error in the middle of a
# deploy, on whichever box happened to be unlucky.
#
# So the first thing it does is copy itself somewhere nothing will touch and hand
# over. The consequence is worth stating plainly: a deploy runs the version of
# this script you invoked, not the one it just pulled. That is the predictable
# choice - the alternative is changing procedure half way through following it.
if [ -z "${DEPLOY_FROM_COPY:-}" ]; then
    # Resolved HERE, while $0 is still the real path. In the copy it is a name
    # under /tmp and says nothing about which checkout to deploy.
    DEPLOY_REPO=$(cd "$(dirname "$0")/.." && pwd)
    export DEPLOY_REPO
    export DEPLOY_FROM_COPY=1

    _copy=$(mktemp) || { echo "could not create a temp file" >&2; exit 1; }
    cat "$0" > "$_copy"
    chmod +x "$_copy"
    "$_copy" "$@" && _rc=0 || _rc=$?
    rm -f "$_copy"
    exit "$_rc"
fi

cd "$DEPLOY_REPO"

TARGET=""
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *)  TARGET="$1"; shift ;;
    esac
done

die() { echo "REFUSED: $*" >&2; exit 1; }
psql_q() { docker exec -i postgres psql -U aivoice -d aivoice -qAt -c "$1"; }

# ── Which box is this ───────────────────────────────────────────────────────
[ -f admin/.env ] || die "admin/.env is missing. Copy admin/.env.example and fill it in."

APP_ENV=$(sed -n 's/^APP_ENV=//p' admin/.env | tr -d '"' | tr -d "'" | head -1)
case "$APP_ENV" in
    production|development) ;;
    '') die "APP_ENV is not set in admin/.env. Add 'APP_ENV=production' or
         'APP_ENV=development'. Nothing here guesses: guessing production on a
         development box is the mistake that matters." ;;
    *)  die "APP_ENV is '$APP_ENV'. It must be exactly 'production' or 'development'." ;;
esac

echo "Box       : $APP_ENV  ($(hostname -I | tr -s ' ' | sed 's/ $//'))"

# ── Get the code ────────────────────────────────────────────────────────────
git diff --quiet && git diff --cached --quiet \
    || die "the working tree has uncommitted changes. A deploy would either
         destroy them or carry them into a build nobody can reproduce."

if [ "$APP_ENV" = "production" ]; then
    [ -n "$TARGET" ] || die "production needs the release to deploy, e.g.
         $0 v0.7.0
         Cut one on the development box first:  server-configs/release.sh 0.7.0"
    git fetch --tags --quiet origin
    git rev-parse -q --verify "refs/tags/$TARGET" >/dev/null \
        || die "$TARGET is not a tag. Existing releases:
         $(git tag -l 'v*' | sort -V | tail -5 | tr '\n' ' ')"
    git checkout --quiet "$TARGET"
else
    [ -z "$TARGET" ] || die "development follows main; it does not check out a
         release. Run it without an argument."
    git pull --quiet --ff-only
fi

VERSION=$(git describe --tags --always --dirty)
echo "Version   : $VERSION"
echo

# ── Migrations, before anything restarts ────────────────────────────────────
# Tracked in the database from now on, rather than worked out from which commit
# a box happens to be checked out at. That inference was wrong at least once:
# the production clone sat on a checkout that said 047 while nobody could say
# what its database actually held.
psql_q "CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now())" > /dev/null

KNOWN=$(psql_q "SELECT count(*) FROM schema_migrations")
if [ "$KNOWN" = "0" ] && [ -n "$(psql_q "SELECT to_regclass('public.calls')")" ]; then
    # An existing database meeting this table for the first time. Its schema is
    # already current, so every file is recorded as applied rather than re-run.
    #
    # Adopting is the safe direction here only because both boxes were verified
    # current on 12 Sep 2026. If a database is ever genuinely BEHIND when it
    # first meets this table, delete the rows for the files it is missing and
    # run this again - they are all safe to re-run.
    echo "First run: adopting the existing schema as current."
    # One statement, not one per file. Fifty-one docker exec round trips take
    # the better part of a minute for something that reads as instant.
    ROWS=$(ls migrations/*.sql | xargs -n1 basename | sed "s/.*/('&')/" | paste -sd, -)
    psql_q "INSERT INTO schema_migrations (filename) VALUES $ROWS
            ON CONFLICT DO NOTHING" > /dev/null
    echo "  recorded $(psql_q "SELECT count(*) FROM schema_migrations") migrations as already applied"
    echo
fi

# Read once and compared in the shell. Asking the database per file meant 51
# round trips on every single deploy to answer one question.
APPLIED=$(psql_q "SELECT filename FROM schema_migrations")
PENDING=""
for f in migrations/*.sql; do
    b=$(basename "$f")
    printf '%s\n' "$APPLIED" | grep -qxF "$b" && continue
    # No leading space: the hint below picks the first item with ${PENDING%% *},
    # and a leading space makes that expand to nothing at all.
    PENDING="$PENDING${PENDING:+ }$b"
done

if [ -n "$PENDING" ]; then
    echo "Migrations: $PENDING"
    for b in $PENDING; do
        echo "--- $b"
        # --single-transaction so a file that fails half way leaves nothing
        # behind. Every migration here is transaction-safe; none uses
        # CONCURRENTLY, which would break this.
        docker exec -i postgres psql -U aivoice -d aivoice \
            -v ON_ERROR_STOP=1 --single-transaction < "migrations/$b"
        psql_q "INSERT INTO schema_migrations (filename) VALUES ('$b')" > /dev/null
    done
    echo
else
    echo "Migrations: none pending"
    echo
fi

# ── The console ─────────────────────────────────────────────────────────────
# Rebuilt before the agents are touched, and it cannot affect a call in
# progress: it is a separate compose project that only joins the media network
# to reach postgres.
#
# APP_VERSION and APP_ENV are exported here and nowhere else. This is the reason
# this script exists rather than a line in COMMANDS.md: `docker compose up`
# typed by hand has no way to know what `git describe` says, and would quietly
# stamp the build `unknown`.
export APP_VERSION="$VERSION"
export APP_ENV
docker compose -f admin/docker-compose.yml up -d --build

# ── The agents ──────────────────────────────────────────────────────────────
# These DO drop calls in progress, so they are last and they are guarded.
ACTIVE=$(asterisk -rx "core show channels" 2>/dev/null \
           | sed -n 's/^\([0-9]\+\) active call.*/\1/p' | head -1)
ACTIVE="${ACTIVE:-0}"

if [ "$ACTIVE" != "0" ] && [ "$FORCE" = "0" ]; then
    echo
    echo "$ACTIVE call(s) in progress - the agent workers were NOT restarted."
    echo "The console is updated and running $VERSION; the workers are still on"
    echo "the code they started with. Finish the deploy when the line is quiet:"
    echo
    # Plain, with no --force in it. ${FORCE:+--force} was here and always
    # expanded - FORCE is the string "0", which is set and therefore non-empty -
    # so the script suggested interrupting live calls as the normal way to
    # finish a deploy.
    echo "    $0 $TARGET"
    echo
    echo "Adding --force restarts them anyway and drops those $ACTIVE call(s)."
    exit 0
fi

systemctl restart aivoice-agent@1 aivoice-agent@2 aivoice-agent@3 \
                  aivoice-agent@4 aivoice-agent@5 aivoice-agent@6

# ── Did it take ─────────────────────────────────────────────────────────────
# Checked rather than assumed. A compose build that fails a later stage can
# leave the previous container running, and `up -d` exits 0 having changed
# nothing that matters.
sleep 8

DOWN=""
for i in 1 2 3 4 5 6; do
    # Built without a leading space so ${DOWN%% *} below yields the FIRST worker
    # rather than the empty string - the journalctl hint is the whole point of it.
    systemctl is-active --quiet "aivoice-agent@$i" || DOWN="$DOWN${DOWN:+ }$i"
done

SERVED=$(curl -fsS --max-time 5 http://127.0.0.1:8090/api/version 2>/dev/null || echo '')

echo
echo "Deployed  : $VERSION on $APP_ENV"
echo "API says  : ${SERVED:-no answer from 127.0.0.1:8090}"
if [ -z "$DOWN" ]; then
    echo "Workers   : all six active"
else
    echo "Workers   : NOT ACTIVE -> $DOWN"
    echo "            journalctl -u aivoice-agent@${DOWN%% *} -n 40"
fi

case "$SERVED" in
    *"\"$VERSION\""*) ;;
    '') echo
        echo "The API did not answer. It may still be starting; check again in a"
        echo "few seconds with: curl -s localhost:8090/api/version" ;;
    *)  echo
        echo "WARNING: the API reports a different version than was just deployed."
        echo "         That is a half-finished deploy - the container did not get"
        echo "         replaced. Try: docker compose -f admin/docker-compose.yml up -d --force-recreate" ;;
esac

[ -z "$DOWN" ] || exit 1
