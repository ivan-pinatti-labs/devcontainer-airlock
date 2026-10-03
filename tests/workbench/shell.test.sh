#!/usr/bin/env bash
#
# host/workbench shell, claude, codex and remote: a session in an agent's
# workbench (started first when it is not running), with the host
# microphone when WORKBENCH_VOICE asks.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

touch "${PODMAN_STATE}/secret/gh-devcontainer"
export WORKBENCH_GH_OWNERS=acme TERM=xterm-256color
app="${WORKBENCH_ROOT}/app"
clone "${app}"
mkdir -p "${app}/src"
cd "${app}/src"

run "${wb}" shell
check "shell needs an agent" 2 err \
  "workbench: which agent? host/workbench shell claude|codex[-<account>] [WORKSPACE] (accounts: none, see WORKBENCH_ACCOUNTS)"
run "${wb}" shell gemini
check "one there is a workbench for" 2 err "workbench: which agent?"
WORKBENCH_ACCOUNTS=personal run "${wb}" shell claude-work
check "an account must be one of WORKBENCH_ACCOUNTS" 2 err "(accounts: personal)"

run "${wb}" shell claude
check "a shell in a workbench not running starts the workspace first" 0 calls \
  "podman run -d --name workbench-claude-app"
check "then opens a shell there, in the folder you are in" 0 calls \
  "podman exec -it -e TERM -e COLORTERM -w ${app}/src workbench-claude-app bash"
assert "up says nothing of it" test ! -s "${__scratch}/out"

run "${wb}" shell codex "${app}" git status
check "a command, in an agent's workbench of the workspace named" 0 calls \
  "podman exec -it -e TERM -e COLORTERM -w ${app}/src workbench-codex-app git status"
refute "already running, nothing starts" "podman run -d"

cd "${WORKBENCH_ROOT}"
run "${wb}" codex "${app}"
check "codex runs codex, at the workspace from outside it" 0 calls \
  "podman exec -it -e TERM -e COLORTERM -w ${app} workbench-codex-app codex"
cd "${app}/src"

WORKBENCH_ACCOUNTS=personal run "${wb}" claude-personal
check "an account's agent starts though up leaves it out" 0 calls \
  "podman run -d --name workbench-claude-personal-app --label workbench.workspace=${app} --label workbench.agent=claude-personal"
check "and runs there" 0 calls "podman exec -it -e TERM -e COLORTERM -w ${app}/src workbench-claude-personal-app claude"

# remote
run "${wb}" remote codex
check "codex has no remote control" 1 err "workbench: codex has no remote control mode"
run "${wb}" remote gemini
check "remote needs an agent there is" 2 err "workbench: which agent? host/workbench remote claude[-<account>] [WORKSPACE]"
gone workbench-claude-app
run "${wb}" remote
check "remote starts the workbench" 0 calls "podman run -d --name workbench-claude-app"
check "and runs claude --remote-control there" 0 calls \
  "podman exec -it -e TERM -e COLORTERM -w ${app}/src workbench-claude-app claude --remote-control"
WORKBENCH_ACCOUNTS=personal run "${wb}" remote claude-personal "${app}"
check "of an account too" 0 calls "workbench-claude-personal-app claude --remote-control"
cd "${WORKBENCH_ROOT}"
WORKBENCH_AGENTS=codex run "${wb}" remote claude "${app}"
check "from outside the workspace, at its root" 0 calls "-w ${app} workbench-claude-app claude --remote-control"
cd "${app}/src"

# Voice.
WORKBENCH_VOICE=loud run "${wb}" claude
check "WORKBENCH_VOICE is 1 or 0" 1 err "workbench: WORKBENCH_VOICE is 'loud'; use 1 or 0"
export WORKBENCH_VOICE=1
WORKBENCH_VOICE=on run "${wb}" codex
refute "codex has no voice" "WORKBENCH_VOICE_MIC"

container workbench-claude-old running workbench.workspace="${app}"
rule 'inspect -f {{.State.Running}} workbench-claude-app' 'echo true'
rule 'inspect --format {{index .Config.Labels "workbench.voice"}} workbench-claude-app' 'echo'
run "${wb}" claude
check "a workbench from before voice gets none" 0 err \
  "workbench: voice: workbench-claude-app was started before voice existed, so this session has no microphone"
check "the session starts without it" 0 calls "podman exec -it -e TERM -e COLORTERM -w ${app}/src workbench-claude-app claude"
unrule

# No pactl on the host: a PATH of links to everything else.
bare_path="${__scratch}/bare_path"
mkdir "${bare_path}"
IFS=: read -ra dirs <<<"${PATH}"
for d in "${dirs[@]}"; do
  for f in "${d}"/*; do
    n="$(basename "${f}")"
    [[ "${n}" = pactl ]] || [[ -e "${bare_path}/${n}" ]] || ln -s "${f}" "${bare_path}/${n}"
  done
done
PATH="${bare_path}" run "${wb}" claude
check "no pactl on the host, no microphone" 0 err \
  "workbench: voice: no pactl on the host (PipeWire's or PulseAudio's tools), so this session has no microphone"
check "the session starts without it" 0 calls "podman exec -it -e TERM -e COLORTERM -w ${app}/src workbench-claude-app claude"

PACTL_PIPE_FAILS=1 run "${wb}" claude
check "a pipe PipeWire refuses" 0 err "workbench: voice: PipeWire refused the microphone pipe, so this session has no microphone"
MKFIFO_FAILS=1 run "${wb}" claude
check "a control pipe that cannot be made" 0 err \
  "workbench: voice: could not make the control pipe, so this session has no microphone"
check "gives the microphone pipe back" 0 calls "pactl unload-module 101"

voice="${XDG_RUNTIME_DIR}/workbench/voice/workbench-claude-app"
# The session says start, start again, something else and stop, stop
# again, and start; then it ends.
# shellcheck disable=SC2016 # run by the podman stub
rule 'exec -it -e TERM -e COLORTERM -e WORKBENCH_VOICE_MIC=*' '
ctl="$(ls "${XDG_RUNTIME_DIR}"/workbench/voice/*/ctl-*)"
exec 4>"${ctl}"
echo start >&4
wait_log "pactl load-module module-loopback" || exit 9
echo start >&4
echo hello >&4
echo stop >&4
wait_log "pactl unload-module 202" || exit 9
echo stop >&4
echo start >&4
wait_log "pactl load-module module-loopback" 2 || exit 9
exit 3'
run "${wb}" claude
check "a voice session ends with the session's status" 3 calls \
  "pactl load-module module-pipe-sink sink_name=airlock_voice_"
check "the microphone pipe in the workbench's voice folder, 16 kHz mono" 3 calls \
  "file=${voice}/mic-"
check "the session gets both pipes" 3 calls \
  "podman exec -it -e TERM -e COLORTERM -e WORKBENCH_VOICE_MIC=/run/workbench-voice/mic-"
check "in the folder you are in" 3 calls "-w ${app}/src workbench-claude-app claude"
check "start routes the microphone" 3 calls "pactl load-module module-loopback source=@DEFAULT_SOURCE@ sink=airlock_voice_"
assert "once per recording" test "$(grep -c 'module-loopback' "${STUB_LOG}")" -eq 2
assert "stop and the end of the session unroute it" test "$(grep -c 'unload-module 202' "${STUB_LOG}")" -eq 2
check "the pipe is given back" 3 calls "pactl unload-module 101"
assert "and both pipes are gone" test -z "$(ls -A "${voice}")"

unrule
rule 'exec -it -e TERM -e COLORTERM -e WORKBENCH_VOICE_MIC=*' 'exit 0'
run "${wb}" claude
check "a voice session that ends well" 0 calls "pactl unload-module 101"
assert "leaves no pipe behind either" test -z "$(ls -A "${voice}")"
unrule
rule 'exec -it -e TERM -e COLORTERM -e WORKBENCH_VOICE_MIC=*' 'exit 3'
run "${wb}" remote
check "remote control with voice" 3 calls "workbench-claude-app claude --remote-control"
check "through the voice session" 3 calls "-e WORKBENCH_VOICE_MIC=/run/workbench-voice/mic-"
unrule

finish
