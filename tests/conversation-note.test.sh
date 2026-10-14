#!/usr/bin/env bash
#
# Tests for images/workbench/bin/conversation-note, Claude Code's
# SessionStart hook in a workbench: it writes down the conversation the
# agent is in, for host/workbench to resume. jq is a stub that prints the
# input's session_id with python3, and fails on input that is not JSON.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

# The body expands when the stub runs, not here.
# shellcheck disable=SC2016
stub jq 'exec python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(5)
v = data.get(\"session_id\") if isinstance(data, dict) else None
if v is not None:
    print(v)
"'
export AIRLOCK_CONVERSATION_FILE="${__scratch}/conversation"

# note JSON: the hook run with that input.
note() {
  rm -f "${AIRLOCK_CONVERSATION_FILE}"
  __status=0
  bash "${__repo}/images/workbench/bin/conversation-note" <<<"${1}" >"${__scratch}/out" 2>"${__scratch}/err" || __status=$?
}

id=0b4c2f8e-6a1d-4e55-9c3b-7d2e1f0a9b86
note "{\"session_id\":\"${id}\",\"source\":\"resume\"}"
assert "it exits 0" test "${__status}" -eq 0
assert "and writes the conversation down" grep -qx "${id}" "${AIRLOCK_CONVERSATION_FILE}"
assert "printing nothing, since that would reach the conversation" test ! -s "${__scratch}/out"
next=7c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f
bash "${__repo}/images/workbench/bin/conversation-note" <<<"{\"session_id\":\"${next}\",\"source\":\"clear\"}"
assert "the next one is added after it, so the last is the one the agent is in" \
  test "$(cat "${AIRLOCK_CONVERSATION_FILE}")" = "$(printf '%s\n%s' "${id}" "${next}")"

note 'not json'
assert "input it cannot read never stops the session" test "${__status}" -eq 0
assert "and writes nothing" test ! -e "${AIRLOCK_CONVERSATION_FILE}"

note '{"source":"startup"}'
assert "nor does input with no conversation" test "${__status}" -eq 0
assert "nothing written" test ! -e "${AIRLOCK_CONVERSATION_FILE}"

note '{"session_id":"x; rm -rf /"}'
assert "anything but a UUID is ignored" test "${__status}" -eq 0
assert "and not written" test ! -e "${AIRLOCK_CONVERSATION_FILE}"

__status=0
AIRLOCK_CONVERSATION_FILE="${__scratch}/no/such/folder/conversation" \
  bash "${__repo}/images/workbench/bin/conversation-note" <<<"{\"session_id\":\"${id}\"}" >"${__scratch}/out" 2>"${__scratch}/err" || __status=$?
assert "a file it cannot write never stops the session either" test "${__status}" -eq 0
assert "and says nothing about it" test ! -s "${__scratch}/err"

finish
