#!/usr/bin/env bash
#
# Tests for images/egress-proxy/bin/egress-reload. EGRESS_OUT points the
# lock at the scratch directory instead of /run/egress.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export EGRESS_OUT="${__scratch}"
stub flock
stub egress-refresh
stub squid

run images/egress-proxy/bin/egress-reload
check "uses the cached provider lists by default" 0 calls "egress-refresh --cached"
check "takes the lock first" 0 calls "flock 9"
check "then reloads squid in place" 0 calls "squid -k reconfigure -f /etc/squid/squid.conf"
[[ -f "${__scratch}/reload.lock" ]] || {
  echo "FAIL the lock file is not under EGRESS_OUT" >&2
  __failures=$((__failures + 1))
}

run images/egress-proxy/bin/egress-reload --fetch
check "--fetch asks the providers again" 0 calls "egress-refresh "
refute "without --cached" "egress-refresh --cached"

stub egress-refresh 'echo "bad set" >&2; exit 2'
run images/egress-proxy/bin/egress-reload
check "rules that cannot be built exit 1" 1 err "bad set"
refute "and leave squid alone" "squid"

finish
