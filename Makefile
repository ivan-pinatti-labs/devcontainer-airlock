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
# The shell scripts, held at 100%. Each has tests/<name>.test.sh (its file
# name less any .sh), or a folder tests/<name>/ of *.test.sh files (one per
# command of host/workbench), which runs it. A shell script left out of this
# list has no coverage gate at all, and SonarQube counts it as uncovered.
#
# A script the image installs only after filling in a template (git-hook) is
# tested in the form it is installed in: its test renders it into a
# directory named kcov-rendered, at the template's own path below that, and
# runs it from there. kcov is told to keep any file under kcov-rendered, and
# scripts/kcov_to_sonar.py counts its lines for the template. That reads
# every test's own report besides kcov's merged one, which leaves out a file
# that was gone (with the test's scratch directory) by the time it merged. Rendering only
# fills in placeholders within a line, so line N of one is line N of the
# other, which the test checks.
SHELL_SCRIPTS := \
	host/workbench \
	images/egress-proxy/bin/egress-proxy \
	images/egress-proxy/bin/egress-reload \
	images/l2-engine/containers/crun-without-masked-paths \
	images/l2/bin/actionlint \
	images/l2/bin/docker \
	images/l2/engine-bin/podman \
	images/workbench/bin/airlock-worktree \
	images/workbench/bin/claude \
	images/workbench/bin/finish-image \
	images/workbench/bin/l2 \
	images/workbench/bin/l2-hooks-install \
	images/workbench/bin/l2-pre-commit \
	images/workbench/bin/rec \
	images/workbench/bin/status-line \
	images/workbench/bin/workbench-init \
	images/workbench/share/git-hook \
	scripts/build-images.sh
JS_SOURCES := images/workbench/bin/airlock-relay
# Lines kcov counts as code that bash never reports running, because they
# hold no command of their own: an empty case arm, `fi ;;`, and the end of a
# loop or a { } group read or written through a redirection. kcov leaves
# out every line holding one of these.
KCOV_STRUCTURE := done <,) ;;,fi ;;,} >

comma := ,
space := $(subst ,, )

# Builds $$out/src.tar: the files git would commit (tracked, plus new ones
# not ignored), minus any deleted in the working tree, each step checked,
# so the containers never measure a partial tree.
_sources := git ls-files -z --cached --others --exclude-standard --deduplicate \
		>"$$out/all" || exit 1; \
	xargs -0 sh -c 'for f do if [ -e "$$f" ] || [ -L "$$f" ]; then printf "%s\0" "$$f"; fi; done' sh \
		<"$$out/all" >"$$out/list" || exit 1; \
	tar --create --owner=0 --group=0 --numeric-owner --null --files-from="$$out/list" --file="$$out/src.tar" || exit 1
_unpack := set -e; mkdir /tmp/w; tar -x --no-same-owner -C /tmp/w; cd /tmp/w
_locked := --cap-drop=ALL --security-opt no-new-privileges
_sealed := $(_locked) --network=none --read-only --tmpfs /tmp

coverage:
	@set -u; out="$$(mktemp -d)"; trap 'rm -rf "$$out"' EXIT; \
	$(_sources); \
	mkdir "$$out/python" "$$out/shell" "$$out/js"; py=0; sh=0; js=0; \
	$(PODMAN) run <"$$out/src.tar" --rm --interactive $(_locked) \
		-v "$$out/python:/out:rw,Z" "$(PYTHON_IMAGE)" sh -c '$(_unpack); \
			pip install --quiet --disable-pip-version-check --root-user-action=ignore \
				--require-hashes --only-binary=:all: -r tests/requirements.txt; \
			status=0; coverage run -m pytest tests -q -p no:cacheprovider || status=1; \
			coverage xml -q -o /out/coverage.xml || status=1; \
			coverage report || status=1; \
			python3 scripts/kcov_to_sonar.py /tmp/w /out/coverage.xml /tmp/python.xml \
				$(PYTHON_SOURCES) || status=1; \
			exit $$status' || py=$$?; \
	$(PODMAN) run <"$$out/src.tar" --rm --interactive $(_sealed) \
		-v "$$out/shell:/out:rw,Z" "$(KCOV_IMAGE)" sh -c '$(_unpack); \
			status=0; \
			for t in $(foreach s,$(SHELL_SCRIPTS),$(wildcard tests/$(basename $(notdir $(s))).test.sh tests/$(basename $(notdir $(s)))/*.test.sh)); do \
				kcov --exclude-line="$(KCOV_STRUCTURE)" --include-pattern=$(subst $(space),$(comma),$(addprefix /tmp/w/,$(SHELL_SCRIPTS)) /kcov-rendered/) \
					/out/kcov "$$t" || status=1; \
			done; \
			reports="$$(printf "%s," /out/kcov/*/cobertura.xml)"; \
			python3 scripts/kcov_to_sonar.py /tmp/w "$${reports%,}" \
				/out/shell.xml $(SHELL_SCRIPTS) || status=1; \
			exit $$status' || sh=$$?; \
	$(PODMAN) run <"$$out/src.tar" --rm --interactive $(_sealed) \
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
	mkdir -p "$(COVERAGE_DIR)" && rm -f "$(COVERAGE_DIR)/coverage.xml" "$(COVERAGE_DIR)/shell.xml" "$(COVERAGE_DIR)/lcov.info" || exit 1; \
	for report in "$$out/python/coverage.xml" "$$out/shell/shell.xml" "$$out/js/lcov.info"; do \
		if [ -f "$$report" ]; then cp "$$report" "$(COVERAGE_DIR)"/ || exit 1; fi; \
	done; \
	echo "coverage: python exit $$py, shell exit $$sh, javascript exit $$js"; \
	test "$$py" -eq 0 && test "$$sh" -eq 0 && test "$$js" -eq 0 && \
		test -s "$(COVERAGE_DIR)/coverage.xml" && test -s "$(COVERAGE_DIR)/shell.xml" && test -s "$(COVERAGE_DIR)/lcov.info"
