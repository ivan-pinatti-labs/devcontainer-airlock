# Secret scan

<!-- cspell:words gitleaks changeme getpass HMAC MIIE showsecret -->

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
| `make scrub-list` | the ledger | nothing: the secrets still to rotate, see below |
| `make scrub-show ID=<n>` | the files that secret was found in | nothing: the text around its placeholder, see below |
| `make scrub SCRUB_DIR=<folder>`, `make scrub-check SCRUB_DIR=<folder>` | every file of the conversations that ran in that folder or below it | as without it, see below |

Each secret is one line, whatever number of files it is in: where it was
redacted, where it waits for a session to end, where it cannot be redacted.
In `make scrub` a secret that was redacted in some files and waits or
cannot be redacted in others has two: what was redacted is said once the
files are replaced, after the rest (and a file that changed meanwhile is
said to still hold it, with its number).
`make scrub` and `make scrub-check` then list every secret found before and
not rotated yet; a session start or end only counts them (and the secrets
waiting for a running session or that cannot be redacted), and says each
secret it redacted just then, which for the few files changed since the
last scan is usually none or a handful. After rotating,
`make scrub-rotated ID=<n>` takes a secret off the list; `ID="3 5 8"` takes
several, `ID=all` every one (for false positives, or after rotating them
all). The GitHub token, the ssh key and the values of the files in
`WORKBENCH_SCRUB_SECRETS` are taken off on their own, once the value the
host holds changes.

The first scan reads all the history there is, which can take minutes, so a
session start or end does not run it: until `make scrub` (or
`make scrub-check`, which keeps nothing, so it does not count) has run once,
they only say so. Run `make scrub-check` first to see what the first
`make scrub` would change.

### One folder at a time

`SCRUB_DIR=<folder>` (`--dir` to `host/workbench scrub`) narrows `make scrub`
or `make scrub-check` to the conversations that ran in that folder or in one
below it, every file of them, so a large history can be gone through in
batches. Nothing names a folder ahead of time; the folder given is matched
against what each conversation recorded:

- A Claude Code project folder is in when a transcript in it says it ran
  there (its `cwd`), or, holding no transcript (only its memory, say), when
  its name is the one Claude Code gives that folder or one below it. Claude
  Code deletes transcripts after 30 days and keeps the memory, and by then
  the folder may be gone or moved, so the name is all there is to go by. A
  name cannot tell a folder below from a sibling whose name starts the
  same (`app-old` beside `app`), so such a sibling's memory is in too. Then
  all of it is in,
  with the files elsewhere in that home named for its conversations
  (`file-history/`, `todos/`, `debug/`).
- A Codex session is in when its first lines say it ran there.
- Files every conversation shares (`history.jsonl`, `paste-cache/`,
  `shell-snapshots/`, Codex's `log/`) are out; a scan of everything looks
  at them.

The secrets still to rotate it lists are those found in the files it
looked at. The index keeps what it knew of every other file, so the next
session start goes on as before. A workbench's own folder records the
paths inside the workbench, so give the folder as the workbench saw it to
reach those.

### The list to rotate

`make scrub-list` prints the ledger as a table, sorted by number, without
scanning anything: the secrets not rotated yet. `ALL=1` adds the rotated
ones, `FILES=1` the files each one was found in.

```text
ID  TYPE               ENDS IN  FIRST SEEN  LAST SEEN   FILES  STATUS
2   aws-access-token   7R2Q     2026-10-22  2026-10-22  12     open
33  slack-webhook-url  u3cC     2026-10-22  2026-10-22  4      open
scrub: 2 secrets open, 0 secrets rotated; once rotated: make scrub-rotated ID=<n>
```

The value itself is gone; the four characters it ends in are what a
provider's console shows to tell keys apart (AWS lists the whole access key
id). After rotating, `make scrub-rotated ID=2` takes it off.

### What a secret was

A secret shorter than 20 characters has no tail to go by (`-` in the
list), and neither has a private key from before key blocks were redacted
whole (its tail is `----`). What is left to tell what it was is the text
around its placeholder: the command that printed it, the variable it was
given to, the file being edited. `make scrub-show ID=<n>` (`ID="3 5"` for
several) prints that, for every file the secret was found in: a summary of
the secret (number, type, tail, status, when it was first seen, how many
files), then for each file up to five placeholders with the 200 characters
before each and the 80 after.

```text
#87 private-key ...---- (open), first seen 2026-10-22, 1 file
  /home/me/.codex/sessions/2026/04/10/rollout-....jsonl
    ...$ cat ~/.ssh/deploy_key [REDACTED:private-key:...----] ...
```

A placeholder with no tail is the same for every secret of its rule, so in
a file that holds several, each one is shown and the line says it could be
any of them. So is one whose tail another secret of the same rule shares,
and the line names that secret. A file gone since, or rewritten so it no
longer holds the placeholder, is said so. It reads copies of the files in
the scan's container, as everything else here does, and prints to the
terminal only; anything in a file or a file name that is not printable (an
escape sequence, say) is shown as a space. The text around a placeholder
can be sensitive in its own right
(other people's names or addresses in a tool's output), so read it there
rather than pasting it elsewhere.

### Dry run

`make scrub-check` makes every decision `make scrub` would and changes
nothing: no file, no ledger entry, no index. For each secret it says where
it is and what would happen to it there:

```text
scrub: secret #4 (github-pat ...Q7r8): would be redacted in 9 files; would wait for a session to end in <file>. Rotate it
scrub: aws-access-token ...MN23: would be redacted in <file>. Rotate it
scrub: <file>: redacting it would break its JSON, so it is left as it is
scrub: generic-api-key ...e74a: cannot be redacted in <file>. Rotate it
scrub: 412 random looking strings in 37 files, reported only, not redacted (docs/SCRUB.md); ENTROPY=1 (or --entropy) lists them
scrub: 812 files looked at, 5 secrets in them: 10 would be redacted, 1 wait for a session to end, 2 keep something that cannot be redacted; dry run, nothing was changed
```

A secret already in the ledger carries its number (`secret #4`), the one
`make scrub-rotated` takes.

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
   Slack tokens and the rest, including its generic rule for a value
   written beside a word like `token`, `key` or `password`.

   Private keys are the exception. gitleaks reads the decoded view, where
   its key rule can run from a `BEGIN` header in one string to an `END`
   header in another, or to one named much further on, and that text is in
   no file to redact. So its key hits are left out, and the scan finds key
   blocks in each file as the file holds it: the lines split by newlines
   real or escaped at any depth (a key in a tool output inside a JSON
   string), each perhaps after a line number, with at least 64 characters
   of key material that mixes cases and digits. A header named in prose,
   or an example such as `MIIE...`, is no key. Part of a key (the first
   lines of one) is redacted too. The ledger knows a key by its material,
   so the same key written two ways is one secret. A key block that is all
   or part of one key the host holds (the ssh key, a value in
   `WORKBENCH_SCRUB_SECRETS`) is that key's entry, taken off on its own once
   the key changes. Part that two held keys share (keys of one type can
   start with the same line) is neither's: it stays a `private-key` entry
   until `make scrub-rotated ID=<n>` takes it off, once both keys are
   rotated.

   Two rules of `host/scrub-gitleaks.toml` close what tier 3 showed on real
   history:

   - `password-assignment`: a value given to a name that ends in
     `password`, `passwd` or `pwd` (`REDIS_PASSWORD="..."`,
     `db_pwd: ...`), whatever characters it holds. gitleaks' generic rule
     wants token characters, so a password with `^` or `!` went by. An
     unquoted value runs on through a backslash and the character after
     it (a shell's `\!`, `\$` or an escaped space), so a password holding
     one is not cut short at it. A
     quoted value is taken whole, up to its own closing quote, spaces and
     the other kind of quote included (`password-assignment-quoted` for
     double quotes, `password-assignment-single-quoted` for single); a
     template (`"${X}"`, `'$(...)'`, `'{{ x }}'`, `"<x>"`) and a value
     already redacted are not. A reference is no password: a variable
     (`$X`, `${X}`; a value that only starts with `$` still is one), a call
     or an identifier in code (`getpass()`, `self.password`,
     `newPassword`), a path or URL, a placeholder (`********`, `<...>`,
     `changeme`). So is a value of lowercase letters only, which a weak
     password can be too. A value CircleCI shows masked (`xxxx` and its
     last four) is still taken, since from the value alone it cannot be
     told from a password shaped the same. The text around it, which
     `make scrub-show` prints, usually tells (a tool listing masked
     variables), though not always; `make scrub-rotated` takes a mask off
     the list. An unquoted value is also redacted as a shell reads it
     (`abc\!def` as `abc!def` too), wherever the file holds that.
   - `password-hash`: a crypt(3) hash (bcrypt `$2y$...`, sha256crypt,
     sha512crypt, argon2). Not the password, but it can be cracked offline.
3. **Long random looking strings** with no known format
   (`generic-high-entropy` in `host/scrub-gitleaks.toml`): **reported only**,
   on `make scrub` and `make scrub-check` as one count (`ENTROPY=1` lists each
   one), and never redacted or kept in the ledger. Run
   `make scrub-check ENTROPY=1` over your history and look at what it lists;
   making this tier redact is a decision for after that. A string is not
   counted when everywhere the file holds it, it comes right after an
   `event_id`, `node_id`, `tool_use_id` or `Sid` key, `SHA256:` (a key
   fingerprint) or a public key type such as `ssh-ed25519`: on real
   history those were most of the hits. Still counted, and harmless: AWS
   resource ids and ARNs, and URL encoded one time values (an OAuth
   `state`).

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
over it, and only then does the scan say it was redacted.

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

The two password rules this repository adds are also held to their cases in
`tests/test_scrub_scan.py`, with Python's `re` standing in for Go's (the
same syntax for them), and were run the same way through the real gitleaks.

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
