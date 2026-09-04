-- The widget's icon, uploaded rather than linked.
--
-- A URL meant the customer had to host the file somewhere first, which is a
-- step between them and a working widget for no benefit to anyone.
--
-- Stored in the DATABASE, not on disk. A logo is tens of kilobytes: keeping it
-- here means it is in the backup already, there is no volume to mount, no path
-- to keep in step between the container and the host, and nothing to go
-- missing when this is deployed somewhere new.
--
-- icon_url is dropped rather than kept alongside. Two places to put an icon is
-- two places to look when the wrong one appears, and the column is hours old
-- with nothing in it.

ALTER TABLE chat_widgets
    ADD COLUMN IF NOT EXISTS icon_data BYTEA,
    -- image/png, image/jpeg or image/webp. Not SVG: an SVG can carry script,
    -- and this is served from our own origin - harmless inside an <img>, and
    -- not harmless if somebody opens the URL directly.
    ADD COLUMN IF NOT EXISTS icon_mime TEXT;

ALTER TABLE chat_widgets DROP COLUMN IF EXISTS icon_url;

SELECT count(*) AS widgets,
       count(*) FILTER (WHERE icon_data IS NOT NULL) AS with_an_icon
  FROM chat_widgets;
