#!/usr/bin/env bash
#
# host/workbench scrub, and the secret scan at session start and end
# (docs/SCRUB.md): the context host/workbench hands host/scrub, which homes
# it names, what it says is live, and which secrets it says the host holds.
# host/scrub runs for real; its scan container is a rule that keeps what it
# was given.
# cspell:words showsecret
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

touch "${PODMAN_STATE}/secret/gh-devcontainer"
mkdir -p "${HOME}/.ssh/devcontainer"
touch "${HOME}/.ssh/devcontainer/id_ed25519"
image localhost/airlock-l2:local sha256:l2
image localhost/airlock-base:local sha256:base
image "$(sed -n 's/^GITLEAKS_IMAGE="\(.*\)"$/\1/p' host/scrub)" sha256:gitleaks
export WORKBENCH_GH_OWNERS=acme
data="${HOME}/.local/share/workbench"
sessions="${data}/sessions"
got="${__scratch}/got"
# The scan: what host/scrub handed it, kept in ${got}, and nothing found.
scan_rule() {
  # shellcheck disable=SC2016 # expanded when the rule runs
  rule 'run --rm -i --network=none*' '
    cat >/dev/null
    for a in "$@"; do case "$a" in *:/work:Z) w="${a%:/work:Z}" ;; esac; done
    [ -z "${w:-}" ] || { rm -rf "'"${got}"'"; cp -r "$w" "'"${got}"'"; : >"$w/out/actions"; }
    exit "${SCAN_EXIT:-0}"'
}
scan_rule

run "${wb}" scrub bogus
check "scrub takes --check or rotated" 2 err "workbench: host/workbench scrub [--check] [--entropy] | scrub list [--all] [--files] | scrub rotated ID"
run "${wb}" scrub rotated
check "rotated needs an id" 1 err "workbench: which secret? host/workbench scrub rotated ID"
run "${wb}" scrub rotated 3
check "rotated goes to host/scrub" 0 calls "localhost/airlock-base:local python3 -I - rotated 3"
run "${wb}" scrub rotated 3 4 all
check "several, or all" 0 calls "localhost/airlock-base:local python3 -I - rotated 3 4 all"
run "${wb}" scrub list --all --files
check "list goes to host/scrub" 0 calls "localhost/airlock-base:local python3 -I - list --all --files"
WORKBENCH_SCRUB_QUIET=10m run "${wb}" scrub
check "the quiet time is whole minutes" 1 err "workbench: WORKBENCH_SCRUB_QUIET is '10m'; use a whole number of minutes"

# The homes: the host's, the history ones, the extra ones, the workbenches'.
mkdir -p "${HOME}/.claude/projects" "${HOME}/.codex" "${HOME}/.claude-personal" "${HOME}/extra" \
  "${data}/claude" "${data}/claude-personal" "${data}/codex" "${data}/stranger" "${sessions}"
# A Claude Code running on the host (a process started for it), one whose
# process is gone, and a file that is not a process record.
mkdir -p "${HOME}/.claude/sessions"
"${REAL_SLEEP}" 120 &
host_claude=$!
echo '{"pid":1,"sessionId": "aaaaaaaa-1111-4111-8111-111111111111","cwd":"/w"}' >"${HOME}/.claude/sessions/${host_claude}.json"
echo '{"sessionId":"bbbbbbbb-1111-4111-8111-111111111111"}' >"${HOME}/.claude/sessions/999999999.json"
echo '{}' >"${HOME}/.claude/sessions/notes.json"
echo '{"pid":2}' >"${HOME}/.claude-personal/sessions.json"
# Running workbenches: a Claude Code session, a Codex one, the workspace's own.
mkdir -p "${sessions}/calm-yak"
printf 'agent=claude\nworkspace=/w\nclaude_id=cccccccc-1111-4111-8111-111111111111\n' >"${sessions}/calm-yak/session"
# shellcheck disable=SC2016
rule 'ps --filter label=workbench.agent *' 'printf "%s\n" "workbench-claude-s-calm-yak claude calm-yak" "workbench-codex-s-blue-owl codex blue-owl" "workbench-claude-personal-app claude-personal "'
# shellcheck disable=SC2016
rule 'exec workbench-claude-s-calm-yak cat /tmp/airlock-conversation' 'printf "%s\n" dddddddd-1111-4111-8111-111111111111 not-an-id'
rule 'secret inspect --showsecret *' 'echo token-value-0123456789'
echo 'TOKEN=x' >"${HOME}/.env.secrets"
WORKBENCH_ACCOUNTS=personal WORKBENCH_HISTORY="claude-personal=~/.claude-personal" \
  WORKBENCH_SCRUB_HOMES="claude=~/extra" WORKBENCH_SCRUB_SECRETS="~/.env.secrets" WORKBENCH_SCRUB_QUIET=5 \
  run "${wb}" scrub
check "scrub looks at everything, waiting for a running scan" 0 calls "localhost/airlock-base:local python3 -I - scan --manual"
for h in "host claude ${HOME}/.claude" "host codex ${HOME}/.codex" "host claude ${HOME}/.claude-personal" \
  "host claude ${HOME}/extra" "workbench claude ${data}/claude" "workbench claude ${data}/claude-personal" \
  "workbench codex ${data}/codex"; do
  assert "home ${h}" grep -qxF "${h}" "${got}/homes"
done
assert "not a folder that is no workbench's" bash -c "! grep -q stranger '${got}/homes'"
assert "the quiet time, in seconds" grep -qx "quiet 300" "${got}/context"
assert "a host Claude Code that runs holds its folder" grep -qx "busy ${HOME}/.claude" "${got}/context"
assert "and its conversation" grep -qx "live aaaaaaaa-1111-4111-8111-111111111111" "${got}/context"
assert "not one whose process is gone" bash -c "! grep -q bbbbbbbb '${got}/context'"
assert "a running session holds its workbench folder" grep -qx "busy ${data}/claude" "${got}/context"
assert "and its conversation" grep -qx "live cccccccc-1111-4111-8111-111111111111" "${got}/context"
assert "and the ones it moved to" grep -qx "live dddddddd-1111-4111-8111-111111111111" "${got}/context"
assert "a Codex session holds all of its folder" grep -qx "busy-all ${data}/codex" "${got}/context"
assert "so does a workspace workbench" grep -qx "busy-all ${data}/claude-personal" "${got}/context"
assert "and the history folder it mounts" grep -qx "busy-all ${HOME}/.claude-personal" "${got}/context"
assert "the gh token is searched for" test -f "${got}/known/gh-broker-token"
check "read from its podman secret" 0 calls "podman secret inspect --showsecret --format {{.SecretData}} gh-devcontainer"
assert "and the ssh key" test -f "${got}/known/ssh-key"
assert "and the files of secrets named" grep -qx "TOKEN=x" "${got}/known-env/1"
kill "${host_claude}"
wait "${host_claude}" 2>/dev/null || true
unrule
scan_rule

rm -rf "${HOME}/.ssh/devcontainer/id_ed25519" "${PODMAN_STATE}/secret/gh-devcontainer" "${HOME}/.claude/sessions"
run "${wb}" scrub --check
check "check only reports" 0 calls "python3 -I - scan --check --manual"
run "${wb}" scrub --check --entropy
check "and --entropy lists each random looking string" 0 calls "python3 -I - scan --entropy --check --manual"
assert "with no secret the host does not hold" test -z "$(ls "${got}/known")"
touch "${PODMAN_STATE}/secret/gh-devcontainer" "${HOME}/.ssh/devcontainer/id_ed25519"

# At a session's start and end.
app="${WORKBENCH_ROOT}/app"
clone "${app}"
cd "${app}"
run "${wb}" session start claude
refute "off, no scan" "python3 -I - scan"
export WORKBENCH_SCRUB=1
run "${wb}" session start claude
s="$(sed -n 's/^workbench: session \([^ ]*\) (.*/\1/p' "${__scratch}/out" | head -n 1)"
id="$(sed -n 's/^claude_id=//p' "${sessions}/${s}/session")"
check "on, a session start and end scan what changed" 0 calls "python3 -I - scan"
assert "twice" test "$(grep -c 'python3 -I - scan' "${STUB_LOG}")" = 2
assert "the end knows its conversation ended" grep -qx "closed ${id}" "${got}/context"
SCAN_EXIT=1 run "${wb}" session attach "${s}"
check "a failed scan never stops a session" 0 err "workbench: the secret scan failed (above); make scrub-check shows what is left"
check "which runs" 0 calls "workbench-claude-s-${s} claude --resume ${id}"
mkdir "${__scratch}/ctx-tmp"
TMPDIR="${__scratch}/ctx-tmp" WORKBENCH_SCRUB_QUIET=10m run "${wb}" session attach "${s}"
check "nor does a setting the scan refuses" 0 err "workbench: WORKBENCH_SCRUB_QUIET is '10m'; use a whole number of minutes"
check "the session runs all the same" 0 calls "workbench-claude-s-${s} claude --resume ${id}"
check "and ends as it should" 0 out "workbench: session ${s} stopped; make attach-${s} resumes it"
assert "leaving no context file behind" test -z "$(ls "${__scratch}/ctx-tmp")"
container "workbench-claude-s-${s}" running workbench.session="${s}" workbench.agent=claude
rm -rf "${got}"
run "${wb}" session stop "${s}"
check "stop scans too" 0 out "workbench: session ${s} stopped"
assert "with its conversation ended" grep -qx "closed ${id}" "${got}/context"

finish
