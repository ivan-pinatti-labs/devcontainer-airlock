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

run images/workbench/vscode/select-extensions claude "${list}"
check "Claude gets the anthropic extensions and the shared ones, in list order" 0 out \
  $'anthropic.claude-code@1.0.0\nAnthropic.other@1.0.0\nzeta.dependency@3.0.0\nalpha.dependent@3.0.0'
assert "nothing from openai, in any case" bash -c "! images/workbench/vscode/select-extensions claude '${list}' | grep -qi '^openai\.'"

run images/workbench/vscode/select-extensions codex "${list}"
check "Codex gets a dependency before the extension that needs it, unsorted" 0 out \
  $'openai.codex-audio@2.0.0\nopenai.chatgpt@2.0.0\nOpenAI.other@2.0.0\nzeta.dependency@3.0.0\nalpha.dependent@3.0.0'
assert "nothing from anthropic, in any case" bash -c "! images/workbench/vscode/select-extensions codex '${list}' | grep -qi '^anthropic\.'"

run images/workbench/vscode/select-extensions vim "${list}"
check "an unknown agent is refused" 2 err "unknown agent: vim"

run images/workbench/vscode/select-extensions claude
check "so is a missing list" 2 err "usage: select-extensions"

finish
