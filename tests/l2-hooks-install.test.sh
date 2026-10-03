#!/usr/bin/env bash
#
# Tests for images/workbench/bin/l2-hooks-install. git is a stub answering
# for a scratch repository, and WORKBENCH_SHARE points at the template in
# this tree instead of /usr/local/share/workbench.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export WORKBENCH_SHARE="${__repo}/images/workbench/share"
repo="${__scratch}/repo"
hooks="${repo}/.git/hooks"
mkdir -p "${repo}"
export REPO="${repo}"
# git rev-parse prints the top or the git directory; git config fails unless
# HOOKS_PATH is set, as git does for an unset key.
stub git '
case "$*" in
  "rev-parse --show-toplevel") echo "${REPO}" ;;
  "rev-parse --path-format=absolute --git-common-dir") echo "${REPO}/.git" ;;
  "config --get core.hooksPath") [ -n "${HOOKS_PATH:-}" ] && echo "${HOOKS_PATH}" ;;
esac'

run images/workbench/bin/l2-hooks-install
check "without .pre-commit-config.yaml it refuses" 1 err "no .pre-commit-config.yaml in ${repo}"

echo "repos: []" >"${repo}/.pre-commit-config.yaml"
HOOKS_PATH=/elsewhere run images/workbench/bin/l2-hooks-install
check "with core.hooksPath set it refuses" 1 err "core.hooksPath is set to /elsewhere"

run images/workbench/bin/l2-hooks-install
check "by default it installs the pre-commit hook" 0 out "installed pre-commit hook (runs in L2)"
assert "rendered from the template" grep -q -- "--hook-type=pre-commit " "${hooks}/pre-commit"
assert "and executable" test -x "${hooks}/pre-commit"

run images/workbench/bin/l2-hooks-install
refute "its own hook is replaced, not kept aside" "kept the previous"
assert "so there is no .pre-l2" test ! -e "${hooks}/pre-commit.pre-l2"

printf 'default_install_hook_types: [pre-commit, "commit-msg"]\nrepos: []\n' \
  >"${repo}/.pre-commit-config.yaml"
echo "#!/bin/sh" >"${hooks}/commit-msg"
run images/workbench/bin/l2-hooks-install
check "a flow list installs each type" 0 out "installed commit-msg hook (runs in L2)"
check "and keeps a foreign hook aside" 0 out "kept the previous commit-msg hook as commit-msg.pre-l2"
assert "as commit-msg.pre-l2" grep -qx "#!/bin/sh" "${hooks}/commit-msg.pre-l2"

printf 'default_install_hook_types:\n  - pre-push   # before a push\n  - '"'"'pre-commit'"'"'\nrepos: []\n' \
  >"${repo}/.pre-commit-config.yaml"
run images/workbench/bin/l2-hooks-install
check "a block list installs each type" 0 out "installed pre-push hook (runs in L2)"
assert "rendered for that type" grep -q -- "--hook-type=pre-push " "${hooks}/pre-push"

stub git 'exit 128'
run images/workbench/bin/l2-hooks-install
check "outside a git repository it fails" 1 calls "git rev-parse --show-toplevel"

finish
