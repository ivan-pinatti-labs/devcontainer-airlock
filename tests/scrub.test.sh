#!/usr/bin/env bash
#
# host/scrub: the host side of the secret scan (docs/SCRUB.md). podman is a
# stub, but its `run` runs the real host/scrub-scan with python3, against a
# stand in gitleaks that finds GitHub tokens, so these follow a scan from
# the context host/workbench writes to the files put back. Every secret here
# is made up.
# cspell:words showsecret
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export HOME="${__scratch}/home"
export XDG_STATE_HOME="${__scratch}/state"
mkdir -p "${HOME}"
unset NO_COLOR SCRUB_IMAGE WORKBENCH_TAG
state="${XDG_STATE_HOME}/workbench/scrub"
# Made up, and split so the literal has no token's shape.
pat="ghp_"
pat+="A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" # pragma: allowlist secret
known="known-secret-value-0123456789"
live_id=11111111-2222-4333-8444-555555555555
done_id=3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d41

REAL_CHMOD="$(command -v chmod)"
REAL_CP="$(command -v cp)"
export REAL_CHMOD REAL_CP
# shellcheck disable=SC2016 # expanded when the stubs run
stub flock 'case "$1" in -n) [ -z "${FLOCK_BUSY:-}" ] ;; *) [ -z "${FLOCK_WAIT_FAILS:-}" ] ;; esac'
# shellcheck disable=SC2016
stub chmod 'case "$*" in *--reference*) [ -z "${CHMOD_FAILS:-}" ] || exit 1 ;; esac; exec "${REAL_CHMOD}" "$@"'
# shellcheck disable=SC2016
stub cp 'if [ -n "${CP_FAILS:-}" ]; then case "$*" in *"${CP_FAILS}"*) exit 1 ;; esac; fi; exec "${REAL_CP}" "$@"'

# gitleaks: every GitHub token in the view, as gitleaks reports it.
export FAKE_GITLEAKS="${__scratch}/gitleaks"
cat >"${FAKE_GITLEAKS}" <<'GITLEAKS'
#!/bin/bash
view="$2"
report="${*: -1}"
sep=""
{
  printf '['
  for f in "${view}"/*; do
    for s in $(grep -oE 'ghp_[A-Za-z0-9]{36}' "${f}" | sort -u); do
      printf '%s{"RuleID":"github-pat","File":"%s","Secret":"%s"}' "${sep}" "${f}" "${s}"
      sep=,
    done
  done
  printf ']'
} >"${report}"
GITLEAKS
"${REAL_CHMOD}" +x "${FAKE_GITLEAKS}"

# podman: images, the secret, and `run`, which runs the scan given on
# standard input with its folders where host/scrub mounted them, or the
# shell text in PODMAN_RUN instead (WORK and STATE set, the scan's own
# arguments as "$@").
cat >"${__scratch}/bin/podman" <<'PODMAN'
#!/bin/bash
echo "podman $*" >>"${STUB_LOG}"
case "$1 $2" in
  "image exists")
    case "$3" in *gitleaks*) [ -z "${NO_GITLEAKS:-}" ] ;; *) [ -z "${NO_IMAGE:-}" ] ;; esac
    exit ;;
  "pull -q") [ -z "${PULL_FAILS:-}" ]; exit ;;
  "secret inspect") [ -z "${SECRET_FAILS:-}" ] || exit 125; echo "${SECRET_VALUE}"; exit ;;
esac
a=("$@") work="" state="" rest=()
for i in "${!a[@]}"; do
  case "${a[i]}" in
    -v)
      v="${a[i + 1]}"
      case "${v}" in *:/work:Z) work="${v%:/work:Z}" ;; *:/state:Z) state="${v%:/state:Z}" ;; esac ;;
    python3) rest=("${a[@]:i+3}"); break ;;
  esac
done
script="$(cat)"
if [ -n "${PODMAN_RUN:-}" ]; then
  WORK="${work}" STATE="${state}" bash -c "${PODMAN_RUN}" sh "${rest[@]}"
  exit
fi
# Its fixed folders, where this stub found them mounted.
script="$(sed -e "s|^WORK = Path(\"/work\")|WORK = Path(\"${work}\")|" \
  -e "s|^STATE = Path(\"/state\")|STATE = Path(\"${state}\")|" \
  -e "s|^GITLEAKS = .*|GITLEAKS = \"${FAKE_GITLEAKS}\"|" <<<"${script}")"
exec python3 -I -c "${script}" "${rest[@]}"
PODMAN
"${REAL_CHMOD}" +x "${__scratch}/bin/podman"

# old FILE...: last written an hour ago, so no session is taken to hold it.
old() { touch -d '1 hour ago' "$@"; }
# transcript FILE TEXT: a one line transcript saying TEXT.
transcript() {
  mkdir -p "$(dirname "${1}")"
  printf '{"type":"user","message":{"content":"%s"}}\n' "${2}" >"${1}"
  old "${1}"
}

run host/scrub
check "no command shows the usage" 2 err "host/scrub scan CONTEXT [--full] [--check] [--manual]"
run host/scrub scan "${__scratch}/none"
check "scan needs a context file" 2 err "host/scrub rotated ID"
ctx="${__scratch}/ctx"
: >"${ctx}"
run host/scrub scan "${ctx}" --bogus
check "an unknown option" 2 err "host/scrub scan CONTEXT"
run host/scrub rotated one
check "rotated needs a number" 2 err "host/scrub rotated ID"

# The homes: a host Claude Code folder, a Codex one, a workbench's own,
# one named twice (through a link), one that does not exist.
claude="${HOME}/.claude"
codex="${HOME}/.codex"
wb_home="${__scratch}/data/workbench/claude"
odd="${HOME}/odd%home\\x"
transcript "${claude}/projects/-w/${done_id}.jsonl" "export GITHUB_TOKEN=${pat}"
transcript "${claude}/projects/-w/${live_id}.jsonl" "${pat}"
transcript "${claude}/history.jsonl" "echo ${known} and ssh-key-body-0123456789 and ENV_VALUE_0123456789"
mkdir -p "${claude}/sessions"
echo '{"pid":1}' >"${claude}/sessions/1.json"
transcript "${claude}/sessions/1.key" "${pat}"
transcript "${claude}/projects/-w/clean.jsonl" "nothing"
transcript "${codex}/sessions/2026/rollout.jsonl" "${pat}"
transcript "${codex}/log/codex.log" "${pat}"
transcript "${wb_home}/file-history/${done_id}/a" "${pat}"
transcript "${odd}/projects/x/t.jsonl" "${pat}"
transcript "${HOME}/sp ace/.claude/history.jsonl" "${pat}"
chmod 600 "${claude}/projects/-w/${done_id}.jsonl"
old "${claude}/projects/-w/${done_id}.jsonl"
ln -s "${claude}" "${HOME}/claude-link"
echo "ssh-key-body-0123456789" >"${__scratch}/id_key"
echo "TOKEN=ENV_VALUE_0123456789" >"${__scratch}/env"
cat >"${ctx}" <<EOF
# made by host/workbench
quiet 600
home host claude ${claude}
home host claude ${HOME}/claude-link
home host claude ${HOME}/missing
home host codex ${codex}
home workbench claude ${wb_home}
home host claude ${odd}
home host claude ${HOME}/sp ace/.claude
busy-all ${HOME}/sp ace/.claude
live ${live_id}
secret gh-broker-token gh-devcontainer
known ssh-key ${__scratch}/id_key
known gone ${__scratch}/no-such-key
env ${__scratch}/env
env ${__scratch}/no-such-env
EOF
mtime="$(stat -c %Y "${claude}/projects/-w/${done_id}.jsonl")"
run host/scrub scan "${ctx}"
check "the first scan is not one a session runs" 0 out "scrub: no secret scan has run here yet; make scrub runs the first"
refute "so nothing is looked at" "podman"
assert "nor kept" test ! -e "${state}/index"
SECRET_VALUE="${known}" run host/scrub scan "${ctx}" --full --check
assert "a check does not count as the first scan" test ! -e "${state}/index"
PODMAN_RUN='exit 1' run host/scrub scan "${ctx}" --full
assert "nor does one that failed" test ! -e "${state}/index"
SECRET_VALUE="${known}" run host/scrub scan "${ctx}" --full
check "a scan redacts what no session holds" 0 out \
  "(github-pat ...Q7r8): redacted in "
assert "the token is gone from it" grep -q "REDACTED:github-pat:...Q7r8" "${claude}/projects/-w/${done_id}.jsonl"
assert "which stays valid JSON" python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "${claude}/projects/-w/${done_id}.jsonl"
assert "with its mode" test "$(stat -c %a "${claude}/projects/-w/${done_id}.jsonl")" = 600
assert "and its time" test "$(stat -c %Y "${claude}/projects/-w/${done_id}.jsonl")" = "${mtime}"
check "the podman secret is searched for" 0 out "(gh-broker-token ...6789): redacted in ${claude}/history.jsonl. Rotate it"
check "so is the ssh key" 0 out "(ssh-key ...6789): redacted in ${claude}/history.jsonl. Rotate it"
check "and each KEY=value file" 0 out "(env:TOKEN ...6789): redacted in ${claude}/history.jsonl. Rotate it"
check "the secret is read, never passed as an argument" 0 calls \
  "podman secret inspect --showsecret --format {{.SecretData}} gh-devcontainer"
refute "its value is in no call" "${known}"
check "a known file that is missing is said" 0 err "scrub: no ${__scratch}/no-such-key, so its value is not searched for"
check "so is an env file" 0 err "scrub: no ${__scratch}/no-such-env, so its values are not searched for"
assert "Codex sessions are looked at" grep -q REDACTED "${codex}/sessions/2026/rollout.jsonl"
assert "and its logs" grep -q REDACTED "${codex}/log/codex.log"
assert "a workbench's own folder too" grep -q REDACTED "${wb_home}/file-history/${done_id}/a"
assert "a folder whose name holds % and a backslash" grep -q REDACTED "${odd}/projects/x/t.jsonl"
check "a live conversation is only reported" 0 out \
  "scrub: 1 secret in files a running session holds, redacted once it ends; make scrub-check lists them"
assert "and left as it is" grep -qF "${pat}" "${claude}/projects/-w/${live_id}.jsonl"
assert "so is a folder a session holds whole" grep -qF "${pat}" "${HOME}/sp ace/.claude/history.jsonl"
assert "Claude Code's process records are not looked at" grep -qF "${pat}" "${claude}/sessions/1.key"
check "the gitleaks image is mounted, and the state" 0 calls \
  "--mount type=image,source=$(pinned host/scrub GITLEAKS_IMAGE)"
check "in a container with no network" 0 calls "podman run --rm -i --network=none --cap-drop=all"
check "the scan image" 0 calls "localhost/airlock-base:local python3 -I - scan"
assert "the state is private" test "$(stat -c %a "${state}")" = 700
assert "each home once" test "$(grep -c "/projects/-w/${done_id}.jsonl" "${state}/index")" = 1
assert "what was handled is kept in the index" grep -qF "${claude}/projects/-w/clean.jsonl" "${state}/index"
assert "what waits for its session is not" bash -c "! grep -qF '${live_id}' '${state}/index'"
assert "the ledger is kept" test -f "${state}/ledger.json"
refute "nothing is pulled when the image is there" "podman pull"

SECRET_VALUE="${known}" run host/scrub scan "${ctx}" --manual
check "the next scan looks only at what changed or waits" 0 out "scrub: 2 files looked at"
check "and lists each secret found before and not rotated" 0 out "): found before, first on"
check "with how to mark them rotated" 0 out "once rotated: make scrub-rotated ID=<n>"

# A file some process holds open is live.
transcript "${claude}/projects/-w/open.jsonl" "${pat}"
(
  exec 3<"${claude}/projects/-w/open.jsonl"
  exec "${REAL_SLEEP}" 30
) &
holder=$!
"${REAL_SLEEP}" 0.2
SECRET_FAILS=1 run host/scrub scan "${ctx}"
check "a file held open is only reported" 0 out "scrub: 1 secret in files a running session holds, redacted once it ends"
kill "${holder}"
wait "${holder}" 2>/dev/null || true
check "a secret podman cannot give is said" 0 err "scrub: could not read podman secret gh-devcontainer"
SECRET_FAILS=1 run host/scrub scan "${ctx}"
check "and redacted once it is closed" 0 out "(github-pat ...Q7r8): redacted in ${claude}/projects/-w/open.jsonl. Rotate it"

# Put back only what has not changed meanwhile.
transcript "${claude}/projects/-w/raced.jsonl" "${pat}"
# shellcheck disable=SC2016 # expanded by the stub
PODMAN_RUN='mkdir -p "${WORK}/out/redacted"; : >"${WORK}/out/actions"
  while IFS="$(printf "\t")" read -r n _ _ _ _ p; do
    touch "${p}"; echo x >"${WORK}/out/redacted/${n}"; printf "hit %s secret #10 (raced %s)\nhit %s secret #9 (raced %s)\nreplace %s\ndone %s 2\n" "${n}" "${n}" "${n}" "${n}" "${n}" "${n}" >>"${WORK}/out/actions"
  done <"${WORK}/manifest"' run host/scrub scan "${ctx}"
check "a file written while it was scanned is left" 0 out \
  "scrub: ${claude}/projects/-w/raced.jsonl changed while it was scanned, so it is left for the next scan"
assert "and not said to be redacted" bash -c "! grep -q 'redacted in' '${__scratch}/out'"
check "but said to be still there, with its number" 0 out "secret #9 (raced 1): still there, the next scan tries again, in "
assert "in number order, #9 before #10" test "$(grep -n "secret #9 (raced 1)" "${__scratch}/out" | cut -d: -f1)" -lt "$(grep -n "secret #10 (raced 1)" "${__scratch}/out" | cut -d: -f1)"
assert "as it is" grep -qF "${pat}" "${claude}/projects/-w/raced.jsonl"
assert "and looked at again next time" bash -c "! grep -qF raced.jsonl '${state}/index'"
old "${claude}/projects/-w/raced.jsonl"
CHMOD_FAILS=1 run host/scrub scan "${ctx}"
check "one that cannot be rewritten is said" 0 err \
  "scrub: ${claude}/projects/-w/raced.jsonl could not be rewritten, so it still holds what was found"
assert "and nothing is left beside it" bash -c "! ls '${claude}/projects/-w/' | grep -q scrub-"
CP_FAILS=raced.jsonl run host/scrub scan "${ctx}" --manual
assert "a file that cannot be copied is skipped" bash -c "! grep -q raced '${__scratch}/out'"

# --check and --full.
cp "${state}/index" "${__scratch}/index"
run host/scrub scan "${ctx}" --full --check
check "check looks at every file" 0 out "(github-pat ...Q7r8): would be redacted in "
check "and passes --check on" 0 calls "python3 -I - scan --check"
assert "it changes nothing" grep -qF "${pat}" "${claude}/projects/-w/raced.jsonl"
assert "not even the index" cmp -s "${state}/index" "${__scratch}/index"

# A file already scanned clean, holding a value that only becomes a secret
# the host holds later: a full scan finds it, and if it cannot be rewritten
# its old index row must go, or the next scan would skip it.
transcript "${claude}/projects/-w/later.jsonl" "LATER_VALUE_0123456789"
run host/scrub scan "${ctx}"
assert "it is indexed clean first" grep -qF later.jsonl "${state}/index"
echo "LATER=LATER_VALUE_0123456789" >>"${__scratch}/env"
CHMOD_FAILS=1 run host/scrub scan "${ctx}" --full
check "a full scan finds it" 0 err "scrub: ${claude}/projects/-w/later.jsonl could not be rewritten"
assert "and drops its old index row" bash -c "! grep -qF later.jsonl '${state}/index'"
run host/scrub scan "${ctx}"
assert "so the next scan redacts it" grep -q 'REDACTED:env:LATER' "${claude}/projects/-w/later.jsonl"
# The same for a file a full scan cannot copy.
assert "a file indexed" grep -qF clean.jsonl "${state}/index"
CP_FAILS=clean.jsonl run host/scrub scan "${ctx}" --full
assert "that a full scan cannot copy leaves the index" bash -c "! grep -qF clean.jsonl '${state}/index'"
run host/scrub scan "${ctx}"
assert "and the next scan looks at it again" grep -qF clean.jsonl "${state}/index"

# --dir: only the conversations that ran in one folder or below it.
in_id=22222222-3333-4444-8555-666666666666
out_id=44444444-5555-4666-8777-888888888888
inside="${HOME}/wo/in"
mkdir -p "${inside}"
ran_in() { # FILE CWD: a transcript of a conversation that ran in CWD
  mkdir -p "$(dirname "$1")"
  printf '{"cwd":"%s","type":"user","message":{"content":"%s"}}\n' "$2" "${pat}" >"$1"
}
ran_in "${claude}/projects/-in/${in_id}.jsonl" "${inside}/sub"
ran_in "${claude}/projects/-inside/${out_id}.jsonl" "${inside}side"
transcript "${claude}/file-history/${in_id}/f" "${pat}"
transcript "${claude}/file-history/${out_id}/f" "${pat}"
transcript "${claude}/todos/${in_id}-agent-${in_id}.json" "${pat}"
# A project folder holding no transcript, only its memory: in by its name.
transcript "${claude}/projects/${inside//[^A-Za-z0-9]/-}/memory/m.md" "${pat}"
transcript "${claude}/projects/-other/memory/m.md" "${pat}"
ran_in "${codex}/sessions/2026/11/rollout-in.jsonl" "${inside}"
ran_in "${codex}/sessions/2026/11/rollout-out.jsonl" "${HOME}"
transcript "${claude}/history.jsonl" "${pat}"
old "${claude}"/projects/-in*/*.jsonl "${codex}"/sessions/2026/11/*.jsonl
run host/scrub scan "${ctx}" --dir
check "--dir needs a folder" 2 err "host/scrub scan CONTEXT"
run host/scrub scan "${ctx}" --manual --dir "${inside}/../in"
check "it says which folder" 0 out "(only the conversations that ran in ${inside})"
for f in "projects/-in/${in_id}.jsonl" "file-history/${in_id}/f" "todos/${in_id}-agent-${in_id}.json" \
  "projects/${inside//[^A-Za-z0-9]/-}/memory/m.md"; do
  assert "its conversation's ${f%%/*} is redacted" grep -q REDACTED "${claude}/${f}"
done
assert "so is a Codex session that ran there" grep -q REDACTED "${codex}/sessions/2026/11/rollout-in.jsonl"
for f in "${claude}/projects/-inside/${out_id}.jsonl" "${claude}/file-history/${out_id}/f" \
  "${claude}/projects/-other/memory/m.md" "${codex}/sessions/2026/11/rollout-out.jsonl" "${claude}/history.jsonl"; do
  assert "not ${f#"${HOME}"/}" grep -qF "${pat}" "${f}"
done
run host/scrub scan "${ctx}"
assert "a scan of everything still looks at the rest" grep -q REDACTED "${claude}/projects/-inside/${out_id}.jsonl"
assert "and at the shared files" grep -q REDACTED "${claude}/history.jsonl"

# One scan at a time.
FLOCK_BUSY=1 run host/scrub scan "${ctx}"
check "a scan while another runs is skipped" 0 out "scrub: another scan is running, so this one is skipped"
refute "without starting a container" "podman run"
FLOCK_BUSY=1 run host/scrub scan "${ctx}" --manual
check "a manual one waits" 0 out "scrub: waiting for another scan to finish"
FLOCK_BUSY=1 FLOCK_WAIT_FAILS=1 run host/scrub scan "${ctx}" --manual
check "for fifteen minutes at most" 1 err "scrub: another scan has run for fifteen minutes; try again later"

# Images and failures.
NO_IMAGE=1 run host/scrub scan "${ctx}"
check "the scan image has to be there" 1 err "scrub: no image localhost/airlock-base:local; host/workbench build (or pull) makes it"
SCRUB_IMAGE=localhost/other:x NO_IMAGE=1 run host/scrub scan "${ctx}"
check "host/workbench names it" 1 err "scrub: no image localhost/other:x"
NO_GITLEAKS=1 run host/scrub scan "${ctx}"
check "gitleaks is pulled when missing" 0 calls "podman pull -q $(pinned host/scrub GITLEAKS_IMAGE)"
NO_GITLEAKS=1 PULL_FAILS=1 run host/scrub scan "${ctx}"
check "and stops the scan when it cannot be" 1 err "scrub: could not pull docker.io/zricethezav/gitleaks"
PODMAN_RUN='exit 1' run host/scrub scan "${ctx}"
check "a failed scan changes nothing" 1 err "scrub: nothing was changed"
PODMAN_RUN='true' run host/scrub scan "${ctx}"
check "nor one that wrote no result" 1 err "scrub: the scan wrote no result, so nothing was changed"
printf 'bogus x\n' >"${__scratch}/bad"
run host/scrub scan "${__scratch}/bad"
check "an unknown context line" 1 err "scrub: unknown line in the context: bogus"
printf 'known a/b %s\n' "${__scratch}/id_key" >"${__scratch}/bad"
run host/scrub scan "${__scratch}/bad"
check "a secret's name is its file name, so it is checked" 1 err "scrub: a secret name holds more than A-Za-z0-9_.-: a/b"

# Color on a terminal only.
transcript "${claude}/projects/-w/again.jsonl" "${pat}"
SCRUB_TTY=1 run host/scrub scan "${ctx}"
assert "red on a terminal" grep -q $'\e\\[1;31m' "${__scratch}/out"
transcript "${claude}/projects/-w/again.jsonl" "${pat}"
SCRUB_TTY=1 NO_COLOR=1 run host/scrub scan "${ctx}"
assert "unless NO_COLOR says otherwise" bash -c "! grep -q $'\e' '${__scratch}/out'"

run host/scrub rotated 1
check "rotated marks a secret of the ledger" 0 out "scrub: secret #1 ("
check "in the same container, with only the state" 0 calls "python3 -I - rotated 1"
refute "and nothing to scan" "/work:Z"
run host/scrub rotated 2 3
check "several at once" 0 calls "python3 -I - rotated 2 3"
run host/scrub rotated all
check "or every open one" 0 calls "python3 -I - rotated all"
run host/scrub rotated 2 three
check "each one a number or all" 2 err "host/scrub rotated ID... | all"
run host/scrub list
check "list shows the open secrets, here none" 0 out "scrub: no secret is open; make scrub-list ALL=1 shows the rotated ones"
check "from the same container" 0 calls "python3 -I - list"
refute "never mounting the live state, which a running scan holds" "${state}:/state:Z"
refute "and nothing to scan" "/work:Z"
run host/scrub list --all --files
check "every secret, and where" 0 calls "python3 -I - list --all --files"
check "the rotated ones too, as a table" 0 out "ID  TYPE"
check "with their date" 0 out "rotated 20"
run host/scrub list --bogus
check "only --all and --files" 2 err "host/scrub list [--all] [--files]"

finish
