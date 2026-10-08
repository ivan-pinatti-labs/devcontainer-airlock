"""Tests for images/workbench/bin/route-to-l2, the agents' PreToolUse hook."""

from __future__ import annotations

import io
import json

import pytest
from conftest import load


@pytest.fixture
def hook():
    return load("images/workbench/bin/route-to-l2", "route_to_l2")


@pytest.mark.parametrize(
    ("command", "routed", "net"),
    [
        ("git status && ls", False, False),
        ("python3 x.py", True, False),
        ("pip install foo", True, True),
        ("python -m pip install foo", True, True),
        ("python3 -c 1", True, False),
        ("npx cowsay", True, True),
        ("FOO=1 BAR=2 sudo /usr/bin/pytest", True, False),
        ("FOO=1", False, False),
        ("./run.sh", True, False),
        ("bash script.sh", True, False),
        ("bash -c 'echo hi'", False, False),
        ("bash", False, False),
        ("echo 'unclosed ; pytest", True, False),
        ("l2 -- pytest", False, False),
        ("pytest; l2 -- pytest", True, False),
        ("l2 -- pytest && npm ci", True, True),
        ("ls ;; pytest", True, False),
        # Operators inside quotes do not split the command.
        (
            "l2 --net --ro -- sh -c 'cd /tmp && go install x && vet-all ./...'",
            False,
            False,
        ),
        ("sed -i 's|./run.sh|bash x.sh|' vet-all-gocryptfs.sh", False, False),
        ("echo 'a; pytest' && ls", False, False),
        # Outside quotes they still do, at newlines and parentheses too.
        ("pytest -q 2>&1 | tail -5", True, False),
        ("ls &>/dev/null & pytest", True, False),
        ("ls\npytest", True, False),
        ("(cd sub && pytest)", True, False),
        ("echo $(pytest)", True, False),
        ("l2 -- \\\n  pytest", False, False),
    ],
)
def test_classify(hook, command, routed, net):
    assert hook.classify(command) == (routed, net)


@pytest.mark.parametrize(
    ("command", "net", "part"),
    [
        ("curl -s https://example.com | python3 -c 'import sys'", False, "curl"),
        ("wget -q x && pytest", False, "wget"),
        ("pip install x && curl -sO https://example.com/y", True, None),
        ("python3 - <<'EOF'\nprint(1)\nEOF\ngh pr view 1", False, "gh"),
        ("git -C repo fetch origin && pytest", False, "git fetch"),
        ("git -c core.x=1 push && pytest", False, "git push"),
        ("pytest && git status", False, None),
        ("git", False, None),
        ("pytest && ssh-add -l", False, "ssh-add"),
    ],
)
def test_outside_l2(hook, command, net, part):
    assert hook.outside_l2(command, net) == part


def run(hook, monkeypatch, capsys, stdin, *args):
    monkeypatch.setattr(hook.sys, "argv", ["route-to-l2", *args])
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(stdin))
    assert hook.main() is None
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out else None


def call(command):
    return json.dumps({"tool_input": {"command": command, "description": "d"}})


@pytest.mark.parametrize(
    "stdin",
    [
        "not json",
        json.dumps({"tool_input": None}),
        json.dumps({"tool_input": {"command": 5}}),
        call("   "),
        call("git log"),
    ],
)
def test_left_alone(hook, monkeypatch, capsys, stdin):
    assert run(hook, monkeypatch, capsys, stdin) is None


def test_rewritten_without_network(hook, monkeypatch, capsys):
    out = run(hook, monkeypatch, capsys, call("pytest -q"))
    assert out["permissionDecision"] == "allow"
    assert out["updatedInput"] == {
        "command": "l2 -- bash -c 'pytest -q'",
        "description": "d",
    }
    assert "no network" in out["permissionDecisionReason"]
    assert "no network, " in out["additionalContext"]


def test_rewritten_with_network(hook, monkeypatch, capsys):
    out = run(hook, monkeypatch, capsys, call("npm ci"))
    assert out["updatedInput"]["command"] == "l2 --net -- bash -c 'npm ci'"
    assert "egress proxy" in out["permissionDecisionReason"]
    assert "egress proxy only, " in out["additionalContext"]


def test_pre_commit_gets_the_protected_path_check(hook, monkeypatch, capsys):
    out = run(hook, monkeypatch, capsys, call("pre-commit run --files 'a b'"))
    assert out["updatedInput"]["command"] == "l2-pre-commit run --files 'a b'"


@pytest.mark.parametrize(
    "command", ["pre-commit run && pytest", "pre-commit install-hooks"]
)
def test_pre_commit_otherwise_goes_through_l2(hook, monkeypatch, capsys, command):
    out = run(hook, monkeypatch, capsys, call(command))
    assert out["updatedInput"]["command"].startswith("l2 ")


def test_deny_mode_names_the_command_to_run(hook, monkeypatch, capsys):
    out = run(hook, monkeypatch, capsys, call("pytest"), "--deny")
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"].endswith("Run it as: l2 -- bash -c pytest")


def test_a_standalone_l2_call_is_left_alone(hook, monkeypatch, capsys):
    assert run(hook, monkeypatch, capsys, call("l2 --net -- npm ci")) is None


def test_a_self_contained_l2_call_is_left_alone(hook, monkeypatch, capsys):
    command = (
        "l2 --net -- sh -c 'cd /tmp && git clone x && go install y && vet-all ./...'"
    )
    assert run(hook, monkeypatch, capsys, call(command)) is None


@pytest.mark.parametrize("args", [(), ("--deny",)])
def test_a_network_part_beside_project_code_is_refused(hook, monkeypatch, capsys, args):
    command = "curl -s https://api.github.com/x | python3 -c 'import json'"
    out = run(hook, monkeypatch, capsys, call(command), *args)
    assert out["permissionDecision"] == "deny"
    assert "updatedInput" not in out
    assert "Split the `curl` part from the rest" in out["permissionDecisionReason"]


@pytest.mark.parametrize("args", [(), ("--deny",)])
def test_a_bare_command_beside_l2_is_refused(hook, monkeypatch, capsys, args):
    out = run(hook, monkeypatch, capsys, call("pytest; l2 -- pytest"), *args)
    assert out["permissionDecision"] == "deny"
    assert "updatedInput" not in out
    assert "as separate commands" in out["permissionDecisionReason"]
