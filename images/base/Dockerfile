# The floor every other image in this repository builds on.
#
# It carries what all of them need and nothing that only one of them needs:
# the tools a script or a hook in any layer reaches for, the apt signing keys
# this organization trusts, and the 'dev' account. It deliberately carries no
# container runtime, no coding agent and no language toolchain. Those belong
# to a layer (docs/LAYERS.md):
#
#   workbench  where you and the coding agents work, and the only layer that
#              holds credentials
#   l2         where hooks, tests, installs and throwaway binaries run, with no
#              network and no credentials
#   helpers    gh-broker and egress-proxy, which run beside the workbench
#
# Ubuntu rather than debian:13-slim: measured 2026-09-14, the two are the same
# size, and Ubuntu is what this organization develops against.
#
# Pinned by digest as well as by tag, so a point release cannot change what a
# build sits on without a commit saying so. Renovate updates both halves.
# renovate: datasource=docker depName=ubuntu
FROM docker.io/library/ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7

ENV DEBIAN_FRONTEND=noninteractive

# Why each of these, since an unexplained package list grows forever:
#
#   ca-certificates  every https fetch in every layer
#   curl             used by this organization's scripts and hooks
#   git              every layer reads or writes a repository
#   gnupg            verifies and dearmors the apt signing keys below
#   jq               used by this organization's scripts and hooks
#   procps           ps, which pre-commit and several scripts shell out to
#   python3          the workbench's shims, the gh broker, and pre-commit in l2
#
# Package versions are deliberately unpinned (hadolint DL3008); see
# docs/TOOL_SOURCES.md. The digest pin above is what makes a build
# reproducible, and the upgrade here is what makes a rebuild current.
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get upgrade -y \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    jq \
    procps \
    python3 \
  && rm -rf /var/lib/apt/lists/*

# The apt signing keys this organization trusts, and nothing else about those
# repositories. Vendored rather than downloaded, so a key changing is a diff
# somebody reviews.
#
# Each file's set of primary certificates is compared against this list
# exactly: every expected fingerprint present and no unexpected one. `gpg
# --dearmor` installs every certificate a file holds, so checking only that
# the expected one is present would let an appended certificate through.
# github-cli genuinely ships two, which is why each entry is a set.
#
# A plain string comparison rather than a pipefail guard: Podman's default OCI
# image format ignores SHELL, so a pipefail guard would hold under Docker and
# silently not under the runtime this organization builds with.
#
# Installing a key grants nothing on its own. apt reads a keyring only when a
# sources.list entry names it, and the layers that need one add that entry
# themselves.
COPY keyrings/ /tmp/keyrings/
# hadolint ignore=DL4006
RUN set -eu; \
  install -d -m 0755 /usr/share/keyrings; \
  for entry in \
      "github-cli:2C6106201985B60E6C7AC87323F3D4EA75716059,7F38BBB59D064DBCB3D84D725612B36462313325" \
      "nodesource:6F71F525282841EEDAF851B42F59B5F99B1BE0B4" \
      "hashicorp:D55C0D1AC78A8D8126CB631CFC9CA96ACA026560"; do \
    name="${entry%%:*}"; \
    want="${entry#*:}"; \
    got="$( \
      gpg --show-keys --with-colons "/tmp/keyrings/${name}.asc" \
        | awk -F: '$1=="pub"{p=1;next} $1=="fpr"&&p{print $10;p=0;next} {p=0}' \
        | sort \
        | paste -sd, - \
    )"; \
    if [ "${got}" != "${want}" ]; then \
      echo "${name}.asc: primary certificates ${got:-none} do not match the reviewed set ${want}" >&2; \
      exit 1; \
    fi; \
    gpg --dearmor < "/tmp/keyrings/${name}.asc" \
      > "/usr/share/keyrings/${name}.gpg"; \
    chmod 0644 "/usr/share/keyrings/${name}.gpg"; \
  done; \
  rm -rf /tmp/keyrings

# Ubuntu's image already has an account at uid 1000, named 'ubuntu'. Renaming
# it keeps uid and gid 1000, which is what makes a bind mounted working tree
# readable and writable under rootless Podman with --userns=keep-id.
RUN usermod --login dev --home /home/dev --move-home ubuntu \
  && groupmod --new-name dev ubuntu \
  && sed -i '/^ubuntu:/d' /etc/subuid /etc/subgid

USER 1000:1000
WORKDIR /workspace
ENV HOME=/home/dev \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="The floor of the ivan-pinatti-labs images: git, common command line tools, the trusted apt signing keys and the dev account. No runtime, agent or toolchain." \
      org.opencontainers.image.licenses="Apache-2.0"

CMD ["/bin/bash"]
