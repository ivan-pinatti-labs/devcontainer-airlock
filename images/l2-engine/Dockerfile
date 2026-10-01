# The L2 engine: the container runtime that starts L2 containers.
#
# It runs beside the workbench, one per workspace, with the workspace mounted
# and nothing else: no credentials, no agent, no home directory of yours. The
# workbench (and a test suite that starts containers of its own) talks to it
# over a podman socket. Because L2 containers run in here rather than inside
# the workbench, they cannot see the workbench's processes or reach anything
# it listens on. docs/LAYERS.md has the whole picture.
ARG BASE_IMAGE=localhost/airlock-base:local
# hadolint ignore=DL3006
FROM ${BASE_IMAGE}
USER 0:0

# The nested runtime (docs/IMAGES.md, "Running containers inside it"):
#
#   podman, crun, catatonit, fuse-overlayfs, passt, uidmap, libcap2-bin
#   nftables         a bridge network of its own, for workspaces that opt in
#   podman-compose   a test workload can be a whole stack
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    catatonit \
    crun \
    fuse-overlayfs \
    libcap2-bin \
    nftables \
    passt \
    podman \
    podman-compose \
    uidmap \
  && rm -rf /var/lib/apt/lists/*

# Ubuntu ships newuidmap and newgidmap setuid root, and inside a rootless
# container the kernel refuses that write (measured 2026-09-15). A file
# capability works, as Fedora and quay.io/podman/stable ship them. The dev
# account gets every id its own user namespace has, except its own.
RUN chmod 0755 /usr/bin/newuidmap /usr/bin/newgidmap \
  && setcap cap_setuid=ep /usr/bin/newuidmap \
  && setcap cap_setgid=ep /usr/bin/newgidmap \
  && usermod \
    --add-subuids 1-999 --add-subuids 1001-65535 \
    --add-subgids 1-999 --add-subgids 1001-65535 \
    dev

# The runtime's configuration and the crun wrapper; each file explains itself.
COPY containers/ /tmp/containers/
RUN install -m 0644 /tmp/containers/containers.conf /tmp/containers/storage.conf /etc/containers/ \
  && install -D -m 0755 /tmp/containers/crun-without-masked-paths /usr/local/libexec/devcontainer/crun \
  && install -D -m 0644 /tmp/containers/containers-bridge-network.conf /usr/local/share/devcontainer/containers-bridge-network.conf \
  && install -d -o 1000 -g 1000 /home/dev/.config /home/dev/.config/containers \
    /home/dev/.local /home/dev/.local/share /home/dev/.local/share/containers \
  && install -m 0644 -o 1000 -g 1000 /tmp/containers/user-containers.conf /home/dev/.config/containers/containers.conf \
  && install -m 0644 -o 1000 -g 1000 /tmp/containers/storage.conf /home/dev/.config/containers/storage.conf \
  && install -d -o 1000 -g 1000 /run/l2-engine /var/tmp/l2-scratch \
  && install -d -o 1000 -g 1000 /var/lib/airlock/l2-store/storage \
  && rm -rf /tmp/containers

USER 1000:1000
WORKDIR /workspace
LABEL org.opencontainers.image.source="https://github.com/ivan-pinatti-labs/devcontainer-airlock" \
      org.opencontainers.image.description="The L2 engine of the ivan-pinatti-labs workbench: rootless podman serving a socket, with the workspace mounted and no credentials." \
      org.opencontainers.image.licenses="Apache-2.0"
ENTRYPOINT ["catatonit", "--"]
CMD ["podman", "system", "service", "--time=0", "unix:///run/l2-engine/podman.sock"]
