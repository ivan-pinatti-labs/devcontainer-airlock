#!/usr/bin/env bash
#
# host/workbench down, and load-l2: stopping a workspace (and the shared
# services with the last one), and copying the L2 images to a running one.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

touch "${PODMAN_STATE}/secret/gh-devcontainer"
export WORKBENCH_GH_OWNERS=acme
app="${WORKBENCH_ROOT}/app"
other="${WORKBENCH_ROOT}/other"
clone "${app}"
clone "${other}"
egress="${XDG_RUNTIME_DIR}/workbench/egress"
mirror="${XDG_RUNTIME_DIR}/workbench/mirror"
image localhost/airlock-l2:local sha256:l2

run "${wb}" up "${app}"
run "${wb}" up "${other}"
check "two workspaces are up" 0 out "workbench: up for ${other}"
container workbench-app running
container egress-proxy-app running

cd "${app}"
run "${wb}" down
check "down stops the workspace you are in" 0 out "workbench: down"
check "the state is locked meanwhile" 0 calls "flock -w 120 9"
check "every workbench it labelled" 0 calls "podman ps -a --filter label=workbench.workspace=${app} --format {{.Names}}"
check "removed together" 0 calls "podman rm -f -t 5 workbench-claude-app workbench-codex-app"
check "with its engine, and the workbench and proxy from before the split" 0 calls \
  "podman rm -f -t 5 workbench-app l2-engine-app egress-proxy-app"
check "it leaves the mirror" 0 calls "podman network disconnect -f workbench-net-app mirror-gate"
check "and the proxy, after a reload without it" 0 calls "podman network disconnect -f workbench-net-app egress-proxy"
check "then its network goes" 0 calls "podman network rm workbench-net-app"
assert "no longer registered with the proxy" test ! -e "${egress}/app.conf"
assert "nor the mirror" test ! -e "${mirror}/app.ws"
assert "nor its registry settings" test ! -e "${mirror}/app.registries.conf"
assert "the other workspace still is" test -e "${egress}/other.conf"
refute "another workspace left, the shared services stay" "mirror-nexus"

rule 'ps -a --format {{.Names}}' 'exit 125'
run "${wb}" down "${other}"
check "a failed listing stops nothing on a guess" 1 err \
  "workbench: could not list the containers left, so the shared services keep running"
refute "nothing shared is removed" "podman rm -f -t 5 mirror-gate"
unrule

run "${wb}" down "${other}"
check "down from anywhere, of the workspace named" 0 out \
  "workbench: nothing else running, so the shared services stopped too; the next start brings them back"
check "the shared services stop with the last one" 0 calls \
  "podman rm -f -t 5 mirror-gate mirror-nexus egress-proxy gh-broker devcontainer-ssh-agent"
check "and the mirror's network" 0 calls "podman network rm workbench-net-airlock-mirror"
assert "and the mirror's registration" test ! -e "${egress}/airlock-mirror.conf"

run "${wb}" down "${other}"
check "down again finds nothing to stop" 0 out "workbench: nothing else running"
refute "the proxy is not running, so nothing to reload" "podman exec"

# Another host/workbench holds the state lock.
FLOCK_FAILS=1 run "${wb}" down "${app}"
check "a lock held too long stops it" 1 err \
  "workbench: another host/workbench has held ${XDG_RUNTIME_DIR}/workbench/state.lock for two minutes (fuser -v ${XDG_RUNTIME_DIR}/workbench/state.lock shows which)"
refute "before anything is stopped" "podman rm"

# load-l2
run "${wb}" load-l2 "${app}"
check "load-l2 needs the engine" 1 err "workbench: no L2 engine running for ${app}; host/workbench up first"
run "${wb}" up "${app}"
image localhost/airlock-l2:local sha256:l2new
run "${wb}" load-l2 "${app}"
check "load-l2 copies a changed image into the shared store" 0 out \
  "workbench: copying localhost/airlock-l2:local into the shared L2 store"
check "through the engine image, on the store's volume" 0 calls \
  "-v l2-store:/home/dev/.local/share/containers --entrypoint podman localhost/airlock-l2-engine:local load --quiet"
run "${wb}" load-l2 "${app}"
refute "and nothing when it is there" "podman save"

finish
