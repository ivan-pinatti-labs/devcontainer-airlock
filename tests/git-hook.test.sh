#!/usr/bin/env bash
#
# Tests for images/workbench/share/git-hook, in the form l2-hooks-install
# installs it: rendered for pre-commit. It is rendered under kcov-rendered,
# at the template's own path, which is how `make coverage` counts its lines
# for the template (the Makefile's SHELL_SCRIPTS notes); rendering keeps
# every line where it was, which the first check holds it to.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

template="${__repo}/images/workbench/share/git-hook"
hooks="${__scratch}/kcov-rendered/images/workbench/share"
hook="${hooks}/git-hook"
mkdir -p "${hooks}"
sed "s/@HOOK_TYPE@/pre-commit/g" "${template}" >"${hook}"
assert "rendering keeps every line where it was" \
  test "$(wc -l <"${hook}")" -eq "$(wc -l <"${template}")"
assert "and fills in every placeholder" \
  test "$(grep -c @HOOK_TYPE@ "${hook}")" -eq 0

stub l2
stub l2-pre-commit
run "${hook}" one two
check "prepares the hook environments in L2, with network, read only" 0 calls \
  "l2 --net --ro -- l2-prepare-hooks"
check "then runs the hooks in L2" 0 calls \
  "l2-pre-commit hook-impl --config=.pre-commit-config.yaml --hook-type=pre-commit --hook-dir ${hooks} -- one two"

stub l2 'echo "no egress"; exit 1'
run "${hook}"
check "an environment that cannot be prepared fails the hook" 1 err "no egress"
refute "before any hook runs" "l2-pre-commit"

# Outside the workbench: no l2 anywhere on PATH.
rm "${__scratch}/bin/l2"
# A sh script, which kcov does not trace: only the hook is to be counted.
# shellcheck disable=SC2016 # expanded when the hook runs
printf '#!/bin/sh\necho "pre-commit.pre-l2 $*" >>"${STUB_LOG}"\necho "previous hook ran"\n' \
  >"${hooks}/pre-commit.pre-l2"
chmod +x "${hooks}/pre-commit.pre-l2"
PATH="${__scratch}/bin:/usr/bin:/bin" run "${hook}" one
check "without l2, the hook that was there before runs" 0 out "previous hook ran"
check "with the same arguments" 0 calls "pre-commit.pre-l2 one"

rm "${hooks}/pre-commit.pre-l2"
PATH="${__scratch}/bin:/usr/bin:/bin" run "${hook}"
check "without either, the commit fails saying where to make it" 1 err \
  "pre-commit: this hook runs in L2; commit from the workbench"

finish
