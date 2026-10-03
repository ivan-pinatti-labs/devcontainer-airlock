#!/usr/bin/env bash
#
# host/workbench up: which folder is the workspace, and what of it the
# workbenches and the engine mount: a clone, a worktree, a clone developed
# from worktrees only, a folder of clones (a group), and the session
# history shared with the host.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

touch "${PODMAN_STATE}/secret/gh-devcontainer"
export WORKBENCH_GH_OWNERS=acme WORKBENCH_AGENTS=claude WORKBENCH_MIRROR=0
src="${WORKBENCH_ROOT}"
# up_in DIR [WORKSPACE]: up from DIR, with no workbench or engine running.
up_in() {
  gone workbench-claude-app workbench-codex-app l2-engine-app
  cd "${1}"
  run "${wb}" up ${2:+"${2}"}
  cd "${src}"
}

app="${src}/app"
clone "${app}"
mkdir -p "${app}/sub"
up_in "${app}/sub"
check "the clone you are in, from a folder in it" 0 out "workbench: up for ${app} "
check "is mounted at its own path" 0 calls "-v ${app}:${app}:Z -v ${XDG_RUNTIME_DIR}/workbench/app:/run/l2-engine:Z"
refute "with no mirror, nothing joins it" "mirror-gate"
check "and no gate in the proxy settings" 0 calls \
  "-e HTTPS_PROXY=http://10.203.1.2:8888 -e HTTP_PROXY=http://10.203.1.2:8888 -e NO_PROXY=localhost,127.0.0.1 -e https_proxy"

wt="$(worktree "${app}" fix)"
up_in "${wt}"
check "a worktree's workspace is its main clone" 0 out "workbench: up for ${app} "

up_in "${src}" "${wt}"
check "a worktree named is its own workspace" 0 out "workbench: up for ${wt} (L2 engine l2-engine-app-fix)"
check "with its clone's git directory mounted too" 0 calls "-v ${wt}:${wt}:Z -v ${app}/.git:${app}/.git:Z"

mkdir -p "${app}/.devcontainer"
touch "${app}/.devcontainer/workbench-worktree-only"
up_in "${wt}"
check "a clone marked worktree only: the worktree you are in" 0 out "workbench: up for ${wt} "
up_in "${app}"
check "never its main clone" 1 err \
  "workbench: ${app} is developed from worktrees only (.devcontainer/workbench-worktree-only): its main clone holds data other containers use. Run this from a worktree, git -C ${app} worktree add .claude/worktrees/<name>"
refute "and nothing starts" "podman run"
up_in "${wt}" "${app}"
check "nor when it is named, even from a worktree" 1 err "workbench: ${app} is developed from worktrees only"
rm "${app}/.devcontainer/workbench-worktree-only"
mkdir -p "${wt}/.devcontainer"
touch "${wt}/.devcontainer/workbench-worktree-only"
up_in "${wt}"
check "the marker in the worktree you are in counts too" 0 out "workbench: up for ${wt} "
rm "${wt}/.devcontainer/workbench-worktree-only"
on_remote "${app}" origin/main .devcontainer/workbench-worktree-only
up_in "${wt}"
check "and on the remote's default branch, not pulled yet" 0 out "workbench: up for ${wt} "
rm -r "${app}/.git/remote-tree"

plain="${src}/notes"
mkdir -p "${plain}"
up_in "${plain}"
check "a folder outside git is its own workspace" 0 out "workbench: up for ${plain} "
refute "with no git directory to add" "-v ${plain}/.git"

up_in "${src}" "${__scratch}"
check "a workspace outside WORKBENCH_ROOT is refused" 1 err \
  "workbench: ${__scratch} is not under WORKBENCH_ROOT (${src}); the gh broker could not see it"

# A group: a folder of clones, not itself in git.
org="${src}/org"
clone "${org}/one"
clone "${org}/two"
mkdir -p "${org}/two/.devcontainer" "${org}/data" "${org}/.claude"
touch "${org}/two/.devcontainer/workbench-worktree-only" "${org}/AGENTS.md"
worktree "${org}/two" t1 >/dev/null
state="${HOME}/.local/share/workbench/groups/org-$(printf '%s' "${org}" | sha256sum | cut -c1-12)"
gone workbench-claude-org l2-engine-org
up_in "${org}"
check "a folder of clones is a group workspace" 0 out "workbench: up for ${org} (L2 engine l2-engine-org)"
check "backed by a folder kept with the state" 0 calls "-v ${state}/root:${org}:Z"
check "each clone mounted on its own" 0 calls "-v ${org}/one:${org}/one:Z"
check "a worktree only clone brings its git directory and worktrees" 0 calls \
  "-v ${org}/two/.git:${org}/two/.git:Z -v ${org}/two/.claude/worktrees:${org}/two/.claude/worktrees:Z -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=${org}/two"
check "said safe to git" 0 calls "-e GIT_CONFIG_COUNT=1"
check "and the folder's own agent files" 0 calls "-v ${org}/AGENTS.md:${org}/AGENTS.md:Z -v ${org}/.claude:${org}/.claude:Z"
check "an empty server list stands in for .mcp.json" 0 calls "-v ${state}/.mcp.json:${org}/.mcp.json:ro,Z"
assert "kept with the state" grep -qx '{"mcpServers":{}}' "${state}/.mcp.json"
refute "the other folders are never mounted" "-v ${org}/data"
refute "nor the worktree only clone whole" "-v ${org}/two:${org}/two:Z"
echo '{"mcpServers":{"x":{}}}' >"${org}/.mcp.json"
gone workbench-claude-org l2-engine-org
up_in "${org}"
check "the folder's own .mcp.json, read only" 0 calls "-v ${org}/.mcp.json:${org}/.mcp.json:ro,Z"
rm -r "${org}/two/.devcontainer" "${org}/.mcp.json"
gone workbench-claude-org l2-engine-org
up_in "${org}"
refute "with no worktree only clone, no git settings" "GIT_CONFIG_COUNT"

mkdir -p "${src}/empty/folder"
up_in "${src}/empty"
check "a folder with no clones is a plain workspace" 0 calls "-v ${src}/empty:${src}/empty:Z"

# Session history shared with the host.
hist="${HOME}/host-claude/projects"
slug="$(printf '%s' "${app}" | tr -c 'A-Za-z0-9' -)"
export WORKBENCH_HISTORY="codex=/nowhere claude=~/host-claude"
up_in "${app}"
check "WORKBENCH_HISTORY needs the host folder" 1 err "workbench: WORKBENCH_HISTORY: no ${hist}"
mkdir -p "${hist}/${slug}-sub" "${hist}/${slug}-other" "${hist}/${slug}-new" "${hist}/unrelated"
echo "{\"cwd\":\"${app}/sub\"}" >"${hist}/${slug}-sub/a.jsonl"
printf '%s\n' "{\"cwd\":\"${app}\"}" "{\"cwd\":\"${app}/../other\"}" >"${hist}/${slug}-other/b.jsonl"
up_in "${app}"
assert "the workspace's own folder is made" test -d "${hist}/${slug}"
check "and mounted, with no transcripts yet" 0 calls "-v ${hist}/${slug}:/home/dev/.claude/projects/${slug}:Z"
check "a folder whose sessions all ran under the workspace" 0 calls \
  "-v ${hist}/${slug}-sub:/home/dev/.claude/projects/${slug}-sub:Z"
refute "not one that ran elsewhere" "${slug}-other"
refute "nor one with no transcripts that is not the workspace's" "${slug}-new"
refute "nor anything else" "unrelated"
WORKBENCH_AGENTS=codex up_in "${app}"
refute "codex shares no history" "/home/dev/.claude/projects"
unset WORKBENCH_HISTORY

finish
