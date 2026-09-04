-- What the widget looks like on somebody else's site.
--
-- It sits on a page the customer designed. A fixed blue bubble in the corner
-- of a red-and-white brand looks like something that does not belong there,
-- which is exactly what a visitor decides about it.
--
-- The text colour is NOT stored. It is computed from the accent, because a
-- stored one can disagree with it: pick a pale yellow accent, leave the text
-- white, and the header becomes unreadable with both fields looking filled in.

ALTER TABLE chat_widgets
    -- #rrggbb. Validated on the way in - a bad value here would land in a
    -- style attribute on a stranger's page.
    ADD COLUMN IF NOT EXISTS accent_color TEXT NOT NULL DEFAULT '#2563eb',
    -- Any URL the customer's page can already load. Not an upload: their logo
    -- is already on their own site, and asking them to send us a copy is
    -- asking them to keep two in step.
    ADD COLUMN IF NOT EXISTS icon_url TEXT;

COMMENT ON COLUMN chat_widgets.accent_color IS
    'Bubble, header and the visitor''s own messages. The text colour on top of '
    'it is computed from its luminance, never stored.';

SELECT count(*) AS widgets,
       count(*) FILTER (WHERE accent_color <> '#2563eb') AS recoloured
  FROM chat_widgets;
