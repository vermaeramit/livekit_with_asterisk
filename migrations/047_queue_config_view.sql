-- What Asterisk is allowed to know about a campaign's call limit.
--
-- Same shape and the same reasoning as transfer_routes in 033: a view rather
-- than a grant on agent_config, because this is the whole surface the
-- asterisk_ro user can reach. That table holds prompts, postback credentials
-- and provider hints; the dialplan needs seven values and gets seven values.
--
-- ONE DELIBERATE DIFFERENCE from transfer_routes, which returns its columns
-- separately and lets func_odbc.conf join them with '^'. Here the joining is
-- done in the view.
--
-- The reason is that func_odbc.conf lives in /etc/asterisk on the server and
-- nowhere else. It is edited by hand, on a box carrying live calls, and the
-- repo's copy of the Asterisk config has already drifted behind the running
-- one. Putting the field list in the view means adding a field later is a
-- migration - reviewed, in git, applied like every other one - instead of an
-- SSH session and a dialplan reload. Asterisk's config is touched once, for
-- this feature, and then not again.

CREATE OR REPLACE VIEW queue_routes AS
    SELECT r.did,
           -- '^' because that is what the TRANSFER lookup already uses. None
           -- of these can contain one: the numbers are integers, the action is
           -- CHECK-constrained to two words, and the paths are a directory
           -- plus a hex hash.
           --
           -- An empty max_parallel_calls means unlimited, which is what the
           -- dialplan has to be able to tell apart from zero - zero is a
           -- campaign that has been deliberately closed.
           concat_ws('^',
                     ac.campaign_id::text,
                     coalesce(ac.max_parallel_calls::text, ''),
                     coalesce(ac.queue_audio_file, ''),
                     ac.queue_gap_seconds::text,
                     ac.queue_max_wait_seconds::text,
                     ac.queue_timeout_action,
                     coalesce(ac.queue_timeout_audio_file, '')) AS cfg
      FROM campaign_routes r
      JOIN agent_config ac ON ac.campaign_id = r.campaign_id;

COMMENT ON VIEW queue_routes IS
    'Read by Asterisk over ODBC on every inbound call, keyed by the dialled '
    'extension. cfg is campaign_id^limit^audio^gap^maxwait^action^byeaudio, '
    'with an empty limit meaning unlimited. A DID with no row here is a DID '
    'with no limit - the dialplan fails open on purpose.';

-- The role exists on this server; the guard is for a rebuild from scratch,
-- where the ODBC setup script has not run yet. A migration that fails on a
-- missing role would stop every later one behind it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'asterisk_ro') THEN
        GRANT SELECT ON queue_routes TO asterisk_ro;
    ELSE
        RAISE NOTICE 'asterisk_ro does not exist yet - grant it SELECT on '
                     'queue_routes when setup-asterisk-odbc.sh runs';
    END IF;
END $$;

-- Every route, and what the dialplan will read for it. A limited campaign
-- should show its number here; everything else shows an empty second field,
-- which is the unlimited behaviour every campaign has today.
SELECT did, cfg FROM queue_routes ORDER BY did;
