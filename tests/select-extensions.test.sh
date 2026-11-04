#!/usr/bin/env bash
#
# Tests for images/workbench/vscode/select-extensions, which picks each
# agent's VS Code extensions from the pinned list and keeps their order.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

list="${__scratch}/extensions.txt"
cat >"${list}" <<'LIST'
# a comment

anthropic.claude-code@1.0.0
Anthropic.other@1.0.0
openai.codex-audio@2.0.0
openai.chatgpt@2.0.0
OpenAI.other@2.0.0
zeta.dependency@3.0.0
alpha.dependent@3.0.0
LIST

# check matches one line at a time, so the whole output is compared here:
# a sorted, shortened or reordered list has to fail.
want="${__scratch}/want"

run images/workbench/vscode/select-extensions claude "${list}"
printf '%s\n' anthropic.claude-code@1.0.0 Anthropic.other@1.0.0 \
  zeta.dependency@3.0.0 alpha.dependent@3.0.0 >"${want}"
check "Claude's selection succeeds" 0 out "anthropic.claude-code@1.0.0"
assert "Claude gets exactly the anthropic and shared extensions, in list order, no openai one in any case" \
  cmp -s "${want}" "${__scratch}/out"

run images/workbench/vscode/select-extensions codex "${list}"
printf '%s\n' openai.codex-audio@2.0.0 openai.chatgpt@2.0.0 OpenAI.other@2.0.0 \
  zeta.dependency@3.0.0 alpha.dependent@3.0.0 >"${want}"
check "Codex's selection succeeds" 0 out "openai.codex-audio@2.0.0"
assert "Codex gets exactly its extensions, a dependency before its dependent, unsorted, no anthropic one" \
  cmp -s "${want}" "${__scratch}/out"

run images/workbench/vscode/select-extensions vim "${list}"
check "an unknown agent is refused" 2 err "unknown agent: vim"

run images/workbench/vscode/select-extensions claude
check "so is a missing list" 2 err "usage: select-extensions"

finish
