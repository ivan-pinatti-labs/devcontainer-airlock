#!/usr/bin/env bash
#
# Tests for images/workbench/bin/airlock-worktree. git is a stub: `clone`
# makes the destination a repository, and origin's default branch is known
# as origin/main, or found only by `remote set-head` (origin/trunk).
# l2-hooks-install is a stub too.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

# The bodies expand when the stubs run, not here.
# shellcheck disable=SC2016
stub git '
if [ "$1" = -C ]; then shift 2; fi
case "$*" in
  "remote get-url origin") [ -z "${NO_ORIGIN:-}" ] || exit 2; echo https://example.com/app.git ;;
  "clone "*) mkdir -p "${5}/.git" ;;
  "symbolic-ref --quiet --short refs/remotes/origin/HEAD") echo origin/main ;;
  "symbolic-ref --short refs/remotes/origin/HEAD") echo origin/trunk ;;
  "branch --show-current") echo taken ;;
esac'
stub l2-hooks-install

src="${__scratch}/src"
app="${src}/app"
dest="${app}/.claude/worktrees/brave-otter"
mkdir -p "${app}/.git" "${dest}"
cd "${src}"
unset AIRLOCK_SESSION

run images/workbench/bin/airlock-worktree app
check "only in a session" 1 err "airlock-worktree: not in a session; host/workbench session start (make claude) starts one"
export AIRLOCK_SESSION=brave-otter
run images/workbench/bin/airlock-worktree
check "a repository is needed" 1 err "airlock-worktree: usage: airlock-worktree REPO [BRANCH]"
run images/workbench/bin/airlock-worktree nowhere
check "one that exists" 1 err "airlock-worktree: no repository nowhere"
mkdir -p "${src}/notes"
run images/workbench/bin/airlock-worktree notes
check "and is a git repository" 1 err "airlock-worktree: ${src}/notes is not a git repository"
mkdir -p "${src}/other/.git"
run images/workbench/bin/airlock-worktree "${src}/other"
check "the session must cover it" 1 err \
  "airlock-worktree: ${src}/other/.claude/worktrees/brave-otter is missing: the session mounts it at start, so this session does not cover ${src}/other"
touch "${dest}/stray"
run images/workbench/bin/airlock-worktree app
check "its folder must be empty" 1 err "airlock-worktree: ${dest} is not empty"
rm "${dest}/stray"
NO_ORIGIN=1 run images/workbench/bin/airlock-worktree app
check "and the repository needs an origin" 1 err "airlock-worktree: ${app} has no origin remote"

run images/workbench/bin/airlock-worktree app
check "it clones the repository by name, into the session's folder" 0 out \
  "airlock-worktree: ${dest} on brave-otter, from origin/main"
check "with its own objects" 0 calls "git clone --quiet --no-hardlinks ${app} ${dest}"
check "origin the main clone's remote" 0 calls "git remote set-url origin https://example.com/app.git"
check "fetched" 0 calls "git fetch --quiet origin"
check "the session's branch from origin's default branch" 0 calls "git switch --quiet -c brave-otter --no-track origin/main"
refute "with no hooks to install" "l2-hooks-install"

run images/workbench/bin/airlock-worktree app
check "a second time, the clone is there" 1 err "airlock-worktree: ${dest} already holds a clone (branch taken)"

rm -r "${dest}"
mkdir -p "${dest}"
cd "${app}"
# This clone brings a pre-commit configuration with it.
echo 'repos: []' >"${__scratch}/config"
# shellcheck disable=SC2016
stub git '
if [ "$1" = -C ]; then shift 2; fi
case "$*" in
  "remote get-url origin") echo https://example.com/app.git ;;
  "clone "*) mkdir -p "${5}/.git"; cp "${CONFIG}" "${5}/.pre-commit-config.yaml" ;;
  "symbolic-ref --quiet --short refs/remotes/origin/HEAD") exit 1 ;;
  "symbolic-ref --short refs/remotes/origin/HEAD") echo origin/trunk ;;
esac'
CONFIG="${__scratch}/config" run images/workbench/bin/airlock-worktree "${app}" feat/x
check "a branch of its own name, from a default branch found now" 0 out \
  "airlock-worktree: ${dest} on feat/x, from origin/trunk"
check "asking origin for it" 0 calls "git remote set-head origin --auto"
check "the hooks installed, to run in L2" 0 calls "l2-hooks-install"

finish
