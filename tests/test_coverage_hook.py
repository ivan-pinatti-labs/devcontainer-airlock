"""The `coverage` pre-push hook runs only when a pushed file matches its
files: pattern. Every file `make coverage` measures has to match, or a push
that changes only that file skips the gate. This test reads the source lists
from the Makefile and the pattern from .pre-commit-config.yaml, and holds the
one to the other the way pre-commit does (re.search on the path)."""

from __future__ import annotations

import re

import yaml
from conftest import REPO_ROOT

LISTS = ("PYTHON_SOURCES", "SHELL_SCRIPTS", "JS_SOURCES")


def measured():
    makefile = (REPO_ROOT / "Makefile").read_text()
    files = []
    for name in LISTS:
        value = re.search(rf"^{name} := ((?:.*\\\n)*.*)$", makefile, re.MULTILINE)
        files += value.group(1).replace("\\\n", " ").split()
    return files


def hook_pattern():
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"]]
    (hook,) = [hook for hook in hooks if hook["id"] == "coverage"]
    return re.compile(hook["files"])


def test_the_lists_are_read():
    files = measured()
    assert "host/workbench" in files
    assert "images/workbench/bin/airlock-relay" in files
    assert "scripts/kcov_to_sonar.py" in files


def test_every_measured_file_triggers_the_hook():
    pattern = hook_pattern()
    missed = [path for path in measured() if not pattern.search(path)]
    assert missed == []


def test_an_unmeasured_file_does_not():
    assert not hook_pattern().search("docs/LAYERS.md")
