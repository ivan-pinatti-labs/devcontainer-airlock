#!/usr/bin/env bash
#
# Tests for images/egress-proxy/bin/egress-proxy, the proxy container's main
# process. squid is a stub that stays up for a moment, or until it is
# killed; the refresh interval is a stub sleep that returns at once.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

stub egress-refresh
stub egress-reload
# The bodies expand when the stub runs, not here.
# shellcheck disable=SC2016
stub sleep '"${REAL_SLEEP}" 0.1'
# shellcheck disable=SC2016
stub squid 'touch "${STUB_LOG}.squid"; exec "${REAL_SLEEP}" "${SQUID_UP:-0.5}"'

export EGRESS_REFRESH_SECONDS=60
run images/egress-proxy/bin/egress-proxy
check "builds the rules before squid starts" 0 calls "egress-refresh "
check "runs squid in the foreground mode" 0 calls "squid -N -f /etc/squid/squid.conf"
check "waits the refresh interval" 0 calls "sleep 60"
check "and reloads with fresh provider lists" 0 calls "egress-reload --fetch"

stub egress-reload 'exit 1'
run images/egress-proxy/bin/egress-proxy
check "a failed reload keeps it running" 0 calls "egress-reload --fetch"

stub egress-refresh 'exit 2'
run images/egress-proxy/bin/egress-proxy
check "rules that cannot be built stop it before squid" 2 calls "egress-refresh"
refute "squid never starts" "squid"

# SIGTERM, as podman stop sends it, stops squid and exits 0.
stub egress-refresh
stub egress-reload
rm -f "${STUB_LOG}.squid"
: >"${STUB_LOG}"
SQUID_UP=30 bash "${__repo}/images/egress-proxy/bin/egress-proxy" \
  >"${__scratch}/out" 2>"${__scratch}/err" &
__pid=$!
for _ in $(seq 100); do
  [[ -f "${STUB_LOG}.squid" ]] && break
  "${REAL_SLEEP}" 0.05
done
"${REAL_SLEEP}" 0.2
kill -TERM "${__pid}"
__status=0
wait "${__pid}" || __status=$?
check "SIGTERM stops squid and exits 0" 0 calls "squid -N"

finish
