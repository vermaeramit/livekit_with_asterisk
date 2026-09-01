-- Add a dialler from the console, without touching the server.
--
-- 033 kept the credentials in iax.conf on purpose: a console that can write
-- them is a console that can dial anywhere. The operational cost turned out to
-- be the bigger problem - every new dialler meant an ssh session and a file
-- edit - and the decision was taken to move them here.
--
-- WHAT THAT COSTS, WRITTEN DOWN BECAUSE IT IS EASY TO FORGET LATER
--
-- IAX2 authenticates by MD5 challenge-response, so Asterisk needs the SECRET
-- ITSELF and not a hash of it. It cannot be encrypted at rest the way provider
-- keys are, because the thing that reads it is Asterisk over ODBC and Asterisk
-- has no key. So:
--
--   * every pg_dump and every database backup now contains dialler passwords
--     in clear text. Whoever can read a backup can dial that trunk.
--   * the asterisk_ro role can read them. That role's password is in
--     res_odbc.conf, 0640 root:asterisk.
--
-- Backup access and backup retention are now trunk credential access. That is
-- the trade that was made for not editing files.
--
-- Asterisk reads these through iax_peers below - REALTIME, so a row saved in
-- the console is dialable on the next transfer with no reload and no restart.

ALTER TABLE diallers
    -- All four NULL = an unmanaged dialler: the peer is a hand-written section
    -- in iax.conf and this row only names it. That is how 033 worked and it
    -- keeps working, so nothing had to be migrated on the day this ran.
    ADD COLUMN IF NOT EXISTS host     TEXT,
    ADD COLUMN IF NOT EXISTS port     INTEGER,
    ADD COLUMN IF NOT EXISTS username TEXT,
    ADD COLUMN IF NOT EXISTS secret   TEXT;

COMMENT ON COLUMN diallers.secret IS
    'Clear text, and it has to be: IAX2 is MD5 challenge-response and Asterisk '
    'needs the secret to compute the response. Never returned by the API, '
    'never logged. It IS in every database backup.';

-- What chan_iax2 reads. Realtime maps a family to a table, and every column it
-- finds is fed to the peer builder as if it were a line in iax.conf - so the
-- column names below are iax.conf option names, and nothing else may appear
-- here or Asterisk logs an unknown-keyword warning for each row it reads.
--
-- Only rows that are ACTIVE and actually carry credentials. A half-filled row
-- would otherwise become a peer with no secret, which fails authentication at
-- the far end and reads like the dialler is down.
CREATE OR REPLACE VIEW iax_peers AS
    SELECT peer                            AS name,
           'peer'::text                    AS type,
           host,
           coalesce(port, 4569)::text      AS port,
           coalesce(username, peer)        AS username,
           secret,
           -- Only takes effect if rtcachefriends is ever turned on: a realtime
           -- peer is built when it is dialled and freed afterwards, and there
           -- is nothing left to poke. Kept so that turning caching on later
           -- does the expected thing, not because it does anything today.
           'yes'::text                     AS qualify
      FROM diallers
     WHERE active
       AND coalesce(host, '') <> ''
       AND coalesce(secret, '') <> '';

COMMENT ON VIEW iax_peers IS
    'chan_iax2 realtime source - see extconfig.conf. Columns are iax.conf '
    'option names; adding one that is not an option makes Asterisk warn on '
    'every lookup. Codecs and call tokens are deliberately absent so they '
    'come from iax.conf [general], exactly as they did for the peers this '
    'replaced.';

SELECT 'diallers' AS t, count(*) FROM diallers
UNION ALL
SELECT 'peers asterisk can dial', count(*) FROM iax_peers
UNION ALL
SELECT 'routes visible to asterisk', count(*) FROM transfer_routes;
