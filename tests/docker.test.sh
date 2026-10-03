#!/usr/bin/env bash
#
# Tests for images/l2/bin/docker, L2's `docker`. podman-remote and the baked
# tools are stubs.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export L2_BAKED_IMAGES="docker.io/hadolint/hadolint=v2.15.1 docker.io/rhysd/actionlint=1.7.12 docker.io/dotenvlinter/dotenv-linter=4.0.0 docker.io/library/unmapped=1"
stub podman-remote
stub hadolint 'echo "hadolint in $PWD"'
stub actionlint 'echo "actionlint in $PWD"'
stub dotenv-linter
unset CONTAINER_HOST
docker=images/l2/bin/docker
src="${__scratch}/src"
mkdir -p "${src}/sub"

run "${docker}" system info
check "system info says rootless, as L2 is" 0 out '{"SecurityOptions": ["name=rootless"]}'

CONTAINER_HOST=unix:///run/l2-engine/podman.sock run "${docker}" system info
check "with the engine, the engine answers" 0 calls "podman-remote system info"

run "${docker}" ps -a
check "without the engine, only docker run" 125 err "only 'docker run' is supported here, not 'ps'"
check "saying why" 125 err "L2 cannot start containers without the engine"

CONTAINER_HOST=unix:///run/l2-engine/podman.sock run "${docker}" ps -a
check "with the engine, anything else goes to it, as given" 0 calls "podman-remote ps -a"

CONTAINER_HOST=unix:///run/l2-engine/podman.sock \
  run "${docker}" run --rm docker.io/library/postgres:17 postgres
check "and so does an image that is not baked" 0 calls "podman-remote run --rm docker.io/library/postgres:17 postgres"

run "${docker}" run --rm -i -t -it --interactive --tty --init \
  -v "${src}:/src:rw,Z" --volume /a:/b --volume=/c:/d \
  -u 1000 --user 0 -e A=1 --env B=2 --network none --name n --entrypoint x \
  --platform linux/amd64 --security-opt label=disable --pull=never \
  --workdir=/src/sub docker.io/hadolint/hadolint:v2.15.1 hadolint Dockerfile
check "a baked image runs its tool, every option it can ignore ignored" 0 calls "hadolint Dockerfile"
check "in the mounted directory the workdir stands for" 0 out "hadolint in ${src}/sub"

run "${docker}" run -v "${src}:/src" -w /src docker.io/rhysd/actionlint:1.7.12 -color
check "actionlint, at the mount itself" 0 out "actionlint in ${src}"
check "with the image's arguments" 0 calls "actionlint -color"

run "${docker}" run docker.io/dotenvlinter/dotenv-linter@sha256:abc .env
check "dotenv-linter, by digest" 0 calls "dotenv-linter .env"

run "${docker}" run docker.io/hadolint/hadolint:v2.14.0 hadolint Dockerfile
check "a baked image at another version fails" 125 err \
  "hook asks for docker.io/hadolint/hadolint:v2.14.0 but this L2 image carries v2.15.1"

run "${docker}" run -p 80:80 docker.io/hadolint/hadolint
check "an option it does not know" 125 err "unsupported option -p"

run "${docker}" run --rm
check "no image" 125 err "no image given"

run "${docker}" run docker.io/library/postgres
check "an image that is not baked" 125 err "image docker.io/library/postgres is not baked into this L2 image"

run "${docker}" run -v "${src}:/src" -w /elsewhere docker.io/rhysd/actionlint
check "a workdir outside every volume" 125 err "working directory /elsewhere is not inside a mounted volume"

run "${docker}" run docker.io/library/unmapped:1
check "a baked image with no command for it" 125 err "no command mapping for docker.io/library/unmapped"

finish
