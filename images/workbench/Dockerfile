# The workbenches: where you and the coding agents work.
#
# One per agent, built from this file as two targets: `claude` (Claude Code
# and its editor extension) and `codex` (Codex and its extension). Each holds
# one agent's login and nothing of the other's, so neither agent can read the
# other's credentials. Everything they share is the `common` stage below,
# built once and stored once: both images carry the same layers underneath,
# and only a thin agent layer on top differs.
#
# In each, the terminal agent and the VS Code server with its extensions run
# as two views of the same container. It is the only layer holding
# credentials, and even here the dangerous ones are kept out: the GitHub token
# stays in the gh broker, the ssh key in the ssh-agent container, and all
# traffic leaves through the egress proxy. Project code (hooks, tests,
# installs, throwaway binaries) does not run here at all; `l2` sends it to the
# L2 engine, a separate container. docs/LAYERS.md has the whole picture.
ARG BASE_IMAGE=localhost/airlock-base:local

# The pinned VS Code extensions, installed in a stage of their own so the VS
# Code server used to install them never reaches an image. The server the
# editor actually talks to is downloaded by the editor when it attaches, for
# whatever VS Code version you run; only the extensions are fixed here. Each
# agent's image gets one complete extensions directory: the shared extensions
# plus that agent's own, and not the other agent's.
# hadolint ignore=DL3006
FROM ${BASE_IMAGE} AS extensions
# A build stage only; nothing from it runs, and only the extensions
# directories are copied out.
# hadolint ignore=DL3002
USER 0:0
# Any recent release installs extensions the same way; this one only has to
# exist and match its published checksum.
ARG VSCODE_COMMIT=7debcd0e2acdea1c52de81bf9ee1620444407dda
COPY vscode/extensions.txt /tmp/extensions.txt
# No pipefail (DL4006): Podman's default image format ignores SHELL, so it
# would not hold under the runtime this builds with. Each pipe below ends in a
# command that fails on its own when its input is wrong (sha256sum -c, diff).
# hadolint ignore=DL4006
RUN set -eu; \
  meta="$(curl -fsSL --proto '=https' "https://update.code.visualstudio.com/api/versions/commit:${VSCODE_COMMIT}/server-linux-x64/stable")"; \
  url="$(printf '%s' "${meta}" | jq -r .url)"; \
  sum="$(printf '%s' "${meta}" | jq -r .sha256hash)"; \
  curl -fsSL --proto '=https' -o /tmp/server.tgz "${url}"; \
  echo "${sum}  /tmp/server.tgz" | sha256sum -c -; \
  mkdir -p /tmp/server; \
  tar -xzf /tmp/server.tgz -C /tmp/server --strip-components=1; \
  grep -vE '^\s*(#|$)' /tmp/extensions.txt | sort > /tmp/all.txt; \
  for agent in claude codex; do \
    case "${agent}" in \
      claude) other='^openai\.chatgpt@' ;; \
      codex) other='^anthropic\.claude-code@' ;; \
    esac; \
    grep -viE "${other}" /tmp/all.txt > "/tmp/${agent}.txt"; \
    dir="/opt/vscode-extensions-${agent}"; \
    mkdir -p "${dir}"; \
    while read -r ext; do \
      /tmp/server/bin/code-server --accept-server-license-terms \
        --extensions-dir "${dir}" --install-extension "${ext}" \
        || exit 1; \
    done < "/tmp/${agent}.txt"; \
    /tmp/server/bin/code-server --accept-server-license-terms \
      --extensions-dir "${dir}" --list-extensions --show-versions \
      | sort > "/tmp/${agent}.installed"; \
    diff -u "/tmp/${agent}.txt" "/tmp/${agent}.installed"; \
    chmod -R a-w,a+rX "${dir}"; \
  done

# Everything both workbenches carry. Not an image of its own: each agent
# stage builds on it and ends as the dev account.
# hadolint ignore=DL3006
FROM ${BASE_IMAGE} AS common
# hadolint ignore=DL3002
USER 0:0

# Why each of these:
#
#   nodejs           the runtime both agent CLIs are installed for
#   openssh-client   git over ssh, through the ssh-agent container's socket
#   socat            carries that ssh connection through the egress proxy,
#                    and relays Claude Code's sandbox network
#   make             most repositories drive their tasks with it
#   podman-remote    a client of the L2 engine's socket (CONTAINER_HOST
#                    below), installed as `podman`. There is no container
#                    runtime in here at all
#   bubblewrap, ripgrep
#                    the agents' own sandboxes and searches
#
# There is no gh here: bin/gh sends every gh command to the gh broker, which
# holds the token. There is no pre-commit either: hooks run in L2.
# hadolint ignore=DL3008
RUN printf '%s\n' \
      'deb [arch=amd64 signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main' \
      > /etc/apt/sources.list.d/nodesource.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
    bubblewrap \
    catatonit \
    make \
    nodejs \
    openssh-client \
    podman-remote \
    ripgrep \
    socat \
  && rm -rf /var/lib/apt/lists/* \
  && ln -s /usr/bin/podman-remote /usr/local/bin/podman

# The workbench's own tools: root owned and read only to the dev account.
COPY bin/l2 bin/l2-hooks-install bin/l2-pre-commit bin/gh bin/workbench-init bin/airlock-worktree /usr/local/bin/
COPY bin/route-to-l2 bin/status-line /usr/local/libexec/workbench/
# A quoting slip in one of these fails the build, not the first person to run
# it (a stray apostrophe in a header comment did, twice).
RUN for f in /usr/local/bin/l2 /usr/local/bin/l2-hooks-install /usr/local/bin/l2-pre-commit /usr/local/bin/workbench-init /usr/local/bin/airlock-worktree /usr/local/libexec/workbench/status-line; do \
      bash -n "${f}" || exit 1; \
    done
COPY share/git-hook /usr/local/share/workbench/git-hook

# The editor's server looks for extensions under ~/.vscode-server/extensions,
# a root owned link to a root owned, read only directory, which each agent's
# stage fills. ~/.vscode-server itself is root owned and sticky, like /tmp:
# the server can create what it needs in there (its own download, its data
# directory), but nothing running as dev can rename or replace the link, so
# no extension or anything else here can add, update or swap one. Auto update
# is off in the server's machine settings.
COPY vscode/machine-settings.json /home/dev/.vscode-server/data/Machine/settings.json
RUN ln -s /opt/vscode-extensions /home/dev/.vscode-server/extensions \
  && install -d -o 1000 -g 1000 /home/dev/.config \
  && chown 0:0 /home/dev/.vscode-server \
  && chmod 1777 /home/dev/.vscode-server \
  && chown -R 1000:1000 /home/dev/.vscode-server/data

WORKDIR /opt/agent-clis

# Run last by each agent's stage: its editor settings on top of the shared
# machine settings, and the ownership check under /home/dev.
COPY bin/finish-image /usr/local/libexec/workbench/finish-image

# NODE_USE_ENV_PROXY makes Node's fetch honour the egress proxy variables the
# workbench is started with. CONTAINER_HOST points podman, and so `l2`, at
# the L2 engine.
# DO_NOT_TRACK is the general opt out convention (consoledonottrack.com):
# unset, npm, Next.js and other tools in here would phone home by default.
# Claude Code 2.1.283 starts remote control with it set.
ENV NODE_USE_ENV_PROXY=1 \
    DO_NOT_TRACK=1 \
    GH_BROKER_SOCK=/run/gh-broker/gh.sock \
    CONTAINER_HOST=unix:///run/l2-engine/podman.sock \
    L2_IMAGE=localhost/airlock-l2:local

LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.licenses="Apache-2.0"

CMD ["/bin/bash"]

# Claude Code's workbench. The CLI is pinned, installed with lifecycle scripts
# off, checked with `npm audit signatures`, and only then allowed to run the
# one postinstall it needs. See docs/TOOL_SOURCES.md for why npm, why pinned,
# and what the package's signature does and does not prove.
FROM common AS claude
# renovate: datasource=npm depName=@anthropic-ai/claude-code
ARG CLAUDE_CODE_VERSION=2.1.283
RUN set -eu; \
  export HOME=/root npm_config_cache=/tmp/npm-cache; \
  npm init --yes >/dev/null; \
  npm install --ignore-scripts --no-audit --no-fund --save-exact \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"; \
  npm audit signatures; \
  npm rebuild @anthropic-ai/claude-code; \
  rm -rf /tmp/npm-cache; \
  mkdir -p /root/version-check; \
  HOME=/root/version-check /opt/agent-clis/node_modules/.bin/claude --version; \
  rm -rf /root/version-check
# SoX, which Claude Code's voice mode records with (`rec`), and a rec of our
# own in front of it that reads a voice session's microphone pipe. There is
# no audio device and no audio server here; without a voice session
# (WORKBENCH_VOICE) rec fails. docs/LAYERS.md, "Voice".
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install -y --no-install-recommends sox \
  && rm -rf /var/lib/apt/lists/*
COPY bin/rec /usr/local/bin/rec
# Claude Code itself runs behind a relay of ours on localhost, which sends the
# requests it hands the proxy in absolute form through a CONNECT tunnel
# instead. docs/LAYERS.md, "Remote Control".
COPY bin/claude /usr/local/bin/claude
COPY bin/airlock-relay /usr/local/libexec/workbench/airlock-relay
RUN bash -n /usr/local/bin/claude && bash -n /usr/local/bin/rec \
  && node --check /usr/local/libexec/workbench/airlock-relay
# The policies Claude Code cannot edit.
COPY claude/managed-settings.json claude/CLAUDE.md /etc/claude-code/
COPY --from=extensions /opt/vscode-extensions-claude /opt/vscode-extensions
COPY vscode/claude-settings.json /tmp/agent-settings.json
RUN install -d -o 1000 -g 1000 /home/dev/.claude \
  && /usr/local/libexec/workbench/finish-image /tmp/agent-settings.json \
  && rm /tmp/agent-settings.json
USER 1000:1000
WORKDIR /workspace
# Its configuration lives in the directory the workbench mounts for it, never
# in the host's own ~/.claude. The CLI is pinned by this image, so its own
# updater is off.
ENV CLAUDE_CONFIG_DIR=/home/dev/.claude \
    DISABLE_AUTOUPDATER=1 \
    WORKBENCH_AGENT=claude
LABEL org.opencontainers.image.description="The ivan-pinatti-labs Claude Code workbench: Claude Code, its VS Code extension, and the l2 client of the L2 engine. Holds no GitHub token, no ssh key and no other agent's login."

# Codex's workbench, installed the same way.
FROM common AS codex
# renovate: datasource=npm depName=@openai/codex
ARG CODEX_VERSION=0.157.0
RUN set -eu; \
  export HOME=/root npm_config_cache=/tmp/npm-cache; \
  npm init --yes >/dev/null; \
  npm install --ignore-scripts --no-audit --no-fund --save-exact \
    "@openai/codex@${CODEX_VERSION}"; \
  npm audit signatures; \
  ln -s /opt/agent-clis/node_modules/.bin/codex /usr/local/bin/codex; \
  rm -rf /tmp/npm-cache; \
  mkdir -p /root/version-check/.codex; \
  HOME=/root/version-check CODEX_HOME=/root/version-check/.codex codex --version; \
  rm -rf /root/version-check
# The policies Codex cannot edit, and the rule workbench-init puts back into
# its rules folder at every start.
COPY codex/requirements.toml codex/managed_config.toml /etc/codex/
COPY codex/workbench.rules /usr/local/share/workbench/codex-workbench.rules
COPY --from=extensions /opt/vscode-extensions-codex /opt/vscode-extensions
COPY vscode/codex-settings.json /tmp/agent-settings.json
RUN install -d -o 1000 -g 1000 /home/dev/.codex \
  && /usr/local/libexec/workbench/finish-image /tmp/agent-settings.json \
  && rm /tmp/agent-settings.json
USER 1000:1000
WORKDIR /workspace
# Its configuration lives in the directory the workbench mounts for it, never
# in the host's own ~/.codex.
ENV CODEX_HOME=/home/dev/.codex \
    WORKBENCH_AGENT=codex
LABEL org.opencontainers.image.description="The ivan-pinatti-labs Codex workbench: Codex, its VS Code extension, and the l2 client of the L2 engine. Holds no GitHub token, no ssh key and no other agent's login."
