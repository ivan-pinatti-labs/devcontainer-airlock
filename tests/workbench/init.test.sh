#!/usr/bin/env bash
#
# host/workbench init: a repository set up for the workbench, its egress
# sets guessed from its files, an L2 image on the published one, and the
# make targets; and run again, everything kept.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

reg=ghcr.io/ivan-pinatti-labs
repo="${WORKBENCH_ROOT}/project"
mkdir -p "${__scratch}/plain"

run "${wb}" init "${__scratch}/plain"
check "init needs a git repository" 1 err "workbench: ${__scratch}/plain is not in a git repository"

clone "${repo}"
mkdir -p "${repo}/web" "${repo}/infra"
touch "${repo}/pyproject.toml" "${repo}/web/package.json" "${repo}/go.mod" "${repo}/Containerfile" \
  "${repo}/infra/main.tf" "${repo}/Cargo.toml"
rule 'pull --quiet *' 'exit 1'
run "${wb}" init "${repo}"
check "a failed pull of the L2 image" 1 err "workbench: could not pull ${reg}/airlock-l2; nothing written"
assert "writes nothing" test ! -e "${repo}/.devcontainer"
unrule

cd "${repo}/web"
run "${wb}" init
sets="${repo}/.devcontainer/egress-sets"
check "init in the repository you are in" 0 out "workbench: wrote ${sets} (python node golang docker-hub hashicorp)"
assert "the sets its files suggest" test "$(grep -v '^#' "${sets}" | paste -sd' ')" = "python node golang docker-hub hashicorp"
check "a registry no set covers is pointed out" 0 out \
  "workbench: this repository uses a package registry no egress set covers yet"
check "an L2 image on the published one" 0 out "workbench: wrote ${repo}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2@sha256:mine)"
assert "pinned by digest" grep -qx "ARG L2_IMAGE=${reg}/airlock-l2@sha256:mine" "${repo}/.devcontainer/l2/Dockerfile"
# What init writes holds make and Dockerfile variables, taken literally.
# shellcheck disable=SC2016
assert "built on it" grep -qx 'FROM ${L2_IMAGE}' "${repo}/.devcontainer/l2/Dockerfile"
check "a new Makefile" 0 out "workbench: added the workbench targets to ${repo}/Makefile"
# shellcheck disable=SC2016
assert "whose help lists the workbench targets" grep -qxF "$(printf '\t@$(MAKE) --no-print-directory workbench-help')" "${repo}/Makefile"
# shellcheck disable=SC2016
assert "which it includes" grep -qxF -- '-include $(WORKBENCH_HOME)/host/workbench.mk' "${repo}/Makefile"
# shellcheck disable=SC2016
assert "with a help of its own when the clone is missing" grep -qxF 'ifeq ($(wildcard $(WORKBENCH_HOME)/host/workbench.mk),)' "${repo}/Makefile"
check "and says what next" 0 out "workbench: next: commit these, then make workbench-up (or make claude)"

run "${wb}" init "${repo}"
check "run again, it keeps the sets" 0 out "workbench: kept ${sets}"
check "the L2 image" 0 out "workbench: kept ${repo}/.devcontainer/l2/Dockerfile"
check "and the Makefile" 0 out "workbench: kept ${repo}/Makefile (it already includes the workbench targets)"
refute "with nothing pulled" "podman pull"

other="${WORKBENCH_ROOT}/other"
clone "${other}"
touch "${other}/.pre-commit-config.yaml"
printf '%s\n' 'all:' >"${other}/Makefile"
run "${wb}" init "${other}"
check "pre-commit hooks need PyPI and npm" 0 out "(python node)"
check "a Makefile gains the include" 0 out "workbench: added the workbench targets to ${other}/Makefile"
assert "after what it had" test "$(head -n 1 "${other}/Makefile")" = "all:"
assert "no registry warning" test -z "$(grep "no egress set covers" "${__scratch}/out")"

bare="${WORKBENCH_ROOT}/bare"
clone "${bare}"
run "${wb}" init "${bare}"
check "a repository of nothing known gets no sets" 0 out "(no sets)"

finish
