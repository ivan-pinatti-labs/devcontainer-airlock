# The gh broker: the only place the GitHub token lives.
#
# It runs beside the workbench, receives gh command lines over a unix socket
# from the workbench's gh shim, runs the ones on its allowlist with the real gh
# and the token, and returns their output. The workbench, and anything running
# in it (an agent, an extension), can use GitHub through it, but can never
# read the token itself. docs/LAYERS.md, "GitHub access".
ARG BASE_IMAGE=localhost/airlock-base:local
# hadolint ignore=DL3006
FROM ${BASE_IMAGE}
USER 0:0
# hadolint ignore=DL3008
RUN printf '%s\n' \
      'deb [arch=amd64 signed-by=/usr/share/keyrings/github-cli.gpg] https://cli.github.com/packages stable main' \
      > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends gh \
  && rm -rf /var/lib/apt/lists/*
COPY broker.py /usr/local/libexec/gh-broker/broker.py
# The hardened list, and what each other level adds to the one before it.
COPY allowlist.json allowlist.assisted.json allowlist.unattended.json /etc/gh-broker/
# The allow and refuse cases: a change that reopens one fails the build.
COPY test_broker.py /tmp/test_broker.py
RUN python3 /tmp/test_broker.py && rm /tmp/test_broker.py
USER 1000:1000
# The root filesystem is read only when the broker runs; gh keeps what it
# caches (run logs, for one) in the /tmp it is given instead.
ENV GH_PROMPT_DISABLED=1 \
    GH_NO_UPDATE_NOTIFIER=1 \
    XDG_CACHE_HOME=/tmp \
    GH_BROKER_DIR=/run/gh-broker
LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="Holds the GitHub token and runs allowlisted gh commands for the ivan-pinatti-labs workbench." \
      org.opencontainers.image.licenses="Apache-2.0"
CMD ["python3", "/usr/local/libexec/gh-broker/broker.py"]
