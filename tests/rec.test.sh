#!/usr/bin/env bash
#
# Tests for images/workbench/bin/rec. The voice session's pipes are named
# pipes in the scratch directory (WORKBENCH_VOICE_DIR), sox is a stub
# (REC_SOX), and so are dd (which logs each word said to the host as
# `said WORD`), pgrep, ps and sleep. Processes standing in for an earlier
# recording are real sleeps this test starts.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export WORKBENCH_VOICE_DIR="${__scratch}/voice"
export REC_SOX="${__scratch}/bin/sox"
mkdir "${WORKBENCH_VOICE_DIR}"
mic="${WORKBENCH_VOICE_DIR}/mic.1"
ctl="${WORKBENCH_VOICE_DIR}/ctl.1"
mkfifo "${mic}" "${ctl}"
rec=images/workbench/bin/rec
# dd: a word for the host is logged; a read of the microphone succeeds
# DRAIN times, then fails as an empty pipe does.
export DRAIN_LEFT="${__scratch}/drain"
# shellcheck disable=SC2016 # expanded when the stub runs
stub dd '
case "$*" in
  of=*) echo "said $(cat)" >>"${STUB_LOG}" ;;
  if=*)
    left="$(cat "${DRAIN_LEFT}")"
    [ "${left}" -gt 0 ] || exit 1
    echo $((left - 1)) >"${DRAIN_LEFT}" ;;
esac'
# shellcheck disable=SC2016
stub pgrep 'echo "${PGREP_OUT:-}"'
# shellcheck disable=SC2016
stub ps 'echo "${PS_OUT:-}"'
stub sleep
stub sox
echo 2 >"${DRAIN_LEFT}"

unset WORKBENCH_VOICE_MIC WORKBENCH_VOICE_CTL
run "${rec}" --version
check "no voice session: no microphone" 1 err "rec: no microphone in this session"

WORKBENCH_VOICE_MIC=/tmp/mic WORKBENCH_VOICE_CTL="${ctl}" run "${rec}" out.wav
check "a microphone outside the voice directory is not this session's" 1 err "rec: no microphone in this session"

WORKBENCH_VOICE_MIC="${mic}" WORKBENCH_VOICE_CTL=/tmp/ctl run "${rec}" out.wav
check "nor a control pipe outside it" 1 err "rec: no microphone in this session"

export WORKBENCH_VOICE_MIC="${mic}" WORKBENCH_VOICE_CTL="${ctl}"
WORKBENCH_VOICE_MIC="${WORKBENCH_VOICE_DIR}/gone" run "${rec}" out.wav
check "a pipe that is gone ends the session" 1 err "rec: the microphone pipes of this session are gone"

run "${rec}" --version
check "--version is sox's own, in a session" 0 calls "sox --version"
refute "saying nothing to the host" "said"

run "${rec}" -t wav out.wav silence 1 0.1 1%
check "records the microphone with the effects given" 0 calls \
  "sox -t raw -r 16000 -e signed -b 16 -c 1 ${mic} -t wav out.wav silence 1 0.1 1%"
check "telling the host start first" 0 calls "said start"
check "and stop when sox ends" 0 calls "said stop"
check "after dropping what was in the pipe" 0 calls "dd if=${mic} iflag=nonblock of=/dev/null"
check "looking for an earlier recording of the same pipe" 0 calls \
  "pgrep -f -- ^${REC_SOX} .* ${mic//./\\.} "

stub sox 'exit 2'
echo 9 >"${DRAIN_LEFT}"
run "${rec}" out.wav
check "sox's exit status is rec's" 2 calls "said stop"

# An earlier recording: its sox (killed here) and its rec, already gone.
stub sox
"${REAL_SLEEP}" 30 &
old_sox=$!
true &
gone=$!
wait "${gone}"
PGREP_OUT="${old_sox}" PS_OUT="${gone}" run "${rec}" out.wav
check "an earlier recording's sox is stopped first" 0 calls "ps -o ppid= -p ${old_sox}"
check "and this one goes ahead" 0 calls "said start"
killed=0
wait "${old_sox}" 2>/dev/null || killed=$?
assert "killed" test "${killed}" -eq 137

"${REAL_SLEEP}" 30 &
old_rec=$!
PGREP_OUT="${gone}" PS_OUT="${old_rec} $$" run "${rec}" out.wav
check "an earlier rec that does not stop refuses this one" 1 err \
  "rec: an earlier recording of this session is still stopping"
check "after waiting a second for it" 1 calls "sleep 0.1"
refute "before saying start" "said start"
kill "${old_rec}"

# Stopped by a signal, with a sox that ignores TERM and has to be killed.
# shellcheck disable=SC2016 # expanded when the stub runs
stub sox 'trap "" TERM; echo "sox started" >>"${STUB_LOG}"; exec "${REAL_SLEEP}" 30'
signal() {
  local name="${1}" want="${2}" tries=0
  : >"${STUB_LOG}"
  __status=0
  bash "${__repo}/${rec}" out.wav >"${__scratch}/out" 2>"${__scratch}/err" &
  local pid=$!
  until grep -q "sox started" "${STUB_LOG}" || [[ "${tries}" -gt 100 ]]; do
    tries=$((tries + 1))
    "${REAL_SLEEP}" 0.05
  done
  kill "-${name}" "${pid}"
  wait "${pid}" || __status=$?
  check "${name} stops sox and tells the host" "${want}" calls "said stop"
}
signal TERM 143
signal HUP 129
set -o monitor
signal INT 130
set +o monitor

finish
