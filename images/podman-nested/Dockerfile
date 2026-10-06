# A rootless Podman in Podman test runner: a repository runs a whole
# throwaway container stack nested inside this one container, and never
# touches the engine socket of the machine it runs on. The stack, its
# networks, volumes and images all live in this container's own storage,
# and go with it.
#
# It is not one of the workbench's layers and does not build on the base
# image: it is a runner a repository's CI (or a developer) starts directly,
# with the run flags in docs/IMAGES.md, "The nested test runner". Built,
# scanned and published with the other images, as airlock-podman-nested.
#
# quay.io/podman/stable rather than the base image with Ubuntu's podman: the
# upstream image is Fedora with podman's own maintainers' configuration for
# exactly this use (file capabilities on newuidmap and newgidmap, a 'podman'
# account with its subordinate ids, fuse-overlayfs storage), and Fedora
# carries a current podman and podman-compose. The immutable tag is never
# rebuilt upstream; pinned by digest as well, and Renovate moves both halves.
# renovate: datasource=docker depName=quay.io/podman/stable
FROM quay.io/podman/stable:v5.8.7-immutable@sha256:f345d8d4fddea244d3a1ced27bcec82d14cc9e32cb5ead7f5882b0baf59dc8b0
USER 0:0

# What a test suite's Makefile and scripts reach for, besides podman:
#
#   make, jq, yq, xmlstarlet       driving a suite and reading its config
#   python3-pip                    a suite's own Python dependencies
#   podman-compose                 the stack itself
#   acl, openssl                   permissions and certificates a suite sets up
#   procps-ng, which, iproute      ps, which and ip, for scripts that probe
#   util-linux                     setpriv, which podman-nested-init drops to
#                                  the podman account with; in the base image
#                                  today, named so a slimmer one cannot drop it
#   diffutils, git-core, kmod      in the base image today; named so a slimmer
#                                  base cannot drop them unnoticed
#
# Package versions are deliberately unpinned, as everywhere in this
# organization (docs/TOOL_SOURCES.md, "No version pins"). The upgrade is what
# makes the scheduled rebuild current, the same as the base image's.
# hadolint ignore=DL3041
RUN dnf -y upgrade \
  && dnf -y install \
    acl \
    diffutils \
    git-core \
    iproute \
    jq \
    kmod \
    make \
    openssl \
    podman-compose \
    procps-ng \
    python3-pip \
    util-linux \
    which \
    xmlstarlet \
    yq \
  && dnf clean all \
  && rm -rf /var/cache/dnf /var/cache/libdnf5

# The nested runtime's user configuration (the file explains itself), the
# runtime directory the API socket is served from, the program that sets up
# cgroups and drops to the podman account, the entrypoint, and the ticker it
# runs.
COPY --chown=1000:1000 containers.conf /home/podman/.config/containers/containers.conf
COPY bin/podman-nested-init bin/podman-nested-entrypoint bin/podman-health-ticker /usr/local/bin/
RUN install -d -m 0700 -o podman -g podman /run/user/1000

# The image starts as root and podman-nested-init drops to the podman account
# (uid 1000, no capabilities) before the entrypoint or any command runs. Root
# is needed for that one step only: it alone can move the container's
# processes out of its cgroup root and hand the podman account a subtree,
# which is what gives nested containers cgroups of their own. Under rootless
# podman that root is the invoking user in a user namespace of its own.
# hadolint ignore=DL3002
USER 0:0
ENV XDG_RUNTIME_DIR=/run/user/1000 \
    DOCKER_HOST=unix:///run/user/1000/podman/podman.sock
LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="Rootless Podman in Podman test runner: a whole throwaway container stack nested in one container, with no host engine socket." \
      org.opencontainers.image.licenses="Apache-2.0"
# catatonit as the first process reaps the nested containers' conmon
# processes, which are handed to it, whether or not the run passes --init.
ENTRYPOINT ["/usr/libexec/catatonit/catatonit", "--", "podman-nested-init"]
CMD ["bash"]
