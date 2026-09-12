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
// v0.10.0-3-gff6b7a2[-dirty] -> ["v0.10.0", "3", "ff6b7a2", "-dirty"]
const AHEAD = /^(.+)-(\d+)-g([0-9a-f]+)(-dirty)?$/.exec(APP_VERSION)

export const IS_UNRELEASED = AHEAD !== null

/**
 * `v0.10.0` on a release, `v0.10.0+3` when three commits past one.
 *
 * Two goes at this, and the reasoning matters more than the format.
 *
 * `git describe` writes `v0.9.0-1-g8165da5`, naming the release BEFORE the
 * commit. Next to production's `v0.10.0` that read as though production were a
 * version ahead of development, when both were the same commit - development's
 * label had simply been built before the tag existed, and names the older
 * release by construction either way.
 *
 * The first attempt dropped the number on development and showed the commit
 * alone. That removed the false comparison by removing the thing being
 * compared, which is not the same as fixing it - and the version was what
 * somebody wanted to see.
 *
 * `+3` fixes it properly: the same release name as production, plus how far
 * past it this build is. It cannot read as older, because it is the same number
 * with something added. `+0` never appears - that is a release, and shows as one.
 *
 * One honest limit: this names the newest release that existed WHEN THE BUILD
 * RAN. Tag a release afterwards and a development box keeps naming the previous
 * one until it is redeployed. The `+N` marks it as a development build, which is
 * what stops that being misleading, and the next deploy corrects it.
 */
export const DISPLAY_VERSION = AHEAD
  ? `${AHEAD[1]}+${AHEAD[2]}${AHEAD[4] ?? ''}`
  : APP_VERSION
