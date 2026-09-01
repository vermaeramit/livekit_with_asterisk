-- Which dialler a campaign hands its calls to.
--
-- One dialler today; the requirement is several, one per campaign. The split
-- below is the point of the whole thing:
--
--   iax.conf   the peer - host, port, username, secret. Infrastructure, changes
--              rarely, and belongs in a file that is not in git.
--   here       which campaign uses which peer, and at what extension. Business
--              configuration, changes often, and belongs in the console.
--
-- Adding a dialler is a one-off block in iax.conf plus a row here. Pointing a
-- campaign at it is then a dropdown, with nobody touching the server.

CREATE TABLE IF NOT EXISTS diallers (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    -- The peer name in iax.conf. Asterisk dials IAX2/<peer>/<extension>, so
    -- this must match a section there exactly or the transfer fails at the last
    -- step - after the caller has already been told to hold.
    peer        TEXT        NOT NULL UNIQUE,
    description TEXT,
    active      BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS transfer_dialler_id BIGINT
        REFERENCES diallers(id) ON DELETE SET NULL,
    -- The extension ON THAT DIALLER. Two campaigns may both use 5000 on
    -- different diallers, which is exactly why the extension alone was never
    -- enough to route by.
    ADD COLUMN IF NOT EXISTS transfer_extension TEXT;

-- The ONLY thing Asterisk is allowed to see.
--
-- A view rather than a grant on the tables: Asterisk's read-only user gets
-- three columns and no way to reach a transcript, a provider key or a user
-- row. If the dialplan is ever tricked into running a query nobody intended,
-- this is the whole of what it could return.
CREATE OR REPLACE VIEW transfer_routes AS
    SELECT ac.campaign_id,
           d.peer,
           ac.transfer_extension AS extension
      FROM agent_config ac
      JOIN diallers d ON d.id = ac.transfer_dialler_id
     WHERE ac.transfer_enabled
       AND d.active
       AND coalesce(ac.transfer_extension, '') <> '';

COMMENT ON VIEW transfer_routes IS
    'Read by Asterisk over ODBC at transfer time. Keep it to what the dialplan '
    'needs - it is the entire surface that user can reach.';

SELECT 'diallers' AS created, count(*) FROM diallers
UNION ALL
SELECT 'routes visible to asterisk', count(*) FROM transfer_routes;
