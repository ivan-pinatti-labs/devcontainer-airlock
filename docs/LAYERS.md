# The layers

How the images in this repository fit together, what each one is allowed to
see, and how to use them day to day.
[ARCHITECTURE.md](ARCHITECTURE.md) has the same in one picture, with tables
of who talks to whom and how anything reaches the internet.

<!-- cspell:words tinyproxy userns initializeCommand codeload conmon -->

## Why layers

Everything a developer runs used to run in one development container: the
coding agents, the editor's extensions, git, and every pre-commit hook, test
and package install. That container also held the GitHub token and the ssh
agent socket, so any hook, any npm postinstall script and any extension could
read the token, reach the network and plant code that ran later. Measured
2026-09-24: a test hook read the token, reached the internet, and set
`core.fsmonitor` in the repository's git config, which git would have run the
next time anyone used the repository.

The layers separate what you trust from what you only run.

```text
host         podman, the VS Code desktop, podman secrets. Nothing else runs here.
 ├─ ssh-agent        holds the ssh key; the key never leaves it
 ├─ gh-broker        holds the GitHub token; runs allowlisted gh commands
 ├─ egress-proxy     one per host: the only way out, by each workspace's sets
 ├─ workbench-claude you, Claude Code, its editor extension, git
 ├─ workbench-codex  you, Codex, its editor extension, git
 │                   each: no GitHub token, no ssh key, no direct network,
 │                   no container runtime, no other agent's login
 └─ L2 engine        starts L2 containers; the workspace, nothing else
       └─ L2         hooks, tests, installs, throwaway binaries
                     no network, no credentials, only the working tree
```

| Layer | Runs | Can reach |
| --- | --- | --- |
| host | podman, the editor's window | everything, which is why nothing else runs here |
| ssh-agent | `ssh-agent` | nothing: no network, read only, no capabilities |
| gh-broker | `gh` with the token | GitHub, for the commands in its allowlist |
| egress-proxy | squid, one per host | for each workspace, the domains of that workspace's egress sets |
| workbench (one per agent) | that agent, the VS Code server and that agent's extensions, git | the proxy, the broker socket, the agent socket, the engine socket, the workspace, that agent's own login |
| L2 engine | rootless podman | the proxy, the workspace |
| L2 | pre-commit hooks, tests, package installs, anything the agents run that executes project code | the working tree, and the proxy only when a run asks for network |

L2 containers run in the engine, not in the workbench, so they cannot see
the workbench's processes or connect to anything it listens on (measured
2026-09-24; when they ran nested in the workbench they could list its
processes, read their command lines and reach its localhost). The workbench
itself has no container runtime and no FUSE device; its `podman` is a client
of the engine's socket.

## Using it

Once, on the host:

```shell
podman secret create gh-devcontainer /path/to/a/file/holding/the/token
ssh-keygen -t ed25519 -C devcontainer -f ~/.ssh/devcontainer/id_ed25519
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/workbench"
echo WORKBENCH_GH_OWNERS=<your user or organization> >> "${XDG_CONFIG_HOME:-$HOME/.config}/workbench/config"
make workbench-pull      # the published images; or make workbench-build
```

`~/.config/workbench/config` holds the settings, one `KEY=value` per line; it
is read and never run, and an environment variable of the same name wins.
`WORKBENCH_GH_OWNERS` is the one without a default: the GitHub users or
organizations whose repositories the broker may act on, comma separated.
The others (the secret name, the key path, where the workspaces live, which
agents start) are listed by `host/workbench help`.

The token is a fine grained personal access token for the organization with:

| Permission | Access | Why |
| --- | --- | --- |
| Pull requests | read and write | open, comment on and edit pull requests |
| Issues | read and write | open and comment on issues |
| Contents | read and write | `gh pr ready` and `gh pr merge --auto`: GitHub requires contents write to take a pull request out of draft, surprising as that is ([cli/cli#6924](https://github.com/cli/cli/discussions/6924)), and to put one in the merge queue |
| Actions | read and write | follow checks, read run logs, and `gh run rerun` a run that failed on something outside the change (a flaky check, an outage) |
| Commit statuses | read | follow checks |

Nothing else: no administration, secrets or organization permissions. The
broker never lets contents write be used for anything but `gh pr ready` and
enqueueing a merge: it refuses every `gh api` call that is not a GET,
except a reply to a review comment and the GraphQL `resolveReviewThread`
mutation, and `gh pr merge --admin`, which would skip the queue. Actions
write reaches only `gh run rerun`: `gh workflow run` and `gh run cancel`
are not on the allowlist, and the api takes no other write. Code still
reaches GitHub only by `git push` over ssh, and `main` only through the
merge queue. The
key's public half is added to your GitHub account as an authentication key.

Each day, from the repository you are working on:

```shell
make unlock              # type the key's passphrase; lasts 8 hours
make claude              # a new Claude Code session over the folder you are in,
                         # with voice and remote control (REMOTE=0: without)
make claude-plain        # neither voice nor remote control
make codex               # a new Codex session (no voice)
make sessions            # every session, running or stopped, and its branches
make attach-<session>    # resume one; stop-, shell-, prune- the same way
make prune               # remove stopped sessions holding nothing of their own
make claude-shell        # or codex-shell: a terminal in the workspace workbench
```

`make` alone lists the targets, and Tab completes them. They come from
`host/workbench.mk`, which each repository's Makefile includes, and call
`host/workbench` (`host/workbench help` has every command). `make claude` and
`make codex` start the workspace (proxy, broker, ssh-agent, L2 engine with
the current L2 image, both workbenches) when it is not running;
`make workbench-up` starts it on its own.

### One workbench per agent

Each agent has a workbench of its own, `workbench-claude-<folder>` and
`workbench-codex-<folder>`, and each mounts only that agent's login folder.
So neither agent can read the other's credentials, which a shared workbench
allowed (Claude Code's managed settings could only ask it not to read
Codex's). Both workbenches share the workspace, its L2 engine, its egress
proxy and the helpers, so they work on the same files and the same pull
requests. `WORKBENCH_AGENTS=claude host/workbench up` starts one only.

The two images are targets of one Dockerfile. Everything below the agent's
own layer is built once and stored once, and the agent layer holds only that
agent's CLI, its managed policy and its editor extensions (measured
2026-09-26: 545 MB shared, 486 MB of Claude's own, 1.0 GB of Codex's, most of
it the Codex extension).

For the editor, attach VS Code to the workbench of the agent whose extension
you want (**Dev Containers: Attach to Running Container**). Each image
carries only its own agent's extension, and its machine settings title the
window "Claude workbench" or "Codex workbench" and color the title and
status bars (rust for Claude, green for Codex), so the window says which one
it is. The terminal and the editor are two views of the same container.

Copy and paste in the terminal work through the terminal itself: Ctrl+Shift+V
pastes in, and in a full screen program such as Claude Code, hold Shift while
dragging to select, then Ctrl+Shift+C. The workbench has no access to the
host's clipboard on purpose, since anything running in it could then read
whatever was last copied on the host; Claude Code's `/copy` therefore works
only in a terminal that honours the OSC 52 escape sequence.

The first time in a repository, inside either workbench:

```shell
l2-hooks-install         # git hooks that run pre-commit in L2
```

Then `git commit` and `git push` work as they always have. Hooks that were
installed before are kept as `<hook>.pre-l2` and still run when a commit is
made from outside the workbench. Log each agent in once in its own
workbench (`host/workbench claude`, `host/workbench codex`); their logins
live in `~/.local/share/workbench/claude` and `.../codex`, not in your own
`~/.claude` or `~/.codex`.

### More than one account

Someone with two subscriptions (work and personal, say) lists the extra
accounts in `WORKBENCH_ACCOUNTS` (`host/workbench help`): with
`WORKBENCH_ACCOUNTS=personal`, `make claude-personal` and `make
codex-personal` (and their `-shell` targets) start workbenches of their own,
`workbench-claude-personal-<folder>`, from the same images, with their own
login folder under `~/.local/share/workbench/`. Each logs in once. `make
claude` stays the default account.

Each workbench keeps its own Claude Code configuration and login: the
host's `~/.claude/settings.json` carries hooks that run on the host, which a
workbench must not be able to change. Session history can be shared all the
same, with `WORKBENCH_HISTORY` (say, `claude=~/.claude
claude-personal=~/.claude-personal`): the host's history for the workspace
path and the paths under it (transcripts and memory, nothing else) is
mounted into the matching workbench, so `claude --resume` there lists the
sessions started on the host, and the other way round.

### Sessions

`make claude` and `make codex` each start a session (docs/SESSIONS.md):
one agent run with a workbench, an internal network and an L2 engine of its
own, named at random (`brave-otter`). Sessions run side by side, in any mix
of agents and accounts, and cannot reach each other's processes, files or
containers. A session reads the workspace's clones read only and writes
only in its own folder in each repository, `<repo>/.claude/worktrees/<name>`.
Other sessions' folders are inside the read only clone, so a session can
read them but not write them. The workspace folder's own `.claude` is a
folder of the session's state with each of its entries mounted read only,
because Claude Code's command sandbox creates placeholders there (`skills/`,
`hooks/` and more) before running anything; those stay with the session and
never reach the real folder.
`airlock-worktree <repo> [<branch>]`, run in the session, clones the
repository there, independent of the main clone. Transcripts, history and
memory stay shared: every session starts in the workspace's root, so it
writes under the same project. So does the account's agent folder, login
and settings with it: Claude Code saves its login by renaming a file over it
in that folder, so it cannot be shared on its own (docs/SESSIONS.md, "Why
the account folder is shared"). The session stops when its agent exits, and
its record, folders and engine stay, so `make attach-<name>` brings it back
with the same conversation, after a reboot too. `make prune` removes the
stopped ones whose clones hold no uncommitted change and no commit missing
from every remote.

### Several repositories at once

For work that spans repositories, start the workspace from the folder that
holds their clones side by side (one that is not itself in a git checkout):
`host/workbench up` there, or `host/workbench claude`. Or `make claude`, with
a Makefile in that folder that includes `devcontainer-airlock/host/workbench.mk`.
That is a group workspace:

- Each clone is mounted on its own, never the folder as a whole, so nothing
  else in it is relabelled. A clone marked worktree only brings just its git
  directory and its `.claude/worktrees`: its data stays where it is, and work
  in it happens in a worktree created from inside the workbench.
- Each repository keeps its own environment. `l2` in a checkout builds and
  uses that repository's L2 image and cache, as in its own workspace, so its
  hooks and tests run exactly as they would there.
- The proxy allows every egress set any of the repositories asks for, and the
  engine gets every profile any of them opts in to; each read from the
  checkout and from the remote's default branch, since a main clone may be
  behind. The folder's own `.devcontainer/egress-sets` and
  `workbench-profile`, when it has them, add to those.
- The folder's `CLAUDE.md`, `AGENTS.md` and `.claude` come along, so an
  agent started there reads the same instructions as one started on the
  host.
- The folder itself, in the workbench, is a directory kept with the
  workbench's state, `~/.local/share/workbench/groups/<name>-<hash>/root`
  (the hash is of the folder's full path, so two groups whose folders share
  a name never share it), with the clones and those files mounted on top. It
  has to be writable, since
  Claude Code's command sandbox creates a placeholder there for each file it
  guards, and the real folder cannot be mounted without relabelling
  everything in it. So a file created at the root in a workbench stays in
  that state directory, not in the folder: work belongs in the clones.

### Per repository

`host/workbench init` sets up an existing repository (run it in the
repository, or pass its path): `.devcontainer/egress-sets` from the files it
finds (Python and Node for any repository with pre-commit hooks, plus Go,
Docker Hub or HashiCorp when it sees their files), `.devcontainer/l2/Dockerfile`
on the published L2 image pinned by digest, and the `include` that gives its
Makefile the workbench targets (a new Makefile when there is none). It writes
only what is missing and never edits anything else, so it is safe to run
again; check the egress sets it guessed before committing them. A project
created from `ivan-pinatti-labs/github-template` has all of this already.

A repository adds what its hooks and tests need on top of the shared L2
image in `.devcontainer/l2/Dockerfile`. `l2` builds that image in the engine
(behind the egress proxy) the first time it is needed, and again whenever the
file changes, so there is no build step to remember. It opts in to more for its engine in
`.devcontainer/workbench-profile`, one name per line, from a fixed list
(`host/workbench --help`): `nested-network` for tests whose containers talk
to each other, `hooks-engine` for hooks that build or start containers,
`nested-devices` for FUSE or TUN inside those containers. Anything else in
that file stops `up`, so a repository cannot pass arbitrary flags to podman
on the host.

A repository whose main clone holds data other containers use (the volumes
of a stack it runs, for instance) adds an empty
`.devcontainer/workbench-worktree-only`. Its workspace is then always the
worktree the command runs in, and `up` refuses the main clone, which it
would otherwise mount and relabel under those containers.

When a branch changes `.devcontainer/egress-sets` or adds an L2 image, start
the workspace from that branch's worktree: the proxy takes a workspace's sets
from the checkout `up` runs in, and every `up` registers them again, so a
changed file takes effect on the next one.

## L2

`l2 COMMAND` runs a command in a throwaway container, started by the engine
from the repository's L2 image.

- No network. `l2 --net` gives it the egress proxy, for installs.
- No credentials and nothing from the workbench's environment
  (`l2 --env NAME=VALUE` passes one variable on purpose).
- The working tree at its own path, read write (`l2 --ro` for read only).
- The git directory read write, because git needs its index, with
  `.git/config` and `.git/hooks` read only on top. Those are where a hook
  could plant code that later runs in the workbench. A fully read only git
  directory is not an option: pre-commit then fails to write `index.lock`
  (measured 2026-09-24).
- `.devcontainer`, `.vscode`, `.claude`, `.codex`, `Makefile` and `host`
  read only, because the workbench or the host execute them.
- `/root` on a volume per repository, which is where pre-commit keeps its
  hook environments.
- An `l2` round trip costs about 90 milliseconds.

Every engine on the host reads the L2 images from one shared store, the
`l2-store` volume, mounted read only and listed in the engine's
`storage.conf` as an additional image store. `host/workbench up` copies the
host's L2 images into it when they change, so an engine keeps no copy of
its own: measured 2026-10-01, an engine reached its socket in 0.16 seconds
with 124 KB of disk, an L2 run from the store took 0.16 seconds, and
filling the store with the 996 MB L2 image took 5.5 seconds, once for every
engine. Images an engine builds or pulls stay in its own store, but an
image built on the L2 image keeps its lower layers in the shared one. So a
replaced L2 image is never pruned automatically: the engine volumes, which
outlive their engines, may hold containers and images that still need its
layers. To reclaim the space, with every workspace down, remove the
`l2-store` volume together with the `l2-engine-*` volumes; the next `up`
fills the store again and repository L2 images are rebuilt on first use.

### pre-commit

`l2-pre-commit` is pre-commit run in L2, and what the git hooks
`l2-hooks-install` writes call. Before each run the hook environments are
installed or confirmed (`l2 --net --ro -- l2-prepare-hooks`, a quick no-op
once they exist, which also installs what the pre-commit-checklists hooks run
in their own nested pre-commit). Then the hooks run with no network.

Formatters open files for writing even when they change nothing, so during a
pre-commit run the protected paths above are writable. They are compared
before and after instead, and a run in which a hook actually changed one
fails, naming the files. Measured 2026-09-24 with a hostile test hook: its
rewrite of `.devcontainer/devcontainer.json` was caught, its write to
`.git/config` refused, and it saw no token.

Hooks that only work with the open internet (`markdown-link-check`,
`lychee`) are skipped in local runs and left to CI. `L2_SKIP_HOOKS`
overrides that list.

### Tests

A test suite runs in L2 like any other command. A step that needs GitHub (a
fixture fetched with `gh`) runs in the workbench first, through the broker;
L2 never gets the broker, because that would hand GitHub access to the code
under test.

A suite that builds images and starts containers of its own runs with
`l2 --engine`: that run gets the engine's socket (its `docker` and `podman`
talk to the engine), and a scratch directory as `TMPDIR` that the containers
it starts can mount at the same path. Those containers are L2 as well; the
engine holds no credentials.

Measured 2026-09-24, all in L2:

| Repository | What | Result |
| --- | --- | --- |
| devcontainer-airlock | every commit on this branch | all hooks, through the git hooks |
| gh-actions | 44 hooks over every file; the pytest suite | 4 seconds; 261 passed, 94% coverage, in 18 seconds |
| pre-commit-checklists | its own self-test suite (`l2 --net`) | 160 of 160 assertions |
| rsync-crypt | the suite, which builds the image and starts sshd and gocryptfs containers (`l2 --engine`) | 311 passed in 20 seconds |
| rsync-crypt | the pre-push trivy image scan (`hooks-engine`) | passed |

### Container based hooks

L2 cannot start containers on its own. The linters that upstream ships only
as images (hadolint, actionlint, dotenv-linter) are copied out of their
official images into the L2 image, and L2's `docker` command runs them for
the hooks that call `docker run`. A hook asking for a version the image does
not carry fails and says so. In an `--engine` run, anything else `docker` is
asked to do goes to the engine.

actionlint runs as an account of its own inside L2. v1.7.12 deadlocks when a
`run:` script is larger than the pipe capacity left to the calling uid
(upstream rhysd/actionlint#702), and L2 runs as your host uid, which usually
has little left: on gh-actions it hung past 60 seconds as that uid and
finished at once as nobody. That is why L2 keeps the `setuid` and `setgid`
capabilities, which act only inside its own user namespace.

## The coding agents

Both agents run in the workbench, and both are held to the same policy by
files they cannot edit (root owned, read only):

- `/etc/claude-code/managed-settings.json`: a `PreToolUse` hook rewrites
  any command that runs project code (a language runtime, a package manager,
  a test runner, a script from the working tree) into `l2 ...`, and
  `pre-commit ...` into `l2-pre-commit ...`, so it runs in L2 without a
  prompt. `allowManagedHooksOnly` stops project or user settings removing
  it. Claude Code's own sandbox is on, in its nested mode, as an extra layer
  inside the workbench. It blocks every unix socket on Linux (its per path
  socket list is macOS only) and sends traffic through a proxy of its own,
  so the commands that need the broker, the ssh-agent or the engine are in
  its `excludedCommands`: `l2`, `git`, `podman`, `gh` and `ssh-add -l`.
  A call leaves the sandbox only when every part of it matches an
  exclusion: `cd ... && git push` and `gh ... | head` stay inside, since
  `cd` and `head` are not excluded. `git -C` and `git -c` do not match
  `git *` at all (measured with Claude Code 2.1.283). Whatever stays inside
  cannot reach the sockets and fails. Excluded commands still go through
  the permission prompts.
- Which commands run without asking, which ask and which are refused comes
  from agent-policy and the airlock's overlay on it (below). For `podman`
  (a client of the engine) that means reads (`ps`, `images`, `inspect`,
  `logs` and the like), harmless additions (`pull`, `network create`,
  `volume create`) and `run`, `exec`, `build`, `start` and `cp` run without
  asking. Those last ones are a way around `l2`'s protections: a container
  started by hand gets whatever it mounts read write, `.git/config`
  included, which `l2` keeps read only. That is accepted, since what
  protects the credentials is that the engine holds none, and the guard
  asks before a run that would reach further (below). Anything that
  removes, stops, loads, pushes or reconfigures asks, as does a global
  flag, which could point the client at another engine.
- Also there, the status line names the workbench the session runs in
  (`AIRLOCK_WORKBENCH`, the container name), then the folder (`~` for
  your home folder on the host, `...` for `.claude/worktrees`), its branch in a
  repository, the model and its effort, so one terminal can be told from
  another. It takes the place of a status line of your own. The
  container's host name is the same name, so a shell prompt in it,
  Codex's included, shows it too.
- `/etc/codex/requirements.toml`: Codex keeps its own sandbox (measured
  working inside the workbench: read only, no network) and asks before
  acting, and the same hook refuses project code with the `l2` command line
  to use instead.

This is policy, not a boundary. Command matching can be defeated by a
determined agent (`sh -c` inside a script, for instance); what actually
protects the credentials is that the workbench does not hold the GitHub
token or the ssh key, and that L2 holds nothing at all.

### agent-policy

[agent-policy](https://github.com/ivan-pinatti-labs/agent-policy) is the
source of truth for what the agents may run: every allow, ask and deny rule,
the guard hook and the sandbox path lists live there, so a new rule, a
hardening or a fix to a rule is a pull request there. It is a repository of
its own because it serves more than the airlock (an agent on a host, say),
but the airlock is its main use. The workbench renders it at a pinned
release when the image is built (`AGENT_POLICY_REF` in
`images/workbench/Dockerfile`, a tag and its commit; the build refuses a tag
that has moved), together with the airlock's overlay, for both agents.

- **The rules**, agent-policy's and the overlay's, as one managed drop-in,
  `/etc/claude-code/managed-settings.d/50-agent-policy.json`. The workbench's
  own `managed-settings.json` keeps no rules, only its mechanics.
- **The guard hook**, a second managed `PreToolUse` hook beside
  `route-to-l2`, reads the whole command line: force pushes in any
  spelling, hook bypasses (`--no-verify`, `SKIP=`, `HUSKY=0`,
  `git -c core.hooksPath=...`), reads of credential files (here, the
  agent's own login), and container runs that would reach too far
  (`--privileged`, host namespaces, a mount of the home folder or of the
  engine socket). It answers ask or deny, or nothing. Claude Code takes the
  strictest answer of the two hooks, so it only ever tightens what
  `route-to-l2` allows, and it judges the command as written, not as
  `route-to-l2` rewrites it.
- **The sandbox path lists**, merged into `managed-settings.json`:
  credential folders and files that no sandboxed command may read, and PATH
  and startup folders that none may write. The workbench keeps its own
  short list of commands that run outside the sandbox (agent-policy's lets
  more out, such as `ssh` and `docker`).
- **For Codex**, the rules as prefix rules, `agent-policy.rules` in Codex's
  rules folder, put back from the image at every start beside
  `workbench.rules` (Codex takes the strictest rule that matches), and the
  guard as a second managed hook in `/etc/codex/requirements.toml`, where an
  ask becomes a refusal that says approval is needed. Codex loads a managed
  hook only with `[features] hooks = true` and the hook's script in
  `[hooks] managed_dir`, so the guard is installed beside `route-to-l2` in
  `/usr/local/libexec/workbench`, the folder named there.

**The overlay**, `images/workbench/agent-policy/*.toml`, is what the
airlock adds on top: rules in agent-policy's own format and severity scale,
copied into its policy before the render, so its validator checks them and
a file named like one of agent-policy's fails the build. Claude Code
resolves deny over ask over allow across every rule, so the overlay can
only add or harden, never loosen: a rule that should be looser belongs in
agent-policy, and so does any rule here that every agent environment would
want. Today it holds what only the workbench has (`l2 --image`, a podman
global flag that could point at another engine) and a hardening
(`podman stop` asks, where agent-policy allows it). Two rules that started
here moved upstream in agent-policy v0.2.0: asking on the last podman
subcommands it did not list, and refusing Claude Code's own file tools on
the agents' logins. The workbench renders it with `--no-scratch`, since its
agents run containers through the L2 engine, not agent-policy's scratchpad.

All of it is root owned, as the rest of `/etc/claude-code` is. It is still
policy: the same caveat as above applies to every one of its rules. A new
release reaches the workbench through a Renovate pull request that waits
for a person, since a release can loosen as well as tighten.

### Remote Control

Claude Code sends most of its HTTPS through the egress proxy as CONNECT
tunnels, but Remote Control (2.1.283) sends its requests to the proxy in
absolute form, from the registration (`POST
https://api.anthropic.com/v1/environments/bridge`) to the polling for work,
leaving the TLS to the proxy. The egress proxy refuses that: opening the
TLS itself would let it read the request, login token included. Claude Code
reports the refusal as "Registration: Access denied (403). Check your
organization permissions", which reads like an account setting and is not
one.

So `claude` in the workbench is a wrapper (`images/workbench/bin/claude`)
that runs Claude Code behind a relay of ours on `127.0.0.1:8889`
(`airlock-relay`), started by the first session and shared by the rest.
The relay turns an absolute form `https://` request into a CONNECT tunnel
through the egress proxy and opens the TLS itself, in the workbench, which
already holds the login. Everything else, CONNECT included, passes through
untouched, so the egress sets still decide every destination (measured: a
host in no set is refused through the relay as it is without it).
`AIRLOCK_EGRESS_PROXY` keeps the egress proxy's address, and `l2 --net`
passes that one on, since an L2 container cannot reach the workbench's
localhost.

## Voice

Claude Code's voice mode records the microphone with SoX (`rec`), while
space is held. The workbench has no audio device and no way to the host's
audio server, so the microphone reaches it as a stream of bytes, per
session and only while Claude Code records:

- `make claude` and `make claude-remote` (and `make claude-<account>`,
  `-remote`) start Claude Code with voice; `make claude-plain`,
  `make claude VOICE=0`, `host/workbench` on its own and Codex have none.
  With `WORKBENCH_VOICE=1`, `host/workbench` asks PipeWire on the host,
  through `pactl`, for a named pipe (16 kHz, 16 bit, mono) in that
  workbench's voice folder, makes a second pipe the other way, and starts
  Claude Code with `WORKBENCH_VOICE_MIC` and `WORKBENCH_VOICE_CTL` naming
  them.
- The `rec` in the Claude workbench image writes `start` on the second
  pipe when Claude Code starts it and `stop` when it ends. Only then does
  `host/workbench` route the microphone into the first pipe, and a single
  recording is cut off after ten minutes. It reads at most 16 bytes at a
  time from that pipe and compares them with those two words; nothing it
  reads is run. `rec` reads the first pipe instead of a device, dropping
  what an earlier recording left. Outside a voice session it fails, so
  Claude Code says there is no microphone.
- When the session ends, both pipes and PipeWire's modules go with it.
- When voice cannot be set up (no `pactl`, PipeWire refusing), the session
  says why and starts without it.

Every Claude workbench mounts its own voice folder
(`$XDG_RUNTIME_DIR/workbench/voice/<name>`) read only, and it stays empty
unless a voice session runs. A workbench started before this existed has no
such folder; a voice session there says so and starts without the
microphone until the workbench is restarted (`host/workbench down`, which
ends every session in it).

Why a pipe and not the audio server's socket: the socket was tried first
and measured 2026-09-28. Any client of it can load modules into the host's
PipeWire, which can then open connections to the internet around the egress
proxy, listen on the host, or create files there. It also needed a host
SELinux module letting the workbench domain connect to desktop processes.
Reading a pipe needs no policy change and gives the workbench audio and
nothing else.

What it does allow: while a voice session runs, anything in that workbench
able to write `start` on its control pipe turns the microphone on, and
anything able to read the other pipe hears it, for at most ten minutes at a
time. The audio goes to Anthropic's speech service like the rest of the
agent's traffic, through the egress proxy.

Voice works in a remote control session too, with the host's microphone,
so from the computer rather than from the paired device. `make claude`
starts a remote control session unless `REMOTE=0`, so the session shows
up on your other devices. `DO_NOT_TRACK` stays set: Claude Code 2.1.283
starts Remote Control with it (measured 2026-10-01). It reaches only
`api.anthropic.com`, most of it in a form the egress proxy refuses, which
is why Claude Code runs behind a relay here ("Remote Control", above).

## GitHub access

The everyday flow runs entirely from the workbench, for you and for the
agents alike: `git push` a branch, `gh pr create --draft`, follow the checks
(`gh pr checks`, `gh run view --log-failed`), then `gh pr ready` once they
are green, which is what starts CodeRabbit. Once the review is answered,
every thread resolved and the pull request approved, `gh pr merge --auto`
puts it in the merge queue, which merges it when its own run of the checks
passes. The broker refuses `--admin`, so nothing merges around the queue.

There is no `gh` binary and no token in the workbench. The `gh` command there
sends its arguments to the gh broker, which runs the real `gh` with the token
if the command is in `images/gh-broker/allowlist.json`, and returns the
output. Anything running in the workbench can use GitHub through it; nothing
can take the token away. Every request is logged: `podman logs gh-broker`.

Refused: anything outside the allowlist (including `gh auth token`, `repo
delete`, `secret`), repositories and owners outside `WORKBENCH_GH_OWNERS`
(named by `-R`, `--repo`, `--owner`, a URL, or a `repo:`, `org:` or `user:`
qualifier in a search), `gh api` with a method other than GET, and GraphQL
mutations, except the two writes `allowlist.json` names (a reply to a review
comment, and `resolveReviewThread`). Reads are the exception to the owners:
the commands under `public_reads` (issue, pull request and release `list`
and `view`) and `gh api` GETs under `repos/` may name a repository outside
them when GitHub says it is public, so an upstream issue can be followed.
The broker asks GitHub (`repos/<owner>/<repo>`, with the token) and keeps
the answer for ten minutes; anything private, missing, or not plain
`owner/name` stays refused, and so does every write. GraphQL queries are
not held to any repository by the broker; the token holds them. Being fine
grained for the organization, it reads the organization's private
repositories and public ones elsewhere, nothing else. That boundary is the
token's: a classic token, which reaches every repository its user can,
would remove it, so keep the token fine grained. Interactive prompts are not
available, so pass the flags a prompt would ask for. The broker never reads
a file named on the command line, since it would read it beside the token;
the workbench `gh` reads a `--body-file` (or `-F`) path itself and sends the
text as the body on stdin. A refusal says which rule it hit.

`git push` goes over ssh through the ssh-agent container, which signs the
login without ever handing over the key. The connection runs to
`ssh.github.com` on port 443 through the egress proxy, and GitHub's host key
is checked against keys fetched from GitHub's own API. `host/workbench
unlock` adds the key for eight hours at a time.

## Network

Each workspace has an internal podman network of its own (no route, no DNS)
holding its workbenches and its L2 engine. One egress proxy serves every
workspace on the host: it sits at `.2` on each of those networks, which is
their only way out, and on podman's own network for its way out. One
project's allowances never apply to another's: a request is matched to a
workspace by the subnet it comes from and by the proxy address it arrived
at, and gets only that workspace's sets. A container on one workspace
network has no route to another's (measured 2026-09-29: a connection from
one workspace to the proxy's address on another fails, and a request
reaching the proxy from podman's own network is refused with a 403).

`host/workbench up` registers the workspace with the proxy, a file per
workspace in `$XDG_RUNTIME_DIR/workbench/egress` holding its subnet and
sets, connects the proxy to its network and reloads squid; `down` does the
reverse. A reload leaves every open connection alone (measured: a tunnel
kept carrying data through two reloads, one workspace joining and one
leaving) and takes about 0.1 seconds. A set name the proxy does not know
stops that workspace's `up` and leaves the others as they were.

Restarting the proxy itself (`host/workbench restart-proxy`, after
rebuilding its image, or `helpers-down`) cuts every workspace off for the
few seconds it takes. It starts again with every registered workspace's
network, so nothing needs to be brought up again.

The shared services (the egress proxy, the package mirror, the gh broker
and the ssh-agent) follow the workspaces. The `up` that finds one of them
not running starts it, and the `down` that leaves no workbench or L2 engine
on the host stops them all, so nothing runs that nothing uses. Their
volumes stay, so the next start is warm. Stopping the ssh-agent drops the
unlocked key, so `host/workbench unlock` again after that. `up` and `down`
take a lock, so a `down` cannot stop the services under an `up` starting
another workspace.

### Egress sets

What a proxy allows is built from **egress sets**, one per service, in
`images/egress-proxy/sets/`:

| Set | Allows | Provider list, refreshed |
| --- | --- | --- |
| `workbench` (always) | the agents' APIs, VS Code server and extension downloads, their certificate checks | |
| `github` (always) | github.com, the API, codeload, ssh over 443, release and raw downloads | GitHub's ranges from `api.github.com/meta`, enforced |
| `ghcr` (always) | GitHub's container registry, where these images are published | GitHub's ranges, enforced |
| `python`, `node` | PyPI, npm | |
| `golang` | the Go module proxy, and every Cloud Storage bucket (below) | |
| `ubuntu`, `nodesource`, `hashicorp` | apt repositories, for building images | |
| `docker-hub`, `quay` | those registries and their CDNs | |
| `hashicorp`, `opentofu` | the Terraform and OpenTofu registries and downloads | |
| `alpine`, `fedora`, `trivy`, `sigstore` | Alpine and Fedora packages, trivy's database, sigstore's trust root | |
| `aws` | AWS service APIs | AWS's ranges from `ip-ranges.amazonaws.com`, enforced |
| `sonarqube-cloud` | SonarQube for IDE in connected mode: SonarQube Cloud (EU region), its scanner and events hosts, SonarSource's analyzer downloads | |

`podman run --rm localhost/airlock-egress-proxy:local egress-refresh
--list` prints them with their descriptions. A repository lists the sets it
needs in `.devcontainer/egress-sets`, one per line (with no file: `python`
and `node`); `workbench`, `github` and `ghcr` are always added, and
`ubuntu` and `nodesource` too when the repository has an L2 image of its own
to build. An
unknown name stops the proxy from starting, and the error lists the known
ones.

A set is a small TOML file: a description, its domains (`example.com` for
that name alone, `*.example.com` for it and every name under it), and
optionally a provider whose published list is fetched by the proxy. Where a
set enforces its provider's ranges, a request passes only when its name is on
the set's list **and** the address the name resolves to lies inside those
ranges (measured 2026-09-25: `pypi.org` placed in a set bound to GitHub's
ranges was refused, `github.com` in the same set passed). Adding a service is
adding a file; adding a provider is a function in
`images/egress-proxy/bin/egress-refresh`.

### Keeping the lists current

The proxy fetches its providers' lists when it starts and every six hours
(`EGRESS_REFRESH_SECONDS`), merges overlapping ranges, and reloads squid in
place. The last good copy is kept in the `egress-cache` volume. A fetch that
fails falls back to that copy, saying how old it is; with no copy at all the
set keeps its static domains without the address check, and says so loudly.
That last case favours availability on purpose: a provider's API being down
should not stop anyone working while the domain list still holds.

### The proxy's own DNS

Workspaces have no DNS at all: they name a host to the proxy, and the proxy
resolves it. The proxy resolves only through its own resolver, on its
loopback, which no workspace can reach. unbound caches and DNSSEC validates,
and sends every query encrypted to Cloudflare's security resolvers,
`1.1.1.2` and `1.0.0.2`, which also refuse known malware domains.
`WORKBENCH_DNS` picks how: `doh` (the default), DNS over HTTPS on port 443
through dnscrypt-proxy, or `dot`, DNS over TLS on port 853 from unbound
itself, one process fewer where the network allows it. It takes effect at
the proxy's next start (`host/workbench restart-proxy`). The certificate is checked against
`security.cloudflare-dns.com`, so nothing on the way can read or change a
query or an answer, and nothing in the container asks podman's or the
host's resolver. Answers that point into private ranges are dropped.

Measured 2026-10-01: names resolve (`api.github.com`, `pypi.org`), a domain
with a broken DNSSEC chain (`dnssec-failed.org`) does not, Cloudflare's
malware test domain resolves to `0.0.0.0`, and the container's only
connections out were to `1.1.1.2:443`. DNS over TLS is not the default because
port 853 is blocked on many networks, as it was by the firewall of the host
this was measured on; DNS over HTTPS on 443 is not. With `dot` there and
853 blocked, the proxy starts, says no name resolves, and every request
fails until the setting is back to `doh`.
Port 53 on loopback is opened to the proxy's unprivileged account with
`net.ipv4.ip_unprivileged_port_start`, inside the container's own network
namespace only.

### The proxy

squid, tuned to decide and forward only (no cache, small lookup tables): 13
MB resident with seven sets and every AWS range loaded, measured 2026-09-25,
against 89 MB with squid's defaults. One proxy for every workspace rather
than one each: measured 2026-09-29 in an L2 engine, four workspaces took 146
MB resident behind four proxies (36 MB each, with its shell and conmon) and
37 MB behind one. tinyproxy, which Qubes OS uses for its
updates proxy, is lighter still (4 MB), but it can only match a host name,
and checking where that name resolves is what makes a provider's ranges
worth having. A refused request answers `403 Forbidden`, and `podman logs
egress-proxy` shows each decision (by client address, so by workspace) (`TCP_DENIED` or `TCP_TUNNEL`,
with the address the name resolved to), which is the first place to look
when a tool fails to download something.

The proxy decides by host name and address without inspecting TLS, so it
cannot stop data leaving through a host it allows (a gist on github.com, for
instance). It stops what is not on the list; it does not make the list safe.

The broadest host in any set is in `golang`: Go's module proxy hands its
downloads off to `storage.googleapis.com`, so that set opens every Cloud
Storage bucket there, not only the proxy's. Fetching modules from their own
repositories instead was tried and fails verification for some of them
(measured 2026-09-25: gitleaks v8.30.1 against the checksum database), so
there is no narrower way in.

So golang hooks do not use it. pre-commit builds them from source, which
means they need Go modules rather than a binary, and the L2 image carries
exactly those modules as a read only module proxy
(`images/l2/go-modules.txt`), fetched and verified against Go's checksum
database in a throwaway stage of the image build. The same installs then run
again from that proxy alone, offline, so a module missing from it fails the
image build. L2 runs with `GOPROXY` pointing there and nowhere else, and
does not ask the checksum database again: the check happened at build time,
and the image digest pins the result. (Asking it offline did not work:
measured 2026-09-26, a lookup recorded at one tree size could not be proved
against the later tree head without tiles the build never fetched.) A hook
pinned to a version missing from the list fails with "module lookup disabled
by GOPROXY=off": bump the list first, then the pin. Select `golang` only
to build Go against the network, as building the L2 image itself does.

## Package mirror

A read through mirror of the public registries, one for the host like the
egress proxy, on by default (`WORKBENCH_MIRROR=0` turns it off). Installs and
image pulls in L2 and the engine then go through it, and a workspace that
runs code nobody has reviewed can be given the mirror and nothing else.

```text
L2 run, engine  ──>  gate (.254 on every workspace network)  ──>  backend  ──>  egress proxy  ──>  registries
                     fixed paths, OSV filter                     Nexus CE      the upstream sets only
```

| Piece | Runs | Network |
| --- | --- | --- |
| `mirror-gate` | the only part clients reach: fixed paths per ecosystem, registry mirror ports, the malicious package filter | `.254` on each workspace network, and the mirror network |
| `mirror-nexus` | Nexus Repository Community Edition, the first backend: proxy repositories only | the mirror network only, never a workspace network |
| provisioning | a one shot run of the gate image each time the backend starts | the mirror network |

### What clients see

`l2 --net` gives a run the gate's addresses (`AIRLOCK_MIRROR` in the
workbench says where it is):

| Ecosystem | Setting | Gate path |
| --- | --- | --- |
| PyPI (pip, uv, pre-commit's python hooks) | `PIP_INDEX_URL`, `UV_DEFAULT_INDEX` | `/pypi/simple/` |
| npm (and pre-commit's node hooks) | `NPM_CONFIG_REGISTRY` | `/npm/` |
| Go | `GOPROXY`; `GOSUMDB` stays on, the gate serves `sum.golang.org` too | `/go/` |
| apt (Ubuntu, main and security) | the gate as the plain http proxy | `/apt/ubuntu/`, `/apt/ubuntu-security/` |
| Alpine apk | a repositories line pointing at the gate | `/apk/alpine/` |
| yum and dnf (Fedora) | a baseurl pointing at the gate | `/yum/fedora/` |
| docker.io, ghcr.io | the engine's registries.conf, mirrors on ports 5000 and 5001 | `/v2/` |

The engine gets the same for its own pulls and for the images it builds:
registry mirrors in a registries.conf drop-in, and the gate as its plain http
proxy, so apt in an image build is served from the mirror. pip and npm in an
image build still go through the egress proxy unless the Dockerfile points
them at the gate.

`mirror` in `.devcontainer/egress-sets` is not an egress set: it says the
workspace installs through the mirror, and `up` refuses it when the mirror is
off. A workspace that lists it and nothing else reaches no registry directly
(measured below: pypi.org refused through the proxy, installs through the
gate working).

### The filter

The gate takes out of npm, PyPI and Go answers every version that OSV lists
as malicious (the OpenSSF malicious packages feed, entries named `MAL-`), and
refuses their downloads with a 403 that says why. `WORKBENCH_MIRROR_MIN_AGE`
also hides npm and Go versions published fewer days ago than that. The first
sync downloads each ecosystem's full OSV file once; later ones fetch only the
entries changed since, every six hours. The last good copy is kept in the
`mirror-osv` volume, and `/_status` on the gate says when each feed synced.

Vulnerabilities (CVE and GHSA entries) never block anything here; they are
reported, as everywhere in the organization. Docker Hub has no malicious
package feed in OSV, so images are not filtered; the registries are mirrored
for caching and for keeping the engine off the open network.

### Read through only

Every repository the provisioning keeps is a proxy of a public registry;
everything else, the hosted repositories Nexus ships with included, is
deleted. The anonymous account reads and nothing more, an upload is refused
(401 from the backend, 405 from the gate), and the admin password is replaced
by a random one kept in the `mirror-admin` volume, which only the
provisioning run mounts.

### Telemetry

Community Edition sends usage telemetry that cannot be turned off. Here it
has nowhere to go: the backend is on a network whose only way out is the
egress proxy, and the mirror's registration there holds only the upstream
registries' sets. No Sonatype host is on any set, so every attempt is refused
like any other host (Sonatype says the server works on without it).

### A different backend

The gate reads the mapping from its fixed paths to backend paths from a
routes file (`images/mirror-gate/routes/nexus.toml`), and the provisioning
lives beside it (`images/mirror-gate/backends/nexus/`). Moving to Pulp, say,
is a `pulp.toml` and a `backends/pulp/`; no client changes. The one Nexus
specific step with no stable API is setting its outbound proxy: the REST API
has no endpoint for it, so the provisioning uses the web UI's own
(`coreui_HttpSettings`), which a Nexus upgrade could change.

### Community Edition's terms

Nexus Repository CE is free under Sonatype's own license (the open source
core, under the EPL, lacks the npm, PyPI, Docker and Go formats). It is
limited to 40,000 components and 100,000 requests a day; failed requests
count too. Past either, it stops adding components until usage is back under
both, and keeps serving what it has.

### Measured

2026-09-29, in an L2 engine, with `host/workbench`'s own functions starting
the mirror beside a shared egress proxy, Nexus Repository CE 3.96.3 with a
1 GB heap, and two workspaces: one listing only `mirror`, one listing
`python`.

| Check | Result |
| --- | --- |
| first start: backend image pull, boot, provisioning, gate | 69 s; later starts 46 s |
| resident memory | backend 1.48 GB, gate 144 MB with every OSV feed loaded, proxy 36 MB |
| OSV feeds, first sync | npm 221,698 malicious entries, PyPI 11,762, Go 18, in under a minute |
| the `mirror` only workspace: npm, pip, go (checksum database on), docker.io, apt | all through the gate |
| the same workspace straight to pypi.org | refused |
| the `python` workspace straight to pypi.org | allowed |
| the `mirror` workspace to the gate's address on the other workspace's network, or to the backend | no route |
| hosts the mirror asked the proxy for in its first minutes | the Ubuntu archives and OSV's bucket allowed; `rhc.sonatype.com` (telemetry) refused |
| a version planted as malicious (left-pad 1.3.0, six 1.16.0, uuid v1.6.0) | left out of the npm metadata, PyPI index and Go list; its download refused with a 403 |
| an upload | 401 from the backend, 405 from the gate |

On the host itself (Fedora, SELinux enforcing, rootless podman), with a
group workspace already running: `up` with the mirror took 47 s, and npm,
pip, go, docker.io, ghcr.io, apt, apk and yum (Fedora 43 and 44) all
answered through the gate; the proxy log shows the backend asking for
`rhc.sonatype.com` once, refused. It also found that a low fixed address
for the gate collides with the workbenches, so the gate takes `.254`.

Sonatype's documented minimum heap is 2703 MB (2.3 GB resident, measured);
1 GB served these clients, and `WORKBENCH_MIRROR_HEAP` sets it.

## Extensions

The editor's extensions run in the workbench with the same permissions as
the editor itself; VS Code has no sandbox for them. What limits them here:

- Only the extensions in `images/workbench/vscode/extensions.txt` exist,
  each pinned to an exact version released at least seven days earlier.
- They are baked into a root owned, read only directory. Neither an
  extension nor anything else in the workbench can install, update or replace
  one. Auto update is off.
- They find no GitHub token and no ssh key, and reach only the hosts on the
  workspace's egress sets.
- The coding agents' own logins are readable in the workbench, which the
  agent extensions need. That is accepted: the worst case is someone using
  that subscription, and it can be revoked.

### SonarQube for IDE

`SonarSource.sonarlint-vscode` is in every workbench, with its telemetry off.
Its analysis runs in a JVM of its own, which ignores `HTTPS_PROXY` and which
VS Code's proxy settings never reach (measured 2026-10-01: with neither, it
failed with `UnknownHostException: sonarcloud.io`, as workbenches have no
DNS). So `workbench-init` writes the workspace's egress proxy into
`sonarlint.ls.vmargs` as JVM options when the workbench starts.

Without connected mode it analyzes with the rules it bundles, and needs no
egress set. For connected mode a repository adds the `sonarqube-cloud` egress set to its
`.devcontainer/egress-sets`: `sonarcloud.io` and `api.sonarcloud.io` (the
connection), `scanner.sonarcloud.io` (the server's analyzers, which bound
mode uses instead of the bundled ones: without it the project is left with
no Python rules at all), `events-api.sonarcloud.io` (a WebSocket for server
events) and `binaries.sonarsource.com` (the C#, C and C++ analyzers it
downloads). The set covers the EU region; a US region organization would
need `sonarqube.us` hosts in a set of their own. Opening a `git worktree`
logs a JGit "repository not found" error, harmless, since JGit cannot read
linked worktrees; a session's independent clone does not.

Keep the extensions on the host's own VS Code to the Dev Containers extension:
anything installed there runs on the host.
