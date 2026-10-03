"""The `coverage` pre-push hook runs only when a pushed file matches its
files: pattern. Every file `make coverage` measures has to match, or a push
that changes only that file skips the gate. This test reads the source lists
from the Makefile and the pattern from .pre-commit-config.yaml, and holds the
one to the other the way pre-commit does (re.search on the path).

It also holds SHELL_SCRIPTS to every shell script in the tree, so a new one
cannot be left out of the coverage gate without a test failing."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from conftest import REPO_ROOT

LISTS = ("PYTHON_SOURCES", "SHELL_SCRIPTS", "JS_SOURCES")
SHEBANG = re.compile(rb"#!\s*(?:\S*/)?(?:env\s+(?:-\S+\s+)*)?(?:sh|bash|dash)\b")


def listed(*names):
    makefile = (REPO_ROOT / "Makefile").read_text()
    files = []
    for name in names:
        value = re.search(rf"^{name} := ((?:.*\\\n)*.*)$", makefile, re.MULTILINE)
        files += value.group(1).replace("\\\n", " ").split()
    return files


def measured():
    return listed(*LISTS)


def tree_files():
    """Every file in the tree. `make coverage` runs the tests on a copy of
    exactly the files git would commit, with no .git, so there this is git's
    list. Nothing in a test runs git itself (see conftest.py). Run in a
    checkout, it leaves out .git and any other checkout nested in this one
    (the worktrees under .claude/worktrees)."""
    files = []
    for root, dirs, names in os.walk(REPO_ROOT):
        dirs[:] = [
            d for d in dirs if d != ".git" and not (Path(root, d) / ".git").exists()
        ]
        files += [str(Path(root, name).relative_to(REPO_ROOT)) for name in names]
    return sorted(files)


def is_shell(path):
    if path.endswith((".sh", ".bash")):
        return True
    if not (REPO_ROOT / path).is_file():
        return False
    with (REPO_ROOT / path).open("rb") as f:
        return SHEBANG.match(f.readline()) is not None


def shell_scripts():
    return [
        path
        for path in tree_files()
        if not path.startswith("tests/") and is_shell(path)
    ]


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


def test_shell_detection():
    assert is_shell("host/workbench")
    assert is_shell("scripts/build-images.sh")
    assert not is_shell("images/workbench/bin/gh")
    assert not is_shell("Makefile")


def test_every_shell_script_is_measured():
    found = shell_scripts()
    assert "images/l2-engine/containers/crun-without-masked-paths" in found
    missing = sorted(set(found) - set(listed("SHELL_SCRIPTS")))
    assert missing == []
