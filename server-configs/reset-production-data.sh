#!/bin/sh
# Empty a cloned server of the data it inherited, keeping the accounts.
#
#     server-configs/reset-production-data.sh                       # dry run
#     server-configs/reset-production-data.sh --confirm 10.130.9.244
#     server-configs/reset-production-data.sh --confirm 10.130.9.244 --recordings
#
# A VM clone arrives carrying the development box's calls, transcripts,
# knowledge base and audit trail. None of it belongs on a production server, and
# a client logging in to find somebody else's campaigns is the kind of thing
# that is noticed once and remembered for a long time.
#
# KEPT:    tenants, users, roles, role_permissions, provider_rates,
#          platform_settings, and tenant-level alert_rules.
# REMOVED: everything else - calls and their turns, campaigns and their configs,
#          the knowledge base, chat, alerts fired, the audit trail, provider
#          keys, diallers, and every login session.
#
# ⚠️ Provider keys are removed. Production will not place a call until keys are
# entered again in the console. They cannot be read back from here - they are
# Fernet-encrypted and this script does not decrypt anything - but the same rows
# still exist on the source box, which shares SECRETS_KEY.
#
# THE GUARD THAT MATTERS: --confirm takes the IP of the box you mean, and the
# script refuses to run unless the machine actually holds it. Both servers
# present a prompt of [root@localhost ~]# and there is nothing on screen to tell
# them apart. Running this on the development box by mistake would destroy the
# only copy of everything.
set -eu

CONFIRM_IP=""
DO_RECORDINGS=0
REC_DIR="${REC_DIR:-/var/spool/asterisk/recordings}"
BACKUP_DIR="${RESET_BACKUP_DIR:-/opt/aivoice/backups/pre-reset}"

while [ $# -gt 0 ]; do
    case "$1" in
        --confirm)     CONFIRM_IP="${2:?--confirm needs the IP of this box}"; shift 2 ;;
        --recordings)  DO_RECORDINGS=1; shift ;;
        -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
        *)             echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

psql_q() {
    docker exec -i postgres psql -U aivoice -d aivoice -qAt -c "$1"
}

# ── What is here now ────────────────────────────────────────────────────────
# Read before anything else, and printed whether or not this is a dry run. The
# number nobody looked at is the number nobody misses.
echo "Host          : $(hostname -I | tr -s ' ')"
echo "Database      : $(psql_q 'SELECT current_database();')"
echo

echo "WILL BE KEPT"
psql_q "
SELECT format('  %-20s %6s', t, n) FROM (
  SELECT 'tenants' t, count(*) n FROM tenants
  UNION ALL SELECT 'users',             count(*) FROM users
  UNION ALL SELECT 'roles',             count(*) FROM roles
  UNION ALL SELECT 'role_permissions',  count(*) FROM role_permissions
  UNION ALL SELECT 'provider_rates',    count(*) FROM provider_rates
  UNION ALL SELECT 'platform_settings', count(*) FROM platform_settings
  UNION ALL SELECT 'alert_rules (tenant-level)',
                   count(*) FROM alert_rules WHERE campaign_id IS NULL
) x ORDER BY t;"

echo
echo "WILL BE REMOVED"
psql_q "
SELECT format('  %-20s %6s', relname, n) FROM (
  SELECT relname,
         (xpath('/row/c/text()', query_to_xml(
            format('SELECT count(*) AS c FROM public.%I', relname),
            false, true, '')))[1]::text::bigint AS n
    FROM pg_stat_user_tables
   WHERE relname NOT IN ('tenants','users','roles','role_permissions',
                         'provider_rates','platform_settings','alert_rules')
  UNION ALL
  -- Listed separately because this table is only half removed, and a report
  -- that under-states what a destructive script deletes is worse than no
  -- report: it is read once, believed, and not read again.
  SELECT 'alert_rules (campaign)', count(*) FROM alert_rules
   WHERE campaign_id IS NOT NULL
) y WHERE n > 0 ORDER BY n DESC;"

if [ "$DO_RECORDINGS" = "1" ]; then
    echo
    if [ -d "$REC_DIR" ]; then
        echo "  recordings           $(find "$REC_DIR" -type f -name '*.wav' | wc -l) files, $(du -sh "$REC_DIR" 2>/dev/null | cut -f1)"
    else
        echo "  recordings           $REC_DIR does not exist"
    fi
fi

# ── Dry run stops here ──────────────────────────────────────────────────────
if [ -z "$CONFIRM_IP" ]; then
    echo
    echo "Dry run - nothing was changed."
    echo "To go ahead:  $0 --confirm <this box's IP>"
    exit 0
fi

# ── The guard ───────────────────────────────────────────────────────────────
# Checked against the interfaces this machine actually holds, not against
# anything typed twice. Typing the wrong IP is the mistake being guarded; asking
# for it a second time would only collect the same wrong answer.
if ! hostname -I | tr ' ' '\n' | grep -qx "$CONFIRM_IP"; then
    echo >&2
    echo "REFUSED: this machine does not hold $CONFIRM_IP." >&2
    echo "         It has: $(hostname -I | tr -s ' ')" >&2
    echo "         You are on a different server than you think." >&2
    exit 1
fi

# ── Backup ──────────────────────────────────────────────────────────────────
# Its own directory, NOT the nightly one: that has a 14 day retention and would
# quietly delete the only copy of what this removed, a fortnight after anyone
# could still remember what was in it.
mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/before-reset-$(date +%Y-%m-%d-%H%M).dump"
echo
echo "Backing up to $DUMP"
docker exec -i postgres pg_dump -U aivoice -Fc aivoice > "$DUMP.partial"

# Verified by reading it back, for the reason backup-db.sh gives: a dump nobody
# has ever opened is a file, not a backup, and the difference shows up on the
# one day it matters.
if ! docker exec -i postgres pg_restore -l < "$DUMP.partial" > /dev/null 2>&1; then
    echo "REFUSED: the backup could not be read back. Nothing was deleted." >&2
    rm -f "$DUMP.partial"
    exit 1
fi
mv "$DUMP.partial" "$DUMP"
echo "Backup verified: $(du -h "$DUMP" | cut -f1)"

# ── The wipe ────────────────────────────────────────────────────────────────
# One transaction. The deletes are explicit and in child-to-parent order rather
# than leaning on ON DELETE CASCADE - the cascade graph here has 48 edges and
# three of the ones that matter are SET NULL, not CASCADE. `calls` is the trap:
# deleting campaigns does NOT remove calls, it nulls their campaign_id and
# leaves every row behind.
#
# The check at the end is the real safety net. If any kept table lost a row to
# a cascade nobody predicted, it raises, the transaction rolls back, and the
# database is exactly as it was.
echo
docker exec -i postgres psql -U aivoice -d aivoice -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;

CREATE TEMP TABLE _keep_before AS
  SELECT 'tenants' AS t, count(*) AS n FROM tenants
  UNION ALL SELECT 'users',             count(*) FROM users
  UNION ALL SELECT 'roles',             count(*) FROM roles
  UNION ALL SELECT 'role_permissions',  count(*) FROM role_permissions
  UNION ALL SELECT 'provider_rates',    count(*) FROM provider_rates
  UNION ALL SELECT 'platform_settings', count(*) FROM platform_settings;

CREATE TEMP TABLE _rules_before AS
  SELECT count(*) AS n FROM alert_rules WHERE campaign_id IS NULL;

-- Calls and everything hanging off one.
DELETE FROM turns;
DELETE FROM call_errors;
DELETE FROM call_postbacks;
DELETE FROM tool_invocations;
DELETE FROM knowledge_gaps;
DELETE FROM calls;

-- Chat, deepest first.
DELETE FROM chat_messages;
DELETE FROM chat_conversations;
DELETE FROM chat_widgets;

-- Knowledge base.
DELETE FROM kb_chunks;
DELETE FROM kb_documents;
DELETE FROM kb_sources;

-- Campaign configuration.
DELETE FROM prompt_versions;
DELETE FROM campaign_routes;
DELETE FROM campaign_tools;
DELETE FROM agent_config;

-- Alerts that have fired, and the rules that belonged to a campaign. The
-- tenant-level rules stay: migrations seeded them per tenant and nothing
-- recreates them afterwards, so deleting them would leave a production server
-- with no alerting at all and no sign that it was missing.
DELETE FROM alerts;
DELETE FROM alert_rules WHERE campaign_id IS NOT NULL;

-- Credentials, history and sessions.
DELETE FROM provider_keys;
DELETE FROM config_audit;
DELETE FROM system_acks;
DELETE FROM user_sessions;

-- Parents last.
DELETE FROM campaigns;
DELETE FROM diallers;

-- Ids start again from 1. A production server's first call being number 591
-- invites the reasonable, wrong conclusion that 590 came before it.
DO $reset$
DECLARE
    r record;
BEGIN
    -- Found through pg_depend rather than pg_get_serial_sequence(table, 'id').
    -- That form has to be told the column name and RAISES on a table whose key
    -- is not called `id` - inside this transaction one such table would roll
    -- the whole wipe back for a cosmetic step. This asks instead which
    -- sequences these tables actually own, whatever their columns are called.
    FOR r IN
        SELECT s.oid::regclass AS seq
          FROM pg_class s
          JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
          JOIN pg_class t ON t.oid = d.refobjid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE s.relkind = 'S'
           AND n.nspname = 'public'
           AND t.relname = ANY (ARRAY['turns','call_errors','call_postbacks',
                            'tool_invocations','knowledge_gaps','calls',
                            'chat_messages','chat_conversations','chat_widgets',
                            'kb_chunks','kb_documents','kb_sources',
                            'prompt_versions','campaign_routes','campaign_tools',
                            'agent_config','alerts','provider_keys',
                            'config_audit','system_acks','user_sessions',
                            'campaigns','diallers'])
    LOOP
        PERFORM setval(r.seq, 1, false);
    END LOOP;
END
$reset$;

-- Did anything we meant to keep go with it?
DO $verify$
DECLARE
    r      record;
    now_n  bigint;
BEGIN
    FOR r IN SELECT * FROM _keep_before LOOP
        EXECUTE format('SELECT count(*) FROM public.%I', r.t) INTO now_n;
        IF now_n <> r.n THEN
            RAISE EXCEPTION
              'ROLLED BACK: % went from % rows to % - something cascaded that '
              'should not have. The database is unchanged.', r.t, r.n, now_n;
        END IF;
    END LOOP;

    SELECT count(*) INTO now_n FROM alert_rules WHERE campaign_id IS NULL;
    IF now_n <> (SELECT n FROM _rules_before) THEN
        RAISE EXCEPTION
          'ROLLED BACK: tenant-level alert_rules went from % to % rows. '
          'The database is unchanged.', (SELECT n FROM _rules_before), now_n;
    END IF;

    RAISE NOTICE 'kept tables verified unchanged';
END
$verify$;

COMMIT;
SQL

echo
echo "Database reset. What remains:"
psql_q "
SELECT format('  %-20s %6s', t, n) FROM (
  SELECT 'tenants' t, count(*) n FROM tenants
  UNION ALL SELECT 'users',             count(*) FROM users
  UNION ALL SELECT 'roles',             count(*) FROM roles
  UNION ALL SELECT 'role_permissions',  count(*) FROM role_permissions
  UNION ALL SELECT 'provider_rates',    count(*) FROM provider_rates
  UNION ALL SELECT 'platform_settings', count(*) FROM platform_settings
  UNION ALL SELECT 'alert_rules',       count(*) FROM alert_rules
) x ORDER BY t;"

# ── Recordings ──────────────────────────────────────────────────────────────
# Separate, and opt-in. These are files on disk that no database row points at
# any more once the calls are gone, so nothing breaks if they are left - they
# simply occupy the disk and belong to calls that no longer exist here.
if [ "$DO_RECORDINGS" = "1" ]; then
    echo
    if [ -d "$REC_DIR" ]; then
        N=$(find "$REC_DIR" -type f -name '*.wav' | wc -l)
        # -name '*.wav' and not -delete on the directory: the postprocess script
        # writes alongside these, and a bare rm -rf on a spool directory
        # Asterisk is actively using is not a thing to do at speed.
        find "$REC_DIR" -type f -name '*.wav' -delete
        echo "Deleted $N recordings from $REC_DIR"
    else
        echo "No recordings directory at $REC_DIR - nothing to delete"
    fi
fi

echo
echo "Done. Backup: $DUMP"
echo
echo "Before this server takes a call:"
echo "  1. Enter provider keys in the console - every one was removed"
echo "  2. Create the campaigns, and re-upload their knowledge base"
echo "  3. Set the dialler, if campaigns transfer through one"
echo "  4. Check TRANSFER_SIP_HOST in /opt/aivoice/.env - see docs/REPLICA.md"
