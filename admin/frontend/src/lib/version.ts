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
 * and that suffix is the whole signal: this code has not been released. Used to
 * colour the label rather than to hide anything.
 */
export const IS_UNRELEASED = /-\d+-g[0-9a-f]+$/.test(APP_VERSION)
