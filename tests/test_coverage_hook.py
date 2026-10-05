"""The `coverage` pre-push hook runs only when a pushed file matches its
files: pattern. Every file `make coverage` measures has to match, or a push
that changes only that file skips the gate. This test reads the Python and
JavaScript lists from the Makefile and the pattern from
.pre-commit-config.yaml, and holds the one to the other the way pre-commit
does (re.search on the path).

The shell scripts are not listed: the Makefile finds them with git and awk.
Neither runs where the tests do (a copy of the tree with no .git), so this
test finds them with a Python copy of the same rule, checks that the
Makefile still holds that rule rather than a list, and holds the hook
pattern to every script the copy finds."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from conftest import REPO_ROOT

LISTS = ("PYTHON_SOURCES", "JS_SOURCES")
# The Makefile's awk rule, in Python: a shebang running sh, bash or dash, by
# any path, through env with or without options.
SHEBANG = re.compile(rb"#!\s*(?:\S*/)?(?:env\s+(?:-\S+\s+)*)?(?:ba|da)?sh(?:\s|$)")
# The scripts this repository is known to have, so a rule that quietly finds
# fewer fails here.
KNOWN_SHELL = (
    "host/workbench",
    "images/egress-proxy/bin/egress-proxy",
    "images/egress-proxy/bin/egress-reload",
    "images/l2-engine/containers/crun-without-masked-paths",
    "images/l2/bin/actionlint",
    "images/l2/bin/docker",
    "images/l2/engine-bin/podman",
    "images/podman-nested/bin/podman-health-ticker",
    "images/podman-nested/bin/podman-nested-entrypoint",
    "images/workbench/bin/airlock-worktree",
    "images/workbench/bin/claude",
    "images/workbench/bin/finish-image",
    "images/workbench/bin/l2",
    "images/workbench/bin/l2-hooks-install",
    "images/workbench/bin/l2-pre-commit",
    "images/workbench/bin/rec",
    "images/workbench/bin/status-line",
    "images/workbench/bin/workbench-init",
    "images/workbench/share/git-hook",
    "scripts/build-images.sh",
)


def makefile():
    return (REPO_ROOT / "Makefile").read_text()


def listed(*names):
    text = makefile()
    files = []
    for name in names:
        value = re.search(rf"^{name} :?= ?((?:.*\\\n)*.*)$", text, re.MULTILINE)
        files += value.group(1).replace("\\\n", " ").split()
    return files


def measured():
    return listed(*LISTS) + shell_scripts()


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
    """What SHELL_SCRIPTS finds: the rule, less SHELL_EXCLUDE, plus
    SHELL_EXTRA."""
    exclude = set(listed("SHELL_EXCLUDE"))
    found = [
        path
        for path in tree_files()
        if not path.startswith("tests/") and is_shell(path) and path not in exclude
    ]
    return sorted(set(found) | set(listed("SHELL_EXTRA")))


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


def test_shell_detection_by_shebang():
    for line in (
        b"#!/bin/sh",
        b"#!/bin/bash -eu",
        b"#!/usr/bin/env bash",
        b"#!/usr/bin/env -S bash -e",
        b"#! /bin/dash",
    ):
        assert SHEBANG.match(line), line
    for line in (b"#!/usr/bin/env python3", b"#!/bin/zsh", b"#!/bin/shell", b"# sh"):
        assert not SHEBANG.match(line), line


def test_the_known_shell_scripts_are_found():
    found = shell_scripts()
    assert sorted(set(KNOWN_SHELL) - set(found)) == []
    assert [path for path in found if path.startswith("tests/")] == []


def test_the_makefile_finds_the_shell_scripts():
    """A hand list creeping back would stop new scripts being measured."""
    text = makefile()
    assert re.search(
        r"^SHELL_SCRIPTS = \$\(call _shell_safe,\$\(sort \$\(filter-out \$\(SHELL_EXCLUDE\)",
        text,
        re.MULTILINE,
    )
    rule = text[text.index("SHELL_SCRIPTS = ") :].split("\n\n", 1)[0]
    for part in (
        "git ls-files -z --cached --others --exclude-standard",
        '[ -f "$$f" ]',
        "FILENAME ~ /\\.(sh|bash)$$/",
        "(env[[:space:]]+(-[^[:space:]]+[[:space:]]+)*)?(ba|da)?sh([[:space:]]|$$)",
        "grep -v '^tests/'",
        "$(SHELL_EXTRA))",
    ):
        assert part in rule, part


def sonar_shell_patterns():
    """sonar.lang.patterns.shell from sonar-project.properties, a comma
    separated list continued over lines with a trailing backslash."""
    text = (REPO_ROOT / "sonar-project.properties").read_text()
    value = re.search(
        r"^sonar\.lang\.patterns\.shell=((?:.*\\\n)*.*)$", text, re.MULTILINE
    )
    return [p.strip() for p in value.group(1).replace("\\\n", "").split(",")]


def sonar_glob(pattern):
    """Sonar's path pattern as a regex: `**/` is any number of folders, `**`
    anything, `*` anything within one folder."""
    out = ""
    for part in re.split(r"(\*\*/|\*\*|\*)", pattern):
        out += {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}.get(part, re.escape(part))
    return re.compile(out + r"\Z")


def test_sonar_glob():
    assert sonar_glob("**/*.sh").match("scripts/build-images.sh")
    assert sonar_glob("**/*.sh").match("top.sh")
    assert not sonar_glob("**/*.sh").match("scripts/build-images.py")
    assert sonar_glob("host/workbench").match("host/workbench")
    assert not sonar_glob("host/workbench").match("host/workbench.mk")


def test_sonar_analyzes_every_shell_script_as_shell():
    """Sonar picks a file's language by its extension alone, so a script
    without one that no pattern names is analyzed as nothing at all."""
    patterns = [sonar_glob(p) for p in sonar_shell_patterns()]
    assert len(patterns) > 2
    missed = [
        path
        for path in shell_scripts()
        if not any(pattern.match(path) for pattern in patterns)
    ]
    assert missed == []


def test_discovery_refuses_unsafe_script_names():
    """A script name reaches make's recipes as shell text, so discovery has to
    refuse any name outside [A-Za-z0-9._/+-] (a committed `x;id;#.sh` would
    otherwise run `id`)."""
    here = Path(__file__).resolve().parent
    while not (here / "Makefile").is_file():
        here = here.parent
    text = (here / "Makefile").read_text()
    assert "_shell_safe = $(if $(filter UNSAFE:," in text
    assert "$(call _shell_safe," in text
    assert '? substr(FILENAME, 3) : "UNSAFE:")' in text
    # awk reads an operand like `shell=tool.sh` as a variable assignment, so
    # every path reaches it as `./path` and is printed without that prefix.
    assert 'printf "./%s\\0"' in text
