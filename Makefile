# Tasks for this repository: the workbench targets every repository in the
# organization gets (host/workbench.mk), used here straight from this clone,
# and the coverage gate of the code this repository writes.
#
# checkmake reads only the first physical line of a .PHONY declaration, so
# every .PHONY here is written on one line.
.PHONY: all help coverage

# Bare `make` shows the target list rather than doing something surprising.
all: help

include host/workbench.mk

help:
	@printf '%s\n' 'Usage:' '  make <target>' '' \
		'Targets:' \
		'  coverage                    Python, shell and JavaScript coverage in containers, 100% or fail.' \
		''
	@$(MAKE) --no-print-directory workbench-help

# Coverage of the code this repository writes, held at 100%: the Python
# (lines and branches, .coveragerc) under coverage.py, the JavaScript (lines,
# branches and functions) under node's own test runner, and the shell scripts
# in SHELL_SCRIPTS (lines; kcov reports no branches for shell) under kcov.
# Writes the reports SonarQube Cloud reads into $(COVERAGE_DIR):
# coverage.xml (Python), lcov.info (JavaScript) and shell.xml (shell, in
# SonarQube's generic format), and fails if any language is under 100%.
# .github/workflows/sonarqube.yml runs this, and so does the `coverage`
# pre-push hook.
#
# Every script without an extension is named here by its path: no tool finds
# those on its own, and a script no test ran would otherwise be left out of
# its report rather than counted as uncovered. scripts/kcov_to_sonar.py holds
# each list to 100% of its lines, Python included.
#
# The tools run in containers that cannot see this checkout. The files git
# would commit (tracked, plus new ones not ignored) go in on standard input
# as a tar stream, and the only host path a container gets is an empty
# scratch directory for its report. Nothing else is mounted: no home
# directory, no SSH agent, no token, and podman passes no environment
# variable that is not named. Every container drops every capability; the
# kcov and node ones also get no network and a read only root filesystem.
# The Python container needs the network for its pip install, from a lock
# with hashes. The images are pinned by digest, and Renovate moves the
# digests.
#
# The scratch directory comes from mktemp, so it lands in TMPDIR. In a
# workbench run this as `l2 --engine --net -- make coverage`: the engine can
# only mount paths under the TMPDIR it sets.
#
# Every report is written before any verdict is given, so CI can still hand
# SonarQube the reports of a run that falls short.
COVERAGE_DIR ?= coverage
PODMAN ?= $(if $(CONTAINER_HOST),podman-remote,podman)
# renovate: datasource=docker depName=docker.io/library/python
PYTHON_IMAGE ?= docker.io/library/python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
# renovate: datasource=docker depName=docker.io/library/node
NODE_IMAGE ?= docker.io/library/node:24-trixie@sha256:be40f6a87b9b22215ddb20da0a2320a5c6d583fe3ee3b0024d9fa4f05b40c8fd
# renovate: datasource=docker depName=docker.io/kcov/kcov
KCOV_IMAGE ?= docker.io/kcov/kcov:latest@sha256:481289ae32e55e5b733019515acd10948a4f76dfed381765577db909664fc603

# The Python sources. Files ending in .py are listed too, so a module no test
# imports fails the gate instead of dropping out of the report.
PYTHON_SOURCES := \
	images/egress-proxy/bin/egress-refresh \
	images/gh-broker/broker.py \
	images/l2/bin/l2-prepare-hooks \
	images/mirror-gate/backends/nexus/provision \
	images/mirror-gate/bin/mirror-gate \
	images/mirror-gate/lib/filters.py \
	images/mirror-gate/lib/osvdb.py \
	images/workbench/bin/gh \
	images/workbench/bin/route-to-l2 \
	scripts/extension-pins.py \
	scripts/kcov_to_sonar.py
# The shell scripts held at 100% so far. Each has tests/<name>.test.sh, which
# runs it. Not every shell script here is listed yet: one that is not has no
# coverage gate at all, and SonarQube counts it as uncovered.
SHELL_SCRIPTS := \
	images/egress-proxy/bin/egress-proxy \
	images/egress-proxy/bin/egress-reload \
	images/l2/bin/actionlint \
	images/l2/engine-bin/podman
JS_SOURCES := images/workbench/bin/airlock-relay

comma := ,
space := $(subst ,, )

_sources := git ls-files -z --cached --others --exclude-standard --deduplicate \
	| tar --create --owner=0 --group=0 --numeric-owner --null --files-from=- \
		--ignore-failed-read --file=-
_unpack := set -e; mkdir /tmp/w; tar -x --no-same-owner -C /tmp/w; cd /tmp/w
_locked := --cap-drop=ALL --security-opt no-new-privileges
_sealed := $(_locked) --network=none --read-only --tmpfs /tmp

coverage:
	@set -u; out="$$(mktemp -d)"; trap 'rm -rf "$$out"' EXIT; \
	mkdir "$$out/python" "$$out/shell" "$$out/js"; py=0; sh=0; js=0; \
	$(_sources) | $(PODMAN) run --rm --interactive $(_locked) \
		-v "$$out/python:/out:rw,Z" "$(PYTHON_IMAGE)" sh -c '$(_unpack); \
			pip install --quiet --disable-pip-version-check --root-user-action=ignore \
				--require-hashes --only-binary=:all: -r tests/requirements.txt; \
			status=0; coverage run -m pytest tests -q -p no:cacheprovider || status=1; \
			coverage xml -q -o /out/coverage.xml; \
			coverage report || status=1; \
			python3 scripts/kcov_to_sonar.py /tmp/w /out/coverage.xml /tmp/python.xml \
				$(PYTHON_SOURCES) || status=1; \
			exit $$status' || py=$$?; \
	$(_sources) | $(PODMAN) run --rm --interactive $(_sealed) \
		-v "$$out/shell:/out:rw,Z" "$(KCOV_IMAGE)" sh -c '$(_unpack); \
			status=0; \
			for t in $(foreach s,$(SHELL_SCRIPTS),tests/$(notdir $(s)).test.sh); do \
				kcov --include-path=$(subst $(space),$(comma),$(addprefix /tmp/w/,$(SHELL_SCRIPTS))) \
					/out/kcov "$$t" || status=1; \
			done; \
			python3 scripts/kcov_to_sonar.py /tmp/w /out/kcov/kcov-merged/cobertura.xml \
				/out/shell.xml $(SHELL_SCRIPTS) || status=1; \
			exit $$status' || sh=$$?; \
	$(_sources) | $(PODMAN) run --rm --interactive $(_sealed) \
		-v "$$out/js:/out:rw,Z" "$(NODE_IMAGE)" sh -c '$(_unpack); \
			status=0; node --test --experimental-test-coverage \
				--test-coverage-include=$(JS_SOURCES) \
				--test-coverage-lines=100 --test-coverage-branches=100 \
				--test-coverage-functions=100 \
				--test-reporter=spec --test-reporter-destination=stdout \
				--test-reporter=lcov --test-reporter-destination=/tmp/lcov.info \
				"tests/*.test.js" || status=$$?; \
			sed "s|^SF:/tmp/w/|SF:|" /tmp/lcov.info > /out/lcov.info; \
			exit $$status' || js=$$?; \
	rm -rf "$(COVERAGE_DIR)"; mkdir -p "$(COVERAGE_DIR)"; \
	cp "$$out"/python/coverage.xml "$$out"/shell/shell.xml "$$out"/js/lcov.info \
		"$(COVERAGE_DIR)"/ 2>/dev/null || true; \
	echo "coverage: python exit $$py, shell exit $$sh, javascript exit $$js"; \
	test "$$py" -eq 0 && test "$$sh" -eq 0 && test "$$js" -eq 0
