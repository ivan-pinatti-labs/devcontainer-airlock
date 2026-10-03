# shellcheck shell=bash
#
# Shared by the tests of host/workbench (tests/workbench/*.test.sh), one file
# per command or group of commands. On top of tests/shell-test-lib.sh, it
# stands in for the host: HOME and XDG_RUNTIME_DIR in the scratch directory,
# and stubs for every command host/workbench runs there.
#
# podman is stateful, driven by a directory (PODMAN_STATE) that the tests set
# up and the stub keeps up to date, so a run sees what the calls before it
# did:
#   ctr/NAME/                 a container; `running` in it while it runs,
#                             labels/KEY, nets (one network a line), mounts
#                             (volume names), logs, images/IMAGE (its own
#                             store: the image Id), own-images (what its
#                             `podman images` prints)
#   net/NAME                  a network, holding its subnet
#   vol/NAME, secret/NAME     a volume, a secret
#   img/IMAGE                 a host image, holding its Id; IMAGE.digest and
#                             IMAGE.repo-digests beside it
#   store/IMAGE               an image in the shared L2 store, its Id
#   sets                      what `egress-refresh --list` prints
#   start-logs/NAME           the logs a container started now writes
#                             (default: the line it is ready with)
#   dies/NAME                 a container started now stops at once
#   rules/N                   scripted answers: the first line a pattern
#                             matched against all the arguments, the rest
#                             a body run instead (`rule` below)
# IMAGE is the reference with / : and @ made _. Every call is logged.
#
# git answers from the files in the scratch directory: a checkout is a
# folder with .git (a directory, or a file naming the git directory as a
# linked worktree's does), and what a remote's default branch holds is
# under GIT_DIR/remote-tree/REF/PATH. In GIT_DIR, `branch` holds the
# current branch, `changes` what status prints, `ahead` the commits on
# no remote, and `status-fails` or `log-fails` make those commands fail.

# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/../shell-test-lib.sh"

# The script under test, for the tests that source this.
# shellcheck disable=SC2034
wb=host/workbench
export PODMAN_STATE="${__scratch}/podman"
mkdir -p "${PODMAN_STATE}"/{ctr,net,vol,secret,img,store,start-logs,dies,rules}
printf '%s\n' 'python  PyPI' 'node    npm' 'golang  Go modules' 'docker-hub  Docker Hub' \
  'ghcr  GitHub' 'ubuntu  Ubuntu' 'nodesource  NodeSource' 'alpine  Alpine' 'fedora  Fedora' \
  'hashicorp  HashiCorp' >"${PODMAN_STATE}/sets"

# The host: a home, a runtime folder and the folder workspaces live under.
export HOME="${__scratch}/home"
export XDG_RUNTIME_DIR="${__scratch}/run"
export WORKBENCH_ROOT="${__scratch}/src"
mkdir -p "${HOME}" "${XDG_RUNTIME_DIR}" "${WORKBENCH_ROOT}"
unset XDG_CONFIG_HOME XDG_DATA_HOME WORKBENCH_TAG WORKBENCH_TZ WORKBENCH_VOICE \
  WORKBENCH_AGENTS WORKBENCH_ACCOUNTS WORKBENCH_HISTORY WORKBENCH_GH_OWNERS \
  WORKBENCH_GH_SECRET WORKBENCH_SSH_KEY WORKBENCH_REGISTRY WORKBENCH_MIRROR \
  WORKBENCH_MIRROR_MIN_AGE WORKBENCH_MIRROR_HEAP WORKBENCH_PULL_TAG L2_EXTRA_IMAGES \
  GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE TERM COLORTERM
REAL_MKFIFO="$(command -v mkfifo)"
export REAL_MKFIFO

# rule PATTERN BODY: the next podman call whose arguments match PATTERN (a
# shell pattern) runs BODY instead, and exits with its status. BODY sees the
# arguments as "$@", and its own file as ${rule}, to remove itself when it
# should answer only once.
__rules=0
rule() {
  __rules=$((__rules + 1))
  printf '%s\n%s\n' "${1}" "${2}" >"${PODMAN_STATE}/rules/$(printf '%03d' "${__rules}")"
}
# unrule: no scripted answers any more.
unrule() { rm -f "${PODMAN_STATE}"/rules/*; }

# container NAME [running] [LABEL=VALUE...]: a container that exists.
container() {
  local d="${PODMAN_STATE}/ctr/${1}" l
  shift
  mkdir -p "${d}/labels"
  for l in "$@"; do
    case "${l}" in
      running) touch "${d}/running" ;;
      *) printf '%s' "${l#*=}" >"${d}/labels/${l%%=*}" ;;
    esac
  done
}
# gone NAME...: those containers no longer exist.
gone() {
  local n
  for n in "$@"; do rm -rf "${PODMAN_STATE}/ctr/${n}"; done
}
# reset_podman: no containers, networks, volumes, images or rules.
reset_podman() {
  rm -rf "${PODMAN_STATE}"/{ctr,net,vol,secret,img,store,start-logs,dies,rules}/*
}
# image REF ID: a host image.
image() { printf '%s' "${2}" >"${PODMAN_STATE}/img/$(printf '%s' "${1}" | tr '/:@' '___')"; }

# A checkout: clone DIR makes DIR a main clone; worktree MAIN NAME adds a
# linked worktree at MAIN/.claude/worktrees/NAME and prints its path.
clone() { mkdir -p "${1}/.git"; }
worktree() {
  local wt="${1}/.claude/worktrees/${2}"
  mkdir -p "${1}/.git/worktrees/${2}" "${wt}"
  echo ../.. >"${1}/.git/worktrees/${2}/common-dir"
  echo "gitdir: ${1}/.git/worktrees/${2}" >"${wt}/.git"
  echo "${wt}"
}
# on_remote CLONE REF PATH [TEXT]: PATH on the remote's REF, as CLONE knows it.
on_remote() {
  mkdir -p "$(dirname "${1}/.git/remote-tree/${2}/${3}")"
  if [[ -n "${4:-}" ]]; then echo "${4}"; fi >"${1}/.git/remote-tree/${2}/${3}"
}

# wait_log TEXT [COUNT]: until TEXT is in the call log COUNT times (default
# once), for up to ten seconds.
wait_log() {
  for _ in $(seq 1 200); do
    [[ "$(grep -cF -- "${1}" "${STUB_LOG}")" -lt "${2:-1}" ]] || return 0
    "${REAL_SLEEP}" 0.05
  done
  echo "wait_log: '${1}' never came" >&2
  return 1
}
export -f wait_log

# shellcheck disable=SC2016 # expanded when the stubs run
stub git '
dir="${PWD}"
if [ "$1" = -C ]; then dir="$2"; shift 2; fi
if [ "$1" = config ]; then
  case "$3" in
    user.name) v="${GIT_USER_NAME:-}" ;;
    *) v="${GIT_USER_EMAIL:-}" ;;
  esac
  [ -n "${v}" ] || exit 1
  echo "${v}"
  exit 0
fi
top="$(cd "${dir}" 2>/dev/null && pwd)" || exit 128
while [ ! -e "${top}/.git" ]; do
  [ "${top}" != / ] || exit 128
  top="$(dirname "${top}")"
done
gd="${top}/.git"
if [ -f "${gd}" ]; then gd="$(sed -n "s/^gitdir: //p" "${gd}")"; fi
common="${gd}"
if [ -f "${gd}/common-dir" ]; then common="$(cd "${gd}/$(cat "${gd}/common-dir")" && pwd)"; fi
case "$*" in
  "rev-parse --show-toplevel") echo "${top}" ;;
  "rev-parse --git-dir" | "rev-parse --path-format=absolute --git-dir") echo "${gd}" ;;
  "rev-parse --path-format=absolute --git-common-dir") echo "${common}" ;;
  "cat-file -e "*) [ -f "${common}/remote-tree/${3%%:*}/${3#*:}" ] ;;
  "show "*) cat "${common}/remote-tree/${2%%:*}/${2#*:}" 2>/dev/null || exit 128 ;;
  ls-files) cd "${top}" && find . -path ./.git -prune -o -type f -print | sed "s|^\./||" | sort ;;
  "status --porcelain") [ ! -f "${gd}/status-fails" ] || exit 1; cat "${gd}/changes" 2>/dev/null || true ;;
  "log --branches --not --remotes --oneline") [ ! -f "${gd}/log-fails" ] || exit 1; cat "${gd}/ahead" 2>/dev/null || true ;;
  "branch --show-current") cat "${gd}/branch" 2>/dev/null || true ;;
  *) exit 1 ;;
esac'

# shellcheck disable=SC2016
stub flock '[ -z "${FLOCK_FAILS:-}" ]'
# shellcheck disable=SC2016
stub systemctl '[ -z "${SYSTEMCTL_FAILS:-}" ]'
# pactl: the microphone pipe is module 101 unless PACTL_PIPE_FAILS, each
# loopback 202.
# shellcheck disable=SC2016
stub pactl '
case "$*" in
  "load-module module-pipe-sink"*) [ -z "${PACTL_PIPE_FAILS:-}" ] || exit 1; echo 101 ;;
  "load-module module-loopback"*) echo 202 ;;
esac'
# shellcheck disable=SC2016
stub mkfifo '[ -z "${MKFIFO_FAILS:-}" ] || exit 1; exec "${REAL_MKFIFO}" "$@"'
# sleep is short, and not logged: the lock holder polls with it.
# shellcheck disable=SC2016
printf '#!/bin/bash\nexec "${REAL_SLEEP}" 0.01\n' >"${__scratch}/bin/sleep"
chmod +x "${__scratch}/bin/sleep"

cat >"${__scratch}/bin/podman" <<'PODMAN'
#!/bin/bash
echo "podman $*" >>"${STUB_LOG}"
S="${PODMAN_STATE}"
for rule in "${S}"/rules/*; do
  [ -f "${rule}" ] || continue
  pattern="$(head -n 1 "${rule}")"
  # shellcheck disable=SC2254 # the pattern is a pattern
  case "$*" in
    ${pattern})
      body="$(tail -n +2 "${rule}")"
      eval "${body}"
      exit $? ;;
  esac
done
key() { printf '%s' "$1" | tr '/:@' '___'; }
C="${S}/ctr"
# Arguments that are not options, in order (an option's value skipped).
plain() {
  local a skip=""
  for a in "$@"; do
    if [ -n "${skip}" ]; then skip=""; continue; fi
    case "${a}" in
      --format | -f | --filter | --ip | --subnet | -t | --tail) skip=1 ;;
      -*) ;;
      *) echo "${a}" ;;
    esac
  done
}
# The image commands of a store in folder $1, for arguments $2...
store() {
  local dir="$1"
  shift
  mkdir -p "${dir}"
  case "$1 $2" in
    "images "*) cat "${dir}/../own-images" 2>/dev/null || true ;;
    "image inspect") cat "${dir}/$(key "$5")" 2>/dev/null || exit 125 ;;
    "load --quiet")
      read -r ref id
      printf '%s' "${id}" >"${dir}/$(key "${ref}")" ;;
    "untag "*) echo "untagged $3 $4" >>"${dir}/../untagged" ;;
  esac
}
case "$1" in
  inspect)
    name="$4"
    [ -d "${C}/${name}" ] || exit 125
    case "$3" in
      *State.Running*) if [ -f "${C}/${name}/running" ]; then echo true; else echo false; fi ;;
      *Config.Labels*)
        l="${3#*\"}"
        cat "${C}/${name}/labels/${l%%\"*}" 2>/dev/null
        echo ;;
      *Networks*) tr '\n' ' ' <"${C}/${name}/nets" 2>/dev/null; echo ;;
      *Mounts*) tr '\n' ' ' <"${C}/${name}/mounts" 2>/dev/null; echo ;;
    esac ;;
  ps)
    filter=""
    if [ "$3" = --filter ]; then filter="$4"; fi
    case "$*" in *table*) printf 'NAMES\tSTATUS\tIMAGE\n' ;; esac
    for d in "${C}"/*/; do
      [ -d "${d}" ] || continue
      n="$(basename "${d}")"
      case "${filter}" in
        label=*)
          k="${filter#label=}"
          [ "$(cat "${d}/labels/${k%%=*}" 2>/dev/null)" = "${k#*=}" ] || continue ;;
        name=*)
          re="${filter#name=}"
          grep -qE "${re}" <<<"${n}" || continue ;;
      esac
      case "$*" in
        *table*) printf '%s\tUp\timage\n' "${n}" ;;
        *) echo "${n}" ;;
      esac
    done ;;
  run)
    detach="" name="" args=("$@")
    for i in "${!args[@]}"; do
      a="${args[i]}" v="${args[i + 1]:-}"
      case "${a}" in
        -d) detach=1 ;;
        --name) name="${v}" ;;
      esac
    done
    if [ -n "${detach}" ]; then
      d="${C}/${name}"
      mkdir -p "${d}/labels"
      : >"${d}/nets"
      : >"${d}/mounts"
      for i in "${!args[@]}"; do
        a="${args[i]}" v="${args[i + 1]:-}"
        case "${a}" in
          --label) printf '%s' "${v#*=}" >"${d}/labels/${v%%=*}" ;;
          --network) echo "${v%%:*}" >>"${d}/nets" ;;
          -v)
            case "${v}" in
              /*:/run/l2-engine:Z)
                [ -n "${NO_ENGINE_SOCKET:-}" ] \
                  || python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "${v%%:*}/podman.sock" ;;
              /*) ;;
              *) echo "${v%%:*}" >>"${d}/mounts" ;;
            esac ;;
        esac
      done
      if [ -f "${S}/start-logs/${name}" ]; then
        cp "${S}/start-logs/${name}" "${d}/logs"
      elif [ -f "${S}/dies/${name}" ]; then
        echo "${name}: failed" >"${d}/logs"
      else
        case "${name}" in
          egress-proxy) echo "Accepting HTTP Socket connections" >"${d}/logs" ;;
          mirror-gate) echo "mirror-gate: listening on 8081" >"${d}/logs" ;;
        esac
      fi
      [ -f "${S}/dies/${name}" ] || touch "${d}/running"
      exit 0
    fi
    case "$*" in
      *"egress-refresh --list"*) cat "${S}/sets" ;;
      *api.github.com/meta*) echo "github.com ssh-ed25519 KEY" ;;
      *"--entrypoint podman "*)
        sub=("$@")
        for i in "${!sub[@]}"; do
          if [ "${sub[i]}" = --entrypoint ]; then sub=("${sub[@]:i+3}"); break; fi
        done
        store "${S}/store" "${sub[@]}" ;;
    esac ;;
  rm)
    for n in $(plain "${@:2}"); do rm -rf "${C:?}/${n}"; done ;;
  network)
    case "$2" in
      exists) [ -f "${S}/net/$3" ] ;;
      ls) ls "${S}/net" ;;
      inspect)
        for n in $(plain "${@:3}"); do
          case "$*" in
            *"}} {{end"*) printf '%s \n' "$(cat "${S}/net/${n}")" ;;
            *) cat "${S}/net/${n}"; echo ;;
          esac
        done ;;
      create) printf '%s' "$6" >"${S}/net/$7" ;;
      rm) [ -f "${S}/net/$3" ] || exit 1; rm "${S}/net/$3" ;;
      connect) echo "$5" >>"${C}/$6/nets" ;;
      disconnect) sed -i "/^$4\$/d" "${C}/$5/nets" ;;
    esac ;;
  volume)
    case "$2" in
      exists) [ -f "${S}/vol/$3" ] ;;
      create) touch "${S}/vol/$3" ;;
      rm) [ -f "${S}/vol/$3" ] || exit 1; rm "${S}/vol/$3" ;;
    esac ;;
  # In the user namespace, which here is the scratch directory's own.
  unshare) shift; "$@" ;;
  secret) [ -f "${S}/secret/$3" ] ;;
  image)
    f="${S}/img/$(key "${*: -1}")"
    case "$2 $4" in
      "exists "*) [ -f "${f}" ] ;;
      *.Id*) cat "${f}" 2>/dev/null || exit 125 ;;
      *.Digest*) cat "${f}.digest" ;;
      *RepoDigests*) cat "${f}.repo-digests" 2>/dev/null || true ;;
    esac ;;
  pull)
    f="${S}/img/$(key "$3")"
    printf 'sha256:pulled' >"${f}"
    printf 'sha256:mine' >"${f}.digest" ;;
  tag) cp "${S}/img/$(key "$2")" "${S}/img/$(key "$3")" ;;
  build)
    for i in $(seq 1 $#); do
      if [ "${!i}" = -t ]; then
        j=$((i + 1))
        printf 'sha256:built' >"${S}/img/$(key "${!j}")"
      fi
    done ;;
  save) echo "$2 $(cat "${S}/img/$(key "$2")")" ;;
  logs) cat "${C}/${*: -1}/logs" 2>/dev/null || true ;;
  exec)
    shift
    while true; do
      case "$1" in
        -e | -w) shift 2 ;;
        -*) shift ;;
        *) break ;;
      esac
    done
    name="$1"
    shift
    case "$1" in
      podman) shift; store "${C}/${name}/images" "$@" ;;
    esac ;;
esac
PODMAN
chmod +x "${__scratch}/bin/podman"
