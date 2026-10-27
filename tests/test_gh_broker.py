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
import io
import json
import os
import selectors
import socket
import subprocess
import tempfile
import threading
import time

import pytest
from conftest import REPO_ROOT, load

BROKER_DIR = REPO_ROOT / "images/gh-broker"


def tree_open(path, *args, **kwargs):
    """`open` with the image's allowlist paths pointing at the tree's copies."""
    if str(path).startswith("/etc/gh-broker/"):
        path = BROKER_DIR / str(path).removeprefix("/etc/gh-broker/")
    return builtins.open(path, *args, **kwargs)


def load_broker(monkeypatch, owners="example-org", overrides=None):
    """The broker, reading the tree's allowlists, except for the levels named
    in `overrides`: level to the text of its allowlist.<level>.json, or None
    for no such file."""
    monkeypatch.setenv("GH_BROKER_OWNERS", owners)
    overrides = overrides or {}

    def open_with(path, *args, **kwargs):
        for level, text in overrides.items():
            if path == f"/etc/gh-broker/allowlist.{level}.json":
                if text is None:
                    raise FileNotFoundError(path)
                return io.StringIO(text)
        return tree_open(path, *args, **kwargs)

    return load("images/gh-broker/broker.py", "gh_broker", open=open_with)


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
        ("auth token", "handles credentials, and is never run here"),
        ("repo delete example-org/x", "not on the allowlist"),
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


def serve_request(broker, payload: bytes, level="hardened"):
    """Send one request through serve() over a socket pair, as one that came
    in on the socket of `level`; the reply."""
    ours, theirs = socket.socketpair()
    with ours:
        ours.sendall(payload)
        broker.serve(theirs, level)
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
    reply = serve_request(broker, b'{"argv": ["repo", "delete", "example-org/x"]}\n')
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
        self.path = None

    def bind(self, path):
        self.path = path
        open(path, "w").close()

    def listen(self):
        pass

    def accept(self):
        return f"conn on {self.path}", None


class FakeSelector:
    """Every registered socket ready once, then Stop."""

    def __init__(self):
        self.keys, self.selected = [], False

    def register(self, fileobj, events, data):
        self.keys.append(selectors.SelectorKey(fileobj, 0, events, data))

    def select(self):
        if self.selected:
            raise Stop
        self.selected = True
        return [(key, key.events) for key in self.keys]


@pytest.mark.parametrize("stale", [True, False])
def test_main_listens_on_each_level_mounted_and_serves_it_at_that_level(
    broker, monkeypatch, tmp_path, capsys, stale
):
    """A level the host mounted no folder for gets no socket."""
    for level in ("hardened", "unattended"):
        (tmp_path / level).mkdir()
    if stale:
        (tmp_path / "hardened/gh.sock").write_text("left from a previous run")
    started = []

    class Thread:
        def __init__(self, target, args, daemon):
            assert target is broker.serve and daemon
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(broker, "SOCK_DIR", str(tmp_path))
    monkeypatch.setattr(broker.socket, "socket", FakeSocket)
    monkeypatch.setattr(broker.selectors, "DefaultSelector", FakeSelector)
    monkeypatch.setattr(broker.threading, "Thread", Thread)
    with pytest.raises(Stop):
        broker.main()
    assert started == [
        (f"conn on {tmp_path}/hardened/gh.sock", "hardened"),
        (f"conn on {tmp_path}/unattended/gh.sock", "unattended"),
    ]
    assert not (tmp_path / "assisted").exists()
    for level in ("hardened", "unattended"):
        sock = tmp_path / level / "gh.sock"
        assert sock.read_text() == ""
        assert oct(sock.stat().st_mode & 0o777) == "0o660"
    out = capsys.readouterr().out
    assert (
        f"gh-broker: hardened listening on {tmp_path}/hardened/gh.sock, beyond hardened: {{}}"
        in out
    )


def test_main_needs_the_hardened_folder(broker, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(broker, "SOCK_DIR", str(tmp_path))
    (tmp_path / "unattended").mkdir()
    with pytest.raises(SystemExit) as e:
        broker.main()
    assert e.value.code == 1
    assert f"no {tmp_path}/hardened" in capsys.readouterr().err


# Session levels: allowlist.<level>.json adds to the level before it.

ADDS_GIST = json.dumps({"add": {"commands": ["gist create"]}})


def test_each_level_adds_to_the_one_before_it(monkeypatch):
    fresh = load_broker(
        monkeypatch,
        overrides={
            "assisted": json.dumps(
                {"comment": "c", "add": {"commands": ["label create"]}}
            ),
            "unattended": ADDS_GIST,
        },
    )
    commands = {level: fresh.POLICIES[level]["commands"] for level in fresh.LEVELS}
    assert "label create" not in commands["hardened"]
    assert "label create" in commands["assisted"]
    assert "gist create" not in commands["assisted"]
    assert {"label create", "gist create"} <= commands["unattended"]
    assert commands["hardened"] < commands["assisted"] < commands["unattended"]


def test_a_level_with_no_override_is_the_level_before_it(monkeypatch):
    fresh = load_broker(monkeypatch, overrides={"assisted": None, "unattended": None})
    policies = fresh.POLICIES
    assert policies["hardened"] == policies["assisted"] == policies["unattended"]


def test_the_trees_levels_each_hold_the_one_before(broker):
    for before, level in zip(broker.LEVELS, broker.LEVELS[1:]):
        for key in broker.LIST_KEYS:
            assert broker.POLICIES[before][key] <= broker.POLICIES[level][key]


@pytest.mark.parametrize(
    ("override", "why"),
    [
        ({"add": {}, "remove": {"commands": ["pr view"]}}, "only comment and add"),
        ([], "only comment and add"),
        ({"add": {"owners": ["evil"]}}, "add holds only"),
        ({"add": {"api": {"methods": ["DELETE"]}}}, "add holds only"),
        ({"add": []}, "add holds only"),
        ({"add": {"commands": "release create"}}, "a list of names"),
        ({"add": {"commands": [1]}}, "a list of names"),
        ({"add": {"commands": ["pr merge --admin"]}}, "one or two words"),
        ({"add": {"public_reads": ["Secret list"]}}, "one or two words"),
        ({"add": {"broker_commands": ["run anything"]}}, "broker_commands names only"),
        ({"add": {"commands": ["auth token"]}}, "they handle credentials"),
        ({"add": {"commands": ["auth"]}}, "they handle credentials"),
        ({"add": {"public_reads": ["secret list"]}}, "they handle credentials"),
        ({"add": {"commands": ["ssh-key add"]}}, "they handle credentials"),
        ({"add": {"commands": ["gpg-key list"]}}, "they handle credentials"),
        (
            {"add": {"public_reads": ["pr comment"]}},
            "a public read is a list or view command",
        ),
        (
            {"add": {"public_reads": ["status"]}},
            "a public read is a list or view command",
        ),
    ],
)
def test_an_override_outside_its_form_stops_the_broker(monkeypatch, override, why):
    with pytest.raises(ValueError, match=why):
        load_broker(monkeypatch, overrides={"unattended": json.dumps(override)})


# What the code refuses, no level's list can lift.
ALWAYS_REFUSED = [
    "pr merge 25 --admin",
    "pr view 1 -R evil/x",
    "pr create -t t --body-file /proc/self/environ",
    "api -X DELETE repos/example-org/x",
    'api graphql -f query=mutation{deleteRepository(input:{repositoryId:"R"}){clientMutationId}}',
    "wait pr 7 -R someone-else/x",
    "release create v1 -R evil/x",
    "auth token",
    "auth status --show-token",
    "secret list -R example-org/x",
    "ssh-key list",
    "gpg-key list",
]


@pytest.mark.parametrize("command", ALWAYS_REFUSED)
def test_what_the_code_refuses_holds_at_every_level(monkeypatch, command):
    fresh = load_broker(
        monkeypatch,
        overrides={
            "assisted": None,
            "unattended": json.dumps(
                {"add": {"commands": ["release create", "pr merge"]}}
            ),
        },
    )
    for level in fresh.LEVELS:
        assert not fresh.allowed(command.split(), level), level


def test_a_refusal_names_the_level(monkeypatch):
    fresh = load_broker(monkeypatch, overrides={"unattended": ADDS_GIST})
    assert fresh.refusal(["gist", "create", "f"], "assisted") == (
        "'gh gist create' is not on the allowlist at the assisted level"
    )
    assert fresh.refusal(["gist", "create", "f"], "unattended") is None


def test_serve_says_which_files_hold_the_level(monkeypatch, capsys):
    fresh = load_broker(monkeypatch, overrides={"unattended": ADDS_GIST})
    reply = serve_request(fresh, b'{"argv": ["auth", "token"]}\n', "unattended")
    assert reply["err"].endswith(
        "(images/gh-broker/allowlist.json, allowlist.unattended.json)\n"
    )
    assert capsys.readouterr().out.startswith('REFUSE unattended ["auth", "token"]')


def test_the_level_is_the_socket_a_request_came_in_on(monkeypatch):
    """End to end over real sockets: the same request on each level's
    socket. A request naming a level of its own changes nothing."""
    fresh = load_broker(
        monkeypatch, overrides={"assisted": None, "unattended": ADDS_GIST}
    )
    ran = []

    def run(argv, **kwargs):
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, "made", "")

    monkeypatch.setattr(fresh.subprocess, "run", run)
    monkeypatch.setenv("GH_TOKEN", "t")
    # A short folder of the test's own: a unix socket path has a length limit.
    with tempfile.TemporaryDirectory(prefix="gb-") as d:
        for level in fresh.LEVELS:
            os.mkdir(os.path.join(d, level))
        monkeypatch.setattr(fresh, "SOCK_DIR", d)
        threading.Thread(target=fresh.main, daemon=True).start()
        socks = [os.path.join(d, level, "gh.sock") for level in fresh.LEVELS]
        for _ in range(500):
            if all(os.path.exists(s) for s in socks):
                break
            time.sleep(0.01)

        def ask(level, request):
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(os.path.join(d, level, "gh.sock"))
                s.sendall(json.dumps(request).encode() + b"\n")
                s.shutdown(socket.SHUT_WR)
                data = b""
                while chunk := s.recv(65536):
                    data += chunk
            return json.loads(data)["rc"]

        gist = {"argv": ["gist", "create", "-R", "example-org/x"]}
        assert ask("hardened", gist) == 126
        assert ask("assisted", gist) == 126
        assert ask("hardened", {**gist, "level": "unattended"}) == 126
        assert ran == []
        assert ask("unattended", gist) == 0
        assert ran == [["gh", "gist", "create", "-R", "example-org/x"]]


# gh wait pr: the broker's own bounded wait on a pull request (#145).

WAIT_CASES = [
    (True, "wait pr 7 -R example-org/x"),
    (True, "wait pr 7 --repo example-org/x --timeout 120"),
    (True, "wait pr 7 -R Example-Org/x --timeout 99999"),
    (False, "wait pr 7"),
    (False, "wait pr x -R example-org/x"),
    (False, "wait pr 7 -R someone-else/x"),
    (False, "wait pr 7 -R example-org/.."),
    (False, "wait pr 7 -R example-org/x -R someone-else/y"),
    (False, "wait pr 7 -R example-org/x --timeout 0"),
    (False, "wait pr 7 -R example-org/x --timeout soon"),
    (False, "wait pr 1234567890 -R example-org/x"),
    (False, "wait pr 7 -R example-org/x --timeout 1234567890"),
    (False, "wait pr \u00b2 -R example-org/x"),
    (False, "wait pr 7 -R example-org/x --timeout \u00b2"),
    (False, "wait pr 7 -R example-org/x --timeout"),
    (False, "wait pr 7 -R example-org/x --interval 1"),
    (False, "wait issue 7 -R example-org/x"),
]


@pytest.mark.parametrize(("expected", "command"), WAIT_CASES)
def test_wait_is_held_to_its_own_form_and_the_owners(broker, expected, command):
    assert broker.allowed(command.split()) is expected


def test_wait_says_how_to_call_it(broker):
    assert "usage: gh wait pr NUMBER" in broker.refusal(["wait", "pr", "7"])


def test_wait_never_waits_past_the_cap(broker):
    argv = ["wait", "pr", "7", "-R", "example-org/x", "--timeout", "99999"]
    assert broker.wait_args(argv) == ("example-org/x", 7, broker.WAIT_CAP)
    assert broker.wait_args(argv[:5]) == ("example-org/x", 7, broker.WAIT_CAP)


def threads(*resolved, next_page=None, comments=1):
    """A page of review threads, resolved or not, each with `comments`
    comments; `next_page` is the cursor of a page after it."""
    return {
        "pageInfo": {"hasNextPage": next_page is not None, "endCursor": next_page},
        "nodes": [
            {"isResolved": r, "comments": {"totalCount": comments}} for r in resolved
        ],
    }


def pr_answer(**fields):
    """What `gh api graphql` prints for the wait query."""
    pr = {
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "reviewDecision": None,
        "headRefOid": "abc",
        "mergeQueueEntry": None,
        "reviews": {"totalCount": 0},
        "comments": {"totalCount": 0},
        "reviewThreads": threads(True),
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]},
    }
    pr.update(fields)
    return json.dumps({"data": {"repository": {"pullRequest": pr}}})


def scripted_gh(answers, asked):
    """subprocess.run answering each gh call with the next of `answers`: a
    string for its stdout, (exit status, stdout), or an exception to raise."""

    def run(argv, **kwargs):
        asked.append(argv)
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        rc, out = answer if isinstance(answer, tuple) else (0, answer)
        return subprocess.CompletedProcess(argv, rc, out, "")

    return run


class Clock:
    """time.monotonic and time.sleep, with sleeping moving the time on."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_wait_answers_when_the_pull_request_changes(broker, monkeypatch):
    asked = []
    answers = [
        pr_answer(),
        pr_answer(),
        subprocess.TimeoutExpired("gh", 60),
        pr_answer(reviewDecision="APPROVED", reviewThreads=threads(False)),
    ]
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh(answers, asked))
    clock = Clock()
    reply = broker.wait_pr(
        "example-org/x", 7, 3900, sleep=clock.sleep, clock=clock.monotonic
    )
    assert reply["rc"] == 0
    out = json.loads(reply["out"])
    assert out["changed"] == ["open_threads", "review"]
    assert out["was"] == {"open_threads": 0, "review": None}
    assert out["now"]["review"] == "APPROVED"
    assert clock.slept == [60, 60, 60]
    assert asked[0][:3] == ["gh", "api", "graphql"]
    assert (
        "owner=example-org" in asked[0]
        and "name=x" in asked[0]
        and "number=7" in asked[0]
        and "after=null" in asked[0]
    )


def test_wait_sees_a_reply_on_a_thread_that_stays_open(broker, monkeypatch):
    answers = [
        pr_answer(reviewThreads=threads(False)),
        pr_answer(reviewThreads=threads(False, comments=2)),
    ]
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh(answers, []))
    clock = Clock()
    reply = broker.wait_pr(
        "example-org/x", 7, 3900, sleep=clock.sleep, clock=clock.monotonic
    )
    assert json.loads(reply["out"])["changed"] == ["thread_comments"]


def test_review_threads_are_read_past_the_first_page(broker, monkeypatch):
    asked = []
    answers = [
        pr_answer(reviewThreads=threads(True, next_page="c1")),
        pr_answer(reviewThreads=threads(False, False)),
    ]
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh(answers, asked))
    snapshot = broker.pr_snapshot("example-org/x", 7)
    assert snapshot["open_threads"] == 2
    assert snapshot["thread_comments"] == 3
    assert "after=c1" in asked[1]


@pytest.mark.parametrize(
    "answers",
    [
        # gh failed, though what it printed parses.
        [(1, pr_answer())],
        # More pages, by a cursor that does not move, or by none at all.
        [
            pr_answer(reviewThreads=threads(True, next_page="c1")),
            pr_answer(reviewThreads=threads(True, next_page="c1")),
        ],
        [
            pr_answer(
                reviewThreads={
                    "pageInfo": {"hasNextPage": True, "endCursor": None},
                    "nodes": [],
                }
            )
        ],
        # A thread that is not there.
        [
            pr_answer(
                reviewThreads={
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [None],
                }
            )
        ],
    ],
)
def test_an_answer_that_is_not_a_whole_reading_is_none(broker, monkeypatch, answers):
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh(answers, []))
    assert broker.pr_snapshot("example-org/x", 7) is None


def test_review_threads_stop_at_the_page_limit(broker, monkeypatch):
    monkeypatch.setattr(broker, "WAIT_PAGES", 1)
    answers = [pr_answer(reviewThreads=threads(False, next_page="c1"))]
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh(answers, []))
    assert broker.pr_snapshot("example-org/x", 7)["open_threads"] == 1


def test_wait_gives_up_at_its_timeout(broker, monkeypatch):
    answers = [pr_answer()] * 4
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh(list(answers), []))
    clock = Clock()
    reply = broker.wait_pr(
        "example-org/x", 7, 150, sleep=clock.sleep, clock=clock.monotonic
    )
    assert reply["rc"] == 124
    assert json.loads(reply["out"])["changed"] == []
    assert "unchanged after 150s" in reply["err"]
    assert clock.slept == [60, 60, 30]


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        json.dumps({"data": {"repository": {"pullRequest": None}}}),
        json.dumps({"errors": [{"message": "Could not resolve"}]}),
        subprocess.TimeoutExpired("gh", 60),
    ],
)
def test_wait_on_a_pull_request_it_cannot_read(broker, monkeypatch, answer):
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh([answer], []))
    reply = broker.wait_pr("example-org/x", 7, 60)
    assert reply == {
        "rc": 1,
        "out": "",
        "err": "gh-broker: cannot read example-org/x#7\n",
    }


def test_a_pull_request_with_no_commits_has_no_check_state(broker, monkeypatch):
    monkeypatch.setattr(
        broker.subprocess, "run", scripted_gh([pr_answer(commits={"nodes": []})], [])
    )
    assert broker.pr_snapshot("example-org/x", 7)["checks"] is None


def test_serve_runs_a_wait_itself(broker, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        broker,
        "wait_pr",
        lambda *args: seen.append(args) or {"rc": 0, "out": "{}", "err": ""},
    )
    request = {"argv": ["wait", "pr", "7", "-R", "example-org/x", "--timeout", "60"]}
    reply = serve_request(broker, json.dumps(request).encode() + b"\n")
    assert reply == {"rc": 0, "out": "{}", "err": ""}
    assert seen == [("example-org/x", 7, 60)]
    assert capsys.readouterr().out.startswith("RUN")


# What each level may run beyond the one before it, from the tree's lists.

LEVEL_CASES = [
    # A release, at every level: notes only, never an asset read here.
    ("hardened", True, "release create v1 -R example-org/x --generate-notes"),
    (
        "hardened",
        True,
        "release create v1 -R example-org/x -t Title -n notes --target main",
    ),
    ("hardened", True, "release create v1 -R example-org/x --notes-file - --draft"),
    ("hardened", True, "release create v1 -R example-org/x --title=T --latest=false"),
    ("hardened", False, "release create v1 /proc/self/environ -R example-org/x"),
    ("hardened", False, "release create v1 -R example-org/x --draft dist/x.tar.gz"),
    (
        "hardened",
        False,
        "release create v1 -R example-org/x --notes-file /proc/self/environ",
    ),
    ("hardened", False, "release create v1 -R example-org/x -F notes.md"),
    ("hardened", False, "release create v1 -R evil/x"),
    ("unattended", False, "release create v1 a b -R example-org/x"),
    # Unattended only: workflow runs, labels, and the bot approval.
    ("hardened", False, "workflow run ci.yml -R example-org/x"),
    ("assisted", False, "workflow run ci.yml -R example-org/x"),
    (
        "unattended",
        True,
        "workflow run ci.yml -R example-org/x --ref main -f a=b -F n=1",
    ),
    ("unattended", True, "workflow run ci.yml -R example-org/x --field=n=1 -Fm=2"),
    (
        "unattended",
        False,
        "workflow run ci.yml -R example-org/x -F a=@/proc/self/environ",
    ),
    ("unattended", False, "workflow run ci.yml -R example-org/x --field=a=@f"),
    ("unattended", False, "workflow run ci.yml -R example-org/x -Fa=@f"),
    ("unattended", False, "workflow run ci.yml -R example-org/x -F @f"),
    ("unattended", False, "workflow run ci.yml -R evil/x"),
    ("unattended", False, "workflow view ci.yml -R example-org/x"),
    ("hardened", False, "label create bug -R example-org/x"),
    ("unattended", True, "label create bug -R example-org/x --color ff0000"),
    ("unattended", True, "label edit bug -R example-org/x --description d"),
    ("unattended", False, "label delete bug -R example-org/x"),
    ("hardened", False, "pr review 7 -R example-org/x --approve --body-file -"),
    ("assisted", False, "pr review 7 -R example-org/x --approve --body-file -"),
    ("unattended", True, "pr review 7 -R example-org/x --approve --body-file -"),
    ("unattended", True, "pr review 7 --repo=example-org/x -a -b checked"),
    (
        "unattended",
        True,
        "pr review https://github.com/example-org/x/pull/7 --approve -F -",
    ),
    ("unattended", False, "pr review 7 -R example-org/x --request-changes -b no"),
    ("unattended", False, "pr review 7 -R example-org/x --comment -b hi"),
    ("unattended", False, "pr review 7 -R example-org/x --approve --body-file /etc/x"),
    ("unattended", False, "pr review 7 -R evil/x --approve -b checked"),
    (
        "unattended",
        False,
        "pr review https://github.com/evil/x/pull/7 --approve -b checked",
    ),
    ("unattended", False, "pr merge 7 -R example-org/x --admin"),
]


@pytest.mark.parametrize(("level", "expected", "command"), LEVEL_CASES)
def test_allowed_at_a_level(broker, level, expected, command):
    assert broker.allowed(command.split(), level) is expected


@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("release create v1 x.tgz -R example-org/x", "assets are uploaded by CI"),
        ("workflow run ci -R example-org/x -F a=@f", "-f key=value"),
        ("pr review 7 -R example-org/x --request-changes -b x", "dependency bot"),
    ],
)
def test_an_unattended_refusal_says_why(broker, command, why):
    assert why in broker.refusal(command.split(), "unattended")


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ("7 -R example-org/x --approve --body-file -", ("example-org/x", 7, None)),
        ("7 -R example-org/x -a -b checked", ("example-org/x", 7, "checked")),
        (
            "--approve --body=ok https://github.com/example-org/x/pull/9",
            ("example-org/x", 9, "ok"),
        ),
        (
            "https://github.com/example-org/x/pull/9 -R Example-Org/X -a -F -",
            ("example-org/x", 9, None),
        ),
        ("7 -R example-org/x --body-file -", None),
        ("7 --approve -b x", None),
        ("x -R example-org/x --approve -b x", None),
        ("7 8 -R example-org/x --approve -b x", None),
        ("7 -R example-org/x -R example-org/y --approve -b x", None),
        ("7 -R example-org/x --approve -b x --body-file -", None),
        ("7 -R example-org/x --approve -b x -b y", None),
        ("7 -R example-org/x --approve --body-file - -F -", None),
        ("7 -R example-org/x --approve", None),
        ("7 -R example-org/x --approve -b", None),
        ("7 -R example-org/.. --approve -b x", None),
        ("https://github.com/example-org/x/pull/9 -R example-org/y -a -b x", None),
        ("7 -R example-org/x --approve --body-file /etc/x", None),
        ("7 -R example-org/x --approve -c -b x", None),
    ],
)
def test_review_args(broker, argv, expected):
    assert broker.review_args(["pr", "review", *argv.split()]) == expected


HEAD = "a" * 40


def pull(**fields):
    """GitHub's answer for a pull request: an open one renovate[bot] opened."""
    answer = {
        "state": "open",
        "draft": False,
        "user": {"login": "renovate[bot]", "type": "Bot"},
        "head": {"sha": HEAD, "repo": {"full_name": "example-org/x"}},
        "base": {"ref": "main"},
    }
    answer.update(fields)
    return json.dumps(answer)


def protection(*names):
    """GitHub's answer for the base branch, requiring checks `names`."""
    return json.dumps(
        {"protection": {"required_status_checks": {"contexts": list(names)}}}
    )


def rules(*names):
    """GitHub's answer for the rules on the base branch: a merge queue, and a
    ruleset requiring checks `names` when any are given."""
    answer = [{"type": "merge_queue", "parameters": {}}]
    if names:
        checks = [{"context": n} for n in names]
        answer.append(
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": checks},
            }
        )
    return json.dumps(answer)


# What the base branch requires in the cases below, read after the checks.
REQUIRED = [protection("SonarQube", "Pin Only"), rules()]


def commit(sha=HEAD, author="renovate[bot]", committer="web-flow", verified=True):
    return {
        "sha": sha,
        "author": author and {"login": author},
        "committer": committer and {"login": committer},
        "commit": {"verification": {"verified": verified}},
    }


def run_check(name, conclusion="SUCCESS", status="COMPLETED"):
    return {"name": name, "status": status, "conclusion": conclusion}


def status(context, state="SUCCESS"):
    return {"context": context, "state": state}


# What a bot pull request's head commit reads like: required checks green,
# and a superseded run cancelled, which GitHub's own overall state counts as
# a failure.
GREEN = [
    run_check("SonarQube"),
    status("Pin Only"),
    run_check("gate / Grade", "CANCELLED"),
    run_check("Approve the owner's own pull request", "SKIPPED"),
    status("renovate/stability-days"),
]


def rollup(checks=None, oid=HEAD, more=False, reported=True):
    contexts = {
        "pageInfo": {"hasNextPage": more},
        "nodes": GREEN if checks is None else checks,
    }
    node = {
        "commit": {"oid": oid, "statusCheckRollup": reported and {"contexts": contexts}}
    }
    return json.dumps(
        {"data": {"repository": {"pullRequest": {"commits": {"nodes": [node]}}}}}
    )


READ = [pull(), json.dumps([commit()])]


@pytest.mark.parametrize(
    ("answers", "why"),
    [
        ([pull(state="closed")], "not an open pull request"),
        ([pull(draft=True)], "not an open pull request"),
        ([pull(user={"login": "ivan", "type": "User"})], "opened by ivan"),
        (
            [pull(user={"login": "renovate[bot]", "type": "User"})],
            "opened by renovate[bot]",
        ),
        (
            [pull(head={"sha": HEAD, "repo": {"full_name": "evil/x"}})],
            "outside example-org/x",
        ),
        ([pull(head={"sha": HEAD, "repo": None})], "outside example-org/x"),
        ([pull(), json.dumps([])], "no commits"),
        ([pull(), json.dumps([commit()] * 100)], "too many to check"),
        (
            [pull(), json.dumps([commit(sha="b" * 40, author="ivan"), commit()])],
            "someone else pushed",
        ),
        ([pull(), json.dumps([commit(author=None)])], "someone else pushed"),
        (
            [pull(), json.dumps([commit(author="dependabot[bot]")])],
            "someone else pushed",
        ),
        ([pull(), json.dumps([commit(committer="ivan")])], "someone else pushed"),
        ([pull(), json.dumps([commit(committer=None)])], "someone else pushed"),
        ([pull(), json.dumps([commit(verified=False)])], "someone else pushed"),
        ([pull(), json.dumps([commit(sha="b" * 40)])], "moved while it was checked"),
        ([*READ, rollup(reported=False), *REQUIRED], "no checks are reported"),
        ([*READ, rollup(oid="b" * 40), *REQUIRED], "no checks are reported"),
        ([*READ, rollup(more=True), *REQUIRED], "too many checks"),
        ([*READ, rollup(), protection(), rules()], "requires no check"),
        ([*READ, rollup(), json.dumps({}), rules()], "requires no check"),
        # A required check that has not reported at all, beside green ones.
        (
            [
                *READ,
                rollup(),
                protection("SonarQube", "Pin Only", "Tests Verified"),
                rules(),
            ],
            "required check Tests Verified has not reported",
        ),
        (
            [
                *READ,
                rollup(),
                protection("SonarQube"),
                rules("Pin Only", "Ruleset Check"),
            ],
            "required check Ruleset Check has not reported",
        ),
        (
            [*READ, rollup([*GREEN, status("Pin Only", "PENDING")]), *REQUIRED],
            "required check Pin Only is PENDING",
        ),
        (
            [
                *READ,
                rollup([status("Pin Only"), run_check("SonarQube", "SKIPPED")]),
                *REQUIRED,
            ],
            "required check SonarQube is SKIPPED",
        ),
        # Reported twice, once red: the red one holds, before or after.
        (
            [*READ, rollup([*GREEN, run_check("SonarQube", "FAILURE")]), *REQUIRED],
            "required check SonarQube is FAILURE",
        ),
        (
            [*READ, rollup([run_check("SonarQube", "FAILURE"), *GREEN]), *REQUIRED],
            "required check SonarQube is FAILURE",
        ),
        (
            [
                *READ,
                rollup([*GREEN, run_check("Build", None, status="IN_PROGRESS")]),
                *REQUIRED,
            ],
            "check Build is still running",
        ),
        (
            [*READ, rollup([*GREEN, status("deploy", "EXPECTED")]), *REQUIRED],
            "deploy is still running",
        ),
        (
            [*READ, rollup([*GREEN, run_check("Trivy", "FAILURE")]), *REQUIRED],
            "check Trivy is FAILURE",
        ),
        (
            [*READ, rollup([*GREEN, status("scan", "ERROR")]), *REQUIRED],
            "check scan is ERROR",
        ),
        (
            [*READ, rollup([*GREEN, run_check("x", "TIMED_OUT")]), *REQUIRED],
            "check x is TIMED_OUT",
        ),
    ],
)
def test_an_approval_is_refused_unless_every_condition_holds(
    broker, monkeypatch, answers, why
):
    asked = []
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh(answers, asked))
    reply = broker.bot_approval("example-org/x", 7, "checked the release notes")
    assert reply["rc"] == 126
    assert why in reply["err"]
    assert not any("POST" in a for a in asked)


def test_an_approval_needs_a_body(broker, monkeypatch):
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh([], []))
    reply = broker.bot_approval("example-org/x", 7, "  \n")
    assert reply["rc"] == 126
    assert "give it a body" in reply["err"]


def test_an_approval_lands_on_the_commit_the_checks_were_read_on(broker, monkeypatch):
    asked = []
    answers = [
        pull(base={"ref": "release/1"}),
        json.dumps([commit("b" * 40), commit()]),
        rollup(),
        *REQUIRED,
        '"APPROVED"',
    ]
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh(answers, asked))
    reply = broker.bot_approval("example-org/x", 7, "@/proc/self/environ")
    assert reply == {"rc": 0, "out": f"approved example-org/x#7 at {HEAD}\n", "err": ""}
    assert asked[0] == ["gh", "api", "repos/example-org/x/pulls/7"]
    assert asked[1] == ["gh", "api", "repos/example-org/x/pulls/7/commits?per_page=100"]
    assert asked[2][:3] == ["gh", "api", "graphql"]
    assert "number=7" in asked[2] and "owner=example-org" in asked[2]
    # The base branch's own requirements, its name kept to one path segment.
    assert asked[3] == ["gh", "api", "repos/example-org/x/branches/release%2F1"]
    assert asked[4] == ["gh", "api", "repos/example-org/x/rules/branches/release%2F1"]
    # The body is a raw field: -f never reads a file, whatever it starts with.
    assert asked[5] == [
        "gh",
        "api",
        "-X",
        "POST",
        "repos/example-org/x/pulls/7/reviews",
        "-f",
        "event=APPROVE",
        "-f",
        f"commit_id={HEAD}",
        "-f",
        "body=@/proc/self/environ",
        "--jq",
        ".state",
    ]


def approval_answering(broker, monkeypatch, post):
    """subprocess.run reading a clean bot pull request, and answering the
    approval's POST with `post`: a CompletedProcess, or an exception."""
    reads = scripted_gh([*READ, rollup(), *REQUIRED], [])

    def run(argv, **kwargs):
        if "POST" not in argv:
            return reads(argv)
        if isinstance(post, BaseException):
            raise post
        return post

    monkeypatch.setattr(broker.subprocess, "run", run)


def test_a_failed_approval_says_what_github_said(broker, monkeypatch):
    post = subprocess.CompletedProcess([], 1, "", "HTTP 422: cannot approve\n")
    approval_answering(broker, monkeypatch, post)
    reply = broker.bot_approval("example-org/x", 7, "checked")
    assert reply == {"rc": 1, "out": "", "err": "HTTP 422: cannot approve\n"}


def test_an_approval_that_times_out_says_so(broker, monkeypatch):
    approval_answering(broker, monkeypatch, subprocess.TimeoutExpired("gh", 60))
    reply = broker.bot_approval("example-org/x", 7, "checked")
    assert reply["rc"] == 124
    assert (
        "timed out after 60s; read the pull request before asking again" in reply["err"]
    )


def test_an_approval_gh_cannot_start_for_says_so(broker, monkeypatch):
    approval_answering(broker, monkeypatch, FileNotFoundError(2, "No such file", "gh"))
    reply = broker.bot_approval("example-org/x", 7, "checked")
    assert reply["rc"] == 1
    assert "cannot approve example-org/x#7" in reply["err"]


@pytest.mark.parametrize(
    "answer",
    [
        (1, ""),
        "not json",
        json.dumps({"state": "open"}),
        subprocess.TimeoutExpired("gh", 30),
    ],
)
def test_an_approval_that_cannot_read_the_pull_request_does_nothing(
    broker, monkeypatch, answer
):
    asked = []
    monkeypatch.setattr(broker.subprocess, "run", scripted_gh([answer], asked))
    reply = broker.bot_approval("example-org/x", 7, "checked")
    assert reply["rc"] == 1
    assert "cannot read example-org/x#7" in reply["err"]
    assert len(asked) == 1


@pytest.mark.parametrize(
    ("stdin", "inline"), [("checked on stdin", None), (None, "inline")]
)
def test_serve_sends_a_review_to_the_bot_approval(
    broker, monkeypatch, capsys, stdin, inline
):
    seen = []
    monkeypatch.setattr(
        broker,
        "bot_approval",
        lambda *args: seen.append(args) or {"rc": 0, "out": "approved\n", "err": ""},
    )
    body = ["--body-file", "-"] if inline is None else ["-b", inline]
    request = {"argv": ["pr", "review", "7", "-R", "example-org/x", "--approve", *body]}
    if stdin is not None:
        request["stdin"] = stdin
    reply = serve_request(broker, json.dumps(request).encode() + b"\n", "unattended")
    assert reply["rc"] == 0
    assert seen == [("example-org/x", 7, stdin or inline)]
    assert capsys.readouterr().out.startswith("RUN unattended")


def test_serve_logs_a_refused_approval(broker, monkeypatch, capsys):
    monkeypatch.setattr(
        broker,
        "bot_approval",
        lambda *args: {
            "rc": 126,
            "out": "",
            "err": "gh-broker: refused: opened by ivan\n",
        },
    )
    request = {"argv": ["pr", "review", "7", "-R", "example-org/x", "-a", "-b", "x"]}
    reply = serve_request(broker, json.dumps(request).encode() + b"\n", "unattended")
    assert reply["rc"] == 126
    out = capsys.readouterr().out
    assert "REFUSE unattended" in out and "opened by ivan" in out


def test_serve_refuses_a_review_below_unattended(broker, monkeypatch):
    monkeypatch.setattr(broker, "bot_approval", lambda *args: pytest.fail("approved"))
    request = {"argv": ["pr", "review", "7", "-R", "example-org/x", "-a", "-b", "x"]}
    for level in ("hardened", "assisted"):
        reply = serve_request(broker, json.dumps(request).encode() + b"\n", level)
        assert reply["rc"] == 126
