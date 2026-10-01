#!/usr/bin/env bash
#
# Tests for images/workbench/bin/workbench-init. WORKBENCH_SHARE points at
# the scratch directory instead of /usr/local/share/workbench.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export WORKBENCH_SHARE="${__scratch}/share" CODEX_HOME="${__scratch}/codex"
mkdir "${WORKBENCH_SHARE}"
stub sleep 'echo "slept $*"'

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

finish
