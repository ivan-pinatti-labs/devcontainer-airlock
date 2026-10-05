#!/usr/bin/env bash
#
# Tests for scripts/build-images.sh. The container runtime (podman or
# docker), curl and jq are stubs: nothing is built, pulled, scanned or
# pushed. The podman stub writes a digest for each push, named after the
# image; curl answers the staging registry's probe once CURL_FAILS failures
# have been used up. A container started with `--entrypoint sh` (the skopeo
# copy) runs its script under dash, a POSIX sh without bash's extensions,
# with the -e variables it was given and skopeo stubbed.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export SARIF_DIR="${__scratch}/sarif" STAGING=localhost:5000
export CURL_FAILS="${__scratch}/curl-fails"
unset PUBLISH_TO REGISTRY_USER REGISTRY_TOKEN TAGS
# shellcheck disable=SC2016 # expanded when the stubs run
stub curl '
left="$(cat "${CURL_FAILS}")"
[ "${left}" -gt 0 ] || exit 0
echo $((left - 1)) >"${CURL_FAILS}"
exit 7'
# shellcheck disable=SC2016
run_sh='case "$*" in *"--entrypoint sh"*)
      while [ "$1" != -c ]; do
        case "$2" in *=*) [ "$1" = -e ] && export "$2" ;; esac
        shift
      done
      shift
      exec dash -c "$@" ;;
    esac'
# shellcheck disable=SC2016
stub podman '
case "$1" in
  push)
    while [ $# -gt 0 ]; do
      [ "$1" = --digestfile ] && file="$2"
      last="$1"
      shift
    done
    name="${last##*/airlock-}"
    printf "sha256:%s" "${name%:ci}" >"${file}" ;;
  run)
    case "$*" in *"--scanners secret"*) [ -z "${SECRET_FOUND:-}" ] || exit 1 ;; esac
    '"${run_sh}"' ;;
esac'
stub skopeo
# shellcheck disable=SC2016
stub docker '
case "$1 $2" in
  "buildx build")
    while [ $# -gt 0 ]; do
      [ "$1" = --metadata-file ] && echo "{}" >"$2"
      shift
    done ;;
  "run "*) '"${run_sh}"' ;;
esac'
# shellcheck disable=SC2016
stub jq 'case "$1" in -r) echo sha256:from-buildx ;; *) echo "{}" ;; esac'
stub sleep
images=(base workbench-claude workbench-codex l2 l2-engine gh-broker egress-proxy mirror-gate podman-nested)

echo 0 >"${CURL_FAILS}"
RUNTIME=podman run scripts/build-images.sh
check "with podman, base builds first" 0 calls "podman build --tls-verify=false -t localhost:5000/airlock-base:ci ${__repo}/images/base"
check "then each image on that exact base" 0 calls \
  "podman build --tls-verify=false --build-arg BASE_IMAGE=localhost:5000/airlock-base@sha256:base -t localhost:5000/airlock-l2:ci ${__repo}/images/l2"
check "a workbench is a target of the workbench Dockerfile" 0 calls \
  "--target codex -t localhost:5000/airlock-workbench-codex:ci ${__repo}/images/workbench"
check "a standalone image builds on its own upstream image, not on base" 0 calls \
  "podman build --tls-verify=false -t localhost:5000/airlock-podman-nested:ci ${__repo}/images/podman-nested"
check "the staging registry already up is used" 0 calls "curl -fsS http://localhost:5000/v2/"
refute "not started again" "build-images-staging"
for name in "${images[@]}"; do
  check "${name} is scanned for secrets by its digest" 0 calls \
    "--scanners secret --exit-code 1 localhost:5000/airlock-${name}@sha256:${name}"
  check "and for fixable critical vulnerabilities" 0 calls \
    "--scanners vuln --severity CRITICAL --ignore-unfixed --exit-code 1 localhost:5000/airlock-${name}@sha256:${name}"
  check "${name}'s report gets its own category" 0 calls "jq --arg id trivy-${name}/"
  assert "and lands in SARIF_DIR" test -f "${SARIF_DIR}/${name}.sarif"
  check "the publishing of ${name} is rehearsed into staging" 0 calls \
    "docker://localhost:5000/airlock-${name}@sha256:${name} docker://localhost:5000/rehearsal/airlock-${name}:latest"
done
check "as a rehearsal" 0 calls "-e REHEARSAL=true"
check "which a POSIX sh copies without TLS" 0 calls \
  "skopeo copy --all --preserve-digests --src-tls-verify=false --dest-tls-verify=false docker://localhost:5000/airlock-base@sha256:base"
refute "and without logging in" "skopeo login --authfile /tmp/auth.json --username"
check "the digests are listed" 0 out "mirror-gate=sha256:mirror-gate"
assert "and kept" grep -qx "base=sha256:base" "${SARIF_DIR}/digests.txt"

echo 2 >"${CURL_FAILS}"
RUNTIME=docker PUBLISH_TO=ghcr.io/example REGISTRY_USER=u REGISTRY_TOKEN=t TAGS="latest v1" \
  run scripts/build-images.sh
check "a staging registry that is not up is started" 0 calls \
  "docker run -d --rm --name build-images-staging -p 5000:5000 docker.io/library/registry:3@"
check "and waited for" 0 calls "sleep 0.2"
check "with docker, buildx builds with attestations" 0 calls \
  "--target claude --push --sbom=true --provenance=mode=max --metadata-file ${SARIF_DIR}/workbench-claude.build.json"
check "its digest read from the metadata" 0 calls "jq -r .\"containerimage.digest\" ${SARIF_DIR}/workbench-claude.build.json"
check "PUBLISH_TO gets each tag" 0 calls \
  "docker://localhost:5000/airlock-l2@sha256:from-buildx docker://ghcr.io/example/airlock-l2:v1"
check "with the credentials in the environment, not on the command line" 0 calls \
  "-e REGISTRY_USER -e REGISTRY_TOKEN -e REHEARSAL=false -e REGISTRY_HOST=ghcr.io"
check "and the copy run by skopeo in the container" 0 calls \
  "--entrypoint sh quay.io/skopeo/stable:"
check "logged in from stdin" 0 calls \
  "skopeo login --authfile /tmp/auth.json --username u --password-stdin ghcr.io"
check "and copied with that login" 0 calls \
  "skopeo copy --all --preserve-digests --src-tls-verify=false --dest-authfile /tmp/auth.json docker://localhost:5000/airlock-l2@sha256:from-buildx"

echo 99 >"${CURL_FAILS}"
RUNTIME=podman run scripts/build-images.sh
check "a staging registry that never comes up fails" 1 err "the staging registry at localhost:5000 did not come up"
refute "before any build" "podman build"

echo 0 >"${CURL_FAILS}"
RUNTIME=podman SECRET_FOUND=1 run scripts/build-images.sh
check "a secret in an image fails the run" 1 calls "--scanners secret --exit-code 1 localhost:5000/airlock-base@"
refute "before anything is published" "skopeo"

RUNTIME=podman PUBLISH_TO=ghcr.io/example REGISTRY_USER=u run scripts/build-images.sh
check "PUBLISH_TO without a token is a usage error" 2 err "PUBLISH_TO needs REGISTRY_USER and REGISTRY_TOKEN"

finish
