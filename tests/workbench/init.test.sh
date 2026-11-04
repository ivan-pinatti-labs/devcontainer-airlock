#!/usr/bin/env bash
#
# host/workbench init: a repository set up for the workbench, its egress
# sets guessed from its files, an L2 image on the published one, and the
# make targets; and run again, everything kept.
# cspell:words produção
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
  "${repo}/infra/variables.tf" "${repo}/Cargo.toml"
printf '%s\n' 'provider "aws" {' '  region = "us-east-1"' '}' >"${repo}/infra/main.tf"
track "${repo}" pyproject.toml web/package.json go.mod Containerfile infra/variables.tf Cargo.toml infra/main.tf
touch "${repo}/.git/ls-files-fails"
run "${wb}" init "${repo}"
check "an index git cannot read" 1 err "workbench: could not list the files git tracks in ${repo}; nothing written"
assert "stops init before it writes anything" test ! -e "${repo}/.devcontainer"
refute "or pulls" "podman pull"
rm "${repo}/.git/ls-files-fails"
rule 'pull --quiet *' 'exit 1'
run "${wb}" init "${repo}"
check "a failed pull of the L2 image" 1 err "workbench: could not pull ${reg}/airlock-l2-aws-devops; nothing written"
assert "writes nothing" test ! -e "${repo}/.devcontainer"
unrule

cd "${repo}/web"
run "${wb}" init
sets="${repo}/.devcontainer/egress-sets"
check "init in the repository you are in" 0 out "workbench: wrote ${sets} (python node golang docker-hub hashicorp aws)"
assert "the sets its files suggest" test "$(grep -v '^#' "${sets}" | paste -sd' ')" = "python node golang docker-hub hashicorp aws"
check "a registry no set covers is pointed out" 0 out \
  "workbench: this repository uses a package registry no egress set covers yet"
check "Terraform on the AWS provider gets the AWS L2 image" 0 out "workbench: wrote ${repo}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2-aws-devops@sha256:mine)"
check "the published one" 0 calls "podman pull --quiet ${reg}/airlock-l2-aws-devops:latest"
assert "pinned by digest" grep -qx "ARG L2_IMAGE=${reg}/airlock-l2-aws-devops@sha256:mine" "${repo}/.devcontainer/l2/Dockerfile"
assert "and says what it carries" grep -qx "# kubeseal, pluto and yq." "${repo}/.devcontainer/l2/Dockerfile"
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
track "${other}" .pre-commit-config.yaml Makefile
run "${wb}" init "${other}"
check "pre-commit hooks need PyPI and npm" 0 out "(python node)"
check "and the shared L2 image" 0 out "workbench: wrote ${other}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2@sha256:mine)"
assert "pinned by digest" grep -qx "ARG L2_IMAGE=${reg}/airlock-l2@sha256:mine" "${other}/.devcontainer/l2/Dockerfile"
assert "saying what that carries" grep -qx "# carries pre-commit, Python, Node, Go and the common linters." "${other}/.devcontainer/l2/Dockerfile"
check "a Makefile gains the include" 0 out "workbench: added the workbench targets to ${other}/Makefile"
assert "after what it had" test "$(head -n 1 "${other}/Makefile")" = "all:"
assert "no registry warning" test -z "$(grep "no egress set covers" "${__scratch}/out")"

atmos="${WORKBENCH_ROOT}/atmos"
clone "${atmos}"
touch "${atmos}/atmos.yaml"
run "${wb}" init "${atmos}"
check "an untracked atmos.yaml selects nothing" 0 out "workbench: wrote ${atmos}/.devcontainer/egress-sets (no sets)"
check "and keeps the shared L2 image" 0 out "workbench: wrote ${atmos}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2@sha256:mine)"
rm -r "${atmos}/.devcontainer" "${atmos}/Makefile"
track "${atmos}" atmos.yaml
run "${wb}" init "${atmos}"
check "an atmos.yaml alone gets HashiCorp and AWS" 0 out "workbench: wrote ${atmos}/.devcontainer/egress-sets (hashicorp aws)"
check "and the AWS L2 image" 0 out "workbench: wrote ${atmos}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2-aws-devops@sha256:mine)"

sourced="${WORKBENCH_ROOT}/sourced"
clone "${sourced}"
printf '%s\n' 'terraform {' '  required_providers {' '    aws = { source = "hashicorp/aws" }' '  }' '}' >"${sourced}/versions.tf"
track "${sourced}" versions.tf
run "${wb}" init "${sourced}"
check "so does one naming hashicorp/aws as a source" 0 out "workbench: wrote ${sourced}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2-aws-devops@sha256:mine)"

gcp="${WORKBENCH_ROOT}/gcp"
clone "${gcp}"
printf '%s\n' 'provider "google" {}' >"${gcp}/main.tf"
printf '%s\n' 'provider "aws" {}' >"${gcp}/scratch.tf"
track "${gcp}" main.tf
run "${wb}" init "${gcp}"
check "Terraform with no AWS signal (an untracked AWS file is not read) gets HashiCorp alone" 0 out "workbench: wrote ${gcp}/.devcontainer/egress-sets (hashicorp)"
check "and the shared L2 image" 0 out "workbench: wrote ${gcp}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2@sha256:mine)"
rm -r "${gcp}/.devcontainer" "${gcp}/Makefile"
track "${gcp}" scratch.tf
run "${wb}" init "${gcp}"
check "until it is tracked" 0 out "workbench: wrote ${gcp}/.devcontainer/egress-sets (hashicorp aws)"
check "and then gets the AWS L2 image" 0 out "workbench: wrote ${gcp}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2-aws-devops@sha256:mine)"

commented="${WORKBENCH_ROOT}/commented"
clone "${commented}"
printf '%s\n' 'provider "google" {}' '# source = "hashicorp/aws"' '  // aws = { source = "registry.terraform.io/hashicorp/aws" }' >"${commented}/main.tf"
track "${commented}" main.tf
run "${wb}" init "${commented}"
check "an AWS provider only in a comment is no AWS signal" 0 out "workbench: wrote ${commented}/.devcontainer/egress-sets (hashicorp)"
check "and keeps the shared L2 image" 0 out "workbench: wrote ${commented}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2@sha256:mine)"

blocked="${WORKBENCH_ROOT}/blocked"
clone "${blocked}"
printf '%s\n' 'provider "google" {}' '/* Moved to the other account:' 'provider "aws" {' '  region = "us-east-1"' '}' '*/' >"${blocked}/main.tf"
track "${blocked}" main.tf
run "${wb}" init "${blocked}"
check "nor one inside a /* */ block" 0 out "workbench: wrote ${blocked}/.devcontainer/egress-sets (hashicorp)"
check "which keeps the shared L2 image" 0 out "workbench: wrote ${blocked}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2@sha256:mine)"

trailing="${WORKBENCH_ROOT}/trailing"
clone "${trailing}"
printf '%s\n' 'provider "google" {} # source = "hashicorp/aws"' 'locals { url = "https://example.com/#x" } // provider "aws"' >"${trailing}/main.tf"
track "${trailing}" main.tf
run "${wb}" init "${trailing}"
check "nor one in a comment after code" 0 out "workbench: wrote ${trailing}/.devcontainer/egress-sets (hashicorp)"
check "with the shared L2 image too" 0 out "workbench: wrote ${trailing}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2@sha256:mine)"

quoted="${WORKBENCH_ROOT}/quoted"
clone "${quoted}"
printf '%s\n' '/* a note */ provider "aws" {' '  profile = "a \" # b"' '}' 'locals { x = "//" } # aws = { source = "hashicorp/google" }' >"${quoted}/main.tf"
track "${quoted}" main.tf
run "${wb}" init "${quoted}"
check "code after a comment, or holding # and // in a string, still counts" 0 out "workbench: wrote ${quoted}/.devcontainer/egress-sets (hashicorp aws)"

accented="${WORKBENCH_ROOT}/accented"
clone "${accented}"
mkdir -p "${accented}/produção"
printf '%s\n' 'provider "aws" {}' >"${accented}/produção/main.tf"
track "${accented}" produção/main.tf
run "${wb}" init "${accented}"
check "a Terraform path outside ASCII is still seen" 0 out "workbench: wrote ${accented}/.devcontainer/egress-sets (hashicorp aws)"
check "and gets the AWS L2 image" 0 out "workbench: wrote ${accented}/.devcontainer/l2/Dockerfile (${reg}/airlock-l2-aws-devops@sha256:mine)"

bare="${WORKBENCH_ROOT}/bare"
clone "${bare}"
run "${wb}" init "${bare}"
check "a repository of nothing known gets no sets" 0 out "(no sets)"

finish
