#!/usr/bin/env bash
#
# host/workbench up: a first start on an empty host (the shared services,
# the workspace's network, mirror, engine and workbenches), a second one that
# finds them running, and the settings that change what it starts.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ws="${WORKBENCH_ROOT}/app"
clone "${ws}"
mkdir -p "${HOME}/.ssh/devcontainer"
touch "${HOME}/.ssh/devcontainer/id_ed25519" "${PODMAN_STATE}/secret/gh-devcontainer"
image localhost/airlock-l2:local sha256:l2
export WORKBENCH_GH_OWNERS=acme GIT_USER_NAME="A Dev" GIT_USER_EMAIL=dev@example.com
run_dir="${XDG_RUNTIME_DIR}/workbench"
cd "${ws}"

run "${wb}" up
check "up starts everything for the clone you are in" 0 out "workbench: up for ${ws} (L2 engine l2-engine-app)"
check "and says how to reach each workbench" 0 out \
  "workbench: claude: host/workbench claude, or attach VS Code to workbench-claude-app"
check "codex too" 0 out "workbench: codex: host/workbench codex, or attach VS Code to workbench-codex-app"
check "the state is locked meanwhile" 0 calls "flock -w 120 9"

check "the broker holds the token for the owners named" 0 calls \
  "podman run -d --name gh-broker --userns=keep-id:uid=1000,gid=1000 --security-opt label=type:container_engine_t --security-opt label=level:s0:c555,c666 --cap-drop=all --security-opt no-new-privileges --read-only --tmpfs /tmp --secret gh-devcontainer,type=env,target=GH_TOKEN -e GH_BROKER_OWNERS=acme -v ${run_dir}/gh:/run/gh-broker:Z -v ${WORKBENCH_ROOT}:${WORKBENCH_ROOT}:ro localhost/airlock-gh-broker:local"
check "GitHub's host keys are fetched in a container" 0 calls \
  "podman run --rm --network podman localhost/airlock-base:local sh -c curl -fsS https://api.github.com/meta"
assert "into known_hosts" grep -q "github.com ssh-ed25519 KEY" "${XDG_RUNTIME_DIR}/devcontainer-ssh/known_hosts"
check "the ssh-agent holds the key, with no network" 0 calls \
  "podman run -d --name devcontainer-ssh-agent --network=none --cap-drop=all --read-only --security-opt no-new-privileges --userns=keep-id --security-opt label=type:container_engine_t --security-opt label=level:s0:c555,c666 -v ${XDG_RUNTIME_DIR}/devcontainer-ssh:/sock:Z -v ${HOME}/.ssh/devcontainer/id_ed25519:/key/id:ro,Z --entrypoint ssh-agent localhost/airlock-workbench-claude:local -D -a /sock/agent.sock"
check "the proxy starts with no workspace yet" 0 calls \
  "podman run -d --name egress-proxy --network podman --cap-drop=all --security-opt no-new-privileges --read-only --dns 127.0.0.1 --sysctl net.ipv4.ip_unprivileged_port_start=53 -e EGRESS_DNS=doh --tmpfs /run/egress:U --tmpfs /run/squid:U --tmpfs /tmp -v egress-cache:/var/lib/egress:U -v ${run_dir}/egress:/etc/egress/workspaces:ro,Z localhost/airlock-egress-proxy:local"
check "its cache volume is made" 0 calls "podman volume create egress-cache"
check "the sets are checked against the proxy image" 0 calls \
  "podman run --rm --network none localhost/airlock-egress-proxy:local egress-refresh --list"
check "the workspace gets the first free subnet" 0 calls \
  "podman network create --internal --disable-dns --subnet 10.203.1.0/24 workbench-net-app"
check "a proxy of its own from before is removed" 0 calls "podman rm -f -t 5 egress-proxy-app"
assert "the workspace is registered with the default sets" grep -qx "sets=python,node" "${run_dir}/egress/app.conf"
assert "for its path" grep -qx "workspace=${ws}" "${run_dir}/egress/app.conf"
assert "and its subnet" grep -qx "subnet=10.203.1.0/24" "${run_dir}/egress/app.conf"
assert "readable by the proxy's own account" test "$(stat -c %a "${run_dir}/egress/app.conf")" = 644
check "the proxy joins the network at .2" 0 calls "podman network connect --ip 10.203.1.2 workbench-net-app egress-proxy"
check "and reloads" 0 calls "podman exec egress-proxy egress-reload"

check "the mirror gets a network of its own" 0 calls \
  "podman network create --internal --disable-dns --subnet 10.203.2.0/24 workbench-net-airlock-mirror"
assert "its backend reaches only its registries through the proxy" grep -qx \
  "sets=python,node,golang,docker-hub,ghcr,ubuntu,debian,alpine,fedora" "${run_dir}/egress/airlock-mirror.conf"
check "the backend image is pulled by digest" 0 calls "podman pull --quiet docker.io/sonatype/nexus3:3.96.3@sha256:"
check "the backend runs at .10, with the default heap" 0 calls \
  "podman run -d --name mirror-nexus --network workbench-net-airlock-mirror:ip=10.203.2.10 --cap-drop=all --security-opt no-new-privileges -v mirror-nexus-data:/nexus-data -e INSTALL4J_ADD_VM_PARAMS=-Xms1024m -Xmx1024m -XX:MaxDirectMemorySize=1024m"
check "and is provisioned" 0 err "workbench: provisioning the package mirror (the first start takes about a minute)"
check "through the proxy" 0 calls \
  "podman run --rm --name mirror-provision --network workbench-net-airlock-mirror --add-host mirror-nexus:10.203.2.10 --cap-drop=all --security-opt no-new-privileges -v mirror-nexus-data:/nexus-data:ro -v mirror-admin:/var/lib/mirror-admin:U -e NEXUS_URL=http://mirror-nexus:8081 -e UPSTREAM_PROXY=10.203.2.2:8888 localhost/airlock-mirror-gate:local"
check "the gate runs at .254" 0 calls \
  "podman run -d --name mirror-gate --network workbench-net-airlock-mirror:ip=10.203.2.254 --add-host mirror-nexus:10.203.2.10 --cap-drop=all --security-opt no-new-privileges --read-only --tmpfs /tmp -v mirror-osv:/var/lib/mirror-gate/osv:U -e HTTPS_PROXY=http://10.203.2.2:8888 -e https_proxy=http://10.203.2.2:8888 -e MIN_AGE_DAYS=0 localhost/airlock-mirror-gate:local"
check "and joins the workspace at .254" 0 calls "podman network connect --ip 10.203.1.254 workbench-net-app mirror-gate"
assert "the workspace is the mirror's" grep -qx "${ws}" "${run_dir}/mirror/app.ws"
assert "its engine pulls through the gate" grep -qx 'location = "10.203.1.254:5000"' "${run_dir}/mirror/app.registries.conf"

check "the L2 image goes into the shared store" 0 out "workbench: copying localhost/airlock-l2:local into the shared L2 store"
check "laid out before any engine mounts it" 0 calls \
  "podman run --rm -i --network none --userns=keep-id:uid=1000,gid=1000 --security-opt label=type:container_engine_t --security-opt label=level:s0:c555,c666 --device /dev/fuse -v l2-store:/home/dev/.local/share/containers --entrypoint podman localhost/airlock-l2-engine:local images"
assert "it is there" grep -qx sha256:l2 "${PODMAN_STATE}/store/localhost_airlock-l2_local"
check "the engine is on the workspace network, through the gate for plain http" 0 calls \
  "podman run -d --name l2-engine-app --label workbench.engine=${ws} --userns=keep-id:uid=1000,gid=1000 --security-opt label=type:container_engine_t --security-opt label=level:s0:c555,c666 --device /dev/fuse -v ${run_dir}/mirror/app.registries.conf:/etc/containers/registries.conf.d/50-airlock-mirror.conf:ro,Z --network workbench-net-app -e HTTPS_PROXY=http://10.203.1.2:8888 -e HTTP_PROXY=http://10.203.1.254:8081 -e NO_PROXY=localhost,127.0.0.1,10.203.1.254 -e https_proxy=http://10.203.1.2:8888 -e http_proxy=http://10.203.1.254:8081 -e no_proxy=localhost,127.0.0.1,10.203.1.254 -e AIRLOCK_MIRROR=http://10.203.1.254:8081 -v ${ws}:${ws}:Z -v ${run_dir}/app:/run/l2-engine:Z -v l2-engine-app:/home/dev/.local/share/containers -v l2-store:/var/lib/airlock/l2-store:ro localhost/airlock-l2-engine:local"
refute "an engine sharing the store gets no copy of its own" "podman exec -i l2-engine-app podman load"
check "each workbench is on the workspace network, plain http through the proxy" 0 calls \
  "podman run -d --name workbench-claude-app --label workbench.workspace=${ws} --label workbench.agent=claude --label workbench.session= --tz=local --userns=keep-id:uid=1000,gid=1000 --security-opt label=type:container_engine_t --security-opt label=level:s0:c555,c666 --network workbench-net-app -e HTTPS_PROXY=http://10.203.1.2:8888 -e HTTP_PROXY=http://10.203.1.2:8888 -e NO_PROXY=localhost,127.0.0.1,10.203.1.254"
check "with your git identity" 0 calls \
  "-e GIT_AUTHOR_NAME=A Dev -e GIT_AUTHOR_EMAIL=dev@example.com -e GIT_COMMITTER_NAME=A Dev -e GIT_COMMITTER_EMAIL=dev@example.com -e SSH_AUTH_SOCK=/run/devcontainer-ssh/agent.sock"
check "git over ssh through the proxy" 0 calls \
  "ProxyCommand='socat - PROXY:10.203.1.2:%h:%p,proxyport=8888'"
check "the claude login folder and the sockets" 0 calls \
  "-v ${ws}:${ws}:Z -v ${HOME}/.local/share/workbench/claude:/home/dev/.claude:Z -e AIRLOCK_SESSION= -e AIRLOCK_WORKBENCH=workbench-claude-app -e AIRLOCK_HOST_HOME=${HOME} -e AIRLOCK_WORKSPACE=${ws} --hostname workbench-claude-app -v ${run_dir}/gh:/run/gh-broker -v ${XDG_RUNTIME_DIR}/devcontainer-ssh:/run/devcontainer-ssh -v ${run_dir}/app:/run/l2-engine --label workbench.voice=ready -v ${run_dir}/voice/workbench-claude-app:/run/workbench-voice:ro,Z -w ${ws} --entrypoint catatonit localhost/airlock-workbench-claude:local -- workbench-init"
check "codex has its own login folder and no voice folder" 0 calls \
  "-v ${HOME}/.local/share/workbench/codex:/home/dev/.codex:Z -e AIRLOCK_SESSION= -e AIRLOCK_WORKBENCH=workbench-codex-app -e AIRLOCK_HOST_HOME=${HOME} -e AIRLOCK_WORKSPACE=${ws} --hostname workbench-codex-app -v ${run_dir}/gh:/run/gh-broker -v ${XDG_RUNTIME_DIR}/devcontainer-ssh:/run/devcontainer-ssh -v ${run_dir}/app:/run/l2-engine -w ${ws} --entrypoint catatonit localhost/airlock-workbench-codex:local -- workbench-init"

# Everything is running now: a second up starts nothing.
run "${wb}" up
check "a second up finds everything running" 0 out "workbench: up for ${ws}"
refute "and starts nothing" "podman run -d"
refute "nor connects anything again" "podman network connect"
check "but registers the workspace again" 0 calls "podman exec egress-proxy egress-reload"

# The host image changed since: it reaches the shared store, and an engine
# that kept a copy of its own from before drops it.
image localhost/airlock-l2:local sha256:l2new
printf '%s\n' 'false sha256:old localhost/airlock-l2:local' 'true sha256:l2 localhost/airlock-l2:local' \
  'false sha256:other localhost/other:1' >"${PODMAN_STATE}/ctr/l2-engine-app/own-images"
run "${wb}" up
check "a changed L2 image is copied again" 0 out "workbench: copying localhost/airlock-l2:local into the shared L2 store"
check "and the engine's own copy is untagged" 0 calls "podman exec l2-engine-app podman untag sha256:old localhost/airlock-l2:local"
refute "only that one" "untag sha256:other"
rm "${PODMAN_STATE}/ctr/l2-engine-app/own-images"

# An engine from before the shared store gets its own copy instead.
: >"${PODMAN_STATE}/ctr/l2-engine-app/mounts"
image localhost/extra:1 sha256:extra
L2_EXTRA_IMAGES="localhost/extra:1 localhost/missing:1" run "${wb}" up
check "an older engine gets the image" 0 out "workbench: copying localhost/airlock-l2:local into the L2 engine"
check "loaded into its own store" 0 calls "podman exec -i l2-engine-app podman load --quiet"
check "and the extra images too" 0 out "workbench: copying localhost/extra:1 into the L2 engine"
assert "they are in its store" grep -qx sha256:extra "${PODMAN_STATE}/ctr/l2-engine-app/images/localhost_extra_1"
refute "an extra image the host lacks is skipped" "podman save localhost/missing:1"
L2_EXTRA_IMAGES="localhost/extra:1" run "${wb}" up
refute "an older engine already holding them is left alone" "podman load"

finish
