# L2: where hooks, tests, installs and throwaway binaries run.
#
# The workbench starts one of these for every hook run and every command an
# agent sends through `l2`, with no network (unless a run asks for it and goes
# through the egress proxy), no credentials, no home directory from anywhere,
# and only the working tree mounted. docs/LAYERS.md has the whole picture.
#
# This is the shared L2 image. A repository that needs more (terraform, a test
# framework) builds a thin layer on top of it and names that image in its
# .devcontainer/l2-image.
ARG BASE_IMAGE=localhost/airlock-base:local

# Linters that upstream only ships as container images, and that pre-commit
# hooks used to run with `docker run`. A container cannot start another
# container from inside L2 (measured 2026-09-24: the nested runtime cannot set
# up a user namespace there), so their binaries are copied out of the
# official images, pinned by digest, and bin/docker below answers the hooks
# that still call `docker run` for them.
FROM ghcr.io/hadolint/hadolint:v2.15.1-debian@sha256:aacf4eda44e40057051f5c5b02546e3ada7e8b84d4bbf9312c89524a7e9a0b51 AS hadolint
FROM docker.io/rhysd/actionlint:1.7.12@sha256:9d36088643581e728c969f35141f88139fec77280b2be23c1f66f8e40e1025e7 AS actionlint
FROM docker.io/dotenvlinter/dotenv-linter:4.0.0@sha256:815fbc7bdd429f5182c349c5772d1d4e0e9135cfb6bb324f1d52258b105ea09d AS dotenv-linter

# The Go modules golang hooks build from (go-modules.txt says which, and
# why), fetched here in a throwaway stage with the network, by doing exactly
# what pre-commit does. Go verifies each one against its checksum database
# as it downloads; that is where the check happens. What is kept is the module
# cache's download tree, which is a module proxy as it stands. The same
# installs then run again on the same checkouts, from that tree alone and in
# a fresh cache, with nothing fetched (no clone, GOPROXY off past the tree),
# so a module missing from it fails this build rather than a hook in L2.
# hadolint ignore=DL3006
FROM ${BASE_IMAGE} AS go-modules
# A build stage only; nothing from it runs in L2, and only the module tree is
# copied out.
# hadolint ignore=DL3002
USER 0:0
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install -y --no-install-recommends golang-go \
  && rm -rf /var/lib/apt/lists/*
COPY go-modules.txt /tmp/go-modules.txt
# Every module is pinned to a version, in go-modules.txt (DL3062 cannot see
# through the variable).
# hadolint ignore=DL3062
RUN set -eu; \
  export GOFLAGS=-modcacherw GOTOOLCHAIN=local; \
  grep -vE '^[[:space:]]*(#|$)' /tmp/go-modules.txt > /tmp/list; \
  n=0; \
  while read -r what rev; do \
    if [ -n "${rev}" ]; then \
      n=$((n + 1)); \
      git clone --quiet --depth 1 --branch "${rev}" "${what}" "/tmp/src/${n}"; \
      echo "/tmp/src/${n}" >> /tmp/checkouts; \
    else \
      echo "${what}" >> /tmp/modules; \
    fi; \
  done < /tmp/list; \
  install_all() { \
    if [ -f /tmp/checkouts ]; then \
      while read -r src; do go -C "${src}" install ./...; done < /tmp/checkouts; \
    fi; \
    if [ -f /tmp/modules ]; then \
      while read -r mod; do go install "${mod}"; done < /tmp/modules; \
    fi; \
  }; \
  GOPATH=/tmp/gopath install_all; \
  out=/usr/local/share/go-modules; \
  mkdir -p "${out}"; \
  cp -r /tmp/gopath/pkg/mod/cache/download/. "${out}/"; \
  rm -rf "${out}/sumdb"; \
  find "${out}" -type f \( -name '*.lock' -o -name '*.ziphash' -o -name '*.partial' \) -delete; \
  GOPATH=/tmp/check GOPROXY="file://${out},off" GOSUMDB=off install_all; \
  chmod -R a-w,a+rX "${out}"

# hadolint ignore=DL3006
FROM ${BASE_IMAGE}

USER 0:0

# Why each of these:
#
#   pre-commit       the hook runner; Ubuntu's build, for provenance
#   python3-venv     pre-commit builds a virtual environment per python hook
#   nodejs           node hooks (cspell, markdownlint) use it instead of
#                    downloading a runtime of their own through nodeenv
#   golang-go        golang hooks (checkmake, gitleaks) likewise, instead of
#                    pre-commit fetching a Go toolchain; their modules come
#                    from the go-modules stage above
#   shellcheck       actionlint runs it over `run:` blocks
#   make             most repositories drive their tests through it
#   podman-remote    a client, for `l2 --engine` runs whose tests build and
#                    start containers through the L2 engine (see engine-bin)
#
# hadolint ignore=DL3008
RUN printf '%s\n' \
      'deb [arch=amd64 signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main' \
      > /etc/apt/sources.list.d/nodesource.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
    golang-go \
    make \
    nodejs \
    podman-remote \
    pre-commit \
    python3-venv \
    shellcheck \
  && rm -rf /var/lib/apt/lists/*

COPY --from=hadolint /bin/hadolint /usr/local/bin/hadolint
COPY --from=actionlint /usr/local/bin/actionlint /usr/local/lib/l2/actionlint
COPY --from=dotenv-linter /dotenv-linter /usr/local/bin/dotenv-linter
COPY --from=go-modules /usr/local/share/go-modules /usr/local/share/go-modules

# Which image version each baked binary stands in for. bin/docker refuses a
# hook asking for a different version, so a hook bump that this image has not
# caught up with fails loudly instead of linting with the wrong release.
ENV L2_BAKED_IMAGES="ghcr.io/hadolint/hadolint=v2.15.1 docker.io/hadolint/hadolint=v2.15.1 docker.io/rhysd/actionlint=1.7.12 docker.io/dotenvlinter/dotenv-linter=4.0.0"

COPY bin/docker bin/l2-prepare-hooks bin/actionlint bin/airlock-review /usr/local/bin/
COPY engine-bin/ /usr/local/lib/l2/engine-bin/

# The L2 wrapper runs everything here as uid 0 (--user 0:0, overriding the
# USER below), which inside the workbench's
# nested user namespace is the workbench's own dev account: files a hook writes
# into the working tree stay owned by you. /root is where pre-commit keeps its
# environments, on a volume the wrapper mounts per repository.
USER 1000:1000
WORKDIR /workspace
# GOTOOLCHAIN=local: go uses the toolchain installed here and never downloads
# one of its own. GOPROXY: modules come from the baked proxy and nowhere
# else, even on a run with the network; `off` makes a missing one fail by
# name. GOSUMDB=off: those modules were checked against Go's checksum
# database when this image was built, and the image digest pins them since;
# asking the database again offline needs proofs the build never fetched. A repository whose own L2 layer builds Go against the network sets
# its own GOPROXY there, and selects the golang egress set.
ENV HOME=/root \
    CHECKPOINT_DISABLE=1 \
    GOTOOLCHAIN=local \
    GOPROXY=file:///usr/local/share/go-modules,off \
    GOSUMDB=off

LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="L2 of the ivan-pinatti-labs images: pre-commit, language runtimes and baked linters, run with no network and no credentials." \
      org.opencontainers.image.licenses="Apache-2.0"
