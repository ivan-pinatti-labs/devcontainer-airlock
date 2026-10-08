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
