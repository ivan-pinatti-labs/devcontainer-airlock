#!/usr/bin/env bash
#
# Tests for images/workbench/bin/l2. git and podman are stubs: git answers
# for a scratch repository (GIT_TOP, GIT_COMMON, GIT_DIR_OF; no GIT_TOP is
# no repository), and podman logs each call, answers `image inspect` from
# BASE_ID and HAVE_LABEL, fails a build when BUILD_FAILS is set, and in a
# run does whatever RUN_DOES says, exiting RUN_STATUS.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

l2="${__repo}/images/workbench/bin/l2"
repo="${__scratch}/work/project"
mkdir -p "${repo}/.git" "${repo}/.devcontainer" "${repo}/.vscode"
echo "all:" >"${repo}/Makefile"
ln -s ../Makefile "${repo}/.vscode/link"
export GIT_TOP="${repo}" GIT_COMMON="${repo}/.git" GIT_DIR_OF="${repo}/.git"
# shellcheck disable=SC2016 # expanded when the stubs run
stub git '
[ -n "${GIT_TOP:-}" ] || exit 128
case "$*" in
  "rev-parse --show-toplevel") echo "${GIT_TOP}" ;;
  "rev-parse --path-format=absolute --git-common-dir") echo "${GIT_COMMON}" ;;
  "rev-parse --path-format=absolute --git-dir") echo "${GIT_DIR_OF}" ;;
esac'
# shellcheck disable=SC2016
stub podman '
case "$1 $2" in
  "image inspect")
    case "$*" in
      *.Id*) [ -n "${BASE_ID:-}" ] || exit 125; echo "${BASE_ID}" ;;
      *) [ -n "${HAVE_LABEL:-}" ] || exit 125; echo "${HAVE_LABEL}" ;;
    esac ;;
  "build "*) [ -z "${BUILD_FAILS:-}" ] || exit 1 ;;
  "run "*) eval "${RUN_DOES:-}"; exit "${RUN_STATUS:-0}" ;;
esac'
export L2_IMAGE=ghcr.io/example/l2:1
unset AIRLOCK_MIRROR AIRLOCK_EGRESS_PROXY HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy NODE_USE_ENV_PROXY
cd "${repo}"

run "${l2}" --help
check "--help shows the usage" 0 out "  l2 [--net] [--ro] [--engine]"
check "without the comment marks" 0 out "Run a command in L2: a throwaway container"

run "${l2}" --bogus true
check "an unknown option is a usage error" 2 err "l2: unknown option --bogus"

run "${l2}" --net --
check "so is no command" 2 err "l2: no command given"

RUN_STATUS=4 run "${l2}" make test
check "runs the command in the L2 image, no network" 4 calls \
  "podman run --rm --interactive --user 0:0 --cap-drop=all --cap-add=setuid --cap-add=setgid --security-opt no-new-privileges"
check "the working tree read write" 4 calls "-v ${repo}:${repo}:rw"
check "git's config and hooks read only" 4 calls \
  "-v ${repo}/.git/config:${repo}/.git/config:ro -v ${repo}/.git/hooks:${repo}/.git/hooks:ro"
check "and the paths the workbench executes" 4 calls \
  "-v ${repo}/.devcontainer:${repo}/.devcontainer:ro -v ${repo}/.vscode:${repo}/.vscode:ro -v ${repo}/Makefile:${repo}/Makefile:ro"
check "a home of its own, in the working directory" 4 calls "-v l2-home-project:/root -w ${repo}"
check "with no network" 4 calls \
  "--network=none --http-proxy=false ghcr.io/example/l2:1 make test"
refute "the git directory inside the tree is not mounted twice" "-v ${repo}/.git:${repo}/.git:rw"
assert "the hooks directory is made if missing" test -d "${repo}/.git/hooks"

mkdir -p "${__scratch}/main/.git/worktrees/x"
touch "${__scratch}/main/.git/worktrees/x/config.worktree"
GIT_COMMON="${__scratch}/main/.git" GIT_DIR_OF="${__scratch}/main/.git/worktrees/x" \
  run "${l2}" --ro --protected-rw --env A=1 --env B=2 --image other:2 -- true
check "a worktree gets the clone's git directory" 0 calls "-v ${__scratch}/main/.git:${__scratch}/main/.git:rw"
check "its config.worktree read only" 0 calls \
  "-v ${__scratch}/main/.git/worktrees/x/config.worktree:${__scratch}/main/.git/worktrees/x/config.worktree:ro"
check "--ro mounts the tree read only" 0 calls "-v ${repo}:${repo}:ro"
check "--env passes each variable, --image picks the image" 0 calls "-e A=1 -e B=2 other:2 true"
check "the home is named after the clone" 0 calls "-v l2-home-main:/root"
refute "--protected-rw leaves the protected paths writable" "${repo}/Makefile:ro"

echo "ghcr.io/example/project-l2:3" >"${repo}/.devcontainer/l2-image"
run "${l2}" true
check ".devcontainer/l2-image names the image" 0 calls "ghcr.io/example/project-l2:3 true"
rm "${repo}/.devcontainer/l2-image"

mkdir "${repo}/.devcontainer/l2"
echo "FROM l2" >"${repo}/.devcontainer/l2/Dockerfile"
own=localhost/airlock-l2-project:local
BASE_ID=abc run "${l2}" true
check "a repository's own Dockerfile is built in the engine" 0 calls \
  "podman build --quiet --label workbench.l2.dockerfile="
check "saying so" 0 err "l2: building ${own} from .devcontainer/l2/Dockerfile, in the engine"
check "and its image used" 0 calls "${own} true"
want="$({
  echo "FROM l2"
  echo abc
} | sha256sum | cut -d' ' -f1)"
BASE_ID=abc HAVE_LABEL="${want}" run "${l2}" true
refute "and not built again while it and its base are the same" "podman build"
BASE_ID=abd HAVE_LABEL="${want}" run "${l2}" true
check "a new base builds it again" 0 calls "podman build"
BUILD_FAILS=1 run "${l2}" true
check "a build without the base fails, saying where to get it" 1 err \
  "l2: ghcr.io/example/l2:1 is not in the engine; on the host, host/workbench load-l2 copies it in"
BASE_ID=abc BUILD_FAILS=1 run "${l2}" true
check "any other failed build fails" 1 calls "podman build"
refute "before running" "podman run"
rm -r "${repo}/.devcontainer/l2"

run "${l2}" --engine true
check "--engine gives the engine's socket and scratch directory" 0 calls \
  "--cap-add=sys_chroot -v /run/l2-engine/podman.sock:/run/l2-engine/podman.sock -v /var/tmp/l2-scratch:/var/tmp/l2-scratch -e CONTAINER_HOST=unix:///run/l2-engine/podman.sock -e DOCKER_HOST=unix:///run/l2-engine/podman.sock -e TMPDIR=/var/tmp/l2-scratch -e PATH=/usr/local/lib/l2/engine-bin:"

HTTPS_PROXY=http://egress:3128 HTTP_PROXY=http://egress:3128 no_proxy=localhost \
  run "${l2}" --net true
check "--net passes the proxy settings that are set" 0 calls \
  "-e HTTPS_PROXY=http://egress:3128 -e HTTP_PROXY=http://egress:3128 -e no_proxy=localhost ghcr.io/example/l2:1 true"
refute "and no others" "-e https_proxy"
refute "with network" "--network=none"

HTTPS_PROXY=http://127.0.0.1:8889 https_proxy=http://127.0.0.1:8889 HTTP_PROXY=http://egress:3128 \
  AIRLOCK_EGRESS_PROXY=http://egress:3128 AIRLOCK_MIRROR=http://mirror-gate:8080/ \
  run "${l2}" --net true
check "under the relay, the egress proxy itself" 0 calls \
  "-e HTTPS_PROXY=http://egress:3128 -e https_proxy=http://egress:3128"
check "with a mirror, its gate for packages and apt" 0 calls \
  "-e PIP_INDEX_URL=http://mirror-gate:8080/pypi/simple/ -e PIP_TRUSTED_HOST=mirror-gate -e UV_DEFAULT_INDEX=http://mirror-gate:8080/pypi/simple/ -e UV_INSECURE_HOST=mirror-gate -e NPM_CONFIG_REGISTRY=http://mirror-gate:8080/npm/ -e GOPROXY=http://mirror-gate:8080/go -e HTTP_PROXY=http://mirror-gate:8080 -e http_proxy=http://mirror-gate:8080"
refute "instead of the egress proxy for plain http" "HTTP_PROXY=http://egress:3128"

# shellcheck disable=SC2016 # expanded by the podman stub
RUN_DOES='echo changed >>"${GIT_TOP}/Makefile"; ln -s x "${GIT_TOP}/.claude"' run "${l2}" make
check "a run that changes a protected path fails" 3 err "l2: this run changed files the workbench or the host execute:"
check "naming what it changed" 3 err "  ${repo}/Makefile"
check "or added" 3 err "  .claude"
rm "${repo}/.claude"

cd "${__scratch}/work"
GIT_TOP="" RUN_STATUS=6 run "${l2}" ls
check "outside a repository, the current directory is the tree" 6 calls \
  "-v ${__scratch}/work:${__scratch}/work:rw -v l2-home-work:/root -w ${__scratch}/work"

GIT_TOP="" L2_IMAGE="" run "${l2}" ls
check "and with no image anywhere it says so" 1 err "l2: no image, set L2_IMAGE or .devcontainer/l2-image"

finish
