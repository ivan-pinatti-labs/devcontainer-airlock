# devcontainer-airlock agent instructions

Instructions for AI coding agents working in this repository. Claude Code
reads them through `CLAUDE.md`; Codex and CodeRabbit read this file
directly.

## Organization conventions

Shared by every `ivan-pinatti-labs` repository and kept identical across
them, so change it everywhere at once. Where this repository's own sections
are more specific, follow them.

### Everything here is public

- Nothing sensitive, controversial or borderline goes into a commit, pull
  request, issue, comment or committed agent file. That includes secrets,
  tokens, personal paths, email addresses other than a GitHub noreply one,
  host names, LAN addresses and details of anyone's own deployment.
- Personal or machine specific material stays in gitignored files:
  `CLAUDE.local.md` for notes, `.claude/settings.local.json` for settings,
  `.claude/agents/local/` for agents.
- Sensitive content found already committed is reported to a maintainer.
  Never rewrite history or force push to remove it.

### Run binaries in containers, not on the host

A binary that did not come from the operating system's package manager (a
release download, an installer script, a new version under evaluation, a
scanner, a debugging tool) runs inside a rootless Podman container, never
directly on the host. That holds when validating, testing, checking a new
version and debugging.

It holds one level further in as well. In a
[devcontainer-airlock](https://github.com/ivan-pinatti-labs/devcontainer-airlock)
workbench, where the coding agents and their logins live, project code does
not run in the workbench itself: hooks, tests, package installs and
unreviewed binaries run through `l2`, in an L2 container that gets the
working tree and nothing else (no network, no credentials). `l2 --net` adds
network through the workspace's egress proxy, and `l2 --engine` gives a run
the L2 engine, for tests that build or start containers of their own.

```bash
podman run --rm --network=none \
  -v "<only what it needs>:/work:ro,Z" -w /work \
  <image> <binary> [args]
```

- The container gets what the process needs and nothing else. Mount only the
  specific files and folders required, read only. Add network access or
  `:rw` only when the task requires it, and say so.
- Prefer the tool's official image, pinned to a version. For a bare release
  binary use `debian:13-slim` rather than Alpine: glibc builds fail on musl
  with a misleading "No such file or directory".
- On SELinux hosts a bind mount needs a label (`Z`). Do not relabel a large
  tree that other containers also use; copy what is needed into a scratch
  directory and mount that.
- Podman is the default container runtime: rootless, with no daemon.
- Exceptions: the hook environments pre-commit builds, and the containers
  this repository's own `Makefile` or hooks start. In a workbench both run in
  L2 too.

### Parallel work uses worktrees

More than one agent may work in a repository at the same time. Give each task
its own worktree under `.claude/worktrees/<branch>` (gitignored), and never
switch branches in a checkout someone else may be using.

### Unattended work runs on a bounded tick

Work left running while nobody is watching is driven by a bounded pass, never
by a wait for the outcome you want.

A background wait whose only exit is success does not fail, it disappears. A
pull request sitting in a merge queue is the worked example: a flaky check
ejects it, which is neither merged nor closed, so a loop waiting for "merged"
runs forever, nothing notifies, and the session stops. That cost roughly
sixteen unattended hours here on 2026-09-22, and the giveaway is that silence
and progress look identical from outside.

So:

- **Cap every pass**, around fifty minutes, and report on exit whether or not
  anything moved. Time always advances, so no condition can trap it. Say
  plainly when a pass did nothing, because a quiet pass and a dead session
  have to look different.
- **Re-derive state from the API every pass.** Draft status, review verdict,
  unresolved threads, approval, queue membership. Never carry a belief from
  the previous pass.
- **Handle every outcome, not only the good one.** Released from draft,
  review declined, approval job timed out, ejected from the queue, merged,
  closed. Only the last two are final; the rest are recoverable, and that is
  exactly why they have to be handled rather than waited through. A pass that
  only knows how to recognize success cannot recover anything, and treating a
  recoverable outcome as an ending is the failure this whole section is about.
- **Before arming a wait, ask what would wake you if this failed right now.**
  If the answer is nothing, widen the condition.
- **A pass that ends with nothing moved and no reason is a signal to
  inspect**, not to re-arm the same watch.
- **Never finish a turn** without either a bounded wait armed or an explicit
  statement that work has stopped.

### Writing style

Do not use a hyphen, em dash or en dash as punctuation in prose, code
comments, commit messages or pull request text. Use commas, parentheses or
separate sentences. Hyphens inside compound words and in code, paths, flags
and identifiers are fine.

### Commits and pull requests

- Conventional Commits with an imperative subject. Branch names are lowercase
  slugs such as `fix/flaky-test`. Never commit directly to `main`.
- Open a pull request as a draft and mark it ready once the checks are green;
  marking it ready is what starts CodeRabbit. `docs/MERGE_PIPELINE.md` is the
  authority on required checks and how a pull request merges.
- Answer every CodeRabbit comment on its thread, and say plainly when
  declining one and why.
- Never force push.
- Never add AI attribution: no AI `Co-Authored-By` trailer and no "Generated
  with" line, in commits, pull requests, comments, issues or docs.

## What this repository is

Secure, layered devcontainers for AI coding agents: a framework any project
can be developed in, and the one the other `ivan-pinatti-labs` repositories
are developed in, split into layers by what each one is trusted with.
Nothing specific to one organization belongs in the images or scripts; it
goes in the host settings (`host/workbench help`). The
reference is docs/LAYERS.md; docs/IMAGES.md covers how the images are built
and consumed, docs/TOOL_SOURCES.md where every tool comes from.

| Image | Directory | Role |
| --- | --- | --- |
| base | `images/base/` | the floor: git, common tools, trusted apt keys, the dev account |
| workbench-claude, workbench-codex | `images/workbench/` | one per agent: that agent, its editor extension, git; no GitHub token, no ssh key, no container runtime, no other agent's login |
| l2 | `images/l2/` | where hooks, tests and installs run; no network, no credentials |
| l2-engine | `images/l2-engine/` | the rootless podman that starts L2 containers |
| gh-broker | `images/gh-broker/` | holds the GitHub token; runs allowlisted gh commands |
| egress-proxy | `images/egress-proxy/` | one per host, the only way out to the network, by each workspace's egress sets |
| mirror-gate | `images/mirror-gate/` | the package mirror's front (on by default) and its backend's provisioning; the backend (Nexus CE) is used by digest, never baked |

`host/workbench` starts them; it runs on the host and does nothing there but
call podman.

- Keep each layer to its role. Credentials go nowhere but the broker and
  the ssh-agent; project code runs nowhere but L2; the workbench carries
  nothing that executes project code. A change that moves one of those lines
  needs saying out loud in the pull request, with the measurement behind it.
- Widening an allowlist (an egress set or a new one, gh commands,
  extensions, workbench profiles) is a reviewed change of its own, never folded into something
  else. Extensions are pinned to a version at least seven days old.
- Files the agents are held to (`images/workbench/claude/`,
  `images/workbench/codex/`, `images/workbench/bin/route-to-l2`) are policy,
  not a boundary; docs/LAYERS.md says what is. Do not describe them as more.
- An image is consumed by digest, never by a floating tag, so a rebuild
  cannot change what a repository builds against without a commit saying so.
- Every published image is linted (hadolint, through the pre-commit hooks)
  and scanned, and ships an SBOM and build provenance. A secret found in a
  layer blocks the publish. Vulnerabilities are reported rather than
  blocking, except a critical one with a fix available, which blocks.
- The base image carries only what every image needs. A tool one layer or
  one repository needs is installed with apt in that layer, not here. The
  base carries the apt signing keys for that and enables none of those
  repositories itself: see `docs/TOOL_SOURCES.md`. There is no version
  manager anywhere in the organization.
- SELinux stays enforcing. Never reach for `label=disable`, and never loosen
  the crun-without-masked-paths wrapper beyond removing masked paths. The
  containers that talk over sockets share the `container_engine_t` domain
  and one category, because a different one cannot connect.
- Devices passed on to containers the engine starts (the host policy module
  under `host/selinux/`) are an opt in for the repositories whose tests need
  them, never a default. Keep the module to the `mounton` permission it
  grants today, and never suggest the `container_use_devices` boolean
  instead: it widens every container domain on the machine.
- Rebuilds on a schedule pick up upstream security fixes. They publish new
  digests and cut no release, so nothing consumes them until a repository
  bumps its pin.
- The images are built and run with rootless Podman. Anything that assumes a
  daemon, a privileged container or a Docker socket needs saying out loud in
  the pull request, because the organization's rule is least privilege.
