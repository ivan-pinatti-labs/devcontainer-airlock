# devcontainer-airlock

[![License](https://img.shields.io/github/license/ivan-pinatti-labs/devcontainer-airlock?logo=Github&style=for-the-badge)](LICENSE.md)
[![GitHub issues](https://img.shields.io/github/issues-raw/ivan-pinatti-labs/devcontainer-airlock?logo=Github&style=for-the-badge)](https://github.com/ivan-pinatti-labs/devcontainer-airlock/issues)
[![GitHub Sponsors](https://img.shields.io/github/sponsors/ivan-pinatti?logo=Github&style=for-the-badge)](https://github.com/sponsors/ivan-pinatti)
[![GitHub Repo stars](https://img.shields.io/github/stars/ivan-pinatti-labs/devcontainer-airlock?logo=Github&style=for-the-badge)](https://github.com/ivan-pinatti-labs/devcontainer-airlock)
[![GitHub forks](https://img.shields.io/github/forks/ivan-pinatti-labs/devcontainer-airlock?logo=Github&style=for-the-badge)](https://github.com/ivan-pinatti-labs/devcontainer-airlock/forks)
[![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/ivan-pinatti-labs/devcontainer-airlock?utm_source=oss&utm_medium=github&utm_campaign=ivan-pinatti-labs%2Fdevcontainer-airlock&labelColor=171717&color=FF570A&label=CodeRabbit+Reviews&style=for-the-badge)](https://coderabbit.ai)

Secure, layered devcontainers for AI coding agents.

Coding agents (Claude Code, Codex) and their editor extensions are powerful
and trusted with a lot: a GitHub token, an ssh key, the network, and every
hook, test and `npm install` they run. devcontainer-airlock splits that
into layers by what each one is trusted with:

- **A workbench per agent**, where you and that agent work. It holds no
  GitHub token, no ssh key and no other agent's login, and it has no direct
  network and no container runtime.
- **L2 containers** for hooks, tests, package installs and throwaway
  binaries. They get the working tree and nothing else: no network, no
  credentials.
- **Beside them**, a GitHub broker that holds the token and runs an allowlist
  of `gh` commands, an ssh-agent that holds the key, and an egress proxy
  per workspace that allows only the services a project names.

Nothing runs on the host but podman. [docs/LAYERS.md](docs/LAYERS.md)
explains the layers, says plainly which parts are a boundary and which are
only policy the agents are asked to follow, and covers the daily routine.

"devcontainer" here means a development container, not the Dev Containers
specification: there is no `devcontainer.json`. VS Code attaches to a
running workbench (**Dev Containers: Attach to Running Container**).

## Status

The images are published to
`ghcr.io/ivan-pinatti-labs/airlock-<name>` and are in daily use for the
`ivan-pinatti-labs` repositories. Making them easy to adopt in any project
is in progress: some settings are still specific to that organization (the
GitHub owners the broker allows, for one). Until 2026-09-26 this repository
was `devcontainer-images` and the images were
`ghcr.io/ivan-pinatti-labs/devcontainer-<name>`; those old packages are no
longer updated. [docs/IMAGES.md](docs/IMAGES.md) covers what each image
carries and how the build works.

## Requirements

- [Podman](https://podman.io/), rootless. It is the runtime these images are
  built and run with. Nothing here assumes a daemon or a mounted socket.
- An SELinux enforcing host is what this was built and measured on. Nothing
  here turns labelling off.

## Usage

```shell
host/workbench init     # once, in an existing project: egress sets, L2 image, make targets
make workbench-build    # every image, locally (or make workbench-pull)
make unlock             # the ssh key, for eight hours
make claude             # Claude Code in its workbench, started if needed (or: codex)
make claude-shell       # a terminal in that workbench (or: codex-shell)
```

Or attach VS Code to the running `workbench-claude-<folder>` or
`workbench-codex-<folder>` container, whichever agent's extension you want;
each agent has a workbench of its own and cannot read the other's login.
Published images are consumed by digest rather than by a floating tag, so a rebuild
cannot change what a repository builds against until someone bumps the pin.

## How images are built

`scripts/build-images.sh`, in CI and locally alike: base first, then every
other image on that exact base, each scanned before anything is published,
and what is published is the scanned manifest itself. A secret found in a
layer blocks the publish. Vulnerabilities are reported rather than blocking,
with one exception: a critical one carrying a fix blocks. Scheduled rebuilds
pick up upstream security fixes and publish new digests without cutting a
release.

## License

See [LICENSE.md](LICENSE.md) for full details.

## Contribute / Donate

If you use this project, entirely or partially, or get inspired by it,
consider buying me a coffee or a beer, I would really appreciate it:
[buymeacoffee.com/ivan.pinatti](https://www.buymeacoffee.com/ivan.pinatti).
