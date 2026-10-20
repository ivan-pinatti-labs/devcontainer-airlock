"""The Claude Code managed settings the workbench image ships.

The commands in the sandbox's excludedCommands are the ones that need the gh
broker, the ssh-agent or the L2 engine, whose sockets the sandbox blocks.
Dropping one sends it back inside, where it fails with an error that points
elsewhere (docs/LAYERS.md, "The coding agents").
"""

from __future__ import annotations

import json

import pytest
from conftest import REPO_ROOT

SETTINGS = REPO_ROOT / "images/workbench/claude/managed-settings.json"


def excluded():
    with open(SETTINGS, encoding="utf-8") as handle:
        return json.load(handle)["sandbox"]["excludedCommands"]


@pytest.mark.parametrize(
    "command",
    ["l2 *", "git *", "podman *", "gh *", "ssh-add -l", "airlock-worktree *"],
)
def test_the_command_runs_outside_the_sandbox(command):
    assert command in excluded()


def test_session_start_notes_the_conversation():
    """host/workbench resumes the conversation this hook writes down.

    A /resume or /clear inside a session moves it to another conversation;
    without the note, attach would resume the one the session started with
    (docs/SESSIONS.md, "Lifecycle").
    """
    with open(SETTINGS, encoding="utf-8") as handle:
        entries = json.load(handle)["hooks"]["SessionStart"]
    commands = [hook["command"] for entry in entries for hook in entry["hooks"]]
    assert commands == ["/usr/local/libexec/workbench/conversation-note"]
    assert (REPO_ROOT / "images/workbench/bin/conversation-note").is_file()


def test_permission_checks_cannot_be_bypassed():
    """No level runs with permission checks skipped, unattended included: it
    uses Claude Code's auto mode (docs/MODES.md)."""
    with open(SETTINGS, encoding="utf-8") as handle:
        permissions = json.load(handle)["permissions"]
    assert permissions["disableBypassPermissionsMode"] == "disable"


def test_auto_mode_classifies_locally():
    """Auto mode's server side classifier is off, which leaves the local one.

    On the server path a classifier outage blocks every Bash call, allow
    listed ones too (`sleep`, `date`), so an unattended agent cannot even arm
    a bounded wait (anthropics/claude-code#97870, #97854). On the local path,
    allow rules resolve before any classifier call. Claude Code reads this
    variable at startup; re-check it on each CLI bump (docs/MODES.md).
    """
    with open(SETTINGS, encoding="utf-8") as handle:
        env = json.load(handle)["env"]
    assert env["CLAUDE_CODE_AUTO_MODE_SERVER"] == "0"
