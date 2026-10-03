#!/usr/bin/env bash
#
# Tests for images/l2-engine/containers/crun-without-masked-paths. REAL_CRUN
# is a stub instead of /usr/bin/crun; jq is the stub that rewrites the spec.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

export REAL_CRUN="${__scratch}/bin/crun"
stub crun 'echo "crun ran"; exit 4'
stub jq 'echo "{\"linux\": {\"maskedPaths\": []}}"'
bundle="${__scratch}/bundle"
mkdir "${bundle}"
spec() { echo '{"linux": {"maskedPaths": ["/proc/acpi"]}}' >"${bundle}/config.json"; }

spec
run images/l2-engine/containers/crun-without-masked-paths --root /run/crun create --bundle "${bundle}" ctr
check "--bundle DIR: the spec is rewritten" 4 calls "jq .linux.maskedPaths = [] ${bundle}/config.json"
check "then crun runs with the same arguments, its status kept" 4 calls \
  "crun --root /run/crun create --bundle ${bundle} ctr"
assert "the rewritten spec replaces the old one" grep -qF '"maskedPaths": []' "${bundle}/config.json"

spec
run images/l2-engine/containers/crun-without-masked-paths create -b "${bundle}" ctr
check "-b DIR as well" 4 calls "jq .linux.maskedPaths = [] ${bundle}/config.json"

spec
run images/l2-engine/containers/crun-without-masked-paths create "--bundle=${bundle}" ctr
check "--bundle=DIR as well" 4 calls "jq .linux.maskedPaths = [] ${bundle}/config.json"

run images/l2-engine/containers/crun-without-masked-paths delete ctr
check "no bundle: straight to crun" 4 out "crun ran"
refute "without touching a spec" "jq"

run images/l2-engine/containers/crun-without-masked-paths create --bundle "${__scratch}/none" ctr
refute "a bundle without a spec is left alone" "jq"

spec
stub jq 'exit 5'
run images/l2-engine/containers/crun-without-masked-paths create --bundle "${bundle}" ctr
check "a spec that cannot be rewritten fails" 1 err "could not rewrite ${bundle}/config.json"
refute "before crun" "crun create"
assert "and leaves no partial spec" test ! -e "${bundle}/config.json.unmasked"
assert "nor a changed one" grep -q /proc/acpi "${bundle}/config.json"

finish
