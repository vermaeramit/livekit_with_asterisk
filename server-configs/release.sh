#!/bin/sh
# Cut a production release: tag it, and record what went into it.
#
#     server-configs/release.sh 0.7.0
#
# The version is a git TAG and nothing else. No VERSION file to fall out of step
# with it, and the 0.0.0 in package.json means nothing on purpose - `git
# describe` is the single answer, computed at deploy time.
#
# That is what makes "the version only changes when we go to production" true
# rather than a rule somebody has to remember:
#
#   production  is checked out AT a tag   -> git describe gives  v0.7.0
#   development is some commits past it   -> git describe gives  v0.7.0-20-gabc1234
#
# Development cannot show a clean version even by accident, and production
# cannot show a dirty one without the login page calling it "(untagged)".
#
# Run it from any clone that can PUSH, on main, with nothing uncommitted - which
# in practice means the machine the code is written on, not a server. The servers
# pull and cannot push, and a tag is a property of the commit rather than of the
# machine that named it, so where it is cut makes no difference to what it means.
#
# It does not deploy anything. It makes the release exist; deploy.sh puts it on
# production.
set -eu

NEW="${1:-}"

die() { echo "REFUSED: $*" >&2; exit 1; }

case "$NEW" in
    '') echo "usage: $0 <major.minor.patch>     e.g. $0 0.7.0" >&2; exit 2 ;;
esac

# Shape first, because everything below compares version numbers and a
# non-number sorts in ways nobody predicts.
echo "$NEW" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' \
    || die "'$NEW' is not major.minor.patch (no v prefix here - it is added)"

cd "$(dirname "$0")/.."

BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$BRANCH" = "main" ] || die "on branch '$BRANCH'. Releases are cut from main."

git diff --quiet && git diff --cached --quiet \
    || die "the working tree has uncommitted changes. Commit or stash first."

# BEFORE the two checks below, both of which compare against origin/main. A
# stale remote ref makes them agree with each other and with nothing real.
git fetch --tags --quiet origin 2>/dev/null || true

# A tag pointing at a commit that only exists on this machine is worse than no
# tag: production fetches it, cannot find the commit, and the error names the
# sha rather than the cause.
[ -z "$(git --no-pager log origin/main..HEAD --oneline 2>/dev/null)" ] \
    || die "there are commits here that are not pushed. 'git push' first, or
         production will fetch a tag whose commit does not exist."

# And the other direction, which is the quiet one. A tag is cut at HEAD, so if
# HEAD is behind the remote then everything pushed since gets left out of the
# release - with nothing to say so, because the tag is perfectly valid and the
# omission only shows up as a bug fix that "did not go out".
BEHIND=$(git --no-pager log HEAD..origin/main --oneline 2>/dev/null | wc -l | tr -d ' ')
[ "$BEHIND" = "0" ] \
    || die "origin/main is $BEHIND commit(s) ahead of this checkout. A tag cut
         here would silently leave them out of the release. 'git pull' first."

git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null \
    && die "v$NEW already exists. Versions are never re-pointed - somebody may
         already be running it, and moving a tag makes two different builds
         answer to one number."

LATEST=$(git tag -l 'v*' | sed 's/^v//' | sort -V | tail -1)
if [ -n "$LATEST" ]; then
    HIGHEST=$(printf '%s\n%s\n' "$LATEST" "$NEW" | sort -V | tail -1)
    [ "$HIGHEST" = "$NEW" ] && [ "$NEW" != "$LATEST" ] \
        || die "v$NEW is not newer than v$LATEST."
    RANGE="v$LATEST..HEAD"
else
    RANGE="HEAD"
fi

# ── What is in it ───────────────────────────────────────────────────────────
# Into the tag message, not just onto the screen. `git show v0.7.0` then answers
# "what changed in this release" months later, on any clone, with no access to
# whatever terminal this was run in.
COUNT=$(git --no-pager log --no-merges --oneline "$RANGE" | wc -l | tr -d ' ')

echo "Release v$NEW"
[ -n "$LATEST" ] && echo "  since v$LATEST: $COUNT commits" || echo "  first tagged release"
echo

# --no-pager, and capped.
#
# Without it git opens `less`, which in a script means the release stops dead
# waiting for a keypress nobody knew to make - and on quitting, git takes a
# SIGPIPE, exits non-zero, and `set -e` ends the script with no tag, no error and
# no output. That happened on the first real release: 323 commits opened a pager
# and the run died in silence.
#
# The cap is the other half. Anything long enough to need a pager is too long to
# read on a terminal anyway; the FULL list still goes into the tag message below,
# which is where it is actually useful.
git --no-pager log --no-merges --pretty='  %s' "$RANGE" | head -20
# `if` rather than `[ … ] && echo` for legibility only. The && form is also
# safe here, contrary to what this comment first claimed: POSIX exempts every
# command in an AND-OR list except the last one from set -e, so a test that
# fails there does not end the script. Verified rather than assumed -
# `sh -c 'set -e; false && echo x; echo survived'` prints survived.
if [ "$COUNT" -gt 20 ]; then
    echo "  ... and $((COUNT - 20)) more - all of them go into the tag message"
fi
echo

[ "$COUNT" -gt 0 ] || die "nothing has changed since v$LATEST. Releasing the same
         code under a new number makes the number meaningless."

MSG=$(printf 'Release v%s\n\n%s\n' "$NEW" "$(git --no-pager log --no-merges --pretty='* %s' "$RANGE")")
git tag -a "v$NEW" -m "$MSG"

# If the push fails, take the tag back.
#
# A tag that exists locally and not on origin is worse than no tag at all:
# production cannot fetch it, and the next attempt at the same version stops with
# "v0.7.0 already exists" - pointing at a tag nobody else can see. That is
# exactly what happened the first time this ran on a box with pull access and no
# push access, which is most of them.
if ! git push --quiet origin "v$NEW" 2>/dev/null; then
    git tag -d "v$NEW" >/dev/null
    die "could not push v$NEW to origin, so the local tag has been removed -
         nothing is half-created.

         Usually this box can pull but cannot push. Cut the release from a clone
         that has push access; the tag is a property of the commit, not of the
         machine, so it makes no difference where it is created."
fi

echo "Tagged and pushed v$NEW"
echo
echo "Now put it on production:"
echo
echo "    ssh root@<production>"
echo "    cd /srv/aivoice && server-configs/deploy.sh v$NEW"
echo
echo "Then redeploy DEVELOPMENT, even though its code has not changed:"
echo
echo "    cd /srv/aivoice && server-configs/deploy.sh"
echo
# Not optional tidying. A console's version is `git describe` baked in at build
# time, naming the newest release that existed THEN. Development was built before
# this tag, so it keeps reporting the previous one - and beside production's
# fresh number that reads as production being ahead of development, which is the
# confusion this whole scheme exists to remove. One rebuild and they agree.
echo "Its label still names the release BEFORE this one, so until it is rebuilt"
echo "it reads as older than production while running the same code."
echo
echo "Until production runs, it keeps serving whatever it already had - this"
echo "only made the release exist."
