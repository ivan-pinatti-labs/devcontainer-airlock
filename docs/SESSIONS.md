# Sessions: design

Status: proposal, for review. Nothing here is built yet.

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

## 2. Writing only to its own worktree

A session never mounts a clone read write.

- The workspace's clones are mounted **read only**, so an unscoped session
  can read everything and discuss.
- At start the host creates one empty folder per repository in scope,
  `<clone>/.claude/worktrees/<session>`, and mounts each read write at the
  same path. That is all the session can write in any repository.
- When the work is agreed, the session runs `airlock-worktree <repo>
  [<branch>]` (new, in the workbench image). It makes an **independent
  clone** into its folder: `git clone --no-hardlinks` from the read only
  main clone, `origin` set to the main clone's remote, fetch, a branch from
  `origin/main`, `l2-hooks-install`.

So `.git` is not shared at all. Each session has its own objects, refs,
config and hooks; none can touch another's, and none can write the main
clone's `.git`. The cost is a few megabytes and tens of milliseconds per
repository, measured above.

The trade: these are clones, not `git worktree`s. The main clone's `git
worktree list` does not show them, and the host removes them (section 4).
The path convention stays `<repo>/.claude/worktrees/<name>`.

A repository marked worktree only (its main clone holds data) mounts its
`.git` read only, to clone from, and never the clone itself, as today.

## 3. What is shared, and how

| Thing | Per session or shared | How |
| --- | --- | --- |
| transcripts, history, memory | shared, both accounts | the resolved `projects/` folder (both accounts' project folders already point at one target) mounted into every session. The agent always starts in the workspace root (`~/wo/public`), so every session writes under the same project key, whatever worktree it works in |
| login | shared per account | only the credentials file is shared (one file, so a token refresh by one session does not break the others) |
| the rest of `~/.claude`, `~/.codex` | per session | a private copy seeded from the account's, so one session cannot change another's settings, agents or skills while it runs |
| L2 engine | per session | own socket, own containers; images from one read only store the host fills (`additionalimagestores`, measured above) |
| egress proxy | shared (#54) | one registration per session network, the union of the egress sets of the repositories in scope; `mirror` works as today |
| package mirror (#56) | shared | the gate joins each session network at `.254` |
| gh broker, ssh-agent | shared | sockets, as today |
| voice | per session | already per session: its own pipes |

## 4. Lifecycle

- **Start**: `make claude`, `make claude-personal`, `make codex` start a new
  session named `S` (default: a short generated name) over the folder you
  are in: a repository gives a session scoped to it; `~/wo/public` gives an
  unscoped one over every clone. `REPO=<name>` scopes a group session at
  start.
- **Attach**: `make attach-<session>` (tab completes) starts it again if it
  stopped and resumes the agent's conversation (`claude --resume`).
- **List**: `make sessions`: name, agent and account, running or stopped,
  its worktrees, their branches and PR state.
- **Stop**: when the agent exits the container stops; the folder, branch and
  transcript stay. `make stop-<session>` stops it from outside.
- **Clean up**: `make prune` removes stopped sessions whose branches' pull
  requests merged: the clone folders, the network, the engine volume.
  `make prune-<session>` for one.

## 5. Next to today's model

Sessions replace per workspace workbenches for the agents. The workspace
stays as the thing that says what is mounted read only and which egress
sets apply; a session is one use of it. VS Code attaches to a session's
container. `host/workbench` gains `session start|attach|list|stop|prune`,
and `host/workbench.mk` the targets above. `make claude-shell` and
`codex-shell` become a shell in a session.

## 6. Open questions

1. **The worktree's name is the session's name**, fixed when the session
   starts, since the writable folder is mounted then. Recommendation: name
   sessions after the work when known (`make claude S=fix-login`); an
   unscoped one gets a generated name, and `make rename-<session> S=<new>`
   restarts it under the new name and resumes the conversation (about 1 s).
2. **Clones instead of `git worktree`**. Recommendation: yes; it is what
   keeps `.git` apart. The organization's AGENTS.md section "Parallel work
   uses worktrees" then changes in every repository (fan out after this).
3. **Shared memory is a channel between sessions.** A session can write
   memory, or instructions in the shared project folder, that another
   session will read. That is the sharing you asked for, so isolation holds
   for files, processes and containers, not for what sessions tell each
   other. Recommendation: accept, and say so in docs/LAYERS.md.
4. **One GitHub identity.** Every session can act on every pull request
   through the broker. Recommendation: accept for now; later the broker can
   be narrowed per session to the repositories in scope.
5. **Repository L2 images** (`.devcontainer/l2/Dockerfile`) would be built
   in each session's engine the first time. Recommendation: the host builds
   them into the shared store when their Dockerfile changes, so a session
   never builds one.
6. **Settings changed inside a session** (`/config`) would stay in that
   session. Recommendation: accept; the account's own settings are edited
   in its login folder on the host.

## Plan, once approved

One pull request each, in this order:

1. Shared read only L2 image store for engines, and the host filling it.
2. Sessions in `host/workbench` and the make targets: per session
   container, network and engine; read only clones; per session folders;
   `airlock-worktree`; attach, list, stop, prune.
3. Per session agent configuration with shared credentials and history.
4. docs/ARCHITECTURE.md and docs/LAYERS.md, then the AGENTS.md change in
   every repository.
