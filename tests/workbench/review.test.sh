#!/usr/bin/env bash
#
# host/workbench review-login and restart-review-broker, and the review
# broker among the helpers: started once it has a login, on a network of its
# own that reaches the openai set and nothing else.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

export WORKBENCH_GH_SECRET=gh-token WORKBENCH_GH_OWNERS=acme
touch "${PODMAN_STATE}/secret/gh-token"
egress="${XDG_RUNTIME_DIR}/workbench/egress"
login="${HOME}/.local/share/workbench/review/codex"
sock_dir="${XDG_RUNTIME_DIR}/workbench-review"

# No login yet: the helpers start without it.
run "${wb}" helpers
check "the helpers start without a review broker" 0 out "workbench: helpers up"
refute "none is started" "--name review-broker"
assert "its socket folder is made, private" test "$(stat -c %a "${sock_dir}")" = 700

run "${wb}" restart-review-broker
check "restart-review-broker needs a login" 1 err \
  "workbench: the review broker has no Codex login yet: host/workbench review-login"

run "${wb}" review-login
check "review-login needs the image" 1 err \
  "workbench: no localhost/airlock-review-broker:local; host/workbench build or pull first"

image localhost/airlock-review-broker:local sha256:review
run "${wb}" review-login
check "a login that saved nothing is said" 1 err "workbench: no login was saved in ${login}"
check "the login runs in the broker's image, on its network" 1 calls \
  "podman run --rm -it --name review-login --userns=keep-id:uid=1000,gid=1000 --security-opt label=type:container_engine_t --security-opt label=level:s0:c555,c666 --cap-drop=all --security-opt no-new-privileges --read-only --tmpfs /tmp --network workbench-net-airlock-review"
check "through the proxy there" 1 calls "-e HTTPS_PROXY=http://10.203.1.2:8888 -e https_proxy=http://10.203.1.2:8888"
check "into its own login folder" 1 calls \
  "-v ${login}:/var/lib/review-broker/codex:Z -e HOME=/tmp -e CODEX_HOME=/var/lib/review-broker/codex localhost/airlock-review-broker:local codex login --device-auth"
assert "which is private" test "$(stat -c %a "${login}")" = 700
assert "the network is registered with the openai set" grep -qx "sets=openai" "${egress}/airlock-review.conf"
assert "and nothing else" grep -qx "always=no" "${egress}/airlock-review.conf"
assert "a workspace's registration keeps the default sets" sh -c '! grep -q "^always=no" "$1"/*.conf 2>/dev/null || [ "$(grep -l "^always=no" "$1"/*.conf)" = "$1/airlock-review.conf" ]' _ "${egress}"

# shellcheck disable=SC2016 # expanded when the rule runs
rule 'run --rm -it --name review-login *' 'echo "{}" >"${HOME}/.local/share/workbench/review/codex/auth.json"'
WORKBENCH_REVIEW_MODELS=gpt-x WORKBENCH_REVIEW_PER_HOUR=3 run "${wb}" review-login
check "a login starts the broker" 0 out \
  "workbench: review broker logged in and running; from a workbench: l2 --review -- make cross-review"
check "isolated as the login run was" 0 calls \
  "podman run -d --name review-broker --userns=keep-id:uid=1000,gid=1000 --security-opt label=type:container_engine_t --security-opt label=level:s0:c555,c666 --cap-drop=all --security-opt no-new-privileges --read-only --tmpfs /tmp --network workbench-net-airlock-review"
check "with the models and the hourly cap" 0 calls "-e REVIEW_MODELS=gpt-x -e REVIEW_PER_HOUR=3"
check "and its socket folder" 0 calls "-v ${sock_dir}:/run/review-broker:Z localhost/airlock-review-broker:local"
unrule

run "${wb}" helpers
refute "a running broker is left alone" "--name review-broker"

run "${wb}" restart-review-broker
check "restart-review-broker starts it again" 0 out "workbench: review broker restarted"
check "after removing it" 0 calls "podman rm -f -t 5 review-broker"
check "with the default models and cap" 0 calls "-e REVIEW_MODELS= -e REVIEW_PER_HOUR=10"

# A broker that never opens its socket.
gone review-broker
rm -f "${sock_dir}/review.sock"
echo "review-broker: boom" >"${PODMAN_STATE}/start-logs/review-broker"
NO_REVIEW_SOCKET=1 run "${wb}" helpers
check "a broker that opens no socket is reported" 1 err \
  "workbench: the review broker did not open its socket; podman logs review-broker"
check "with its last log lines" 1 err "review-broker: boom"
rm "${PODMAN_STATE}/start-logs/review-broker"

# A login but no image.
gone review-broker
rm "${PODMAN_STATE}/img/localhost_airlock-review-broker_local"
run "${wb}" helpers
check "no image, no broker, and how to get one" 0 err \
  "workbench: no localhost/airlock-review-broker:local, so no review broker; host/workbench build or pull, then restart-review-broker"
refute "none is started" "--name review-broker"

finish
