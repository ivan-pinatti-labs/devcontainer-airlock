#!/usr/bin/env bash
#
# Tests for images/workbench/bin/workbench-init. WORKBENCH_SHARE and
# WORKBENCH_VSCODE_SETTINGS point into the scratch directory, and jq is a
# stub that writes what it was asked to set.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export WORKBENCH_SHARE="${__scratch}/share" CODEX_HOME="${__scratch}/codex"
export WORKBENCH_VSCODE_SETTINGS="${__scratch}/settings.json"
mkdir "${WORKBENCH_SHARE}"
stub sleep 'echo "slept $*"'
# shellcheck disable=SC2016 # expanded when the stub runs
stub jq 'printf "%s\n" "$3" "$(cat "$5")"'
unset HTTPS_PROXY

run images/workbench/bin/workbench-init
check "the Claude workbench only waits" 0 calls "sleep infinity"
assert "and makes no Codex folder" test ! -e "${CODEX_HOME}"

echo 'prefix_rule(pattern=["git"], decision="prompt")' >"${WORKBENCH_SHARE}/codex-workbench.rules"
mkdir -p "${CODEX_HOME}/rules"
echo 'edited' >"${CODEX_HOME}/rules/workbench.rules"
run images/workbench/bin/workbench-init
check "the Codex workbench waits too" 0 out "slept infinity"
assert "and first puts the image copy of its rule back" \
  cmp -s "${WORKBENCH_SHARE}/codex-workbench.rules" "${CODEX_HOME}/rules/workbench.rules"

# SonarQube for IDE's JVM gets the egress proxy as options, written into
# the machine settings when the file is there to write.
export HTTPS_PROXY=http://10.203.1.2:8888/
run images/workbench/bin/workbench-init
refute "no machine settings file, nothing to write" "jq"

echo '{}' >"${WORKBENCH_VSCODE_SETTINGS}"
run images/workbench/bin/workbench-init
check "the proxy's host and port, for https and http" 0 calls \
  "-Dhttps.proxyHost=10.203.1.2 -Dhttps.proxyPort=8888 -Dhttp.proxyHost=10.203.1.2 -Dhttp.proxyPort=8888"
assert "go into the settings file" grep -qx -- "-Dhttps.proxyHost=10.203.1.2.*" "${WORKBENCH_VSCODE_SETTINGS}"
assert "and the temporary file is gone" test ! -e "${WORKBENCH_VSCODE_SETTINGS}.tmp"

unset HTTPS_PROXY
run images/workbench/bin/workbench-init
refute "no proxy, nothing to write" "jq"

finish
