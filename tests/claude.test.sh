#!/usr/bin/env bash
#
# Tests for images/workbench/bin/claude. AIRLOCK_CLAUDE_BIN is a stub that
# prints the proxy it was given, instead of the real Claude Code. The relay
# is never started: setsid is a stub, and where a relay should be listening,
# a python3 socket listens on a loopback port of its own instead.
# cspell:words setsockopt REUSEADDR
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export AIRLOCK_CLAUDE_BIN="${__scratch}/bin/claude-real"
# shellcheck disable=SC2016 # expanded when the stub runs
stub claude-real 'echo "proxy=${HTTPS_PROXY:-} lower=${https_proxy:-} egress=${AIRLOCK_EGRESS_PROXY:-}"'
stub setsid
export AIRLOCK_RELAY_PORT=$((20000 + RANDOM % 20000))
export LISTENERS="${__scratch}/listeners"
# A listener on the relay port, for a few seconds; its pid goes to
# LISTENERS so the test can stop it.
export LISTEN="python3 -c 'import socket, sys, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((\"127.0.0.1\", int(sys.argv[1])))
s.listen()
time.sleep(10)' ${AIRLOCK_RELAY_PORT}"
stop_listeners() {
  local pid
  for pid in $(cat "${LISTENERS}" 2>/dev/null); do kill "${pid}" 2>/dev/null || true; done
  : >"${LISTENERS}"
  "${REAL_SLEEP}" 0.2
}
unset HTTPS_PROXY https_proxy AIRLOCK_EGRESS_PROXY

run images/workbench/bin/claude --version
check "no egress proxy: Claude Code as is" 0 out "proxy= lower= egress="
check "with its arguments" 0 calls "claude-real --version"
refute "and no relay" "setsid"

HTTPS_PROXY=http://egress:3128 AIRLOCK_EGRESS_PROXY=http://egress:3128 \
  run images/workbench/bin/claude
check "inside another session: the relay is already its proxy" 0 out "proxy=http://egress:3128 lower="
refute "so none is started" "setsid"

https_proxy=http://egress:3128 run images/workbench/bin/claude
check "a relay that does not start leaves the proxy as it was" 0 out "proxy= lower=http://egress:3128 egress="
check "saying so" 0 err "claude: the relay did not start"
check "after asking for one" 0 calls \
  "setsid /usr/local/libexec/workbench/airlock-relay"

# shellcheck disable=SC2016 # expanded when the stub runs
stub setsid 'echo "upstream=${AIRLOCK_RELAY_UPSTREAM} port=${AIRLOCK_RELAY_PORT}" >>"${STUB_LOG}"
eval "${LISTEN}" >/dev/null 2>&1 &
echo $! >>"${LISTENERS}"'
HTTPS_PROXY=http://egress:3128 run images/workbench/bin/claude
want="proxy=http://127.0.0.1:${AIRLOCK_RELAY_PORT} lower=http://127.0.0.1:${AIRLOCK_RELAY_PORT} egress=http://egress:3128"
check "a relay that starts becomes the proxy" 0 out "${want}"
check "in front of the egress proxy" 0 calls "upstream=http://egress:3128 port=${AIRLOCK_RELAY_PORT}"

stub setsid
HTTPS_PROXY=http://egress:3128 run images/workbench/bin/claude
check "a relay already listening is used" 0 out "${want}"
refute "without starting another" "setsid"
stop_listeners

finish
