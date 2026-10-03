#!/usr/bin/env bash
#
# Tests for images/l2/engine-bin/podman, the L2 engine's podman client.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

stub podman-remote 'echo "remote ran"'
run images/l2/engine-bin/podman run --rm "an image"
check "hands every argument to podman-remote" 0 calls "podman-remote run --rm an image"
check "and its output back" 0 out "remote ran"

stub podman-remote 'exit 3'
run images/l2/engine-bin/podman ps
check "and its exit status" 3 calls "podman-remote ps"

finish
