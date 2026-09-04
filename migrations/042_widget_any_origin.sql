-- Let a widget run on any site.
--
-- 041 made an empty origin list mean OFF, and that is still right as a
-- DEFAULT: a widget nobody has configured should refuse rather than serve
-- everybody. But there are real cases for open - a customer with dozens of
-- subdomains, a site whose hostname is not known yet, a quick trial - and the
-- alternative to supporting them is somebody putting the campaign's key
-- somewhere worse.
--
-- Its own column rather than a "*" in allowed_origins. A wildcard in the list
-- looks like one more entry and scrolls past; a flag reads as a decision, and
-- the console presents it as one.
--
-- What this gives up, stated once so it is not rediscovered later: the Origin
-- header is the only check a browser cannot be talked out of. With it off, the
-- daily token cap is the ONLY thing standing between this campaign's key and
-- anyone who reads the page source.

ALTER TABLE chat_widgets
    ADD COLUMN IF NOT EXISTS allow_any_origin BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN chat_widgets.allow_any_origin IS
    'true = serve any site, and requests with no Origin at all. The daily '
    'token cap is then the only limit that remains.';

SELECT count(*) AS widgets,
       count(*) FILTER (WHERE allow_any_origin) AS open_to_everyone
  FROM chat_widgets;
