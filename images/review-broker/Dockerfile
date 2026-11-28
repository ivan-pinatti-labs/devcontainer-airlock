# The review broker: the only place the reviewer's login lives.
#
# It runs beside the workbenches and receives review requests over a unix
# socket from an L2 run given it (`l2 --review`): a prompt with the diff,
# and the tree under review. It runs the one blind review command with the
# login, in a folder of its own that holds nothing else, and returns the
# answer. Nothing that asks for a review can read the login. docs/LAYERS.md,
# "Review broker".
# cspell:words bwrap
ARG BASE_IMAGE=localhost/airlock-base:local

# Codex from npm, in a stage of its own: only its native build is copied out,
# so Node never reaches the broker.
# hadolint ignore=DL3006
FROM ${BASE_IMAGE} AS codex
# A build stage only; nothing from it runs.
# hadolint ignore=DL3002
USER 0:0
# hadolint ignore=DL3008
RUN printf '%s\n' \
      'deb [arch=amd64 signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main' \
      > /etc/apt/sources.list.d/nodesource.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*
# The same version as the Codex workbench, moved by the same reviewed bump.
# renovate: datasource=npm depName=@openai/codex
ARG CODEX_VERSION=0.171.0
WORKDIR /opt/npm
RUN set -eu; \
  export HOME=/root npm_config_cache=/tmp/npm-cache; \
  npm init --yes >/dev/null; \
  npm install --ignore-scripts --no-audit --no-fund --save-exact \
    "@openai/codex@${CODEX_VERSION}"; \
  npm audit signatures; \
  v=node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl; \
  mkdir -p /out/codex; \
  cp -a "${v}/bin" "${v}/codex-path" "${v}/codex-package.json" /out/codex/; \
  mkdir -p /out/codex/codex-resources; \
  cp -a "${v}/codex-resources/bwrap" /out/codex/codex-resources/

# hadolint ignore=DL3006
FROM ${BASE_IMAGE}
USER 0:0
COPY --from=codex /out/codex /opt/codex
RUN ln -s /opt/codex/bin/codex /usr/local/bin/codex \
  && mkdir -p /tmp/version-check/.codex \
  && HOME=/tmp/version-check CODEX_HOME=/tmp/version-check/.codex codex --version \
  && rm -rf /tmp/version-check
COPY broker.py /usr/local/libexec/review-broker/broker.py
# The refuse and withhold cases: a change that reopens one fails the build.
COPY test_broker.py /tmp/test_broker.py
RUN python3 /tmp/test_broker.py && rm /tmp/test_broker.py
USER 1000:1000
ENV REVIEW_BROKER_DIR=/run/review-broker \
    REVIEW_LOGIN_DIR=/var/lib/review-broker/codex
LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="Holds the reviewer's login and runs the blind cross review for the ivan-pinatti-labs workbench." \
      org.opencontainers.image.licenses="Apache-2.0"
CMD ["python3", "/usr/local/libexec/review-broker/broker.py"]
