#!/usr/bin/env bash
#
# host/workbench up: what a workspace asks for in its checkout (egress sets
# and engine profiles), alone and as a group of clones, and the settings
# that shape the mirror and the workbenches.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

touch "${PODMAN_STATE}/secret/gh-devcontainer"
export WORKBENCH_GH_OWNERS=acme WORKBENCH_AGENTS=claude
app="${WORKBENCH_ROOT}/app"
clone "${app}"
mkdir -p "${app}/.devcontainer"
conf="${XDG_RUNTIME_DIR}/workbench/egress/app.conf"
cd "${app}"
# up_again: up, with no workbench or engine running.
up_again() {
  gone workbench-claude-app l2-engine-app
  run "${wb}" up
}

printf '%s\n' '# what the tests reach' '' golang docker-hub >"${app}/.devcontainer/egress-sets"
up_again
check "the sets the checkout lists" 0 out "workbench: up for ${app}"
assert "are registered" grep -qx "sets=golang,docker-hub" "${conf}"

mkdir -p "${app}/.devcontainer/l2"
touch "${app}/.devcontainer/l2/Dockerfile"
up_again
assert "an L2 image of its own adds Ubuntu and NodeSource" grep -qx "sets=golang,docker-hub,ubuntu,nodesource" "${conf}"
rm -r "${app}/.devcontainer/l2"

wt="$(worktree "${app}" fix)"
mkdir -p "${wt}/.devcontainer"
echo python >"${wt}/.devcontainer/egress-sets"
cd "${wt}"
up_again
assert "read from the worktree up runs in" grep -qx "sets=python" "${conf}"
cd "${app}"

echo 'Bad_Name' >"${app}/.devcontainer/egress-sets"
up_again
check "a name that could not be a set stops up" 1 err \
  "workbench: 'Bad_Name' in ${app}/.devcontainer/egress-sets is not an egress set name"
refute "before anything starts" "podman run"

echo 'rust' >"${app}/.devcontainer/egress-sets"
up_again
check "a set the proxy does not know stops it too" 1 err \
  "workbench: ${app} asks for egress set 'rust', which the proxy does not know; known sets: python node golang docker-hub ghcr ubuntu nodesource alpine fedora hashicorp"
assert "and the registration is kept as it was" grep -qx "sets=python" "${conf}"

rule '*egress-refresh --list*' 'exit 1'
up_again
check "a proxy image that cannot list its sets" 1 err \
  "workbench: could not list the egress sets in localhost/airlock-egress-proxy:local"
unrule

printf '%s\n' mirror >"${app}/.devcontainer/egress-sets"
up_again
assert "mirror alone: no set at all on the proxy" grep -qx "sets=" "${conf}"
WORKBENCH_MIRROR=0 up_again
check "mirror with the mirror off" 1 err \
  "workbench: ${app} lists mirror in .devcontainer/egress-sets; the package mirror is off (WORKBENCH_MIRROR=0)"
printf '%s\n' mirror node >"${app}/.devcontainer/egress-sets"
up_again
assert "mirror among others leaves the others" grep -qx "sets=node" "${conf}"
rm "${app}/.devcontainer/egress-sets"

WORKBENCH_MIRROR=maybe up_again
check "WORKBENCH_MIRROR is 1 or 0" 1 err "workbench: WORKBENCH_MIRROR is 'maybe'; use 1 or 0"

# Profiles.
printf '%s\n' '# opt in' '' nested-network nested-devices hooks-engine >"${app}/.devcontainer/workbench-profile"
up_again
check "nested-network gives L2 a bridge" 0 calls \
  "--device /dev/fuse --device /dev/net/tun --security-opt unmask=/proc/sys -e CONTAINERS_CONF_OVERRIDE=/usr/local/share/devcontainer/containers-bridge-network.conf --device /dev/net/tun -v"
echo docker-socket >>"${app}/.devcontainer/workbench-profile"
up_again
check "an unknown profile stops up" 1 err \
  "workbench: unknown profile 'docker-socket' in ${app}/.devcontainer/workbench-profile"
refute "before anything starts" "podman run"
rm "${app}/.devcontainer/workbench-profile"

# A group asks for what its clones and the folder itself ask for.
org="${WORKBENCH_ROOT}/org"
clone "${org}/one"
clone "${org}/two"
clone "${org}/three"
mkdir -p "${org}/one/.devcontainer/l2" "${org}/.devcontainer"
printf '%s\n' '# sets' golang >"${org}/one/.devcontainer/egress-sets"
on_remote "${org}/two" origin/HEAD .devcontainer/egress-sets "$(printf '%s\n' '' hashicorp golang)"
on_remote "${org}/two" origin/HEAD .devcontainer/l2/Dockerfile "FROM x"
echo node >"${org}/.devcontainer/egress-sets"
printf '%s\n' nested-devices >"${org}/.devcontainer/workbench-profile"
printf '%s\n' '# p' '' nested-network >"${org}/one/.devcontainer/workbench-profile"
on_remote "${org}/three" origin/main .devcontainer/workbench-profile hooks-engine
cd "${org}"
run "${wb}" up
check "a group starts" 0 out "workbench: up for ${org}"
assert "with every set of its clones, each once" grep -qx \
  "sets=node,golang,python,hashicorp,ubuntu,nodesource" "${XDG_RUNTIME_DIR}/workbench/egress/org.conf"
check "and every profile" 0 calls "--device /dev/fuse --device /dev/net/tun --device /dev/net/tun --security-opt unmask=/proc/sys"

on_remote "${org}/three" origin/main .devcontainer/workbench-profile docker-socket
gone l2-engine-org
run "${wb}" up
check "an unknown profile in any clone stops it" 1 err \
  "workbench: unknown profile 'docker-socket' in ${org}/three/.devcontainer/workbench-profile"
on_remote "${org}/three" origin/main .devcontainer/workbench-profile
echo Bad >>"${org}/.devcontainer/workbench-profile"
run "${wb}" up
check "so does one in the folder's own" 1 err "workbench: unknown profile 'Bad' in ${org}/.devcontainer/workbench-profile"
rm "${org}/.devcontainer/workbench-profile"
echo 'Bad!' >"${org}/one/.devcontainer/egress-sets"
run "${wb}" up
check "a bad set name in any clone stops it" 1 err "workbench: 'Bad!' in ${org}/one/.devcontainer/egress-sets is not an egress set name"
echo golang >"${org}/one/.devcontainer/egress-sets"
echo 'Bad!' >"${org}/.devcontainer/egress-sets"
run "${wb}" up
check "and in the folder's own" 1 err "workbench: 'Bad!' in ${org}/.devcontainer/egress-sets is not an egress set name"
cd "${app}"

# Settings.
export WORKBENCH_TAG=dev WORKBENCH_TZ=America/Toronto WORKBENCH_MIRROR_HEAP=2048 WORKBENCH_MIRROR_MIN_AGE=7
reset_podman
touch "${PODMAN_STATE}/secret/gh-devcontainer"
printf '10.203.1.0/24' >"${PODMAN_STATE}/net/workbench-net-busy"
WORKBENCH_AGENTS="claude-personal" WORKBENCH_ACCOUNTS=personal run "${wb}" up
check "the tag names every image" 0 calls "localhost/airlock-l2-engine:dev"
check "a subnet in use is skipped" 0 calls "podman network create --internal --disable-dns --subnet 10.203.2.0/24 workbench-net-app"
check "the heap is set" 0 calls "-Xms2048m -Xmx2048m -XX:MaxDirectMemorySize=2048m"
check "the minimum age reaches the gate" 0 calls "-e MIN_AGE_DAYS=7 localhost/airlock-mirror-gate:dev"
check "an account's workbench has a login of its own" 0 calls \
  "--label workbench.agent=claude-personal --label workbench.session= --tz=America/Toronto"
check "mounted as the agent's" 0 calls "-v ${HOME}/.local/share/workbench/claude-personal:/home/dev/.claude:Z"
check "it runs that agent's image" 0 calls "--entrypoint catatonit localhost/airlock-workbench-claude:dev -- workbench-init"
unset WORKBENCH_TAG WORKBENCH_TZ WORKBENCH_MIRROR_HEAP WORKBENCH_MIRROR_MIN_AGE

# Every subnet taken.
reset_podman
touch "${PODMAN_STATE}/secret/gh-devcontainer"
for n in $(seq 1 254); do printf '10.203.%s.0/24' "${n}" >"${PODMAN_STATE}/net/n${n}"; done
run "${wb}" up
check "no subnet left" 1 err "workbench: no free 10.203.N.0/24 subnet left for workbench-net-app"

finish
