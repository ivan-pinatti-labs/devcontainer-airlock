#!/usr/bin/env bash
#
# host/workbench build and pull: every image, built here or taken from the
# registry and tagged as the local one.
# shellcheck source=tests/workbench/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

run "${wb}" build
check "build builds the base first" 0 calls "podman build -t localhost/airlock-base:local ${__repo}/images/base"
check "both workbenches from one Dockerfile, by target" 0 calls \
  "podman build --build-arg BASE_IMAGE=localhost/airlock-base:local --target claude -t localhost/airlock-workbench-claude:local ${__repo}/images/workbench"
check "codex too" 0 calls "--target codex -t localhost/airlock-workbench-codex:local ${__repo}/images/workbench"
for i in l2 l2-engine gh-broker egress-proxy mirror-gate; do
  check "and ${i}" 0 calls \
    "podman build --build-arg BASE_IMAGE=localhost/airlock-base:local -t localhost/airlock-${i}:local ${__repo}/images/${i}"
done
WORKBENCH_TAG=next run "${wb}" build
check "under WORKBENCH_TAG" 0 calls "-t localhost/airlock-l2:next"
rule 'build *images/base' 'exit 1'
run "${wb}" build
check "a failed build stops it" 1 calls "podman build -t localhost/airlock-base:local"
refute "before the next one" "--target claude"
unrule

reg=ghcr.io/ivan-pinatti-labs
# The registry's index digest, besides this platform's manifest.
# shellcheck disable=SC2016 # run by the podman stub
rule 'image inspect --format {{range .RepoDigests}}*' \
  'r="${*: -1}"; r="${r%:*}"; printf "%s\n" "${r}@sha256:mine" "${r}@sha256:index" "other/x@sha256:y"'
run "${wb}" pull
for i in base workbench-claude workbench-codex l2 l2-engine gh-broker egress-proxy mirror-gate; do
  check "pull takes ${i}, pinned by its index digest" 0 out "workbench: ${i} ${reg}/airlock-${i}@sha256:index"
done
check "from the registry" 0 calls "podman pull --quiet ${reg}/airlock-l2:latest"
check "tagged as the local image" 0 calls "podman tag ${reg}/airlock-l2:latest localhost/airlock-l2:local"
unrule

WORKBENCH_REGISTRY=registry.example/me WORKBENCH_PULL_TAG=v1 run "${wb}" pull
check "with no index digest, the manifest's" 0 out "workbench: l2 registry.example/me/airlock-l2@sha256:mine"
check "from WORKBENCH_REGISTRY, at WORKBENCH_PULL_TAG" 0 calls "podman pull --quiet registry.example/me/airlock-l2:v1"

for step in 'pull --quiet *' 'tag *' 'image inspect --format {{.Digest}} *'; do
  rule "${step}" 'exit 1'
  run "${wb}" pull
  check "a failed ${step%% *} stops pull" 1 err "workbench: could not pull ${reg}/airlock-base"
  unrule
done
rule 'image inspect --format {{.Digest}} *' 'echo'
run "${wb}" pull
check "so does an image with no digest" 1 err "workbench: could not pull ${reg}/airlock-base"
unrule

finish
