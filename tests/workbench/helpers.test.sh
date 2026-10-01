#!/usr/bin/env bash
#
# host/workbench helpers, helpers-down, restart-proxy, restart-broker,
# unlock and install-units: the shared services every workspace uses.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

egress="${XDG_RUNTIME_DIR}/workbench/egress"
key="${HOME}/.ssh/devcontainer/id_ed25519"

run "${wb}" helpers
check "the broker needs the token's secret" 1 err \
  "workbench: no podman secret gh-devcontainer; create it: podman secret create gh-devcontainer <file holding the token>"
touch "${PODMAN_STATE}/secret/gh-token"
WORKBENCH_GH_SECRET=gh-token run "${wb}" helpers
check "and the owners it may act on" 1 err \
  "workbench: set WORKBENCH_GH_OWNERS in ${HOME}/.config/workbench/config to the GitHub owners"
export WORKBENCH_GH_SECRET=gh-token WORKBENCH_GH_OWNERS=acme,other

run "${wb}" helpers
check "helpers start the broker, the ssh-agent and the proxy" 0 out \
  "workbench: helpers up (gh-broker, devcontainer-ssh-agent, egress-proxy)"
check "the broker with that secret and those owners" 0 calls \
  "--secret gh-token,type=env,target=GH_TOKEN -e GH_BROKER_OWNERS=acme,other"
check "no key, no ssh-agent" 0 err \
  "workbench: no ${key}, so no ssh-agent; git push will not work (docs/LAYERS.md)"
refute "none is started" "--name devcontainer-ssh-agent"
assert "the broker's folder is private" test "$(stat -c %a "${XDG_RUNTIME_DIR}/workbench/gh")" = 700
assert "the proxy's is readable by its account" test "$(stat -c %a "${egress}")" = 755

mkdir -p "$(dirname "${key}")" "${XDG_RUNTIME_DIR}/devcontainer-ssh"
touch "${key}"
echo "github.com ssh-ed25519 OLD" >"${XDG_RUNTIME_DIR}/devcontainer-ssh/known_hosts"
gone gh-broker egress-proxy
run "${wb}" helpers
check "known hosts already fetched are kept" 0 calls "podman run -d --name devcontainer-ssh-agent"
refute "not fetched again" "api.github.com"
run "${wb}" helpers
refute "helpers already running are left alone" "podman run"

# The proxy starts with the workspaces registered, on their networks.
printf '10.203.4.0/24' >"${PODMAN_STATE}/net/workbench-net-app"
touch "${egress}/app.conf" "${egress}/gone.conf"
touch "${PODMAN_STATE}/vol/egress-cache"
run "${wb}" restart-proxy
check "restart-proxy puts every workspace back" 0 out "workbench: egress proxy restarted, serving: app"
check "removing the old proxy first" 0 calls "podman rm -f -t 5 egress-proxy"
check "the proxy is on each registered network at .2" 0 calls \
  "podman run -d --name egress-proxy --network podman --network workbench-net-app:ip=10.203.4.2 --cap-drop=all"
assert "a registration whose network is gone is dropped" test ! -e "${egress}/gone.conf"
refute "the cache volume is kept" "podman volume create"

rm "${egress}/app.conf"
echo "squid: starting" >"${PODMAN_STATE}/start-logs/egress-proxy"
touch "${PODMAN_STATE}/ctr/egress-proxy/x" "${PODMAN_STATE}/dies/egress-proxy"
run "${wb}" restart-proxy
check "a proxy that stops at once is reported" 1 err "workbench: the egress proxy did not start; podman logs egress-proxy"
check "with its last log lines" 1 err "squid: starting"
rm "${PODMAN_STATE}/dies/egress-proxy"
run "${wb}" restart-proxy
check "so is one that never accepts connections" 1 err "workbench: the egress proxy did not start"
assert "after trying a hundred times" test "$(grep -c 'podman logs egress-proxy' "${STUB_LOG}")" -eq 101
rm "${PODMAN_STATE}/start-logs/egress-proxy"
run "${wb}" restart-proxy
check "with no workspace, it serves nothing yet" 0 out "workbench: egress proxy restarted, serving: nothing yet"

run "${wb}" restart-broker
check "restart-broker starts the broker again" 0 out "workbench: gh broker restarted"
check "after removing it" 0 calls "podman rm -f -t 5 gh-broker"
check "with the token" 0 calls "podman run -d --name gh-broker"

run "${wb}" helpers-down
check "helpers-down stops all three" 0 calls "podman rm -f -t 5 gh-broker devcontainer-ssh-agent egress-proxy"
assert "and they are gone" test ! -e "${PODMAN_STATE}/ctr/gh-broker"

# unlock: the key goes into the running agent, for 8 hours.
run "${wb}" unlock
check "unlock needs the ssh-agent" 1 err "workbench: the ssh-agent is not running; host/workbench up first"
container devcontainer-ssh-agent running
run "${wb}" unlock
check "unlock adds the key for 8 hours" 0 calls \
  "podman exec -it -e SSH_AUTH_SOCK=/sock/agent.sock devcontainer-ssh-agent ssh-add -t 8h /key/id"
refute "an agent holding the key is kept" "podman rm"
rule 'exec devcontainer-ssh-agent test -f /key/id' 'exit 1'
run "${wb}" unlock
check "an agent from before, with the key's folder, is started again" 0 calls \
  "podman rm -f -t 3 devcontainer-ssh-agent"
check "with the key itself" 0 calls "-v ${key}:/key/id:ro,Z --entrypoint ssh-agent"
check "then takes the key" 0 calls "ssh-add -t 8h /key/id"
unrule

# install-units: a user unit for the helpers at login.
unit="${HOME}/.config/systemd/user/workbench-helpers.service"
run "${wb}" install-units
check "install-units writes the unit" 0 out "workbench: installed ${unit}; it starts the helpers at your next login"
check "and says how to remove it" 0 out "workbench: remove with: systemctl --user disable workbench-helpers.service && rm ${unit}"
check "enabled for your user" 0 calls "systemctl --user enable workbench-helpers.service"
assert "it starts the helpers" grep -qx "ExecStart=${__repo}/host/workbench helpers" "${unit}"
assert "and stops them" grep -qx "ExecStop=${__repo}/host/workbench helpers-down" "${unit}"
assert "under WORKBENCH_ROOT" grep -qx "Environment=WORKBENCH_ROOT=${WORKBENCH_ROOT}" "${unit}"
XDG_CONFIG_HOME="${__scratch}/cfg" run "${wb}" install-units
assert "under XDG_CONFIG_HOME when set" test -f "${__scratch}/cfg/systemd/user/workbench-helpers.service"
SYSTEMCTL_FAILS=1 run "${wb}" install-units
check "a systemctl that fails stops it" 1 calls "systemctl --user daemon-reload"
refute "before enabling anything" "systemctl --user enable"

finish
