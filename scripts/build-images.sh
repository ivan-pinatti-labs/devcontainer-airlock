#!/bin/bash
# Build, scan and (on request) publish every image, base first and the rest
# on top of that exact base build, except the few that build on an upstream
# image of their own (STANDALONE). The CI workflow runs this, and so can you,
# with rootless podman, which is how its logic is tested before CI ever sees
# it.
#
# Every image is pushed to a throwaway registry on this machine and scanned
# from there, and publishing copies exactly those manifests, by digest, to
# the real registry. What gets published is therefore what was scanned, not
# a second build of it.
#
# Usage:
#   scripts/build-images.sh
#
# Environment:
#   RUNTIME     docker or podman. Default: podman if it is on PATH.
#   STAGING     the throwaway registry, which this starts if it is not
#               reachable. Default: localhost:5000
#   PUBLISH_TO  when set (for example ghcr.io/ivan-pinatti-labs), copy the
#               scanned images there. Needs REGISTRY_USER and REGISTRY_TOKEN.
#               When unset, the same copy is rehearsed into the throwaway
#               registry instead, so a pull request runs the publishing
#               step too, not only main.
#   TAGS        extra tags to publish besides the digest, space separated.
#               Default: latest
#   SARIF_DIR   where to write one vulnerability report per image, for code
#               scanning. Default: a temporary directory.
#
# Gates, the same for every image:
#   - a secret in any layer fails the run
#   - a critical vulnerability with a fix available fails the run
#   - everything else is reported (SARIF_DIR), not blocking
#
# Exit status codes:
#   1    a gate failed, or a build did
#   2    usage error

set -o errexit
set -o nounset
set -o pipefail

# workbench-<agent> is a target of images/workbench/Dockerfile; the two share
# every layer below their agent stage, so the second build reuses the first.
IMAGES=(workbench-claude workbench-codex l2 l2-engine gh-broker egress-proxy mirror-gate)
# Built on their own pinned upstream image rather than on base, so they are
# given no BASE_IMAGE: podman-nested is quay.io/podman/stable with a test
# runner's tools on top.
STANDALONE=(podman-nested)
PREFIX=airlock

# Pinned by digest; Renovate keeps them current.
# renovate: datasource=docker depName=docker.io/library/registry
REGISTRY_IMAGE=docker.io/library/registry:3@sha256:21078ecfcba8a0cd7e1a384f75b3509899661dd21823bf1da21b5a4674aaceb9
# renovate: datasource=docker depName=docker.io/aquasec/trivy
TRIVY_IMAGE=docker.io/aquasec/trivy:0.74.0@sha256:ee940acbf1f58ebadb42d01434ce4609530bf1b52536afbd1eee66cd7123c5c9
# renovate: datasource=docker depName=quay.io/skopeo/stable
SKOPEO_IMAGE=quay.io/skopeo/stable:v1.21.0@sha256:8342e1e8d7b12d0bd3bf53ce8f4fde7ed876277aa8e4345f53937a505309e6b5

RUNTIME="${RUNTIME:-$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)}"
STAGING="${STAGING:-localhost:5000}"
PUBLISH_TO="${PUBLISH_TO:-}"
TAGS="${TAGS:-latest}"
SARIF_DIR="${SARIF_DIR:-$(mktemp -d)}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

log() { printf '\n=== %s\n' "$*"; }

staging_up() {
  if curl -fsS "http://${STAGING}/v2/" >/dev/null 2>&1; then
    return
  fi
  log "starting a throwaway registry at ${STAGING}"
  "${RUNTIME}" run -d --rm --name build-images-staging \
    -p "${STAGING##*:}:5000" "${REGISTRY_IMAGE}" >/dev/null
  for _ in $(seq 1 50); do
    curl -fsS "http://${STAGING}/v2/" >/dev/null 2>&1 && return
    sleep 0.2
  done
  echo "build-images: the staging registry at ${STAGING} did not come up" >&2
  exit 1
}

# Build one image, push it to staging, print its digest.
build() {
  local name="$1" base="${2:-}" ref dir="$1" args=()
  ref="${STAGING}/${PREFIX}-${name}:ci"
  [[ -n "${base}" ]] && args+=(--build-arg "BASE_IMAGE=${base}")
  case "${name}" in
  workbench-*)
    dir=workbench
    args+=(--target "${name#workbench-}")
    ;;
  esac
  if [[ "${RUNTIME}" = docker ]]; then
    # buildx, for the SBOM and provenance attestations, which travel with the
    # image when it is copied on to the real registry.
    docker buildx build "${args[@]}" --push --sbom=true --provenance=mode=max \
      --metadata-file "${SARIF_DIR}/${name}.build.json" \
      -t "${ref}" "${HERE}/images/${dir}" >&2
    jq -r '."containerimage.digest"' "${SARIF_DIR}/${name}.build.json"
  else
    podman build --tls-verify=false "${args[@]}" -t "${ref}" "${HERE}/images/${dir}" >&2
    podman push --tls-verify=false --digestfile "${SARIF_DIR}/${name}.digest" "${ref}" >&2
    cat "${SARIF_DIR}/${name}.digest"
  fi
}

trivy() {
  "${RUNTIME}" run --rm --network host \
    -v build-images-trivy-cache:/root/.cache/trivy \
    -v "${SARIF_DIR}:/out" \
    "${TRIVY_IMAGE}" image --insecure --skip-version-check --quiet "$@"
}

scan() {
  local name="$1" ref="$2"
  log "scanning ${name}: secrets (blocking)"
  trivy --scanners secret --exit-code 1 "${ref}"
  log "scanning ${name}: vulnerabilities (reported)"
  trivy --scanners vuln --severity CRITICAL,HIGH --format sarif \
    --output "/out/${name}.sarif" "${ref}"
  # One category per image in code scanning, rather than six reports
  # overwriting each other under one.
  jq --arg id "trivy-${name}/" '.runs[].automationDetails = {id: $id}' \
    "${SARIF_DIR}/${name}.sarif" >"${SARIF_DIR}/${name}.sarif.tmp"
  mv "${SARIF_DIR}/${name}.sarif.tmp" "${SARIF_DIR}/${name}.sarif"
  log "scanning ${name}: fixable critical vulnerabilities (blocking)"
  trivy --scanners vuln --severity CRITICAL --ignore-unfixed --exit-code 1 "${ref}"
}

# Copy one scanned image, by digest, to TO. The token never appears on a
# command line, where any process listing would show it: it reaches the
# container in the environment, and skopeo reads it on stdin into an auth
# file only the container can read, which goes when the container does. The
# skopeo image's entrypoint is skopeo itself, so the shell is named as the
# entrypoint. TO is the throwaway registry for a rehearsal, which is plain
# http and has no credentials. The script the container runs is a here
# document rather than a quoted argument, which kcov would count as lines of
# this script that never run. It is POSIX sh, not bash: the image's sh is
# what runs it.
publish() {
  local name="$1" digest="$2" to="$3" rehearsal=false tag copy
  [[ "${to}" = "${STAGING}/rehearsal" ]] && rehearsal=true
  IFS= read -r -d '' copy <<'SH' || true
set -e
if [ "${REHEARSAL}" = true ]; then
  set -- --dest-tls-verify=false "$@"
else
  umask 077
  printf "%s" "${REGISTRY_TOKEN}" | skopeo login --authfile /tmp/auth.json \
    --username "${REGISTRY_USER}" --password-stdin "${REGISTRY_HOST}" >/dev/null
  set -- --dest-authfile /tmp/auth.json "$@"
fi
exec skopeo copy --all --preserve-digests --src-tls-verify=false "$@"
SH
  for tag in ${TAGS}; do
    log "publishing ${name} ${digest} as ${to}/${PREFIX}-${name}:${tag}"
    "${RUNTIME}" run --rm --network host \
      -e REGISTRY_USER -e REGISTRY_TOKEN -e REHEARSAL="${rehearsal}" \
      -e REGISTRY_HOST="${to%%/*}" \
      --entrypoint sh "${SKOPEO_IMAGE}" \
      -c "${copy}" \
      publish \
      "docker://${STAGING}/${PREFIX}-${name}@${digest}" \
      "docker://${to}/${PREFIX}-${name}:${tag}"
  done
}

main() {
  local digests=() name digest base_ref
  if [[ -n "${PUBLISH_TO}" ]] && { [[ -z "${REGISTRY_USER:-}" ]] || [[ -z "${REGISTRY_TOKEN:-}" ]]; }; then
    echo "build-images: PUBLISH_TO needs REGISTRY_USER and REGISTRY_TOKEN" >&2
    exit 2
  fi
  mkdir -p "${SARIF_DIR}"
  staging_up

  log "building base"
  digest="$(build base)"
  digests+=("base=${digest}")
  base_ref="${STAGING}/${PREFIX}-base@${digest}"

  for name in "${IMAGES[@]}"; do
    log "building ${name} on ${base_ref}"
    digests+=("${name}=$(build "${name}" "${base_ref}")")
  done

  for name in "${STANDALONE[@]}"; do
    log "building ${name}"
    digests+=("${name}=$(build "${name}")")
  done

  for entry in "${digests[@]}"; do
    scan "${entry%%=*}" "${STAGING}/${PREFIX}-${entry%%=*}@${entry#*=}"
  done

  for entry in "${digests[@]}"; do
    publish "${entry%%=*}" "${entry#*=}" "${PUBLISH_TO:-${STAGING}/rehearsal}"
  done

  log "digests"
  printf '%s\n' "${digests[@]}" | tee "${SARIF_DIR}/digests.txt"
}

main "$@"
