#!/usr/bin/env bash
#
# host/workbench up when something is in the way or fails: a folder of the
# same name elsewhere, a proxy that will not take the workspace, a package
# mirror or an engine that does not come up.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

touch "${PODMAN_STATE}/secret/gh-devcontainer"
export WORKBENCH_GH_OWNERS=acme WORKBENCH_AGENTS=claude
app="${WORKBENCH_ROOT}/app"
twin="${WORKBENCH_ROOT}/team/app"
clone "${app}"
clone "${twin}"
egress="${XDG_RUNTIME_DIR}/workbench/egress"
cd "${app}"

run "${wb}" up
check "a first workspace" 0 out "workbench: up for ${app}"

# Another folder of the same name maps to the same containers.
run "${wb}" up "${twin}"
check "a folder of the same name is not registered with the proxy" 1 err \
  "workbench: the egress proxy already serves ${app} as app, another folder of the same name; stop it first (host/workbench down ${app})"
assert "the registration is left as it was" grep -qx "workspace=${app}" "${egress}/app.conf"
refute "nothing of it starts" "podman run -d"
rm "${egress}/app.conf"
run "${wb}" up "${twin}"
check "nor is the other folder's engine reused" 1 err \
  "workbench: l2-engine-app belongs to ${app}, another folder of the same name; stop it first (host/workbench down ${app})"
gone l2-engine-app
run "${wb}" up "${twin}"
check "nor its workbench" 1 err "workbench: workbench-claude-app belongs to ${app}, another folder of the same name"
run "${wb}" down "${twin}"
run "${wb}" up
check "the first one again" 0 out "workbench: up for ${app}"

# The proxy cannot take the workspace: its earlier rules come back.
mkdir -p "${app}/.devcontainer"
echo golang >"${app}/.devcontainer/egress-sets"
rule 'exec egress-proxy egress-reload' 'exit 1'
echo "squid: bad rules" >"${PODMAN_STATE}/ctr/egress-proxy/logs"
run "${wb}" up
check "a proxy that cannot reload" 1 err \
  "workbench: the egress proxy could not take ${app} (sets: golang); its previous rules, if any, are back, and the other workspaces keep theirs"
check "shows its last log lines" 1 err "squid: bad rules"
check "after a reload with the old rules" 1 calls "podman logs --tail 5 egress-proxy"
assert "the previous registration is back" grep -qx "sets=python,node" "${egress}/app.conf"
assert "and no copy is left" test ! -e "${egress}/app.conf.prev"
unrule
rm "${egress}/app.conf"
rule 'network connect --ip * egress-proxy' 'exit 1'
gone egress-proxy
container egress-proxy running
run "${wb}" up
check "a proxy that cannot join the network" 1 err "workbench: the egress proxy could not take ${app} (sets: golang)"
assert "a new registration is dropped" test ! -e "${egress}/app.conf"
unrule
# shellcheck disable=SC2016 # run by the podman stub
rule 'rm -f -t 5 egress-proxy-app' 'rm -r "${PODMAN_STATE}/ctr/egress-proxy"; touch "${PODMAN_STATE}/dies/egress-proxy"'
run "${wb}" up
check "a proxy that does not start" 1 err "workbench: the egress proxy could not take ${app} (sets: golang)"
check "says so first" 1 err "workbench: the egress proxy did not start; podman logs egress-proxy"
refute "and is not reloaded" "podman exec egress-proxy egress-reload"
unrule
rm "${PODMAN_STATE}/dies/egress-proxy" "${app}/.devcontainer/egress-sets"
gone egress-proxy

# The package mirror.
run "${wb}" up
check "back up" 0 out "workbench: up for ${app}"
gone mirror-gate
mkdir -p "${PODMAN_STATE}/start-logs"
touch "${XDG_RUNTIME_DIR}/workbench/mirror/gone.ws"
run "${wb}" up
check "a gate that stopped is provisioned and started again" 0 err "workbench: provisioning the package mirror"
refute "its backend kept" "podman run -d --name mirror-nexus"
check "on every registered workspace's network" 0 calls \
  "podman run -d --name mirror-gate --network workbench-net-airlock-mirror:ip=10.203.2.254 --network workbench-net-app:ip=10.203.1.254 --add-host"
assert "a workspace whose network is gone is dropped" test ! -e "${XDG_RUNTIME_DIR}/workbench/mirror/gone.ws"
refute "it is on the network already" "podman network connect --ip 10.203.1.254"

gone mirror-gate
echo "gate: starting" >"${PODMAN_STATE}/start-logs/mirror-gate"
run "${wb}" up
check "a gate that never listens" 1 err "workbench: the package mirror's gate did not start; podman logs mirror-gate"
check "shows its last log lines" 1 err "gate: starting"
assert "after fifty tries" test "$(grep -c 'podman logs mirror-gate' "${STUB_LOG}")" -eq 51
gone mirror-gate
touch "${PODMAN_STATE}/dies/mirror-gate"
run "${wb}" up
check "nor one that stops" 1 err "workbench: the package mirror's gate did not start"
rm "${PODMAN_STATE}/dies/mirror-gate" "${PODMAN_STATE}/start-logs/mirror-gate"

gone mirror-gate mirror-nexus
touch "${PODMAN_STATE}/img/docker.io_sonatype_nexus3_3.96.3_sha256_a406f4e9dc149e050723a93bf57964311f6d1c88e1dcbed2e42ea373319a1772"
rule '*mirror-provision*' 'exit 1'
run "${wb}" up
check "a mirror that cannot be provisioned" 1 err \
  "workbench: the package mirror could not be provisioned; podman logs mirror-nexus"
refute "a backend image there is not pulled again" "podman pull"
unrule

# The engine.
run "${wb}" up
gone l2-engine-app
rm "${XDG_RUNTIME_DIR}/workbench/app/podman.sock"
NO_ENGINE_SOCKET=1 run "${wb}" up
check "an engine that never opens its socket" 1 err \
  "workbench: the L2 engine did not open its socket; podman logs l2-engine-app"
refute "no workbench starts without it" "podman run -d --name workbench-claude-app"

finish
