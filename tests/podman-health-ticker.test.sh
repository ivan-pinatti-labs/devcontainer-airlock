#!/usr/bin/env bash
#
# Tests for images/podman-nested/bin/podman-health-ticker. The loop never
# ends on its own, so the sleep stub ends it: the script runs under errexit,
# and a sleep that fails stops it after the round being tested.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

ticker=images/podman-nested/bin/podman-health-ticker
# shellcheck disable=SC2016 # expanded when the stub runs
stub podman '
case "$1" in
  ps) printf "%s\n" aaa bbb ;;
  healthcheck) echo "a check prints" ; exit "${CHECK_STATUS:-0}" ;;
esac'
stub sleep 'exit 1'

run "${ticker}"
check "lists the containers that have a healthcheck" 1 calls \
  "podman ps --quiet --filter health=starting --filter health=healthy --filter health=unhealthy"
check "runs the first one's" 1 calls "podman healthcheck run aaa"
check "and the second's" 1 calls "podman healthcheck run bbb"
check "then waits the default tick" 1 calls "sleep 10"
assert "printing nothing of the checks" test ! -s "${__scratch}/out"

HEALTH_TICK=3 run "${ticker}"
check "HEALTH_TICK sets the tick" 1 calls "sleep 3"

CHECK_STATUS=1 run "${ticker}"
check "an unhealthy container does not stop the round" 1 calls "sleep 10"

stub podman 'exit 125'
run "${ticker}"
check "a failed listing checks nothing" 1 calls "sleep 10"
refute "and runs no healthcheck" "healthcheck run"

# shellcheck disable=SC2016
stub sleep '
n="$(cat "${STUB_LOG}.rounds" 2>/dev/null || echo 0)"
echo $((n + 1)) >"${STUB_LOG}.rounds"
[ "${n}" -lt 1 ]'
stub podman 'case "$1" in ps) echo aaa ;; esac'
run "${ticker}"
assert "and the next round lists them again" \
  test "$(grep -c '^podman ps' "${STUB_LOG}")" -eq 2

finish
