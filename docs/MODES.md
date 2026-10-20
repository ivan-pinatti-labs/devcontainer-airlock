# Session levels

<!-- cspell:words anthropics -->

A Claude Code session runs at one of three levels. The level decides how far
the agent may go without asking you. It does not move the boundary: at every
level the agent runs in the workbench, holds no GitHub token and no ssh key,
reaches GitHub only through the gh broker's allowlist and the network only
through the egress proxy, and runs project code only in L2. Codex runs
hardened for now.

| Level        | Start it with            | Who is there           | The agent                                       |
| ------------ | ------------------------ | ---------------------- | ----------------------------------------------- |
| `hardened`   | `make claude`            | you, at every step     | asks as the policy is written                   |
| `assisted`   | `make claude-assisted`   | you, nearby            | also reads paths it cannot resolve, unasked     |
| `unattended` | `make claude-unattended` | nobody                 | never asks; does the cycle, refuses the rest    |

The rules behind each level are agent-policy's modes; its `docs/POLICY.md`,
"Modes", lists them one by one.

## Hardened

The default, and what every session was before levels. Reads, builds,
tests, commits, pushes and the pull request flow up to `gh pr merge --auto`
run without asking; anything that changes more (a release, discarding work,
a `gh api` write, removing a container) asks; anything irreversible or that
touches credentials is refused and handed to you.

## Assisted

For working alongside the agent. The prompts that come most often in a
hardened session are reads of a path held in a variable, such as
`gh pr view 1 > "$f"; grep x "$f"`, because the guard cannot see where `$f`
leads. Assisted allows those reads. Writing to such a path still asks, as
does everything else that asks under hardened.

## Unattended

For overnight or weekend runs, with nobody to answer a prompt. A prompt
would only stall the run, so nothing asks:

- **Allowed:** the development cycle. Commits, pushes, pull requests,
  replying to and resolving review threads, enqueueing a merge, `gh workflow
  run`, closing an issue, labels, tags and releases (`git push --tags`, `gh
  release create`), deleting a merged branch, and cleaning up the session's
  own containers.
- **Refused:** everything else that would ask under hardened. Discarding
  work (`git reset --hard`, `restore`, `clean`), `git worktree remove
  --force`, deleting a tag, pushing straight to `main`, `find -exec`, a
  request body sent with `curl`, privileged containers, `gh api` PUT and
  DELETE, repository settings and secrets. And, as at every level, force
  pushes, skipping hooks, `--admin` merges and anything that reads
  credentials.

`make claude-unattended` asks how long to unlock the ssh key for, from a
menu you move through with the arrow keys and Enter: 8, 12 (the default),
24 or 48 hours. `make claude-unattended HOURS=24` skips the menu. Then it:

1. asks for the key's passphrase once and unlocks the key for that long,
   so a push within those hours does not find it locked. Every session shares
   one ssh-agent, and adding the key again replaces its lifetime, so an
   unlock (this one or `make unlock`) never ends before an unattended
   session's own hours do: each records when its key runs out (`ends=`);
2. runs Claude Code in its own auto mode (`--permission-mode auto`), where
   its classifier judges what no rule names. Permission checks are never
   bypassed: `--dangerously-skip-permissions` is turned off for every
   level by the managed settings (`disableBypassPermissionsMode`).
   That classifier runs locally: the managed settings set
   `CLAUDE_CODE_AUTO_MODE_SERVER=0`, which turns off Claude Code's server
   side classifier. On the server path a classifier outage ("gave no
   verdict") blocked every Bash call, allow listed ones such as `sleep` and
   `date` included, so an unattended agent could not even arm a wait
   (anthropics/claude-code#97870 and #97854, measured with Claude Code
   2.1.294). On the local path, allow rules resolve before any classifier
   call. Nothing promises the variable survives a CLI update, so check on
   each bump that the binary still reads it
   (`grep -ac CLAUDE_CODE_AUTO_MODE_SERVER` on it).

The hours are the key's, not the session's. Nothing ends the session but
the agent exiting, `make stop-<session>` or its terminal closing. When the
key's unlock runs out (these hours, or longer if a later `make unlock` or
another session extended it) only the key locks again, so pushes fail until
`make unlock`.

It has voice, as `make claude` does (`VOICE=0`: without), since a run is
often started with you still at the keyboard.

`make attach-<session>` brings an unattended session back at the same
level. The key is not unlocked again; `make unlock HOURS=<n>` does that.

An agent waiting on a pull request (checks, a review, the merge queue) uses
`gh wait pr` in the background, so it wakes on the change instead of
needing you (docs/LAYERS.md, "GitHub access").

## How a session gets its level

The workbench image carries all three. The hardened drop-in is installed as
usual; the assisted and unattended drop-ins, each with its guard allow
file, sit in `/usr/local/share/workbench/levels/`. For a session of another
level, `host/workbench` copies that level's two files out of the image once
per image, into `~/.local/share/workbench/levels/`, which no container
mounts, and mounts them read only over the hardened ones. The session
record keeps the level (`level=`), and an unattended one its key's hours
(`hours=`), so attach starts it the same way. The workbench sees its level
as `AIRLOCK_LEVEL`.

## Not yet

- A GitHub token and broker profile of its own per level, so an unattended
  run could hold narrower rights than a hardened one. Today every level uses
  the same broker and token, and the allowlist is the limit.
- Codex at the assisted and unattended levels.
