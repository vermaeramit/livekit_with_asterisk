-- Tell the agent what day it is.
--
-- Callers ask outright ("टाइम क्या हुआ है अभी?") and were told the agent cannot
-- say. The larger cost is quieter: "कल आ जाऊँगा, सुबह 10 बजे" cannot be turned
-- into a real date without knowing today's, so the postback carried the words
-- and not the appointment.
--
-- Off by default. It puts a line in the prompt, and a campaign that does not
-- need one should not be paying for it.

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS prompt_datetime  boolean NOT NULL DEFAULT false,
    -- An IANA name. Wrong or unknown falls back to +05:30 rather than to UTC:
    -- a clock that is silently five and a half hours out reads as working.
    ADD COLUMN IF NOT EXISTS prompt_timezone  text    NOT NULL DEFAULT 'Asia/Kolkata';

COMMENT ON COLUMN agent_config.prompt_datetime IS
    'Append the current date and time to the end of the prompt, once per call.';
COMMENT ON COLUMN agent_config.prompt_timezone IS
    'IANA timezone for that line, e.g. Asia/Kolkata.';
