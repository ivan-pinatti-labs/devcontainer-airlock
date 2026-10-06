# The images

## What is here

Nine images, each built from `images/<name>/Dockerfile` (the two workbenches
from `images/workbench/Dockerfile`) and published as
`ghcr.io/ivan-pinatti-labs/airlock-<name>`. What each one is trusted
with, and why they are split this way, is in [LAYERS.md](LAYERS.md). The
last one, `podman-nested`, is not a layer of the workbench but a test runner
of its own; see "The nested test runner" below.

| Image | Built on | Carries |
| --- | --- | --- |
| `base` | Ubuntu, by digest | git, curl, jq, python3, procps, the trusted apt keys, the dev account |
| `workbench-claude` | base | Claude Code, its VS Code extension and the shared ones, its managed policy, `l2`, the `gh` shim, a podman client |
| `workbench-codex` | base | the same for Codex; both are targets of `images/workbench/Dockerfile` and share every layer below the agent's own |
| `l2` | base | pre-commit, node, go, shellcheck, the baked linters, a podman client for `--engine` runs |
| `l2-engine` | base | rootless podman serving a socket (the nested runtime below) |
| `gh-broker` | base | gh and the broker |
| `egress-proxy` | base | squid, the egress sets and the program that refreshes them |
| `mirror-gate` | base | the package mirror's gate (OSV malicious package filter, backend routes) and the provisioning of its backend; the backend itself is the official Nexus Repository CE image, pinned by digest in `host/workbench` |
| `podman-nested` | `quay.io/podman/stable`, by digest | rootless podman serving its API socket, podman-compose, a health ticker, and the tools a test suite drives a stack with (make, jq, yq, xmlstarlet, pip) |

There is **no version manager** in any of them. Tools come from signed
package repositories, installed with apt (dnf in `podman-nested`);
`TOOL_SOURCES.md` is the reference for where each one comes from and what
vouches for it.

The base deliberately carries none of github-cli, pre-commit, nodejs or
terraform. It carries their signing keys instead, which is the part that is
genuinely common, and enables none of those repositories itself: a keyring
does nothing until a `sources.list` entry names it.

## How a repository uses them

A repository does not build its own development container any more. It runs
the shared workbench (`host/workbench up`, [LAYERS.md](LAYERS.md)) and adds
what its hooks and tests need on top of the shared L2 image, in
`.devcontainer/l2/Dockerfile`:

```dockerfile
ARG L2_IMAGE=ghcr.io/ivan-pinatti-labs/airlock-l2@sha256:<digest>
FROM ${L2_IMAGE}

USER 0:0
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3-pytest \
  && rm -rf /var/lib/apt/lists/*
USER 1000:1000
```

and names the image in `.devcontainer/l2-image`. By digest, never by a
floating tag. A tag would let a rebuild change the toolchain under a checkout
with no commit saying so, which is the failure the digest pin exists to
prevent. Renovate can keep the digest current, and its pull request is then
the record that the environment changed.

Package versions are deliberately not pinned; `TOOL_SOURCES.md` says why.

## Running containers inside it

This section is about the `l2-engine` image, which is where containers are
started from now: L2 containers, and the containers a test suite in an
`l2 --engine` run starts of its own. `host/workbench` passes the flags below
to the engine; the workbench itself runs none of them. The measurements were
taken when the nested runtime lived in the development container, and apply
unchanged to the engine, which is the same runtime in the same SELinux
domain.

A single nested container needs only the two flags above. A nested **compose
stack** needs the wider opt in as well, because compose gives its services a
network and rootless networking needs `/dev/net/tun`; without it the stack
fails with `setting up Pasta: pasta failed with exit code 1`. See "Nested
containers with a network of their own" below, and measured both ways on
2026-09-20: a nested container ran with the two flags, and the same compose
stack only came up once the network opt in was added.

That capability is not only for hook tooling. This organization's rule is
that a binary it has not installed through a package runs in a container, and
once development happens inside the development container, that means a
container started from inside it. An unreviewed binary is isolated from the
checkout, from the agent credentials mounted in, and from the host, where
nothing is meant to run at all. Only reviewed, packaged tooling runs in the
development container itself.

It works when the development container runs in `container_engine_t`, the
confined SELinux domain meant for running a container engine inside a
container:

```shell
--security-opt label=type:container_engine_t --device /dev/fuse
```

**SELinux stays enforcing.** This is not `label=disable`: the development
container is still confined, cannot see host paths that are not mounted into
it, and the host still masks its `/proc` and `/sys`. Without these flags the
container runs in the default `container_t` domain and nesting fails, so a
repository whose tooling never starts a container keeps the stricter default.
A repository that needs nesting adds the flags to its own devcontainer.json.

Why each piece, all measured on 2026-09-15 on an SELinux enforcing host:

- **`container_engine_t`, not the default `container_t`.** `container_t`
  refuses the mounts a nested runtime makes (a new devpts, then mount
  propagation on `/dev/null`). `label=nested` leaves the domain unchanged, so
  it fails the same way.
- **The crun wrapper** (`images/base/containers/crun-without-masked-paths`).
  `container_engine_t` allows those mounts but refuses the tmpfs mounts podman
  uses to mask directories such as `/proc/acpi`, and no podman option can
  unmask all of them. The wrapper removes masked paths from each nested
  container's spec. A nested container then sees the same `/proc` and `/sys`
  as the development container, which the host has already masked, and
  nothing more.
- **`/dev/fuse`** is what fuse-overlayfs, the nested storage driver, needs.
- **Nested containers share the development container's network namespace
  by default** (`images/base/containers/containers.conf`). That is enough for
  hooks and for test suites whose containers do not talk to each other. A
  repository that needs more opts in to it; see "Nested containers with a
  network of their own" below.
- **Anything a development container connects to over a socket runs in the
  same domain and SELinux category.** A process in `container_engine_t` could
  not connect to an ssh-agent running in `container_t`, and could connect to
  one in `container_engine_t` at the same category.

Verified with rootless Podman on the host, with and without
`--userns=keep-id`: a nested container runs through the `docker` command, a
hadolint run shaped like pre-commit's own (a bind mounted working tree and
`--user` passed through) lints its file, and the same nested run without the
flags fails.

### Nested containers with a network of their own

Some tooling needs nested containers that reach each other by address:
rsync-crypt's test suite starts an sshd container and connects to its IP, and
a compose stack has networks of its own. A repository that needs that adds
two more run arguments:

```shell
--device /dev/net/tun --security-opt unmask=/proc/sys
```

and makes a bridge network the nested default, in its devcontainer.json:

```json
"containerEnv": {
  "CONTAINERS_CONF_OVERRIDE": "/usr/local/share/devcontainer/containers-bridge-network.conf"
}
```

Measured on 2026-09-15:

- With the default shared namespace, a nested container has no address of
  its own (`podman inspect` reports none).
- `--network=bridge` without the two arguments fails in turn: silently
  without `/dev/net/tun`, which pasta needs to set up rootless networking;
  then with `netavark: set sysctl net/ipv4/ip_forward: Read-only file system`,
  because the runtime mounts `/proc/sys` read only and netavark writes the
  setting even when it already holds the right value, so `--sysctl` on the
  development container does not help; then with
  `unable to execute "nft"`, which is why the image carries nftables.
- With both arguments, a nested container got an address on the bridge,
  a second nested container reached it there, and both reached the internet.

What `unmask=/proc/sys` exposes, measured as root of the dev account's user
namespace (where nested podman runs): every `kernel`, `vm` and `fs` setting is
still refused, and so are the network settings of the development
container's own namespace. Only the network settings inside a namespace that
user created itself can be written, which is what netavark needs and all it
gets.

### Devices for nested containers

A FUSE mount inside a nested container (rsync-crypt's gocryptfs and sshfs)
and a VPN client (a TUN device) need the device passed on to that container.
The development container holds both devices and may use them, but host
policy refuses bind mounting either one into a nested container:
`crun: set propagation for dev/fuse: Permission denied`, logged as
`denied { mounton }` for `container_engine_t` on `fuse_device_t`
(`tun_tap_device_t` for TUN). `label=disable` on the nested container does
not change that, because the refusal is the host's.

[host/selinux/devcontainer_nested_devices.te](../host/selinux/devcontainer_nested_devices.te)
allows exactly that one permission, for `container_engine_t`, on those two
device types. It is host setup, installed once by someone with root on that
machine:

```shell
checkmodule -M -m -o devcontainer_nested_devices.mod host/selinux/devcontainer_nested_devices.te
semodule_package -o devcontainer_nested_devices.pp -m devcontainer_nested_devices.mod
sudo semodule -i devcontainer_nested_devices.pp
```

`sudo semodule -r devcontainer_nested_devices` removes it again. The
`container_use_devices` boolean is not the alternative: it grants every
container domain, including the ordinary `container_t` every other container
on the machine runs in, access to every device node.

## The nested test runner

`podman-nested` brings up a whole throwaway container stack, compose
included, nested inside one container. The stack's containers, networks,
volumes and images live in that container's own storage, and nothing talks
to the engine socket of the machine it runs on, so a test suite cannot
start, stop or change anything the host's engine runs. That is the whole
guarantee: it is not network isolation from the host. Under rootless
podman's default pasta networking the outer container can still reach a
service listening on the host (through `host.containers.internal` and the
host's own addresses), so a suite that must not reach host services needs a
network policy of its own on top. It is not a layer of the workbench: a repository's
CI (or a developer) starts it directly. The first user is
docker-torrent-box-with-vpn's integration suite.

It builds on `quay.io/podman/stable`, podman's own image for running podman
in a container, pinned by digest, with the tools a suite drives a stack with
on top. It starts as root, and its first program (`podman-nested-init`)
gives the nested engine cgroups where the host allows it (see "Cgroups for
the nested containers" below), then drops to the image's `podman` account
before anything else runs. The entrypoint then serves the nested engine's
API at `/run/user/1000/podman/podman.sock` (`DOCKER_HOST` points there, for
test libraries that speak the Docker API), starts the health ticker, then
runs the command it was given and exits with its status.

```shell
podman run --rm \
  --device /dev/fuse --device /dev/net/tun \
  --security-opt label=type:container_engine_t --security-opt unmask=ALL \
  --memory 3g --memory-swap 3g \
  -v <repository>-nested-storage:/home/podman/.local/share/containers \
  -v <a copy of the checkout>:/work:Z -w /work \
  ghcr.io/ivan-pinatti-labs/airlock-podman-nested@sha256:<digest> make test
```

What each flag is for:

- **No `--user`**: the image starts as root so `podman-nested-init` can
  set up cgroups, and the nested engine then runs rootless as the image's
  `podman` account, which has the subordinate ids nested containers need.
  `--user podman` still works, with the nested containers' cgroups off.
- **`--device /dev/fuse`**: fuse-overlayfs, the nested storage driver.
- **`--device /dev/net/tun`**: pasta, which a nested network of its own (a
  compose stack's default network) needs.
- **`--security-opt unmask=ALL`**: the runtime masks parts of `/proc` and
  `/sys`, and while it does, a nested container cannot mount a `/proc` of its
  own. The upstream image works around that by bind mounting its own `/proc`
  into every nested container, which hands each of them the outer PID
  namespace (a program reading `/proc/self/exe` or `/proc/1` finds a process
  that is not its own). This image drops that bind mount
  (`images/podman-nested/containers.conf`), so it needs the masks removed
  instead. The container stays rootless, unprivileged and without added
  capabilities, so the kernel still refuses anything its user namespace
  does not own.
- **`--security-opt label=type:container_engine_t`**: on an SELinux host,
  the domain meant for a container engine inside a container, the same one
  the L2 engine runs in. SELinux stays enforcing; `label=disable` is not
  needed. Where SELinux is off the option does nothing. A stack that passes
  a device on to a nested container (a VPN client's TUN device) also needs
  the host policy module in "Devices for nested containers" above.
- **`--memory` and `--memory-swap`**: one cap for the whole stack, which is
  otherwise bounded only by the host. With nested cgroups on, the limits a
  stack sets on its own services are enforced inside that cap as well.
- **A named volume on `/home/podman/.local/share/containers`**: the nested
  storage, kept between runs so images are not pulled again. The upstream
  image declares it (and `/var/lib/containers`) a `VOLUME`, so without a
  name each run gets an anonymous volume: `--rm` removes it, but a container
  removed later needs `podman rm -v`, or the volume is left behind.

Not needed: `--init` (catatonit is already the first process, and reaps the
nested containers' conmon processes), `--privileged`, any `--cap-add`, and
any host socket.

A `podman exec` into the running container is root by default, as the
image's user is; pass `--user podman` to reach the nested engine.

Podman schedules healthchecks with systemd timers, and there is no systemd
in here, so a nested container's healthcheck would never run and a compose
service waiting on `condition: service_healthy` would never start. The
health ticker (`podman-health-ticker`) stands in for the timers: every
`HEALTH_TICK` seconds (10 by default) it runs the healthcheck of every
container that has one. A check therefore runs on that tick rather than on
its own interval. `PODMAN_NESTED_SOCKET` moves the API socket and
`PODMAN_NESTED_WAIT` is how long the entrypoint waits for it (30 seconds).

Measured 2026-10-05 on an SELinux enforcing host, with a 512 MiB cap and
each of `label=type:container_engine_t` and `label=disable`: the API socket
answered (`podman --url unix:///run/user/1000/podman/podman.sock info`), a
nested container ran and saw its own `/proc`, a container with a
healthcheck on a nested bridge network turned healthy in about seven
seconds with nothing but the ticker running it, a second container reached
it by name over that network, and a podman-compose stack whose service
waits on `condition: service_healthy` came up. The outer container used about
80 MB of its cap. A `TERM` sent to the container reached the command, and the
command's exit status was the container's.

### Cgroups for the nested containers

Without cgroups, the nested engine can neither report a nested container's
usage (`podman stats`) nor enforce the limits a stack sets on it (`--memory`,
`--cpus`, compose's `mem_limit` and `cpus`): they are accepted and ignored.
Where the host delegates a cgroup v2 tree to the outer container, which
rootless podman does on a systemd host with cgroup v2 (the outer container
gets a private cgroup namespace whose root it owns), `podman-nested-init`
makes that tree usable for the nested engine. As container root, it:

1. moves every process out of the tree's root into a child cgroup, `init`,
   since a cgroup that holds processes cannot hand controllers down;
2. enables the cpu and memory controllers, and io and pids where the host
   delegates them, for the root's children;
3. creates `user/session`, gives `user` to the `podman` account with the same
   controllers enabled, and moves itself into `user/session`;
4. writes `~/.config/containers/containers.conf.d/50-cgroups.conf` for the
   `podman` account, which sets `cgroups = "enabled"`, `cgroupns =
   "private"` and the `cgroupfs` cgroup manager.

It then drops to uid and gid 1000 with `setpriv`, which leaves no
capabilities in effect, and runs the entrypoint, so no command runs as
root. That root was never the host's: under rootless podman it is the
invoking host user, in a user namespace of its own, and only the outer
container's own cgroup tree is changed.

It prints one line saying which way it went:

```text
podman-nested: nested cgroups on (cpu io memory pids)
podman-nested: nested cgroups off (no delegated cgroup v2 tree), so no container stats or resource limits
podman-nested: nested cgroups off (not started as root), so no container stats or resource limits
```

Off means the host delegated nothing usable (cgroup v1, a read only cgroup
tree, a host cgroup namespace, no cpu or memory controller) or the container
was started with `--user`. The drop in is then removed, the nested engine
keeps cgroups disabled as before, and everything else works: containers,
networks, compose and healthchecks. Turning cgroups on without the steps
above would be wrong rather than merely off, as nested containers would land
in the outer container's root and report its usage as their own.

On a CI runner, rootless podman started from a job's shell inherits that
shell's cgroup, which on a hosted runner is owned by root, so it is given no
delegation and the line says off. A job that wants the limits enforced
starts the outer container in a delegated scope of its own:

```shell
sudo systemd-run --scope --uid="$(id -u)" --gid="$(id -g)" -p Delegate=yes \
  podman run --rm ... ghcr.io/ivan-pinatti-labs/airlock-podman-nested@sha256:<digest> make test
```

Measured 2026-10-05 on an SELinux enforcing host with a 512 MiB cap: started
without `--user`, the line said on, a nested busybox started with
`--memory 64m --cpus 0.5` running a busy loop showed about 50% CPU and a
64 MiB limit in `podman stats --no-stream`, and the shell ran as `podman`
with no capabilities. Started with `--user podman`, or with
`--cgroupns=host`, the line said off and a nested container still ran.

## What the build does

`.github/workflows/build-images.yml` runs `scripts/build-images.sh` on a pull request touching
`images/**`, on a push to `main`, weekly on a schedule, and on demand. It
builds the base first and every other image on top of that exact build
(except `podman-nested`, which builds on its own pinned upstream image), then
takes each image through the same steps:

1. Builds it for `linux/amd64`, loading it locally rather than pushing.
2. Scans it for secrets. A finding **fails the build**: a credential baked
   into a layer is not something to report and move on from.
3. Scans it for vulnerabilities and uploads the result to code scanning.
   Findings are reported rather than blocking, so an upstream CVE with no fix
   available cannot wedge the pipeline.
4. Fails on a **critical** vulnerability that has a fix available, and only
   then. That is the one class where blocking is actionable: the fix is a
   rebuild away.
5. Publishes to the GitHub Container Registry, with an SBOM and provenance
   attached, and only from `main`. The gate is the branch rather than "not a
   pull request": `workflow_dispatch` runs against whatever ref it is
   launched from, so a weaker condition would let a feature branch publish.

The weekly rebuild exists because step 4's "a rebuild away" has to actually
happen. It publishes new digests and cuts no release, so nothing consumes
them until a repository bumps its pin.

Locally, `host/workbench build` builds all of them as `localhost/*:local`,
without scanning or publishing.

## Changing a signing key

The three third party apt keys are vendored under `images/base/keyrings/`
and checked against the fingerprints listed in `TOOL_SOURCES.md` before the
build dearmors them. A key that does not match its fingerprint fails the
build.

Rotating one is a deliberate, reviewed change: replace the armored file, put
the new fingerprint in the Dockerfile and in `TOOL_SOURCES.md`, and say in
the pull request where the new key came from and how it was checked.
Renovate does not touch these files and `Pin Only` refuses any diff that
does, so no dependency bot can rotate a key unattended.
