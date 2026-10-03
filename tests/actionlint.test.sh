#!/usr/bin/env bash
#
# Tests for images/l2/bin/actionlint, which drops to nobody when it can.
# The real actionlint is never there: where the script would run it from
# /usr/local/lib/l2, the exec fails with 127, which is what the cases expect.
# shellcheck source=tests/shell-test-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/shell-test-lib.sh"

__bin=/usr/local/lib/l2/actionlint

stub id 'echo 0'
stub setpriv
run images/l2/bin/actionlint -color .github/workflows/x.yml
check "as root with setpriv working, it runs as nobody" 0 calls \
  "setpriv --reuid 65534 --regid 65534 --clear-groups ${__bin} -color .github/workflows/x.yml"

stub setpriv 'exit 1'
run images/l2/bin/actionlint x.yml
check "as root without the capabilities, it runs as is" 127 err "${__bin}"
refute "and never through setpriv" "setpriv --reuid 65534 --regid 65534 --clear-groups ${__bin}"

stub id 'echo 1000'
stub setpriv
run images/l2/bin/actionlint x.yml
check "as another account, it runs as is" 127 err "${__bin}"
refute "without asking setpriv" "setpriv"

finish
