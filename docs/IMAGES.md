# The images

## What is here

Seven images, each built from `images/<name>/Dockerfile` (the two workbenches
from `images/workbench/Dockerfile`) and published as
`ghcr.io/ivan-pinatti-labs/airlock-<name>`. What each one is trusted
with, and why they are split this way, is in [LAYERS.md](LAYERS.md).

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

There is **no version manager** in any of them. Tools come from signed
package repositories, installed with apt; `TOOL_SOURCES.md` is the reference
for where each one comes from and what vouches for it.

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

## What the build does

`.github/workflows/build-images.yml` runs `scripts/build-images.sh` on a pull request touching
`images/**`, on a push to `main`, weekly on a schedule, and on demand. It
builds the base first and every other image on top of that exact build, then
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

Locally, `host/workbench build` builds all six as `localhost/*:local`,
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
