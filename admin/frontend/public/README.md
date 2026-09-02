# Static files, served from the site root

Anything here is copied to `/` at build time. `src/assets` would be bundled and
hashed; these are not, so a file can be replaced without a code change.

## worxpertise.png

The logo in the sidebar footer. Not in git — drop the brand's own file in, at
this exact name, and rebuild:

    docker compose -f admin/docker-compose.yml up -d --build

A missing file leaves an empty strip rather than a broken-image icon; see the
`onError` in `Layout.tsx`.

**Dark mode whites the logo out** (`dark:brightness-0 dark:invert`), because
the wordmark is dark grey and would be close to invisible on the dark sidebar.
That loses the red. If there is a version drawn for dark backgrounds, it is one
line in `Layout.tsx` to use it instead.

Roughly 3:1 and at least 120px wide renders well at the 28px height used there.
