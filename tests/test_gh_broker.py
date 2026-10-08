"""Tests for images/gh-broker/broker.py.

The allow and refuse cases are the ones images/gh-broker/test_broker.py runs
in the image build, here as pytest cases run from the tree. The broker reads
its allowlist from /etc while it loads, so `load` hands it an `open` that
reads the copy in the tree instead. Nothing here starts gh or listens on a
real socket path outside the test's own directory.
"""

# cspell:words Rsomeone socketpair Srepo
from __future__ import annotations

import builtins
import json
import socket
import subprocess

import pytest
from conftest import REPO_ROOT, load

ALLOWLIST = REPO_ROOT / "images/gh-broker/allowlist.json"


def tree_open(path, *args, **kwargs):
    """`open` with the image's allowlist path pointing at the tree's copy."""
    if path == "/etc/gh-broker/allowlist.json":
        path = ALLOWLIST
    return builtins.open(path, *args, **kwargs)


def load_broker(monkeypatch, owners="example-org"):
    monkeypatch.setenv("GH_BROKER_OWNERS", owners)
    return load("images/gh-broker/broker.py", "gh_broker", open=tree_open)


# What GitHub answers for `gh api repos/<repo> --jq .visibility`, by repo; a
# repository missing here is one the lookup fails for (absent, or no access).
VISIBILITY = {"upstream/project": "public", "upstream/hidden": "private"}


def fake_visibility(asked):
    def run(argv, **kwargs):
        asked.append(argv)
        repo = argv[2].removeprefix("repos/")
        if repo in VISIBILITY:
            return subprocess.CompletedProcess(argv, 0, VISIBILITY[repo] + "\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "HTTP 404: Not Found\n")

    return run


@pytest.fixture
def broker(monkeypatch):
    loaded = load_broker(monkeypatch)
    monkeypatch.setattr(loaded.subprocess, "run", fake_visibility([]))
    return loaded


CASES = [
    (True, "pr list"),
    (True, "pr view 25 -R example-org/devcontainer-airlock"),
    (True, "pr comment https://github.com/example-org/gh-actions/pull/1 --body hi"),
    (True, "api repos/example-org/gh-actions"),
    (True, "api graphql -f query={viewer{login}}"),
    (False, "pr comment https://github.com/someone-else/repo/pull/1 --body spam"),
    (False, "pr view github.com/evil/x/pull/2"),
    (False, "pr list -R evil.com/example-org/x"),
    (False, "api -XPOST repos/example-org/gh-actions/issues"),
    (False, "api -X DELETE repos/example-org/x"),
    (False, "api graphql -F query=@m.graphql"),
    (False, "api graphql -Fquery=@m.graphql"),
    (False, "api graphql -f query=mutation{x}"),
    (False, "api --hostname evil.com repos/example-org/x"),
    (False, "api -H X-HTTP-Method-Override:DELETE repos/example-org/x"),
    (False, "api --input f repos/example-org/x"),
    (True, "issue create -R example-org/x -t t --body-file -"),
    (True, "pr create --title t --body-file - --draft"),
    (False, "issue create -R example-org/x -t t --body-file /proc/self/environ"),
    (False, "pr comment 25 --body-file=/proc/self/environ"),
    (False, "pr create -t t -F /home/other/.env"),
    (False, "pr create -t t -F/proc/self/environ"),
    (False, "issue create -t t --recover /proc/self/environ"),
    (
        True,
        "api -X POST repos/example-org/x/pulls/25/comments/99/replies -f body=Fixed",
    ),
    (True, "api repos/example-org/x/pulls/25/comments/99/replies -f body=Declined"),
    (
        True,
        (
            'api graphql -f query=mutation{resolveReviewThread(input:{threadId:"T_1"})'
            "{thread{isResolved}}}"
        ),
    ),
    (False, "api -X POST repos/someone-else/x/pulls/1/comments/2/replies -f body=spam"),
    (
        False,
        (
            "api -X POST repos/example-org/x/pulls/25/comments/99/replies"
            " -F body=@/proc/self/environ"
        ),
    ),
    (
        False,
        (
            "api -X POST repos/example-org/x/pulls/25/comments/99/replies"
            " -f body=x -f in_reply_to=1"
        ),
    ),
    (False, "api -X DELETE repos/example-org/x/pulls/25/comments/99/replies"),
    (False, "api repos/example-org/x/issues -f title=t"),
    (
        False,
        (
            'api graphql -f query=mutation{deleteRepository(input:{repositoryId:"R"})'
            "{clientMutationId}}"
        ),
    ),
    (
        False,
        (
            'api graphql -f query=mutation{resolveReviewThread(input:{threadId:"T"})'
            '{thread{id}}x:deleteRepository(input:{repositoryId:"R"}){clientMutationId}}'
        ),
    ),
    (
        False,
        (
            'api graphql -f query=mutation{x:deleteRepository(input:{repositoryId:"R"})'
            "{clientMutationId}}"
        ),
    ),
    (False, "api graphql -f query=mutation{...F}"),
    (True, "pr merge 25 --auto -R example-org/x"),
    (True, "pr merge --auto --squash"),
    (False, "pr merge 25 --admin"),
    (False, "pr merge 25 --auto --admin=true"),
    (False, "pr merge https://github.com/evil/x/pull/1 --auto"),
    (False, "auth token"),
    (False, "repo delete example-org/x --yes"),
    (False, "secret list"),
    (False, "api user"),
    # Search is scoped by --owner as well as --repo, and by qualifiers in the
    # query; each is held to the same owners.
    (True, "search issues flaky --owner example-org"),
    (True, "search prs --owner=EXAMPLE-ORG --state open"),
    (True, "search issues --repo example-org/x,example-org/y deadlock"),
    (True, "search issues repo:example-org/x is:open"),
    (True, "search prs org:example-org review"),
    (False, "search issues --owner someone-else"),
    (False, "search issues --owner=someone-else"),
    (False, "search prs --owner example-org,someone-else"),
    (False, "search prs --owner example-org-evil"),
    (False, "search issues --owner"),
    (False, "search issues --repo someone-else/x"),
    (False, "search issues --repo example-org/x,someone-else/y"),
    (False, "pr list -Rsomeone-else/x"),
    (False, "pr comment 1 -Rsomeone-else/x --body spam"),
    (False, "search issues repo:someone-else/x"),
    (False, "search issues is:open org:someone-else"),
    (False, "search prs user:someone-else"),
    (False, "search issues -repo:someone-else/x"),
    # Beyond the image build's cases: the remaining paths through the parser.
    (False, ""),
    (False, "api"),
    (True, "api --method=GET repos/example-org/x"),
    (False, "api --method=PATCH repos/example-org/x"),
    (False, "api repos/example-org/x -X"),
    (False, "api repos/example-org/x --field"),
    (False, "api repos/example-org/x --raw-field=a=b"),
    (True, "api repos/example-org/x/pulls/1/comments/2/replies --field=body=ok"),
    (True, "api /repos/example-org/x --paginate"),
    (False, "api repos/other/x"),
    (False, "api --header=X:y repos/example-org/x"),
    (False, "api repos/example-org/x/pulls/1/comments/2/replies"),
    (False, "api graphql -f query=mutation{a}mutation{b}"),
    (True, "pr view 1 --repo=example-org/x"),
    (False, "pr view 1 -R"),
    (False, "pr create -t t -F"),
    (False, "pr create -t t --notes-file=x"),
    (True, "pr create -t t --notes-file=-"),
    (True, "pr create -t t -F-"),
    (True, "search issues owner:example-org"),
    # Reads of a public repository outside the owners; writes stay refused,
    # and so does anything private, unknown or not plain owner/name.
    (True, "issue view 1035 -R upstream/project"),
    (True, "issue list --repo=upstream/project --state open"),
    (True, "pr view https://github.com/upstream/project/pull/3"),
    (True, "release list -R upstream/project"),
    (True, "release view v1 -R UPSTREAM/project"),
    (True, "issue view 1 -R example-org/x"),
    (False, "issue comment 1035 -R upstream/project --body spam"),
    (False, "pr comment https://github.com/upstream/project/pull/3 --body spam"),
    (False, "issue view 1 -R upstream/hidden"),
    (False, "issue view 1 -R upstream/gone"),
    (False, "issue view 1 -R github.com/upstream/project"),
    (False, "issue view 1 -R upstream/.."),
    (False, "pr view https://github.com/upstream/hidden/pull/3"),
    (False, "search issues repo:upstream/project"),
    (True, "api repos/upstream/project/issues/1035"),
    (True, "api repos/upstream/project/releases?per_page=5"),
    (False, "api repos/upstream/hidden/issues/1"),
    (False, "api repos/upstream/gone"),
    (False, "api repos/upstream"),
    (False, "api users/upstream"),
    (False, "api -X POST repos/upstream/project/issues"),
    (False, "api repos/upstream/project/issues -f title=t"),
    (False, "api repos/upstream/project/../secret"),
    (False, "api repos/example-org/x/../../upstream/hidden"),
    (False, "api repos/example-org/x/%2E%2E/y"),
    # A list command's --search/-S is a search query too: a repo: qualifier
    # in it widens the read, so it is held to the same owners or public.
    (True, "pr list -R example-org/x --search repo:example-org/y"),
    (True, "issue list -S org:example-org"),
    (False, "pr list -R example-org/x --search repo:evil/private"),
    (False, "pr list --search=repo:upstream/hidden"),
    (False, "issue list -S org:evil"),
    (True, "pr list -R upstream/project --search repo:upstream/project"),
    (True, "issue list -R upstream/project -Srepo:upstream/project"),
    (False, "pr list -R upstream/project --search repo:upstream/hidden"),
    (False, "issue list -R upstream/project -S user:evil"),
]

SPACED = ["api", "graphql", "-f"]
ARGV_CASES = [
    (
        True,
        [
            *SPACED,
            (
                "query=mutation($id:ID!){resolveReviewThread(input:{threadId:$id})"
                "{thread{isResolved}}}"
            ),
            "-f",
            "id=T_1",
        ],
    ),
    (
        True,
        [
            *SPACED,
            (
                'query=mutation {\n  resolveReviewThread(input: {threadId: "T_1"}) {\n'
                "    thread { isResolved }\n  }\n}"
            ),
        ],
    ),
    # A comment the server skips hides a quote from the scan, and with it a
    # second operation (reported on PR 25).
    (
        False,
        [
            *SPACED,
            (
                'query=mutation{resolveReviewThread(input:{threadId:"T"}){thread{id}} #"\n'
                'deleteRepository(input:{repositoryId:"R"}){clientMutationId}}'
            ),
        ],
    ),
    (
        False,
        [
            *SPACED,
            'query=mutation{resolveReviewThread(input:{threadId:"""T"""}){thread{id}}}',
        ],
    ),
    (
        False,
        [
            *SPACED,
            'query=mutation{resolveReviewThread(input:{threadId:"T\\""}){thread{id}}}',
        ],
    ),
    (
        False,
        [
            *SPACED,
            'query=mutation{resolveReviewThread(input:{threadId:"T x"}){thread{id}}}',
        ],
    ),
    (
        False,
        [
            *SPACED,
            (
                'query=mutation{﻿deleteRepository(input:{repositoryId:"R"})'
                "{clientMutationId}}"
            ),
        ],
    ),
    (False, [*SPACED, "query=mutation"]),
    # Unbalanced: the scan stops at the end with the one allowed field, and
    # GitHub refuses the syntax error itself.
    (True, [*SPACED, 'query=mutation{resolveReviewThread(input:{x:"a"}']),
    (True, ["search", "issues", "repo:example-org/x is:open deadlock"]),
    (False, ["search", "issues", "deadlock (org:someone-else)"]),
    (True, ["search", "issues", "deadlock (org:example-org)"]),
    (True, ["search", "prs", "(repo:example-org/x OR repo:example-org/y) is:open"]),
    (False, ["search", "prs", "(repo:example-org/x OR repo:someone-else/y)"]),
    (False, ["search", "prs", "is:open repo:someone-else/x"]),
    (
        False,
        [
            "pr",
            "list",
            "-R",
            "upstream/project",
            "--search",
            "is:open repo:upstream/hidden",
        ],
    ),
    (
        True,
        ["pr", "list", "-R", "upstream/project", "--search", "is:open author:someone"],
    ),
]


@pytest.mark.parametrize(("expected", "command"), CASES)
def test_allowed(broker, expected, command):
    assert broker.allowed(command.split()) is expected


@pytest.mark.parametrize(("expected", "argv"), ARGV_CASES)
def test_allowed_argv(broker, expected, argv):
    assert broker.allowed(argv) is expected


@pytest.mark.parametrize(
    ("owners", "count"), [("", 0), ("evil name", 0), ("example-org", 1)]
)
def test_owners_come_from_the_environment(monkeypatch, owners, count):
    """Nothing is allowed without owners."""
    fresh = load_broker(monkeypatch, owners)
    monkeypatch.setattr(fresh.subprocess, "run", fake_visibility([]))
    assert len(fresh.OWNERS) == count
    assert fresh.allowed(["pr", "view", "1", "-R", "example-org/x"]) is bool(count)


@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("pr create -t t --body-file /tmp/body.md", "stdin"),
        ("auth token", "not on the allowlist"),
        ("pr view 1 -R evil/x", "allowed owner"),
        ("search issues --owner evil", "allowed owner"),
        ("pr merge 25 --admin", "merge queue"),
        ("api -X DELETE repos/example-org/x", "gh api call"),
        ("", "no command"),
        ("issue view 1 -R upstream/hidden", "allowed owner, and not public"),
    ],
)
def test_refusal_says_why(broker, command, why):
    assert why in broker.refusal(command.split())


def test_visibility_is_asked_once_then_again_when_stale(broker, monkeypatch):
    asked = []
    monkeypatch.setattr(broker.subprocess, "run", fake_visibility(asked))
    now = [1000.0]
    monkeypatch.setattr(broker.time, "monotonic", lambda: now[0])
    assert broker.is_public("Upstream/Project")
    assert broker.is_public("upstream/project")
    assert asked == [["gh", "api", "repos/upstream/project", "--jq", ".visibility"]]
    now[0] += broker.PUBLIC_TTL
    assert broker.is_public("upstream/project")
    assert len(asked) == 2


def test_a_failed_lookup_is_not_kept(broker, monkeypatch):
    asked = []
    monkeypatch.setattr(broker.subprocess, "run", fake_visibility(asked))
    assert not broker.is_public("upstream/gone")
    assert not broker.is_public("upstream/gone")
    assert len(asked) == 2


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("gh", 30), FileNotFoundError("gh")]
)
def test_a_lookup_that_cannot_answer_is_not_public(broker, monkeypatch, error):
    def run(argv, **kwargs):
        raise error

    monkeypatch.setattr(broker.subprocess, "run", run)
    assert not broker.is_public("upstream/project")


@pytest.mark.parametrize(
    ("query", "fields"),
    [
        ("mutation{ a b }", ["a", "b"]),
        ("mutation{x: del (input:{a:1}){id}}", ["del"]),
        ("mutation{a}", ["a"]),
        ("mutation{ {a} }", []),
        ("mutation{a", []),
        ("mutation{a(x:{b:c})}", ["a"]),
    ],
)
def test_mutation_fields(broker, query, fields):
    assert broker.mutation_fields(query) == fields


def serve_request(broker, payload: bytes):
    """Send one request through serve() over a socket pair; the reply."""
    ours, theirs = socket.socketpair()
    with ours:
        ours.sendall(payload)
        broker.serve(theirs)
        ours.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := ours.recv(65536):
            data += chunk
    return json.loads(data)


@pytest.mark.parametrize("payload", [b"not json\n", b'{"x": 1}\n', b'{"argv": 5}\n'])
def test_serve_refuses_a_malformed_request(broker, payload):
    assert serve_request(broker, payload) == {
        "rc": 2,
        "out": "",
        "err": "gh-broker: malformed request\n",
    }


def test_serve_refuses_a_command_off_the_allowlist(broker, capsys):
    reply = serve_request(broker, b'{"argv": ["auth", "token"]}\n')
    assert reply["rc"] == 126
    assert "not on the allowlist" in reply["err"]
    assert capsys.readouterr().out.startswith("REFUSE")


@pytest.mark.parametrize("cwd_is_dir", [True, False])
def test_serve_runs_gh(broker, monkeypatch, tmp_path, cwd_is_dir):
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 3, "out", "err")

    monkeypatch.setattr(broker.subprocess, "run", run)
    cwd = str(tmp_path) if cwd_is_dir else str(tmp_path / "absent")
    request = {"argv": ["pr", "list"], "cwd": cwd, "stdin": "body"}
    reply = serve_request(broker, json.dumps(request).encode() + b"\n")
    assert reply == {"rc": 3, "out": "out", "err": "err"}
    assert seen["argv"] == ["gh", "pr", "list"]
    assert seen["cwd"] == (cwd if cwd_is_dir else "/")
    assert seen["input"] == "body"


def test_serve_times_out(broker, monkeypatch):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 300)

    monkeypatch.setattr(broker.subprocess, "run", run)
    reply = serve_request(broker, b'{"argv": ["pr", "list"]}\n')
    assert reply["rc"] == 124


def test_main_needs_the_token(broker, monkeypatch, capsys):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(SystemExit) as e:
        broker.main()
    assert e.value.code == 1
    assert "GH_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("owners", ["", "example-org,evil name"])
def test_main_needs_valid_owners(monkeypatch, capsys, owners):
    fresh = load_broker(monkeypatch, owners)
    monkeypatch.setenv("GH_TOKEN", "t")
    with pytest.raises(SystemExit) as e:
        fresh.main()
    assert e.value.code == 1
    assert "GH_BROKER_OWNERS" in capsys.readouterr().err


class Stop(Exception):
    """Ends main()'s accept loop."""


class FakeSocket:
    def __init__(self, family):
        self.family = family
        self.accepted = 0

    def bind(self, path):
        open(path, "w").close()

    def listen(self):
        pass

    def accept(self):
        if self.accepted:
            raise Stop
        self.accepted += 1
        return "conn", None


@pytest.mark.parametrize("stale", [True, False])
def test_main_listens_and_serves_each_connection(broker, monkeypatch, tmp_path, stale):
    sock = tmp_path / "gh.sock"
    if stale:
        sock.write_text("left from a previous run")
    started = []

    class Thread:
        def __init__(self, target, args, daemon):
            assert target is broker.serve and daemon
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(broker, "SOCK", str(sock))
    monkeypatch.setattr(broker.socket, "socket", FakeSocket)
    monkeypatch.setattr(broker.threading, "Thread", Thread)
    with pytest.raises(Stop):
        broker.main()
    assert started == [("conn",)]
    assert sock.read_text() == ""
    assert oct(sock.stat().st_mode & 0o777) == "0o660"
