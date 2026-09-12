/**
 * What this build is, and which server it is running on.
 *
 * Both values are inlined by Vite at build time from the docker build args - see
 * admin/docker-compose.yml. Nothing here asks the API, because the first screen
 * that shows them is the login page, and a version that needs a working API to
 * render would go blank exactly when the API is what has gone wrong.
 *
 * The version is `git describe --tags` from the box being deployed, which means
 * its SHAPE carries information:
 *
 *   v0.7.0                 sitting exactly on a release tag - production
 *   v0.7.0-20-gabc1234     20 commits past v0.7.0 - development, unreleased
 *   unknown                brought up by hand rather than through deploy.sh
 *
 * That last one is deliberately not dressed up as a number. A wrong version is
 * worse than an absent one: somebody eventually reports a bug against it.
 */

export const APP_VERSION =
  (import.meta.env.VITE_APP_VERSION as string | undefined) || 'unknown'

export type AppEnv = 'production' | 'development' | 'unknown'

const RAW_ENV = (import.meta.env.VITE_APP_ENV as string | undefined) || ''

export const APP_ENV: AppEnv =
  RAW_ENV === 'production' || RAW_ENV === 'development' ? RAW_ENV : 'unknown'

export const ENV_LABEL: Record<AppEnv, string> = {
  production: 'Production',
  development: 'Development',
  // Not "Production" as a guess, and not blank. APP_ENV is one line in
  // admin/.env and an unset one means nobody has said what this box is.
  unknown: 'Unnamed server',
}

/**
 * True when this build is not sitting on a release tag.
 *
 * `git describe` appends `-<n>-g<sha>` once there are commits after the tag,
 * and that suffix is the whole signal: this code has not been released.
 */
const UNRELEASED = /-\d+-g([0-9a-f]+)(-dirty)?$/.exec(APP_VERSION)

export const IS_UNRELEASED = UNRELEASED !== null

/**
 * What to actually show. On a release it is the release; off one it is the
 * COMMIT, and deliberately not the version.
 *
 * The first version of this showed the whole `git describe` string, and it was
 * misleading in a way that took a user to spot. Development showed
 * `v0.9.0-1-g8165da5` while production showed `v0.10.0` - the same commit, both
 * correct, and reading as though production were two versions ahead of
 * development.
 *
 * Two things caused that. Development's label names the LAST RELEASE BEFORE it,
 * so its number is always behind by construction; and it is baked at build time,
 * so tagging that same commit afterwards changes what it would say without
 * changing what it says. Neither is a bug on its own. Putting the two strings
 * side by side, where they invite a comparison they cannot support, was.
 *
 * So off a release there is no version number at all - just the commit, which is
 * the only thing that identifies a development build unambiguously. `git show
 * 8165da5` answers everything the version number was being asked for. The full
 * describe string is still on the element's title for anyone who wants it.
 */
export const DISPLAY_VERSION = UNRELEASED
  ? UNRELEASED[1] + (UNRELEASED[2] ?? '')
  : APP_VERSION
