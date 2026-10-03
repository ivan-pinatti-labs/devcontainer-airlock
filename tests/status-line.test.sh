#!/usr/bin/env bash
#
# Tests for images/workbench/bin/status-line, Claude Code's status line in
# a workbench. jq is a stub that reads the alternatives of its filter
# (`.a.b // .c // empty`) with python3, and git answers BRANCH.
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
for alt in sys.argv[1].split(\" // \"):
    v = data
    for k in alt.strip(\".\").split(\".\"):
        v = v.get(k) if isinstance(v, dict) else None
    if alt != \"empty\" and v is not None:
        print(v)
        break
" "$2"'
# shellcheck disable=SC2016
stub git '[ -n "${BRANCH:-}" ] || exit 128; echo "${BRANCH}"'
unset AIRLOCK_WORKBENCH AIRLOCK_HOST_HOME BRANCH

# line JSON: the status line for that input.
line() {
  : >"${STUB_LOG}"
  __status=0
  bash "${__repo}/images/workbench/bin/status-line" <<<"${1}" >"${__scratch}/out" 2>"${__scratch}/err" || __status=$?
}

line '{}'
check "with nothing to go on, just a name" 0 out "workbench"
line 'not json'
check "input it cannot read is left out, never an error" 0 out "workbench"

export AIRLOCK_WORKBENCH=workbench-claude-s-brave-otter AIRLOCK_HOST_HOME=/home/dev-host
line '{"workspace": {"current_dir": "/home/dev-host/src/app/.claude/worktrees/brave-otter"}, "model": {"display_name": "Opus"}, "effort": {"level": "high"}}'
assert "the workbench, the folder and the model with its effort" \
  grep -qx "workbench-claude-s-brave-otter | ~/src/app/.../brave-otter | Opus, high effort" "${__scratch}/out"
check "the folder's branch is asked for" 0 calls "git -C /home/dev-host/src/app/.claude/worktrees/brave-otter branch --show-current"

BRANCH=feat/x line '{"cwd": "/home/dev-host", "model": {"display_name": "Opus"}}'
assert "the home folder itself is ~, with its branch; a model with no effort" \
  grep -qx "workbench-claude-s-brave-otter | ~ (feat/x) | Opus" "${__scratch}/out"
line '{"cwd": "/home/dev-hostile/src"}'
assert "only the home folder, not one whose name starts the same" \
  grep -qx "workbench-claude-s-brave-otter | /home/dev-hostile/src" "${__scratch}/out"
unset AIRLOCK_HOST_HOME
line '{"cwd": "/home/dev-host/src"}'
assert "with no host home known, the path as it is" \
  grep -qx "workbench-claude-s-brave-otter | /home/dev-host/src" "${__scratch}/out"

finish
