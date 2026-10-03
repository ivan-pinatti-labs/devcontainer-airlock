#!/usr/bin/env bash
#
# Tests for images/workbench/bin/finish-image. jq, install, find and rm are
# stubs, so nothing under /home/dev is read or written; TMPDIR keeps the
# merged settings in the scratch directory.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export TMPDIR="${__scratch}"
stub jq 'echo "{\"merged\": true}"'
stub install
stub rm
stub find

run images/workbench/bin/finish-image /tmp/agent-settings.json
check "merges the agent settings over the machine ones" 0 calls \
  "jq -s .[0] * .[1] /home/dev/.vscode-server/data/Machine/settings.json /tmp/agent-settings.json"
check "installs the result for dev" 0 calls \
  "install -o 1000 -g 1000 -m 0644 ${__scratch}/settings.json /home/dev/.vscode-server/data/Machine/settings.json"
check "and removes its own copy" 0 calls "rm ${__scratch}/settings.json"
check "looks for anything dev does not own" 0 calls \
  "find /home/dev ! -user dev ! -path /home/dev/.vscode-server ! -path /home/dev/.vscode-server/extensions"
assert "the merge lands in TMPDIR" grep -q merged "${__scratch}/settings.json"

stub find 'echo /home/dev/.cache'
run images/workbench/bin/finish-image /tmp/agent-settings.json
check "a root owned leftover fails the build" 1 err "root owned paths under /home/dev:"
check "and is named" 1 err "/home/dev/.cache"

finish
