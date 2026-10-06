#!/usr/bin/env bash
#
# Tests for images/podman-nested/bin/podman-nested-init. The cgroup tree is a
# plain directory (PODMAN_NESTED_CGROUP_ROOT) whose files take the writes,
# and a step is made to fail by putting a directory where it writes a file,
# or a file where it makes a directory. id answers ID_U (root by default),
# chown fails when CHOWN_FAIL is set, and setpriv and the entrypoint only log
# how they were called.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

init=images/podman-nested/bin/podman-nested-init
cg="${__scratch}/cgroup"
home="${__scratch}/home"
conf="${home}/.config/containers/containers.conf.d/50-cgroups.conf"
export PODMAN_NESTED_CGROUP_ROOT="${cg}" PODMAN_NESTED_HOME="${home}"
# shellcheck disable=SC2016 # expanded when the stubs run
stub id 'echo "${ID_U:-0}"'
# shellcheck disable=SC2016
stub chown '[ -z "${CHOWN_FAIL:-}" ]'
stub setpriv
stub podman-nested-entrypoint

# tree [CONTROLLERS]: a fresh cgroup root holding two processes, with
# CONTROLLERS available (default: all four, and one this leaves alone).
tree() {
  rm -rf "${cg}"
  mkdir -p "${cg}"
  echo "${1:-cpu io memory pids misc}" >"${cg}/cgroup.controllers"
  printf '%s\n' 1 7 >"${cg}/cgroup.procs"
  : >"${cg}/cgroup.subtree_control"
}

# off: the drop in is gone.
off() { [[ ! -e "${conf}" ]]; }

tree
run "${init}" job with args
check "carves the tree and says so" 0 err "podman-nested: nested cgroups on (cpu io memory pids)"
assert "says it on one line" test "$(wc -l <"${__scratch}/err")" -eq 1
assert "moves the root's processes into init" test "$(cat "${cg}/init/cgroup.procs")" = 7
assert "enables the four controllers it uses" test "$(cat "${cg}/cgroup.subtree_control")" = "+cpu +io +memory +pids"
check "gives the podman account its subtree" 0 calls "chown -R podman:podman ${cg}/user"
assert "with the same controllers" test "$(cat "${cg}/user/cgroup.subtree_control")" = "+cpu +io +memory +pids"
assert "and moves itself into user/session" test -s "${cg}/user/session/cgroup.procs"

assert "turns the nested engine's cgroups on" grep -q '^cgroups = "enabled"' "${conf}"
assert "in a namespace of their own" grep -q '^cgroupns = "private"' "${conf}"
assert "under cgroupfs" grep -q '^cgroup_manager = "cgroupfs"' "${conf}"
check "owned by the podman account" 0 calls "chown podman:podman ${home}/.config/containers/containers.conf.d ${conf}"
check "then drops to podman with no capabilities for the entrypoint" 0 calls \
  "setpriv --reuid=1000 --regid=1000 --init-groups --inh-caps=-all env HOME=${home} USER=podman LOGNAME=podman podman-nested-entrypoint job with args"

tree "cpu memory"
run "${init}" job
check "enables only the controllers available" 0 err "nested cgroups on (cpu memory)"
assert "at the root" test "$(cat "${cg}/cgroup.subtree_control")" = "+cpu +memory"

# A root already empty (nothing left to move) still carves.
tree
: >"${cg}/cgroup.procs"
run "${init}" job
check "an empty root is carved as well" 0 err "podman-nested: nested cgroups on (cpu io memory pids)"
assert "with nothing moved into init" test ! -s "${cg}/init/cgroup.procs"

# A process that cannot be moved (it exited) is passed over.
tree
mkdir -p "${cg}/init/cgroup.procs"
run "${init}" job
check "passes over a process it cannot move" 0 err "nested cgroups on"

# off_case NAME: the last run left cgroups off, said so once, and still ran
# the entrypoint as podman.
off_case() {
  check "${1}" 0 err "podman-nested: nested cgroups off (no delegated cgroup v2 tree), so no container stats or resource limits"
  assert "says it on one line" test "$(wc -l <"${__scratch}/err")" -eq 1
  assert "removes the drop in" off
  check "and still runs the entrypoint as podman" 0 calls "setpriv --reuid=1000 --regid=1000"
}

# Every case below starts with a drop in left over, as from a restart.
leftover() {
  mkdir -p "$(dirname "${conf}")"
  : >"${conf}"
}

tree
rm "${cg}/cgroup.controllers"
leftover
run "${init}" job
off_case "no cgroup v2 tree leaves cgroups off"

tree "cpu io pids"
leftover
run "${init}" job
off_case "no memory controller leaves cgroups off"
assert "and touches nothing" test ! -e "${cg}/init"

tree "memory pids"
leftover
run "${init}" job
off_case "no cpu controller leaves cgroups off"

tree
: >"${cg}/init"
leftover
run "${init}" job
off_case "a root it cannot make init in leaves cgroups off"

tree
rm "${cg}/cgroup.subtree_control"
mkdir "${cg}/cgroup.subtree_control"
leftover
run "${init}" job
off_case "controllers it cannot enable leave cgroups off"

tree
: >"${cg}/user"
leftover
run "${init}" job
off_case "a user cgroup it cannot make leaves cgroups off"

tree
leftover
CHOWN_FAIL=1 run "${init}" job
off_case "a user cgroup it cannot hand over leaves cgroups off"

tree
mkdir -p "${cg}/user/cgroup.subtree_control"
leftover
run "${init}" job
off_case "controllers the user cgroup cannot enable leave cgroups off"

tree
mkdir -p "${cg}/user/session/cgroup.procs"
leftover
run "${init}" job
off_case "a session it cannot join leaves cgroups off"

tree
leftover
ID_U=1000 run "${init}" job with args
check "started as another user, it leaves cgroups off" 0 err \
  "podman-nested: nested cgroups off (not started as root), so no container stats or resource limits"
assert "says it on one line" test "$(wc -l <"${__scratch}/err")" -eq 1
assert "removes the drop in" off
assert "touches no cgroup" test ! -e "${cg}/init"
check "and runs the entrypoint as it is" 0 calls "podman-nested-entrypoint job with args"
refute "without dropping anything" "setpriv"

finish
