#!/usr/bin/env bash
#
# host/workbench session: start, attach, shell, stop, list and prune
# (docs/SESSIONS.md). A session's containers, network and registrations are
# named after it, its clones are mounted read only with a folder of its own
# in each, and its record survives it being stopped.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

touch "${PODMAN_STATE}/secret/gh-devcontainer"
mkdir -p "${HOME}/.ssh/devcontainer"
touch "${HOME}/.ssh/devcontainer/id_ed25519"
image localhost/airlock-l2:local sha256:l2
export WORKBENCH_GH_OWNERS=acme TERM=xterm-256color
sessions="${HOME}/.local/share/workbench/sessions"
app="${WORKBENCH_ROOT}/app"
clone "${app}"
cd "${app}"

# session_name: the session the last run started, from what it printed.
session_name() { sed -n 's/^workbench: session \([^ ]*\) (.*/\1/p' "${__scratch}/out" | head -n 1; }

run "${wb}" session
check "session needs a subcommand" 1 err "workbench: host/workbench session start|attach|shell|stop|list|prune"
run "${wb}" session start
check "start needs an agent" 1 err "workbench: which agent? host/workbench session start claude|codex[-<account>] [--remote] [WORKSPACE]"
run "${wb}" session start claude --bogus
check "an unknown option is refused" 1 err "workbench: session start: unknown option --bogus"
run "${wb}" session start codex --remote
check "only Claude Code has a remote control mode" 1 err "workbench: only Claude Code has a remote control mode"
mkdir -p "${__scratch}/elsewhere"
run "${wb}" session start claude "${__scratch}/elsewhere"
check "a workspace outside WORKBENCH_ROOT is refused" 1 err \
  "workbench: ${__scratch}/elsewhere is not under WORKBENCH_ROOT (${WORKBENCH_ROOT}); the gh broker could not see it"
assert "and leaves no record" test ! -e "${sessions}"

run "${wb}" session start claude
s="$(session_name)"
id="$(sed -n 's/^claude_id=//p' "${sessions}/${s}/session")"
check "start names a new session and runs its agent" 0 out "workbench: session ${s} (claude, ${app})"
assert "named adjective-animal" grep -qE '^[a-z]+-[a-z]+$' <<<"${s}"
assert "its record is kept with the workbench's data" grep -qx "workspace=${app}" "${sessions}/${s}/session"
assert "readable by its owner only" test "$(stat -c %a "${sessions}/${s}")" = 700
check "its own network" 0 calls "podman network create --internal --disable-dns --subnet 10.203.1.0/24 workbench-net-s-${s}"
check "its own engine" 0 calls "podman run -d --name l2-engine-s-${s}"
check "its own workbench, labelled with the session" 0 calls \
  "podman run -d --name workbench-claude-s-${s} --label workbench.session.workspace=${app} --label workbench.agent=claude --label workbench.session=${s}"
check "the clone read only, its own folder in it read write" 0 calls \
  "-v ${app}:${app}:ro,Z -v ${app}/.claude/worktrees/${s}:${app}/.claude/worktrees/${s}:Z"
assert "that folder is made" test -d "${app}/.claude/worktrees/${s}"
check "told which session it is" 0 calls "-e AIRLOCK_SESSION=${s} -e AIRLOCK_WORKBENCH=workbench-claude-s-${s}"
check "Claude Code gets the session's own conversation id" 0 calls \
  "podman exec -it -e TERM -e COLORTERM -w ${app} workbench-claude-s-${s} claude --session-id ${id}"
check "and when it exits, the session's containers go" 0 calls \
  "podman ps -a --filter label=workbench.session=${s} --format {{.Names}}"
check "its engine" 0 calls "podman rm -f -t 5 l2-engine-s-${s}"
check "and its network" 0 calls "podman network rm workbench-net-s-${s}"
assert "the workbench is gone" test ! -e "${PODMAN_STATE}/ctr/workbench-claude-s-${s}"
check "saying how to resume it" 0 out "workbench: session ${s} stopped; make attach-${s} resumes it"
check "the shared services stop with the last one" 0 out "workbench: nothing else running, so the shared services stopped too"
assert "the record stays" test -f "${sessions}/${s}/session"

# attach
run "${wb}" session attach
check "attach needs a session" 1 err "workbench: which session? host/workbench session attach NAME (host/workbench session list)"
run "${wb}" session attach --bogus
check "an unknown option is refused" 1 err "workbench: session attach: unknown option --bogus"
run "${wb}" session attach no-such
check "a session that does not exist" 1 err "workbench: no session no-such (host/workbench session list)"
run "${wb}" session attach "${s}"
check "attach brings a stopped session back" 0 calls "podman run -d --name workbench-claude-s-${s}"
check "and resumes its conversation" 0 calls "workbench-claude-s-${s} claude --resume ${id}"
rule "exec workbench-claude-s-${s} sh -c *" 'exit 2'
run "${wb}" session attach --remote "${s}"
check "with no transcript yet, it starts the same conversation again" 0 calls \
  "workbench-claude-s-${s} claude --session-id ${id} --remote-control"
unrule

container "workbench-claude-s-${s}" running "workbench.session=${s}"
run "${wb}" session attach "${s}"
check "a session whose agent still runs gets a shell beside it" 0 calls \
  "podman exec -it -e TERM -e COLORTERM -w ${app} workbench-claude-s-${s} bash"
check "found by its process" 0 calls "podman exec workbench-claude-s-${s} pgrep -f agent-clis/.*claude"
refute "nothing starts" "podman run -d"
rule "exec workbench-claude-s-${s} pgrep *" 'exit 1'
run "${wb}" session attach "${s}"
check "one whose terminal went away gets its agent again" 0 calls \
  "workbench-claude-s-${s} claude --resume ${id}"
refute "in the workbench already running" "podman run -d --name workbench-claude"
unrule

# shell and stop
run "${wb}" session shell
check "shell needs a session" 1 err "workbench: which session? host/workbench session shell NAME"
container "workbench-claude-s-${s}" running "workbench.session=${s}"
run "${wb}" session shell "${s}"
check "a shell in a running session" 0 calls "podman exec -it -e TERM -e COLORTERM -w ${app} workbench-claude-s-${s} bash"
run "${wb}" session stop
check "stop needs a session" 1 err "workbench: which session? host/workbench session stop NAME"
run "${wb}" session stop "${s}"
check "stop takes it down" 0 out "workbench: session ${s} stopped"
check "its containers" 0 calls "podman ps -a --filter label=workbench.session=${s}"
run "${wb}" session shell "${s}"
check "no shell in a stopped one" 1 err "workbench: session ${s} is not running; make attach-${s}"

# codex, with voice on: only Claude Code gets a microphone or an id.
export WORKBENCH_VOICE=1
rule 'exec -it * codex' 'exit 3'
run "${wb}" session start codex
c="$(session_name)"
unrule
check "codex runs codex" 3 calls "podman exec -it -e TERM -e COLORTERM -w ${app} workbench-codex-s-${c} codex"
check "the session ends with the agent's status, taken down all the same" 3 out \
  "workbench: session ${c} stopped; make attach-${c} resumes it"
refute "with no conversation id" "codex --session-id"
refute "and no voice" "pactl"

run "${wb}" session start claude
v="$(session_name)"
check "Claude Code with voice runs through the voice session" 0 calls \
  "-e WORKBENCH_VOICE_MIC=/run/workbench-voice/mic-"
check "still with its conversation id" 0 calls "workbench-claude-s-${v} claude --session-id"
unset WORKBENCH_VOICE

# list and prune
run "${wb}" session names
check "names lists the sessions" 0 out "${s}"
run "${wb}" session list
check "list says each session's agent and state" 0 out "${s}"
assert "with a header" grep -qE '^SESSION +AGENT +STATE +WORKSPACE$' "${__scratch}/out"
assert "stopped" grep -qE "^${s} +claude +stopped +${app}$" "${__scratch}/out"
refute "a session with no clone lists none" "branch --show-current"

clone "${app}/.claude/worktrees/${s}"
git_s="${app}/.claude/worktrees/${s}/.git"
echo feat/x >"${git_s}/branch"
echo " M a" >"${git_s}/changes"
echo "abc one" >"${git_s}/ahead"
container "workbench-codex-s-${c}" running "workbench.session=${c}"
run "${wb}" session ls
assert "a running one" grep -qE "^${c} +codex +running +${app}$" "${__scratch}/out"
check "and each clone it made, with what is only there" 0 out \
  "    app: feat/x (uncommitted changes, commits not pushed)"
rm "${git_s}/changes"
run "${wb}" session list
check "only what is only there" 0 out "    app: feat/x (commits not pushed)"
rm "${git_s}/branch" "${git_s}/ahead"
run "${wb}" session list
assert "a clean clone on no branch says nothing more" grep -qx "    app: no branch" "${__scratch}/out"

echo " M a" >"${git_s}/changes"
run "${wb}" session prune "${c}"
check "prune leaves a running session" 0 out "workbench: ${c} is running; stop it first (make stop-${c})"
run "${wb}" session prune
check "and a clone with something only there" 0 out \
  "workbench: keeping ${s}: app has uncommitted changes (${app}/.claude/worktrees/${s})"
assert "kept" test -d "${app}/.claude/worktrees/${s}"
check "every other stopped session is pruned" 0 out "workbench: removed session ${v}"
rm "${git_s}/changes"
touch "${git_s}/status-fails"
run "${wb}" session prune "${s}"
check "a git status that fails is a reason to keep it" 0 out "keeping ${s}: app has git status failed"
rm "${git_s}/status-fails"
touch "${git_s}/log-fails"
run "${wb}" session prune "${s}"
check "so is a git log that fails" 0 out "keeping ${s}: app has git log failed"
rm "${git_s}/log-fails"
touch "${PODMAN_STATE}/vol/l2-engine-s-${s}"
run "${wb}" session prune "${s}"
check "a session with nothing only there is removed" 0 out "workbench: removed session ${s}"
check "its clones from inside the user namespace" 0 calls "podman unshare rm -rf ${app}/.claude/worktrees/${s}"
assert "gone" test ! -e "${app}/.claude/worktrees/${s}"
check "its engine's volume" 0 calls "podman volume rm l2-engine-s-${s}"
assert "and its record" test ! -e "${sessions}/${s}"
gone "workbench-codex-s-${c}"
run "${wb}" session prune
check "then the last" 0 out "workbench: removed session ${c}"
run "${wb}" session prune
check "and then there is nothing to prune" 0 out "workbench: nothing to prune"
run "${wb}" session list
assert "nor anything to list" test "$(wc -l <"${__scratch}/out")" -eq 1

# A group: each of its clones read only with the session's folder, a
# worktree only one its git directory.
org="${WORKBENCH_ROOT}/org"
clone "${org}/one"
clone "${org}/two"
mkdir -p "${org}/two/.devcontainer"
touch "${org}/two/.devcontainer/workbench-worktree-only" "${org}/AGENTS.md"
run "${wb}" session start claude "${org}"
g="$(session_name)"
check "a group session" 0 out "workbench: session ${g} (claude, ${org})"
check "backed by a folder kept with the session" 0 calls "-v ${sessions}/${g}/root:${org}:Z"
check "each clone read only, with the session's folder" 0 calls \
  "-v ${org}/one:${org}/one:ro,Z -v ${org}/one/.claude/worktrees/${g}:${org}/one/.claude/worktrees/${g}:Z"
check "a worktree only clone's git directory too, and only that folder" 0 calls \
  "-v ${org}/two/.git:${org}/two/.git:ro,Z -v ${org}/two/.claude/worktrees/${g}:${org}/two/.claude/worktrees/${g}:Z -e GIT_CONFIG_KEY_0=safe.directory"
check "the folder's own agent files, read only" 0 calls "-v ${org}/AGENTS.md:${org}/AGENTS.md:ro,Z"
clone "${org}/two/.claude/worktrees/${g}"
echo main >"${org}/two/.claude/worktrees/${g}/.git/branch"
run "${wb}" session list
check "list goes through every clone of the group" 0 out "    two: main"
run "${wb}" session prune
check "and so does prune" 0 calls "podman unshare rm -rf ${org}/two/.claude/worktrees/${g}"
assert "the other clone's folder too" test ! -e "${org}/one/.claude/worktrees/${g}"

# Every name taken.
for a in amber brave calm clever dusty eager fancy gentle happy jolly keen lively lucky merry mighty noble plucky quick quiet rapid shiny silent sleepy snowy sturdy sunny swift tidy witty zesty; do
  for b in badger beaver bison crane falcon ferret finch gecko heron ibis koala lemur lynx marten moose newt otter panda puffin quail raven robin seal stoat swan tapir toucan walrus wombat yak; do
    mkdir -p "${sessions}/${a}-${b}"
  done
done
run "${wb}" session start claude
check "a new session needs a free name" 1 err "workbench: no free session name; host/workbench session prune"

finish
