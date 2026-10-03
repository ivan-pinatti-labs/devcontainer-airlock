#!/usr/bin/env bash
#
# Tests for images/egress-proxy/bin/egress-proxy, the proxy container's main
# process. The resolvers and squid are stubs that stay up for a moment, or
# until they are killed; the refresh interval is a stub sleep that returns
# at once. EGRESS_OUT is the scratch directory, where forward.conf lands.
# date answers from DATES, one value a call (the last one from then on), so
# the 15 second wait for a name to resolve passes in no time.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export EGRESS_OUT="${__scratch}/run"
mkdir "${EGRESS_OUT}"
stub egress-refresh
stub egress-reload
# The bodies expand when the stub runs, not here.
# shellcheck disable=SC2016
stub sleep '"${REAL_SLEEP}" 0.01'
# shellcheck disable=SC2016
stub squid 'touch "${STUB_LOG}.squid"; exec "${REAL_SLEEP}" "${SQUID_UP:-0.5}"'
# shellcheck disable=SC2016
stub dnscrypt-proxy 'exec "${REAL_SLEEP}" 2'
# shellcheck disable=SC2016
stub unbound '[ -z "${UNBOUND_EXITS:-}" ] || exit 1; exec "${REAL_SLEEP}" 2'
# shellcheck disable=SC2016
stub timeout 'shift; exec "$@"'
# shellcheck disable=SC2016
stub getent '[ -z "${GETENT_FAILS:-}" ]'
# shellcheck disable=SC2016
stub date '
n="$(cat "${STUB_LOG}.date" 2>/dev/null || echo 0)"
echo $((n + 1)) >"${STUB_LOG}.date"
set -- ${DATES:-0}
shift $((n < $# ? n : $# - 1))
echo "$1"'
unset EGRESS_DNS GETENT_FAILS UNBOUND_EXITS

# run_proxy: run it with the date counter back at its first value.
run_proxy() {
  rm -f "${STUB_LOG}.date"
  run images/egress-proxy/bin/egress-proxy
}

export EGRESS_REFRESH_SECONDS=60
run_proxy
check "DNS over HTTPS by default, through dnscrypt-proxy" 0 calls \
  "dnscrypt-proxy -config /etc/dnscrypt-proxy/dnscrypt-proxy.toml"
assert "which unbound forwards to" grep -qx '  forward-addr: 127.0.0.1@5300' "${EGRESS_OUT}/forward.conf"
check "unbound runs in the foreground" 0 calls "unbound -d -c /etc/unbound/unbound.conf"
check "a name is looked up, each lookup capped" 0 calls "timeout 2 getent hosts api.github.com"
check "builds the rules before squid starts" 0 calls "egress-refresh "
check "runs squid in the foreground mode" 0 calls "squid -N -f /etc/squid/squid.conf"
check "waits the refresh interval" 0 calls "sleep 60"
check "and reloads with fresh provider lists" 0 calls "egress-reload --fetch"
refute "a name that resolves at once needs no wait" "sleep 0.2"

export EGRESS_DNS=dot
run_proxy
assert "DNS over TLS forwards from unbound itself" \
  grep -qx '  forward-addr: 1.1.1.2@853#security.cloudflare-dns.com' "${EGRESS_OUT}/forward.conf"
assert "to both resolvers" \
  grep -qx '  forward-addr: 1.0.0.2@853#security.cloudflare-dns.com' "${EGRESS_OUT}/forward.conf"
refute "with no dnscrypt-proxy" "dnscrypt-proxy"

export EGRESS_DNS=plain
run_proxy
check "any other EGRESS_DNS is a usage error" 2 err "EGRESS_DNS is 'plain'; use doh or dot"
refute "nothing starts" "unbound"
unset EGRESS_DNS

export GETENT_FAILS=1 DATES="0 0 0 100"
run_proxy
check "a name that does not resolve is tried again" 0 calls "sleep 0.2"
check "for at most 15 seconds, then a warning" 0 err "no name resolves over doh yet"
check "and the proxy starts anyway" 0 calls "squid -N"

export UNBOUND_EXITS=1 DATES=0
# Long enough for the stub to have exited before it is checked.
# shellcheck disable=SC2016
stub sleep '"${REAL_SLEEP}" 0.3'
run_proxy
check "unbound exiting stops it" 1 err "egress-proxy: unbound exited"
refute "before squid starts" "squid"
# shellcheck disable=SC2016
stub sleep '"${REAL_SLEEP}" 0.01'
unset GETENT_FAILS UNBOUND_EXITS DATES

stub egress-reload 'exit 1'
run_proxy
check "a failed reload keeps it running" 0 calls "egress-reload --fetch"

stub egress-refresh 'exit 2'
run_proxy
check "rules that cannot be built stop it before squid" 2 calls "egress-refresh"
refute "squid never starts" "squid"

# SIGTERM, as podman stop sends it, stops squid and exits 0.
stub egress-refresh
stub egress-reload
rm -f "${STUB_LOG}.squid" "${STUB_LOG}.date"
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
