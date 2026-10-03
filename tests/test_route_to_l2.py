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
        ("pytest; l2 -- pytest", False, False),
        ("ls ;; pytest", True, False),
    ],
)
def test_classify(hook, command, routed, net):
    assert hook.classify(command) == (routed, net)


def run(hook, monkeypatch, capsys, stdin, *args):
    monkeypatch.setattr(hook.sys, "argv", ["route-to-l2", *args])
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(stdin))
    assert hook.main() == 0
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
