-- Log a user out when they walk away.
--
-- There was no session timeout at all. An access token lasts 15 minutes, but
-- that is a blast radius and not a session: the browser renews it silently, so
-- an unlocked laptop stayed signed in until the 7-day refresh token expired.
--
-- WHY THIS IS NOT "LAST REQUEST TIME"
--
-- The console polls alert and gap counts every 60 seconds from the layout, on
-- every page. An open tab therefore makes requests forever with nobody in the
-- chair, and an idle timeout measured from the last API call would never fire
-- once - a feature that looks present and does nothing.
--
-- So last_seen_at is moved only by a heartbeat the browser sends on real input
-- - mouse, keyboard, touch - throttled to once a minute. Polling does not
-- touch it.
--
-- The refresh endpoint enforces it. That matters for the case with no browser
-- left to enforce anything: a closed laptop has no client to log itself out,
-- so the server has to refuse the next refresh on its own.

ALTER TABLE user_sessions
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now();

COMMENT ON COLUMN user_sessions.last_seen_at IS
    'Last REAL user activity, from the heartbeat. Deliberately not touched by '
    'ordinary API traffic: the console polls on a timer and would keep every '
    'abandoned tab alive for ever.';

-- Sessions are rotated on every refresh, so this table grows a row per refresh
-- and is read by hash. The idle sweep below wants the other direction.
CREATE INDEX IF NOT EXISTS user_sessions_seen_idx
    ON user_sessions (last_seen_at)
 WHERE revoked_at IS NULL;

SELECT count(*) AS live_sessions,
       count(*) FILTER (WHERE last_seen_at < now() - interval '30 minutes')
           AS already_idle
  FROM user_sessions
 WHERE revoked_at IS NULL AND expires_at > now();
