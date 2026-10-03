#!/usr/bin/env bash
#
# host/workbench: its usage, the settings file and the commands that only
# print (help, accounts, status).
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

run "${wb}" help
check "help prints the usage" 0 out "host/workbench up [WORKSPACE]   start the workbenches"
check "down to the exit status codes" 0 out "    2  usage error"
run "${wb}" --help
check "so does --help" 0 out "Settings, from the environment or"

run "${wb}" fly
check "an unknown command shows the commands" 2 err "host/workbench install-units    install a systemd user unit"
run "${wb}"
check "so does none" 2 err "host/workbench build            build every image locally"

XDG_RUNTIME_DIR='' run "${wb}" status
check "XDG_RUNTIME_DIR is required" 1 err "XDG_RUNTIME_DIR is not set"

run "${wb}" accounts
assert "no accounts by default" test "$(cat "${__scratch}/out")" = ""

mkdir -p "${HOME}/.config/workbench"
printf '%s\n' '# the accounts' '' 'WORKBENCH_ACCOUNTS=personal,work' >"${HOME}/.config/workbench/config"
run "${wb}" accounts
check "the settings file names them, commas as spaces" 0 out "personal work"
WORKBENCH_ACCOUNTS=other run "${wb}" accounts
check "the environment wins over the file" 0 out "other"
WORKBENCH_ACCOUNTS='' run "${wb}" accounts
assert "even set to nothing" test "$(cat "${__scratch}/out")" = ""

printf 'WORKBENCH_TAG=x\nWORKBENCH_ACCOUNTS=last' >"${HOME}/.config/workbench/config"
run "${wb}" accounts
check "a last line without a newline is read" 0 out "last"

mkdir -p "${__scratch}/xdg/workbench"
echo 'PATH=/evil' >"${__scratch}/xdg/workbench/config"
XDG_CONFIG_HOME="${__scratch}/xdg" run "${wb}" accounts
check "only the known settings are accepted" 1 err \
  "workbench: unknown setting 'PATH' in ${__scratch}/xdg/workbench/config"
rm "${HOME}/.config/workbench/config"

container egress-proxy running
container workbench-claude-app running
container unrelated running
run "${wb}" status
check "status lists the workbench containers" 0 calls \
  "podman ps -a --filter name=^(egress-proxy.*|mirror-.*|gh-broker|devcontainer-ssh-agent|workbench-.*|l2-engine-.*)\$ --format table {{.Names}}\t{{.Status}}\t{{.Image}}"
check "as a table" 0 out "workbench-claude-app"
refute "nothing else is called" "podman rm"
reset_podman

finish
