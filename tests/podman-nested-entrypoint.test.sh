#!/usr/bin/env bash
#
# Tests for images/podman-nested/bin/podman-nested-entrypoint. The podman
# stub serves nothing: for `system service` it binds the unix socket it was
# asked for (unless SOCKET_LATE is set, when a sleep binds it instead, or
# NO_SOCKET, when nothing does) and then stays up, as the real
# service would, writing its pid so a test can see it stopped. For the
# readiness probe (`podman --url ... version`) it answers unless API_DOWN is
# set, a socket that is bound but not yet serving. The ticker
# stub does the same.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

entrypoint=images/podman-nested/bin/podman-nested-entrypoint
export XDG_RUNTIME_DIR="${__scratch}/xdg"
unset PODMAN_NESTED_SOCKET PODMAN_NESTED_WAIT
# shellcheck disable=SC2016 # expanded when the stubs run
bind='python3 -c "import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])"'
# shellcheck disable=SC2016
stub podman '
if [ "$1" = --url ]; then [ -z "${API_DOWN:-}" ]; exit; fi
echo $$ >"${STUB_LOG}.service"
path="${4#unix://}"
echo "${path}" >"${STUB_LOG}.socket"
[ -n "${SOCKET_LATE:-}${NO_SOCKET:-}" ] || '"${bind}"' "${path}"
exec "${REAL_SLEEP}" 30'
# shellcheck disable=SC2016
stub podman-health-ticker 'echo $$ >"${STUB_LOG}.ticker"; exec "${REAL_SLEEP}" 30'
# shellcheck disable=SC2016
stub sleep '
[ -z "${API_DOWN:-}" ] || "${REAL_SLEEP}" 0.1
path="$(cat "${STUB_LOG}.socket" 2>/dev/null || true)"
[ -z "${SOCKET_LATE:-}" ] || [ -z "${path}" ] || [ -S "${path}" ] || '"${bind}"' "${path}"'
# shellcheck disable=SC2016
stub job 'echo "job read: $(cat)"; exit "${JOB_STATUS:-0}"'

# stopped NAME: the stub that wrote its pid to ${STUB_LOG}.NAME is gone.
stopped() {
  local pid
  pid="$(cat "${STUB_LOG}.${1}")"
  for _ in $(seq 1 50); do
    kill -0 "${pid}" 2>/dev/null || return 0
    "${REAL_SLEEP}" 0.1
  done
  return 1
}

run "${entrypoint}"
check "a command is required" 2 err "usage: podman-nested-entrypoint COMMAND"
refute "and nothing starts without one" "podman system service"

JOB_STATUS=3 run "${entrypoint}" job with args <<<"from stdin"
check "serves the API in XDG_RUNTIME_DIR" 3 calls \
  "podman system service --time=0 unix://${XDG_RUNTIME_DIR}/podman/podman.sock"
check "starts the health ticker" 3 calls "podman-health-ticker"
check "runs the command with its arguments" 3 calls "job with args"
check "on this standard input" 3 out "job read: from stdin"
assert "and stops the service on the way out" stopped service
assert "and the ticker" stopped ticker

rm -f "${STUB_LOG}.socket"
PODMAN_NESTED_SOCKET="${__scratch}/elsewhere/api.sock" SOCKET_LATE=1 run "${entrypoint}" job </dev/null
check "PODMAN_NESTED_SOCKET moves the socket" 0 calls \
  "podman system service --time=0 unix://${__scratch}/elsewhere/api.sock"
check "waits for a socket not up yet" 0 calls "sleep 0.2"
check "then runs the command" 0 calls "job"

# The last run's socket is still there, as a restarted container's would be.
NO_SOCKET=1 PODMAN_NESTED_WAIT=1 run "${entrypoint}" job
check "a socket that never comes up fails" 1 err "the API socket ${XDG_RUNTIME_DIR}/podman/podman.sock did not come up"
check "after PODMAN_NESTED_WAIT seconds of tries" 1 calls "sleep 0.2"
assert "five a second" test "$(grep -c '^sleep 0.2' "${STUB_LOG}")" -eq 5
refute "and the command never runs" "job"
assert "a stale socket is removed first" test ! -e "${XDG_RUNTIME_DIR}/podman/podman.sock"
assert "the service is stopped all the same" stopped service

# A bound socket whose API never answers is not ready either.
# Its sleeps are real here, so the service has bound the socket well before
# the tries run out and the probe is what keeps failing.
API_DOWN=1 PODMAN_NESTED_WAIT=2 run "${entrypoint}" job
check "a socket that never answers fails" 1 err "did not come up"
check "after probing the API" 1 calls "podman --url unix://${XDG_RUNTIME_DIR}/podman/podman.sock version"
refute "and the command never runs" "job"
assert "the service is stopped all the same" stopped service

# A signal reaches the command, whose own status is then the exit status.
# shellcheck disable=SC2016
stub job 'trap "echo \"job got TERM\"; exit 7" TERM
touch "${STUB_LOG}.ready"
while :; do "${REAL_SLEEP}" 0.05; done'
: >"${STUB_LOG}"
bash "${__repo}/${entrypoint}" job >"${__scratch}/out" 2>"${__scratch}/err" </dev/null &
pid=$!
for _ in $(seq 1 100); do
  [[ -f "${STUB_LOG}.ready" ]] && break
  "${REAL_SLEEP}" 0.1
done
# The trap is set just after the command starts.
"${REAL_SLEEP}" 1
kill -s TERM "${pid}"
__status=0
wait "${pid}" || __status=$?
check "a TERM is passed on to the command" 7 out "job got TERM"

finish
