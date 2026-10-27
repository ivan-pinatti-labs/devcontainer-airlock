"""gh broker: run allowlisted gh commands for the workbench, holding the token.

Listens on a unix socket. Each connection carries one JSON line:
{"argv": [...], "cwd": "...", "stdin": "..."}. The reply is one JSON object:
{"rc": int, "out": str, "err": str}. Every request is logged to stdout, so
`podman logs gh-broker` is the record of what the workbench did on GitHub.

The token comes from the GH_TOKEN environment variable, which the host
passes as a podman secret and which never leaves this container. Refused:
anything not in /etc/gh-broker/allowlist.json, any repository or owner
outside the allowed owners (-R, --owner, a URL, a search qualifier), flags that read a file, `pr merge --admin`, and every
`gh api` write except replying to a review comment and the GraphQL mutations
the allowlist names (resolving a review thread). The exception: the read
only commands under public_reads, and `gh api` GETs under repos/, may name a
repository outside the owners when GitHub says it is public.

One command is the broker's own rather than gh's: `wait pr`, a bounded wait
on a pull request's state (see wait_pr below).

One broker serves every session level (docs/MODES.md), each on a socket of
its own: GH_BROKER_DIR/<level>/gh.sock. The host mounts into a session only
its own level's folder, so the level a request is held to is the socket it
came in on, never anything the request says. allowlist.json is the hardened
list; allowlist.<level>.json adds to the level before it, so each level
allows everything the one before it does. The refusals written in the code
below (owners, files, --admin, api writes) hold at every level.
"""
import json
import os
import re
import selectors
import socket
import subprocess
import sys
import threading
import time

ALLOW = json.load(open("/etc/gh-broker/allowlist.json"))
# The session levels, each allowing everything the one before it does.
LEVELS = ("hardened", "assisted", "unattended")
# The lists a level's override may add to, and the broker's own commands,
# the only ones broker_commands may name.
LIST_KEYS = ("commands", "public_reads", "broker_commands", "graphql_mutations")
OWN_COMMANDS = frozenset({"wait pr"})
COMMAND_NAME = re.compile(r"^[a-z][a-z-]*( [a-z][a-z-]*)?$")


def read_override(level):
    """What allowlist.<level>.json adds to the level before it, by list, or
    nothing when there is no such file. Anything else in it (a key no list
    has, a value that is not a list of names) stops the broker, so a mistake
    there cannot pass for an empty override."""
    try:
        f = open(f"/etc/gh-broker/allowlist.{level}.json")
    except FileNotFoundError:
        return {}
    with f:
        override = json.load(f)
    if not isinstance(override, dict) or set(override) - {"comment", "add"}:
        raise ValueError(f"allowlist.{level}.json: only comment and add")
    add = override.get("add", {})
    if not isinstance(add, dict) or set(add) - set(LIST_KEYS):
        raise ValueError(f"allowlist.{level}.json: add holds only {', '.join(LIST_KEYS)}")
    for key, names in add.items():
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError(f"allowlist.{level}.json: {key} is a list of names")
    return add


def level_policies():
    """Each level's lists: the hardened ones from allowlist.json, and every
    other level's, the level before it plus its override."""
    lists = {
        "commands": ALLOW["commands"],
        "public_reads": ALLOW.get("public_reads", []),
        "broker_commands": ALLOW.get("broker_commands", []),
        "graphql_mutations": ALLOW["api"].get("graphql_mutations", []),
    }
    policies, current = {}, {key: frozenset(lists[key]) for key in LIST_KEYS}
    for level in LEVELS:
        add = {} if level == "hardened" else read_override(level)
        current = {key: current[key] | frozenset(add.get(key, [])) for key in LIST_KEYS}
        if not all(COMMAND_NAME.match(c) for c in current["commands"] | current["public_reads"]):
            raise ValueError(f"{level}: a command is its name, one or two words")
        if not current["broker_commands"] <= OWN_COMMANDS:
            raise ValueError(f"{level}: broker_commands names only {', '.join(sorted(OWN_COMMANDS))}")
        policies[level] = current
    return policies


POLICIES = level_policies()
# The GitHub owners (users or organizations) whose repositories the broker
# acts on: GH_BROKER_OWNERS from the host, comma separated, else the
# allowlist's own. None built in, so each installation names its own.
_OWNER_NAMES = [o.strip() for o in os.environ.get("GH_BROKER_OWNERS", "").split(",") if o.strip()] or ALLOW["owners"]
OWNER_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
OWNERS = tuple(o.lower() + "/" for o in _OWNER_NAMES if OWNER_NAME.match(o))
# The folder holding a folder per level, each with that level's socket.
SOCK_DIR = os.environ.get("GH_BROKER_DIR", "/run/gh-broker")
REPO_FLAGS = ("-R", "--repo")
# gh search scopes to a user or organization with --owner, and to a
# repository with --repo, each repeatable and comma separated.
OWNER_FLAGS = ("--owner",)


URL = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/", re.I)
# The same scopes written into a search query itself (`repo:x/y`, `org:x`,
# `user:x`), which gh passes through to GitHub unchanged.
QUALIFIER = re.compile(r"(?:^|[\s(])-?(repo|org|user|owner):([^\s()]+)", re.I)


def flag_values(argv, flags):
    """Every value given to one of these flags, as `--flag v`, `--flag=v` or,
    for a short flag, `-Rv`, split at commas."""
    values = []
    for i, a in enumerate(argv):
        if a in flags:
            values.append(argv[i + 1] if i + 1 < len(argv) else "")
            continue
        for f in flags:
            if f.startswith("--") and a.startswith(f + "="):
                values.append(a.split("=", 1)[1])
            elif not f.startswith("--") and a.startswith(f) and len(a) > len(f):
                values.append(a[len(f):])
    return [v.strip() for value in values for v in value.split(",")]


def repos_ok(argv):
    # gh takes full URLs for pull requests, issues and repositories too
    # (`gh pr comment https://github.com/<owner>/<repo>/pull/1`), which name
    # a repository without -R.
    for a in argv:
        for owner in URL.findall(a):
            if not (owner.lower() + "/").startswith(OWNERS):
                return False
    if not all(v.lower().startswith(OWNERS) for v in flag_values(argv, REPO_FLAGS)):
        return False
    if not all(v.lower() + "/" in OWNERS for v in flag_values(argv, OWNER_FLAGS)):
        return False
    for kind, value in qualifiers(argv):
        if kind == "repo":
            if not value.startswith(OWNERS):
                return False
        elif value + "/" not in OWNERS:
            return False
    return True


# `gh issue list` and `gh pr list` take a search query too, and a `repo:`
# qualifier in it widens the read past the -R they name: repeated `repo:`
# qualifiers are OR'ed.
SEARCH_FLAGS = ("--search", "-S")


def qualifiers(argv):
    """Every scope qualifier in a search query of argv, as (kind, value)
    lower cased: in a gh search's own terms, and in a --search/-S value."""
    texts = argv[2:] if argv[:1] == ["search"] else []
    texts = texts + flag_values(argv, SEARCH_FLAGS)
    return [
        (kind.lower(), value.strip("\"'").lower())
        for text in texts
        for kind, value in QUALIFIER.findall(text)
    ]


# Read only commands that may also name a public repository outside the
# owners: following an upstream issue means reading someone else's
# repository. Writes stay held to the owners.
REPO_NAME = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
URL_REPO = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)", re.I)
# How long an answer to "is it public" is kept. A repository made private
# meanwhile stays readable for at most this long, with a token that could
# read it anyway.
PUBLIC_TTL = 600
_public = {}
_public_lock = threading.Lock()


def is_public(repo):
    """Whether GitHub says `repo` (owner/name) is public, asked with the
    token. Only an answer is kept; a failed lookup is asked again."""
    key = repo.lower()
    now = time.monotonic()
    with _public_lock:
        hit = _public.get(key)
    if hit and now - hit[1] < PUBLIC_TTL:
        return hit[0]
    try:
        p = subprocess.run(
            ["gh", "api", f"repos/{key}", "--jq", ".visibility"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    if p.returncode != 0:
        return False
    public = p.stdout.strip() == "public"
    with _public_lock:
        _public[key] = (public, now)
    return public


def plain_repo(repo):
    """`repo` is owner/name and nothing else: no host, no `.` or `..`."""
    return bool(REPO_NAME.match(repo)) and repo.split("/")[1] not in (".", "..")


def public_read_ok(argv, policy):
    """A read only command whose repositories outside the owners (named by
    -R, a URL or a repo: qualifier in its search) are all public. An org:,
    user: or owner: qualifier outside the owners names no one repository to
    check, so it is refused."""
    if " ".join(argv[:2]) not in policy["public_reads"] or flag_values(argv, OWNER_FLAGS):
        return False
    scopes = qualifiers(argv)
    if any(kind != "repo" and value + "/" not in OWNERS for kind, value in scopes):
        return False
    named = flag_values(argv, REPO_FLAGS) + ["/".join(m) for a in argv for m in URL_REPO.findall(a)]
    named += [value for kind, value in scopes if kind == "repo"]
    outside = [r for r in named if not r.lower().startswith(OWNERS)]
    return all(plain_repo(r) and is_public(r) for r in outside)


REPLY = re.compile(r"^repos/([^/]+)/[^/]+/pulls/\d+/comments/\d+/replies$", re.I)


def mutation_fields(query):
    """The top level fields of a GraphQL mutation, the operations it runs.
    Aliases (`x: deleteRepository(...)`) are resolved to the real field, and
    string literals and argument lists are skipped. None when a fragment
    spread appears, which could hide an operation.

    The scan below only has to be right for the plain subset of GraphQL an
    allowed operation needs, so anything outside it is refused (None) rather
    than parsed: a `#` comment, which the server skips to the end of the line
    while a scan would still see its quotes; block strings and escapes; non
    ASCII; and string literals holding anything but identifier characters."""
    if not re.fullmatch(r"[\x20-\x7e\t\r\n]*", query):
        return None
    if "#" in query or "\\" in query or '"""' in query or query.count('"') % 2:
        return None
    if not all(re.fullmatch(r"[A-Za-z0-9_=+/.:-]*", s) for s in re.findall(r'"([^"]*)"', query)):
        return None
    m = re.search(r"\bmutation\b[^{]*\{", query)
    if m is None:
        return None
    names, depth, i, in_string = [], 1, m.end(), False
    token, alias_pending = "", False
    while i < len(query) and depth > 0:
        c = query[i]
        if in_string:
            # No escapes to skip: a query holding a backslash is refused above.
            if c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c in "{(":
            if depth == 1 and token:
                names.append(token)
            token, alias_pending = "", False
            depth += 1
        elif c in "})":
            if depth == 1 and token:
                names.append(token)
            token = ""
            depth -= 1
        elif depth == 1:
            if c.isalnum() or c == "_":
                token += c
            elif c == ":":
                token, alias_pending = "", True
            elif c == ".":
                return None
            elif token and not alias_pending:
                names.append(token)
                token = ""
            elif token and alias_pending:
                alias_pending = False
        i += 1
    return names


def api_ok(argv, policy):
    rest = argv[1:]
    method = None
    path = None
    fields = []
    i = 0
    while i < len(rest):
        a = rest[i]
        # Nothing here needs another host, custom headers or a request body
        # read from a file.
        if a in ("--hostname", "-H", "--header", "--input") or a.startswith(
            ("--hostname=", "--header=", "--input=", "-H")
        ):
            return False
        # Combined short forms, -XPOST and -fbody=..., split into flag and value.
        if len(a) > 2 and a[:2] in ("-X", "-f", "-F") and not a.startswith("--"):
            rest = rest[:i] + [a[:2], a[2:]] + rest[i + 1:]
            a = rest[i]
        if a in ("-X", "--method"):
            method = rest[i + 1].upper() if i + 1 < len(rest) else ""
            i += 2
            continue
        if a.startswith("--method="):
            method = a.split("=", 1)[1].upper()
        elif a in ("-f", "-F", "--field", "--raw-field"):
            fields.append(rest[i + 1] if i + 1 < len(rest) else "")
            i += 2
            continue
        elif a.startswith(("--field=", "--raw-field=")):
            fields.append(a.split("=", 1)[1])
        elif path is None and not a.startswith("-"):
            path = a.lstrip("/")
        i += 1
    if path is None:
        return False
    # A field value of @file reads it from a file here, in the broker.
    if any("=@" in f or f.startswith("@") for f in fields):
        return False
    if path == "graphql":
        query = " ".join(fields)
        if not re.search(r"\bmutation\b", query):
            return True
        # One mutation, running exactly one allowed operation.
        if len(re.findall(r"\bmutation\b", query)) != 1:
            return False
        ops = mutation_fields(query)
        return ops is not None and len(ops) == 1 and ops[0] in policy["graphql_mutations"]
    reply = REPLY.match(path)
    if reply:
        # A reply to a review comment: a POST (gh sends one whenever fields
        # are given) with an inline body and nothing else.
        return (
            (reply.group(1).lower() + "/").startswith(OWNERS)
            and method in (None, "POST")
            and fields != []
            and all(f.startswith("body=") for f in fields)
        )
    # Everything else is read only: GET, and no fields, since gh turns any
    # request with fields into a POST. A `.` or `..` segment would climb out
    # of the repository the path names, so none is taken.
    if method not in (None, "GET") or fields:
        return False
    segments = path.split("?", 1)[0].split("/")
    if any(s in (".", "..") or "%2e" in s.lower() for s in segments):
        return False
    if path.lower().startswith(tuple("repos/" + o for o in OWNERS)):
        return True
    # Any other repository: when it is public.
    return segments[0] == "repos" and len(segments) > 2 and plain_repo(
        "/".join(segments[1:3])) and is_public("/".join(segments[1:3]))


# Flags that make gh read (or write) a file named by the caller. The file
# would be read here, in the broker, where /proc/self/environ holds the token
# and WORKBENCH_ROOT holds every workspace; a body posted from one is a body
# the caller can read back. Bodies come from stdin instead (`--body-file -`),
# which the workbench shim forwards.
FILE_FLAGS = ("--body-file", "-F", "--recover", "--notes-file")


def files_ok(argv):
    for i, a in enumerate(argv):
        if a in FILE_FLAGS:
            if i + 1 >= len(argv) or argv[i + 1] != "-":
                return False
        elif a.startswith(tuple(f + "=" for f in FILE_FLAGS if f.startswith("--"))):
            if a.split("=", 1)[1] != "-":
                return False
        elif a.startswith("-F") and len(a) > 2 and a[2:] != "-":
            return False
    return True


def merge_ok(argv):
    # A merge goes through the merge queue, never around it: --admin merges
    # past the queue and the checks it runs.
    if argv[:2] != ["pr", "merge"]:
        return True
    return not any(a == "--admin" or a.startswith("--admin=") for a in argv)


# `gh wait pr N -R owner/repo [--timeout SECONDS]`: the broker's own command,
# not gh's. It reads the pull request once a minute and answers as soon as
# anything an agent acts on has changed (state, draft, mergeable, the review
# decision, a new push, the merge queue, the overall check result, the count
# of reviews, comments or unresolved threads), or when the timeout passes.
# An agent runs it in the background and is told when it returns, so it waits
# for the event itself instead of guessing when to look, and every step it
# takes afterwards is still a command of its own (#145). Read only, and held
# to the allowed owners like any other command.
# The longest wait, in seconds: one CodeRabbit review slot (about an hour)
# and a margin. A caller that asks for longer gets this, so no wait is
# unbounded. Review threads are read 100 at a time, at most WAIT_PAGES pages
# (a thousand threads) of WAIT_READ_TIMEOUT seconds each, so one look at the
# pull request takes 300 seconds at most, and a whole wait WAIT_CAP plus one
# look. The workbench gh allows that and a margin for the reply.
WAIT_CAP = 3900
WAIT_POLL = 60
WAIT_PAGES = 10
WAIT_READ_TIMEOUT = 30
WAIT_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String){"
    "repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){state isDraft mergeable reviewDecision headRefOid"
    " mergeQueueEntry{state} reviews{totalCount} comments{totalCount}"
    " reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}"
    " nodes{isResolved comments{totalCount}}}"
    " commits(last:1){nodes{commit{statusCheckRollup{state}}}}}}}"
)
# ASCII digits only: str.isdigit() also takes "²", which int() then refuses.
# Nine at most, so int() never meets a string past Python's digit limit.
DIGITS = re.compile(r"[0-9]{1,9}")


def wait_args(argv):
    """(repo, number, timeout) for a well formed `wait pr`, else None."""
    if argv[:2] != ["wait", "pr"] or len(argv) < 3 or not DIGITS.fullmatch(argv[2]):
        return None
    repo, timeout, i = None, WAIT_CAP, 3
    while i < len(argv):
        flag, value = argv[i], argv[i + 1] if i + 1 < len(argv) else None
        if value is None:
            return None
        if flag in REPO_FLAGS and repo is None:
            repo = value
        elif flag == "--timeout" and DIGITS.fullmatch(value) and int(value) > 0:
            timeout = min(int(value), WAIT_CAP)
        else:
            return None
        i += 2
    if repo is None or not plain_repo(repo) or not repo.lower().startswith(OWNERS):
        return None
    return repo, int(argv[2]), timeout


def read_pr(repo, number, after):
    """One page of the wait query: the pull request, with its review threads
    from the cursor `after` (None for the first page)."""
    owner, name = repo.split("/")
    cursor = ["-F", "after=null"] if after is None else ["-f", f"after={after}"]
    p = subprocess.run(
        [
            "gh", "api", "graphql", "-f", f"query={WAIT_QUERY}", "-f", f"owner={owner}",
            "-f", f"name={name}", "-F", f"number={number}", *cursor,
        ],
        capture_output=True, text=True, timeout=WAIT_READ_TIMEOUT,
    )
    # A failed call can still print partial data; it is not a reading.
    if p.returncode != 0:
        raise ValueError(p.stderr)
    return json.loads(p.stdout)["data"]["repository"]["pullRequest"]


def pr_snapshot(repo, number):
    """What `wait` compares: the pull request's state, as a flat dict, or None
    when GitHub could not be read this time, or answered with something that
    is not a whole reading."""
    threads, after = [], None
    try:
        for _ in range(WAIT_PAGES):
            pr = read_pr(repo, number, after)
            if not pr:
                return None
            page = pr["reviewThreads"]
            threads += page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                break
            # A cursor that does not move would read the same page again.
            if page["pageInfo"]["endCursor"] in (None, after):
                return None
            after = page["pageInfo"]["endCursor"]
        commits = pr["commits"]["nodes"]
        rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
        return {
            "state": pr["state"],
            "draft": pr["isDraft"],
            "mergeable": pr["mergeable"],
            "review": pr["reviewDecision"],
            "head": pr["headRefOid"],
            "queue": (pr["mergeQueueEntry"] or {}).get("state"),
            "checks": (rollup or {}).get("state"),
            "reviews": pr["reviews"]["totalCount"],
            "comments": pr["comments"]["totalCount"],
            "open_threads": sum(not t["isResolved"] for t in threads),
            # A reply on a thread that stays open changes nothing above.
            "thread_comments": sum(t["comments"]["totalCount"] for t in threads),
        }
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, TypeError):
        return None


def wait_pr(repo, number, timeout, sleep=time.sleep, clock=time.monotonic):
    """The reply to `wait pr`: rc 0 and what changed as soon as something
    does, rc 124 with the state unchanged at the timeout, rc 1 when the pull
    request cannot be read at all. The first look counts against the timeout,
    so the whole wait is the timeout plus at most the one last look."""
    deadline = clock() + timeout
    first = pr_snapshot(repo, number)
    if first is None:
        return {"rc": 1, "out": "", "err": f"gh-broker: cannot read {repo}#{number}\n"}
    while (left := deadline - clock()) > 0:
        sleep(min(WAIT_POLL, left))
        now = pr_snapshot(repo, number)
        if now is None:
            continue
        changed = sorted(k for k in now if now[k] != first[k])
        if changed:
            out = {"changed": changed, "was": {k: first[k] for k in changed}, "now": now}
            return {"rc": 0, "out": json.dumps(out) + "\n", "err": ""}
    out = {"changed": [], "now": first, "waited": timeout}
    return {"rc": 124, "out": json.dumps(out) + "\n",
            "err": f"gh-broker: {repo}#{number} unchanged after {timeout}s\n"}


def allowed(argv, level="hardened"):
    return refusal(argv, level) is None


# Why a command is refused at `level`, for the message the caller sees, or
# None when it is allowed.
def refusal(argv, level="hardened"):
    policy = POLICIES[level]
    if not argv:
        return "no command"
    if " ".join(argv[:2]) in policy["broker_commands"]:
        return None if wait_args(argv) else (
            "usage: gh wait pr NUMBER -R OWNER/REPO [--timeout SECONDS], on a"
            f" repository under an allowed owner; at most {WAIT_CAP}s")
    if argv[0] == "api":
        return None if api_ok(argv, policy) else (
            "this gh api call is not allowed: only reads, review comment replies"
            " and the listed GraphQL mutations, on the allowed owners (reads of a"
            " public repository too)")
    if " ".join(argv[:2]) not in policy["commands"]:
        return f"'gh {' '.join(argv[:2])}' is not on the allowlist at the {level} level"
    if not repos_ok(argv) and not public_read_ok(argv, policy):
        if " ".join(argv[:2]) in policy["public_reads"]:
            return "the repository is not under an allowed owner, and not public"
        return "the repository is not under an allowed owner"
    if not files_ok(argv):
        return ("a file named on the command line would be read here, beside the"
                " token; pass the body on stdin (--body-file -), which the"
                " workbench gh does for a --body-file path")
    if not merge_ok(argv):
        return "--admin merges around the merge queue"
    return None


def serve(conn, level):
    """Answer one connection, which came in on the socket of `level`."""
    with conn:
        try:
            req = json.loads(conn.makefile().readline())
            argv = [str(a) for a in req["argv"]]
        except (ValueError, KeyError, TypeError):
            conn.sendall(b'{"rc":2,"out":"","err":"gh-broker: malformed request\\n"}')
            return
        why = refusal(argv, level)
        if why is not None:
            print("REFUSE", level, json.dumps(argv), flush=True)
            files = "allowlist.json" if level == "hardened" else f"allowlist.json, allowlist.{level}.json"
            msg = f"gh-broker: refused: {why} (images/gh-broker/{files})\n"
            conn.sendall(json.dumps({"rc": 126, "out": "", "err": msg}).encode())
            return
        print("RUN", level, json.dumps(argv), "in", req.get("cwd"), flush=True)
        if " ".join(argv[:2]) in POLICIES[level]["broker_commands"]:
            conn.sendall(json.dumps(wait_pr(*wait_args(argv))).encode())
            return
        cwd = req.get("cwd")
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else "/"
        try:
            p = subprocess.run(["gh", *argv], cwd=cwd, input=req.get("stdin") or "",
                              capture_output=True, text=True, timeout=300)
            reply = {"rc": p.returncode, "out": p.stdout, "err": p.stderr}
        except subprocess.TimeoutExpired:
            reply = {"rc": 124, "out": "", "err": "gh-broker: timed out after 300s\n"}
        conn.sendall(json.dumps(reply).encode())


def listen(level):
    """A socket listening at SOCK_DIR/<level>/gh.sock, or None when the host
    mounted no folder for that level. Says what the level allows beyond
    hardened, so the log records each level's effective list."""
    folder = os.path.join(SOCK_DIR, level)
    if not os.path.isdir(folder):
        return None
    path = os.path.join(folder, "gh.sock")
    if os.path.exists(path):
        os.unlink(path)
    s = socket.socket(socket.AF_UNIX)
    s.bind(path)
    os.chmod(path, 0o660)
    s.listen()
    beyond = {k: sorted(POLICIES[level][k] - POLICIES["hardened"][k]) for k in LIST_KEYS}
    beyond = {k: v for k, v in beyond.items() if v}
    print(f"gh-broker: {level} listening on {path}, beyond hardened: {json.dumps(beyond)}", flush=True)
    return s


def main():
    if not os.environ.get("GH_TOKEN"):
        print("gh-broker: GH_TOKEN is not set; start it with the token's podman secret", file=sys.stderr)
        sys.exit(1)
    if not OWNERS or len(OWNERS) != len(_OWNER_NAMES):
        print("gh-broker: set GH_BROKER_OWNERS to the GitHub owners it may act on, comma separated (host: WORKBENCH_GH_OWNERS)", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(os.path.join(SOCK_DIR, "hardened")):
        print(f"gh-broker: no {SOCK_DIR}/hardened to listen in; the host mounts a folder per level", file=sys.stderr)
        sys.exit(1)
    # Each socket is registered with its level, once, here: a connection is
    # served at the level of the socket it came in on.
    sel = selectors.DefaultSelector()
    for level in LEVELS:
        s = listen(level)
        if s is not None:
            sel.register(s, selectors.EVENT_READ, level)
    while True:
        for key, _ in sel.select():
            conn, _ = key.fileobj.accept()
            threading.Thread(target=serve, args=(conn, key.data), daemon=True).start()


if __name__ == "__main__":
    main()
