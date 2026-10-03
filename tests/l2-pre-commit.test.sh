#!/usr/bin/env bash
#
# Tests for images/workbench/bin/l2-pre-commit. git and l2 are stubs; the
# repository is a scratch directory.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

repo="${__scratch}/repo"
mkdir -p "${repo}/.devcontainer"
stub git "echo ${repo}"
stub l2 'echo "ran in $PWD"'
unset L2_SKIP_HOOKS SKIP

run images/workbench/bin/l2-pre-commit run --all-files
check "runs pre-commit in L2, protected paths writable, link checkers skipped" 0 calls \
  "l2 --protected-rw --env SKIP=markdown-link-check,lychee -- pre-commit run --all-files"
check "from the top of the repository" 0 out "ran in ${repo}"

SKIP=shellcheck run images/workbench/bin/l2-pre-commit run
check "SKIP adds to the list" 0 calls "--env SKIP=markdown-link-check,lychee,shellcheck --"

L2_SKIP_HOOKS="" SKIP=shellcheck run images/workbench/bin/l2-pre-commit run
check "L2_SKIP_HOOKS replaces it" 0 calls "--env SKIP=shellcheck --"

echo plain >"${repo}/.devcontainer/workbench-profile"
run images/workbench/bin/l2-pre-commit run
refute "a profile without hooks-engine gets no engine" "--engine"

printf 'plain\nhooks-engine\n' >"${repo}/.devcontainer/workbench-profile"
run images/workbench/bin/l2-pre-commit run
check "hooks-engine in the profile gets the L2 engine" 0 calls "l2 --engine --protected-rw"

finish
