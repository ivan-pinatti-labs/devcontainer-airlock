# Secret scan

<!-- cspell:words gitleaks HMAC showsecret -->

Agents sometimes print a token or a key in a tool output, and it stays on
disk in plain text, in their transcripts and the files beside them. The
secret scan finds those secrets, redacts them where no session is still
writing, and keeps listing every one until you say it was rotated.

A secret that reached a transcript was already sent to the model provider.
Redacting the file is hygiene; **rotating the secret is the fix**, so every
finding says "rotate it".

## When it runs

| When | What it looks at | What it changes |
| --- | --- | --- |
| A session starts (before its workbench) | files new or changed since the last scan | redacts what no session holds, reports the rest |
| A session ends (`make claude` exiting, `make stop-<name>`) | the same, and that session's conversation counts as ended | the same |
| `make scrub` | every file | the same |
| `make scrub-check` | every file | nothing: a dry run, see below |

Every run ends by listing the secrets found and not rotated yet. After
rotating one, `make scrub-rotated ID=<n>` takes it off the list. The GitHub
token, the ssh key and the values of the files in `WORKBENCH_SCRUB_SECRETS`
are taken off on their own, once the value the host holds changes.

The first scan reads all the history there is, which can take minutes, so a
session start or end does not run it: until `make scrub` (or
`make scrub-check`, which keeps nothing, so it does not count) has run once,
they only say so. Run `make scrub-check` first to see what the first
`make scrub` would change.

### Dry run

`make scrub-check` makes every decision `make scrub` would and changes
nothing: no file, no ledger entry, no index. For each secret it says which
file holds it and what would happen to it:

```text
scrub: github-pat ...Q7r8 in <file>: would be redacted to [REDACTED:github-pat:...Q7r8]. Rotate it: ...
scrub: aws-access-token ...MN23 in <file>: in a file a running session holds, so it would wait for the session to end. Rotate it now
scrub: <file>: redacting it would break its JSON, so it is left as it is. Rotate what it holds
scrub: generic-high-entropy ...xfCQ in <file>: a random looking string, reported only (docs/SCRUB.md)
scrub: 812 files looked at: 3 would be redacted, 1 wait for a session to end, 1 cannot be redacted; dry run, nothing was changed
```

It also lists the secrets already in the ledger and not rotated. The
values themselves are never printed, only their rule and last four
characters.

A scan at session start or end never stops the session: if it fails, it
says so and the session goes on. `WORKBENCH_SCRUB=0` turns those two off;
`make scrub` still works. Only one scan runs at a time; one started while
another runs is skipped (`make scrub` waits instead).

## Where it looks

In each folder an agent keeps history in:

- Claude Code: `projects/` (transcripts, tool results, subagents, memory),
  `file-history/`, `history.jsonl`, `debug/`, `shell-snapshots/`, `todos/`,
  `paste-cache/`.
- Codex: `sessions/`, `history.jsonl`, `log/`.

The folders: `~/.claude` and `~/.codex`, every host folder in
`WORKBENCH_HISTORY` (`~/.claude-personal`, say), every workbench's own
folder under `${XDG_DATA_HOME}/workbench/` (`claude`, `codex`,
`claude-personal`), and any more in `WORKBENCH_SCRUB_HOMES`.

Never looked at: settings, logins (`.credentials.json`, `auth.json`) and
Claude Code's `sessions/` records of running processes. They hold
credentials on purpose, or nothing an agent printed.

## What counts as live

A file a session may still write is read and reported, never written. It is
redacted by the first scan after its session ends.

- A file any process holds open.
- In a workbench's own folder: everything, while a workbench of that agent
  runs whose conversations are not known (a Codex session, the workbench
  `make workbench-up` starts). While only Claude Code sessions run there,
  the files of their conversations (each conversation's files are named
  for it) and the files of no conversation (`history.jsonl`,
  `paste-cache/`).
- The same for a host folder while a Claude Code runs on the host (its
  record under `sessions/` names its process and conversation). Everything
  in a `WORKBENCH_HISTORY` folder that a workspace workbench mounts, while
  that workbench runs.
- In a host folder, a file written in the last `WORKBENCH_SCRUB_QUIET`
  minutes (default 10), unless it belongs to the conversation that just
  ended. That covers a host session the rules above cannot see, such as a
  Codex run on the host.

## Detection

1. **Exact values.** The secrets the host holds: the gh broker's token (read
   from its podman secret, `podman secret inspect --showsecret`, so nothing
   reads it that could not already), the ssh key, and the values of the
   KEY=value files in `WORKBENCH_SCRUB_SECRETS`. Searched for as they are
   and as a JSON string holds them.
2. **Known formats**, gitleaks' own rules: GitHub, Anthropic, OpenAI, AWS,
   Slack tokens, private key blocks and the rest, including its generic rule
   for a value written beside a word like `token`, `key` or `password`.
3. **Long random looking strings** with no known format
   (`generic-high-entropy` in `host/scrub-gitleaks.toml`): **reported only**,
   on `make scrub` and `make scrub-check`, and never redacted or kept in the
   ledger. Run `make scrub-check` over your history and look at what it
   lists; making this tier redact is a decision for after that.

gitleaks reads a view of each file in which every JSON string is decoded and
written beside its key, so a rule sees `api_key: "..."` rather than its
escaped form. Tier 3 never counts git object names, `sha256` digests,
UUIDs or numbers (run and job ids), and its entropy threshold keeps them
out anyway; gitleaks' own rules keep only their own allowlists, so a
credential with one of those shapes (a 32 hex character API key, a UUID
key) is still found. A hit shorter than eight characters is ignored.
gitleaks does not decode base64 here, so a secret inside a base64 blob is
not found.

## Redaction

A secret becomes `[REDACTED:<rule>:...abcd]`, its last four characters kept
so you can tell whether a value you hold is the one found. One shorter than
20 characters becomes `[REDACTED:<rule>]`.

Only the secret's own text changes, in place, with no backup (a backup would
be another copy of it). A JSON lines file must keep every line that parsed
before parsing after, and a JSON file must still parse; otherwise it is left
as it is and the scan says so, loudly. The file keeps its mode and its time,
so `--resume` still works and Claude Code's 30 day cleanup is not reset.

## The ledger

Kept in `${XDG_STATE_HOME}/workbench/scrub/`, which no container but the
scan's own ever mounts. One entry per distinct secret: an HMAC of its value
under a key made there once (mode 0600), the rule, its last four
characters, when it was first and last seen, the files it was found in, and
whether it is open or rotated. The same secret found again adds to its
entry; found in a new file after it was marked rotated, it is open again.
The folder also holds the size and time of every file already scanned, so a
session start only looks at what changed.

## How it is isolated

`host/scrub` runs on the host and only lists and copies files and calls
podman. The files to look at are copied to a scratch folder, so the history
folders are never mounted or relabelled. The scan, `host/scrub-scan`, runs
in the airlock base image with no network, no capabilities and a read only
root, given the copies, the scratch folder for its results and the state
folder; gitleaks (pinned by digest, Renovate moves it) is mounted into it
as an image. A redacted copy goes back only if the original still has the
size and time it had when it was copied, through a file beside it renamed
over it.

## Settings

In `${XDG_CONFIG_HOME}/workbench/config` or the environment, as for every
other setting (`host/workbench help`):

| Setting | Default | What |
| --- | --- | --- |
| `WORKBENCH_SCRUB` | `1` | `0`: no scan at session start and end |
| `WORKBENCH_SCRUB_HOMES` | none | more `<agent>=<folder>` pairs to look in |
| `WORKBENCH_SCRUB_SECRETS` | none | files of KEY=value lines whose values are secrets |
| `WORKBENCH_SCRUB_QUIET` | `10` | minutes a host file must go unwritten to count as ended |

## Testing

`tests/test_scrub_scan.py` holds the scan to the acceptance cases with a
stand in gitleaks: a secret of each tier redacted, with its tail where it
is long enough, every line still JSON and one ledger entry each; an
identifier of each kind left alone; a live file reported and not written;
running twice changing nothing; a tier 1 secret marked rotated once its
value changes; color only when asked. `tests/scrub.test.sh` runs
`host/scrub` with the real scan against fake history folders, including a
file a process holds open, which is redacted once it closes.
`tests/workbench/scrub.test.sh` checks what host/workbench says is live.

The real gitleaks is not run by the tests (the containers `make coverage`
uses have no engine for it). Its rules were checked against the same kind
of fixture by hand, in the L2 engine, with the pinned image:

```shell
l2 --engine -- podman run --rm --network=none -v "$PWD/fixture:/w:Z" \
  docker.io/zricethezav/gitleaks:v8.30.1 dir /w --config /w/scrub-gitleaks.toml \
  --max-decode-depth 0 --report-format json --report-path -
```

## Limits

- Redaction only reaches files at rest. A secret is still in the model
  provider's logs and in anything the agent did with it; that is why the
  answer is always to rotate.
- A host session the scan cannot see (no open file, no record, quiet for
  longer than `WORKBENCH_SCRUB_QUIET`) can have a file rewritten under it.
  Claude Code appends to its transcript by name, so it carries on in the
  rewritten file.
- What is redacted in a transcript is gone from it for good, including from
  a `--resume` of that conversation.
