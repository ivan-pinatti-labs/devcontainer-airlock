# The layers

How the images in this repository fit together, what each one is allowed to
see, and how to use them day to day.

<!-- cspell:words tinyproxy userns initializeCommand codeload -->

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
 ├─ egress-proxy     one per workspace: the only way out, by egress sets
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
| egress-proxy | squid, one per workspace | the domains of its workspace's egress sets |
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
| Actions, commit statuses | read | follow checks and read run logs |

Nothing else: no administration, secrets or organization permissions. The
broker never lets contents write be used for anything but `gh pr ready` and
enqueueing a merge: it refuses every `gh api` call that is not a GET, and
`gh pr merge --admin`, which would skip the queue. Code still reaches GitHub
only by `git push` over ssh, and `main` only through the merge queue. The
key's public half is added to your GitHub account as an authentication key.

Each day, from the repository you are working on:

```shell
make unlock              # type the key's passphrase; lasts 8 hours
make claude              # Claude Code, in its workbench, in the folder you are in
make codex               # the same for Codex
make claude-shell        # or codex-shell: a plain terminal in that workbench
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
the workspace from that branch's worktree: the proxy takes its sets from the
checkout `up` runs in.

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
  inside the workbench.
- `/etc/codex/requirements.toml`: Codex keeps its own sandbox (measured
  working inside the workbench: read only, no network) and asks before
  acting, and the same hook refuses project code with the `l2` command line
  to use instead.

This is policy, not a boundary. Command matching can be defeated by a
determined agent (`sh -c` inside a script, for instance); what actually
protects the credentials is that the workbench does not hold the GitHub
token or the ssh key, and that L2 holds nothing at all.

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
delete`, `secret`), repositories outside the organization, `gh api` with a
method other than GET, and GraphQL mutations. Interactive prompts are not
available, so pass the flags a prompt would ask for.

`git push` goes over ssh through the ssh-agent container, which signs the
login without ever handing over the key. The connection runs to
`ssh.github.com` on port 443 through the egress proxy, and GitHub's host key
is checked against keys fetched from GitHub's own API. `host/workbench
unlock` adds the key for eight hours at a time.

## Network

Each workspace has an internal podman network of its own (no route, no DNS)
holding its workbench and its L2 engine, and an egress proxy of its own on
that network, which is their only way out. One project's allowances never
apply to another's.

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
| `alpine`, `trivy`, `sigstore` | Alpine packages, trivy's database, sigstore's trust root | |
| `aws` | AWS service APIs | AWS's ranges from `ip-ranges.amazonaws.com`, enforced |

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

### The proxy

squid, tuned to decide and forward only (no cache, small lookup tables): 13
MB resident with seven sets and every AWS range loaded, measured 2026-09-25,
against 89 MB with squid's defaults. tinyproxy, which Qubes OS uses for its
updates proxy, is lighter still (4 MB), but it can only match a host name,
and checking where that name resolves is what makes a provider's ranges
worth having. A refused request answers `403 Forbidden`, and `podman logs
egress-proxy-<folder>` shows each decision (`TCP_DENIED` or `TCP_TUNNEL`,
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

Keep the extensions on the host's own VS Code to the Dev Containers extension:
anything installed there runs on the host.
