# Sessions: design

Status: agreed 2026-09-30 (section 6). Step 1 of the plan is #64, step 2 is
the pull request that brought this file in.

Any number of agent sessions at once (several `claude`, several
`claude-personal`, `codex`, in any mix), each isolated from the others, all
sharing one history and memory. Builds on the shared egress proxy (#54).

<!-- cspell:words additionalimagestores conmon hardlinks -->

## What today's model gives, measured 2026-09-30

- Isolation is per workspace. Every `make claude` in a folder runs
  `podman exec` into the same `workbench-<agent>-<slug>`: sessions share
  its processes, its read write mounts and its L2 engine socket, which
  controls every L2 container of the workspace.
- `host/workbench claude <worktree>` works: the workbench mounts only that
  worktree read write. But it also mounts the clone's whole `.git` read
  write, so it can still move other worktrees' refs and write the shared
  `config` and `hooks`. And its history mount keys on the worktree's own
  path, so it does not share the group's memory.
- The IPAM error on `.2` was the per workspace proxy of main before #54
  meeting #54's shared one. With #54 merged it does not recur.

## 1. The unit of isolation: a session

One session is one workbench container, one internal network and one L2
engine, all its own. Measured on the host:

| Piece | Start | Memory | Disk |
| --- | --- | --- | --- |
| workbench container (one agent) | 0.9 s | 8 MB + conmon 2 MB | none |
| L2 engine, on a shared read only image store | 0.18 s to its socket | 40 MB + conmon 2 MB | 0.1 MB |
| L2 engine, copying the L2 image in (today) | 8.7 s with the copy | 41 MB | 957 MB |
| network and proxy registration (#54) | 0.2 s | 0.1 MB in the proxy | none |
| its own git clone of a repository | 0.04 to 0.06 s | none | 4 to 11 MB |
| attaching to a running one (`podman exec`) | 0.09 s | none | none |

So a session costs about 52 MB and 1.3 s over today's `podman exec`, with
a shared image store. The agent process itself (Claude Code, Codex) costs
the same in either model. An L2 run from the shared store took 0.16 s.

Why not lighter: sessions in one container share a process namespace and,
worse, one engine socket, so one session could stop, read or exec into
another's L2 containers. Claude Code's own sandbox is policy, not a
boundary (docs/LAYERS.md). A container per session is the lightest thing
that separates processes, mounts, network and containers.

What stays shared on purpose: all sessions run in the same SELinux domain
and category, because they must connect to the broker, agent and engine
sockets. Between sessions the boundary is the namespaces, not SELinux.

## 2. Writing only to its own folder

A session never mounts a clone read write.

- The workspace's clones are mounted **read only**, so an unscoped session
  can read everything and discuss.
- At start the host creates one empty folder per repository in scope,
  `<clone>/.claude/worktrees/<session>`, and mounts each read write at the
  same path. That is all the session can write in any repository.
- When the work is agreed, the session runs `airlock-worktree <repo>
  [<branch>]` (new, in the workbench image). It makes an **independent
  clone** into its folder: `git clone --no-hardlinks` from the read only
  main clone (no network), `origin` set to the main clone's remote, fetch, a
  branch from `origin/main`, `l2-hooks-install`. The branch is named after
  the work and can be chosen at any time; the folder keeps the session's
  name.

Why a clone and not `git worktree`: every worktree writes into the main
clone's `.git` (new objects, every branch, `config`, `hooks`), so a session
would need it read write. With it, a session can move or delete another
session's branches, and can set `core.hooksPath`, `core.fsmonitor` or a
hook that then runs in every other session and on the host whenever git
runs there. Git cannot make only a worktree's part of `.git` writable. A
clone has its own objects, refs, config and hooks, and none of the main
clone's.

Not `--reference` either: borrowed objects can be pruned from the main clone
(after a force push and a `gc`), which would break the session's clone. A
full clone costs 4 to 11 MB and 40 to 60 ms, measured above.

The main clone's `git worktree list` does not show these clones; `make
sessions` does. A repository marked worktree only (its main clone holds
data) mounts its `.git` read only, to clone from, and never the clone
itself, as today.

## 3. What is shared, and how

| Thing | Per session or shared | How |
| --- | --- | --- |
| transcripts, history, memory | shared, both accounts | the resolved `projects/` folder (both accounts' project folders already point at one target) mounted into every session. The agent always starts in the workspace root (`~/wo/public`), so every session writes under the same project key, whatever folder it works in |
| login, and the rest of `~/.claude`, `~/.codex` | shared per account | the account's folder, mounted into every session of that account, as into its workspace workbench. Not a private copy per session: see "Why the account folder is shared" below |
| L2 engine | per session | own socket, own containers, own writable image store; below it, one read only store the host fills (`additionalimagestores`, measured above) |
| egress proxy | shared (#54) | one registration per session network, the union of the egress sets of the repositories in scope; `mirror` works as today |
| package mirror (#56) | shared | the gate joins each session network at `.254` |
| gh broker, ssh-agent | shared, through the profile | sockets, as today |
| voice | per session | already per session: its own pipes |

**Why the account folder is shared.** The plan was a private copy of
`~/.claude` for each session with only the login, `.credentials.json`,
shared, so one session could not change another's settings, agents or
skills while it ran. Claude Code (2.1.283, read in its code) rules that out:

- It saves the login by writing a temporary file and renaming it over
  `.credentials.json`, under a lock file in the same folder. A rename onto a
  file that is itself a mount fails, so a credentials file mounted alone
  into a private copy would lose every token refresh.
- A symbolic link to a shared file is replaced by that rename, so the
  session keeps a token of its own; its next refresh rotates the token the
  others hold. Its newer credentials store also refuses a symlinked file.
- Copying the file in at start and back at stop has the same rotation
  problem whenever two sessions overlap.

So the login is shared the only way it can be, by sharing its folder, which
is how Claude Code itself expects several processes on one account to work
(hence its lock). The rest of the account's state goes with it: the
workbench sets `CLAUDE_CONFIG_DIR` to `~/.claude`, so `settings.json` and
`.claude.json` (which defaults to `~/.claude.json` without that variable)
both live in that folder. What keeps a session from changing what the
others run with is what holds today: hooks come only from managed settings
(`allowManagedHooksOnly`); Claude Code's command sandbox refuses writes to
`settings.json`, `CLAUDE.md`, `agents/`, `skills/`, `commands/`, `hooks/`
and `plugins/` under `~/.claude`; and an edit there through the agent's own
editing tools asks first. Codex's `~/.codex` is shared the same way.

**Profile.** A session runs under a profile: the GitHub identity (the
broker, its token and its owners), the git author, the egress defaults.
There is one, `default`, holding today's settings. The session record names
its profile, so a second one (work and personal, say, each with a broker of
its own) is configuration later, not a redesign.

**Repository L2 images.** The host builds each repository's
`.devcontainer/l2/Dockerfile` into the shared store when it changes, so a
new session starts without a build. A session can still change the
Dockerfile and build: the result goes into its own engine's writable store,
on top of the shared layers, and no other session sees it until the change
merges and the host rebuilds.

## 4. Lifecycle

- **Start**: `make claude`, `make claude-personal`, `make codex` start a new
  session with a random name (`brave-otter`) over the folder you are in: a
  repository gives a session scoped to it; `~/wo/public` gives an unscoped
  one over every clone. `REPO=<name>` scopes a group session at start.
  Names never change, so nothing restarts to rename.
- **Attach**: `make attach-<session>` (tab completes) starts it again if it
  stopped and resumes the agent's conversation (`claude --resume`).
- **List**: `make sessions`: name, agent and account, profile, running or
  stopped, its clones, their branches and PR state.
- **Stop**: when the agent exits the container stops. `make stop-<session>`
  stops it from outside. The last session to stop stops the shared services
  (#59).
- **Clean up**: `make prune` removes stopped sessions whose pull requests
  merged and whose clones hold nothing else: no uncommitted change, no
  commit that is not pushed. Anything else is listed and kept. `make
  prune-<session>` for one, with the same checks.

**Crashes.** Everything a session owns that matters is on the host's disk:

| Crash | Kept | Lost |
| --- | --- | --- |
| the agent | everything | nothing; attach resumes the conversation |
| the container | clones and uncommitted files, transcript, engine volume | running processes |
| the computer | the same | running processes |

So session records live in `~/.local/share/workbench/sessions/`, not in
`$XDG_RUNTIME_DIR`, which is emptied at boot. After a reboot `attach`
recreates the network and the container from the record. As with a
worktree today, a commit not pushed exists only on this disk.

## 5. Next to today's model

Sessions replace per workspace workbenches for the agents. The workspace
stays as the thing that says what is mounted read only and which egress
sets apply; a session is one use of it. VS Code attaches to a session's
container. `host/workbench` gains `session start|attach|list|stop|prune`,
and `host/workbench.mk` the targets above. `make claude-shell` and
`codex-shell` become a shell in a session.

## 6. Decisions (2026-09-30)

1. **Names**: random, fixed for the session's life; no rename.
2. **Clones**, full and independent (`--no-hardlinks`), not `git worktree`
   and not `--reference`. The organization's AGENTS.md section "Parallel
   work uses worktrees" changes in every repository after this lands; it
   is now "Parallel work uses separate checkouts".
3. **Shared memory is a channel between sessions**: accepted. Isolation holds
   for files, processes and containers, not for what sessions tell each
   other; docs/LAYERS.md says so.
4. **One profile**, `default`, with the structure for more.
5. **Repository L2 images**: prebuilt by the host into the shared store,
   and buildable in each session's own engine.
6. **Settings changed inside a session** (`/config`) stay in that session,
   for now. Superseded 2026-10-03: they reach every session of the
   account, since the account folder is shared (section 3, "Why the account
   folder is shared").

## Plan

One pull request each, in this order:

1. Shared read only L2 image store for engines, and the host filling it.
2. Sessions in `host/workbench` and the make targets: per session
   container, network and engine; read only clones; per session folders;
   `airlock-worktree`; persistent records; attach, list, stop, prune.
3. Per session agent configuration with shared credentials and history,
   under the `default` profile. Dropped 2026-10-03: the credentials can
   only be shared with their whole folder (section 3).
4. docs/ARCHITECTURE.md and docs/LAYERS.md, then the AGENTS.md change in
   every repository. Done 2026-10-03.
