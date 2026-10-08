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
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

ALLOW = json.load(open("/etc/gh-broker/allowlist.json"))
COMMANDS = set(ALLOW["commands"])
# The GitHub owners (users or organizations) whose repositories the broker
# acts on: GH_BROKER_OWNERS from the host, comma separated, else the
# allowlist's own. None built in, so each installation names its own.
_OWNER_NAMES = [o.strip() for o in os.environ.get("GH_BROKER_OWNERS", "").split(",") if o.strip()] or ALLOW["owners"]
OWNER_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
OWNERS = tuple(o.lower() + "/" for o in _OWNER_NAMES if OWNER_NAME.match(o))
SOCK = os.environ.get("GH_BROKER_SOCK", "/run/gh-broker/gh.sock")
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
PUBLIC_READS = frozenset(ALLOW.get("public_reads", []))
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


def public_read_ok(argv):
    """A read only command whose repositories outside the owners (named by
    -R, a URL or a repo: qualifier in its search) are all public. An org:,
    user: or owner: qualifier outside the owners names no one repository to
    check, so it is refused."""
    if " ".join(argv[:2]) not in PUBLIC_READS or flag_values(argv, OWNER_FLAGS):
        return False
    scopes = qualifiers(argv)
    if any(kind != "repo" and value + "/" not in OWNERS for kind, value in scopes):
        return False
    named = flag_values(argv, REPO_FLAGS) + ["/".join(m) for a in argv for m in URL_REPO.findall(a)]
    named += [value for kind, value in scopes if kind == "repo"]
    outside = [r for r in named if not r.lower().startswith(OWNERS)]
    return all(plain_repo(r) and is_public(r) for r in outside)


REPLY = re.compile(r"^repos/([^/]+)/[^/]+/pulls/\d+/comments/\d+/replies$", re.I)
MUTATIONS = frozenset(ALLOW["api"].get("graphql_mutations", []))


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


def api_ok(argv):
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
        return ops is not None and len(ops) == 1 and ops[0] in MUTATIONS
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


def allowed(argv):
    return refusal(argv) is None


# Why a command is refused, for the message the caller sees, or None when
# it is allowed.
def refusal(argv):
    if not argv:
        return "no command"
    if argv[0] == "api":
        return None if api_ok(argv) else (
            "this gh api call is not allowed: only reads, review comment replies"
            " and the listed GraphQL mutations, on the allowed owners (reads of a"
            " public repository too)")
    if " ".join(argv[:2]) not in COMMANDS:
        return f"'gh {' '.join(argv[:2])}' is not on the allowlist"
    if not repos_ok(argv) and not public_read_ok(argv):
        if " ".join(argv[:2]) in PUBLIC_READS:
            return "the repository is not under an allowed owner, and not public"
        return "the repository is not under an allowed owner"
    if not files_ok(argv):
        return ("a file named on the command line would be read here, beside the"
                " token; pass the body on stdin (--body-file -), which the"
                " workbench gh does for a --body-file path")
    if not merge_ok(argv):
        return "--admin merges around the merge queue"
    return None


def serve(conn):
    with conn:
        try:
            req = json.loads(conn.makefile().readline())
            argv = [str(a) for a in req["argv"]]
        except (ValueError, KeyError, TypeError):
            conn.sendall(b'{"rc":2,"out":"","err":"gh-broker: malformed request\\n"}')
            return
        why = refusal(argv)
        if why is not None:
            print("REFUSE", json.dumps(argv), flush=True)
            msg = f"gh-broker: refused: {why} (images/gh-broker/allowlist.json)\n"
            conn.sendall(json.dumps({"rc": 126, "out": "", "err": msg}).encode())
            return
        print("RUN", json.dumps(argv), "in", req.get("cwd"), flush=True)
        cwd = req.get("cwd")
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else "/"
        try:
            p = subprocess.run(["gh", *argv], cwd=cwd, input=req.get("stdin") or "",
                              capture_output=True, text=True, timeout=300)
            reply = {"rc": p.returncode, "out": p.stdout, "err": p.stderr}
        except subprocess.TimeoutExpired:
            reply = {"rc": 124, "out": "", "err": "gh-broker: timed out after 300s\n"}
        conn.sendall(json.dumps(reply).encode())


def main():
    if not os.environ.get("GH_TOKEN"):
        print("gh-broker: GH_TOKEN is not set; start it with the token's podman secret", file=sys.stderr)
        sys.exit(1)
    if not OWNERS or len(OWNERS) != len(_OWNER_NAMES):
        print("gh-broker: set GH_BROKER_OWNERS to the GitHub owners it may act on, comma separated (host: WORKBENCH_GH_OWNERS)", file=sys.stderr)
        sys.exit(1)
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    s = socket.socket(socket.AF_UNIX)
    s.bind(SOCK)
    os.chmod(SOCK, 0o660)
    s.listen()
    print("gh-broker: listening on", SOCK, flush=True)
    while True:
        conn, _ = s.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
