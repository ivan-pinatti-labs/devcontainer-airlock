#!/usr/bin/env bash
#
# The shared services after a `build` or a `pull`, and when a workspace or a
# session starts: one running an older image than its local tag is restarted
# when no workbench or L2 engine runs, and otherwise named, with the command
# that restarts it, and left running.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

mkdir -p "${HOME}/.ssh/devcontainer" "${XDG_RUNTIME_DIR}/devcontainer-ssh"
touch "${HOME}/.ssh/devcontainer/id_ed25519"
echo "github.com ssh-ed25519 KEY" >"${XDG_RUNTIME_DIR}/devcontainer-ssh/known_hosts"
export WORKBENCH_GH_OWNERS=acme
app="${WORKBENCH_ROOT}/app"
clone "${app}"
cd "${app}"

# Each shared service, and the local image it runs.
declare -A img=(
  ["egress-proxy"]=localhost/airlock-egress-proxy:local
  ["gh-broker"]=localhost/airlock-gh-broker:local
  ["devcontainer-ssh-agent"]=localhost/airlock-workbench-claude:local
  ["mirror-gate"]=localhost/airlock-mirror-gate:local
)
# And the command that restarts it, as the warning names it.
declare -A how=(
  ["egress-proxy"]="host/workbench restart-proxy (every session is cut off for a few seconds)"
  ["gh-broker"]="host/workbench restart-broker (a gh command running then fails)"
  ["devcontainer-ssh-agent"]="host/workbench restart-ssh-agent, then unlock again (it forgets the key)"
  ["mirror-gate"]="host/workbench restart-mirror (an install running then fails)"
)
services="egress-proxy gh-broker devcontainer-ssh-agent mirror-gate"

# fresh: an empty host but for the token's secret.
fresh() {
  reset_podman
  touch "${PODMAN_STATE}/secret/gh-devcontainer"
}
# running_on ID: every shared service running, the mirror's backend too,
# each started from the image ID.
running_on() {
  local s
  for s in ${services} mirror-nexus; do
    container "${s}" running
    printf '%s' "${1}" >"${PODMAN_STATE}/ctr/${s}/image"
  done
}
# ran NAME: the image ID container NAME runs.
ran() { cat "${PODMAN_STATE}/ctr/${1}/image"; }

fresh
run "${wb}" pull
check "a pull with no shared service running" 0 out "workbench: podman-nested"
refute "restarts nothing" "podman rm -f -t 5"
assert "and says nothing of them" test ! -s "${__scratch}/err"

# Started before the pull, and no session: restarted on the new images.
running_on sha256:old
run "${wb}" pull
for s in ${services}; do
  check "a pull restarts ${s}, left on an older image" 0 err "workbench: restarted ${s} on the new ${img[${s}]}"
  check "removing the old ${s} first" 0 calls "podman rm -f -t 5 ${s}"
  check "and starting it again" 0 calls "podman run -d --name ${s}"
  assert "${s} runs the pulled image now" test "$(ran "${s}")" = sha256:pulled
done
check "under the lock" 0 calls "flock -w 120 9"
check "saying the ssh-agent forgot the key" 0 err \
  "workbench: the ssh-agent holds no key now; host/workbench unlock (make unlock) before a git push"
assert "right after saying it restarted" test "$(grep -A1 'restarted devcontainer-ssh-agent' "${__scratch}/err" | tail -n 1)" = \
  "workbench: the ssh-agent holds no key now; host/workbench unlock (make unlock) before a git push"
check "the gate on the networks registered with it, after provisioning" 0 err "workbench: provisioning the package mirror"
refute "the mirror's backend, pinned by digest, is kept" "podman run -d --name mirror-nexus"
assert "the proxy before the gate, which goes out through it" \
  test "$(grep -n 'restarted' "${__scratch}/err" | head -n 1 | cut -d: -f3-)" = " restarted egress-proxy on the new localhost/airlock-egress-proxy:local"

run "${wb}" pull
refute "a second pull finds them current" "podman rm -f -t 5"

# A workbench (a session's, or one `up` started) uses them: nothing restarts.
fresh
running_on sha256:old
container workbench-claude-s-busy-bee running
run "${wb}" pull
for s in ${services}; do
  check "a pull under a session names ${s} and how to restart it" 0 err \
    "workbench: ${s} runs an older image than ${img[${s}]}, and is left running for the sessions using it; it takes the new one when the last workbench or L2 engine stops, or now with ${how[${s}]}"
done
refute "and restarts none" "podman rm -f -t 5"
assert "the proxy keeps its image" test "$(ran egress-proxy)" = sha256:old

gone workbench-claude-s-busy-bee
container l2-engine-app running
run "${wb}" pull
check "an L2 engine alone counts too" 0 err "workbench: egress-proxy runs an older image than localhost/airlock-egress-proxy:local, and is left running"
refute "it restarts none" "podman rm -f -t 5"

rm "${PODMAN_STATE}/ctr/l2-engine-app/running"
run "${wb}" pull
check "a stopped one does not" 0 err "workbench: restarted egress-proxy on the new localhost/airlock-egress-proxy:local"

fresh
running_on sha256:old
rule 'ps --format *' 'exit 1'
run "${wb}" pull
check "a listing that fails counts as a session" 0 err "workbench: gh-broker runs an older image than localhost/airlock-gh-broker:local, and is left running"
refute "so nothing restarts on a guess" "podman rm -f -t 5"
unrule

WORKBENCH_MIRROR=0 run "${wb}" pull
check "with the mirror off, the others still restart" 0 err "workbench: restarted gh-broker on the new localhost/airlock-gh-broker:local"
refute "the gate is left alone" "podman rm -f -t 5 mirror-gate"

# Nothing to compare: a service started from no image the host has, or whose
# local image is gone.
fresh
image localhost/airlock-l2:local sha256:l2
container egress-proxy running
container gh-broker running
printf 'sha256:old' >"${PODMAN_STATE}/ctr/gh-broker/image"
rule 'inspect --format {{.Image}} egress-proxy' 'exit 1'
run "${wb}" up
check "up with nothing to compare" 0 out "workbench: up for ${app}"
refute "keeps a service whose image cannot be inspected" "podman run -d --name egress-proxy"
assert "without removing it" test "$(grep -cx 'podman rm -f -t 5 egress-proxy' "${STUB_LOG}")" -eq 0
refute "and one whose local image is gone" "podman run -d --name gh-broker"
refute "without removing that one either" "podman rm -f -t 5 gh-broker"
unrule

# A service that cannot be replaced is left as it is, and the others restart.
fresh
running_on sha256:old
rule 'rm -f -t 5 gh-broker' 'exit 1'
run "${wb}" pull
check "a removal that fails is said" 0 err "workbench: could not remove gh-broker to restart it; podman rm -f gh-broker"
refute "nothing is started in its place" "podman run -d --name gh-broker"
assert "nor reported restarted" test "$(grep -c 'restarted gh-broker' "${__scratch}/err")" -eq 0
check "the others restart" 0 err "workbench: restarted egress-proxy on the new localhost/airlock-egress-proxy:local"
unrule

fresh
running_on sha256:old
mv "${HOME}/.ssh/devcontainer/id_ed25519" "${__scratch}/key"
run "${wb}" pull
check "an ssh-agent with no key to start again with is said" 0 err \
  "workbench: no ${HOME}/.ssh/devcontainer/id_ed25519, so the ssh-agent is left as it is"
refute "and kept" "podman rm -f -t 5 devcontainer-ssh-agent"
run "${wb}" restart-ssh-agent
check "restart-ssh-agent with no key fails" 1 err \
  "workbench: no ${HOME}/.ssh/devcontainer/id_ed25519, so the ssh-agent is left as it is"
assert "keeping the agent" test -f "${PODMAN_STATE}/ctr/devcontainer-ssh-agent/running"
mv "${__scratch}/key" "${HOME}/.ssh/devcontainer/id_ed25519"

# A build replaces the images as a pull does.
fresh
running_on sha256:old
run "${wb}" build
check "a build restarts them too" 0 err "workbench: restarted gh-broker on the new localhost/airlock-gh-broker:local"
assert "on the image it built" test "$(ran gh-broker)" = sha256:built

# A session starting finds the proxy behind: with nothing else running it is
# restarted before anything joins it.
fresh
for s in ${services}; do image "${img[${s}]}" sha256:new; done
image localhost/airlock-l2:local sha256:l2
running_on sha256:new
printf 'sha256:old' >"${PODMAN_STATE}/ctr/egress-proxy/image"
run "${wb}" session start claude
check "a session restarts a proxy it alone would use" 0 err "workbench: restarted egress-proxy on the new localhost/airlock-egress-proxy:local"
refute "and only that one" "podman rm -f -t 5 gh-broker"
assert "before it registers" test "$(grep -n -m1 'podman rm -f -t 5 egress-proxy$' "${STUB_LOG}" | cut -d: -f1)" -lt \
  "$(grep -n -m1 'podman exec egress-proxy egress-reload' "${STUB_LOG}" | cut -d: -f1)"
check "and runs" 0 out "workbench: session "

# With another session running it is named, and the session carries on.
running_on sha256:new
printf 'sha256:old' >"${PODMAN_STATE}/ctr/egress-proxy/image"
container workbench-claude-s-busy-bee running
run "${wb}" session start claude
check "a session beside another warns" 0 err "workbench: egress-proxy runs an older image than localhost/airlock-egress-proxy:local, and is left running"
assert "restarts nothing" test "$(grep -cx 'podman rm -f -t 5 egress-proxy' "${STUB_LOG}")" -eq 0
check "and starts anyway" 0 out "workbench: session "

run "${wb}" up
check "up does the same" 0 err "workbench: egress-proxy runs an older image than localhost/airlock-egress-proxy:local, and is left running"
check "and comes up" 0 out "workbench: up for ${app}"

# The commands that restart one service by hand.
run "${wb}" restart-ssh-agent
check "restart-ssh-agent restarts it" 0 out "workbench: ssh-agent restarted"
check "removing it first" 0 calls "podman rm -f -t 5 devcontainer-ssh-agent"
check "with the key" 0 calls "-v ${HOME}/.ssh/devcontainer/id_ed25519:/key/id:ro,Z --entrypoint ssh-agent"
check "which it no longer holds unlocked" 0 err "workbench: the ssh-agent holds no key now"

run "${wb}" restart-mirror
check "restart-mirror restarts the gate" 0 out "workbench: package mirror gate restarted"
check "removing it first" 0 calls "podman rm -f -t 5 mirror-gate"
check "on the networks registered with it" 0 calls "podman run -d --name mirror-gate --network workbench-net-airlock-mirror:ip="
check "under the lock" 0 calls "flock -w 120 9"
WORKBENCH_MIRROR=0 run "${wb}" restart-mirror
check "not when the mirror is off" 1 err "workbench: the package mirror is off (WORKBENCH_MIRROR=0)"

finish
