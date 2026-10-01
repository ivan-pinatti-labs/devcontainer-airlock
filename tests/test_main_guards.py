"""The `if __name__ == "__main__":` guard is the one line .coveragerc
excludes, and coverage.py excludes the whole block under it. So that block
holds a single call into main() and nothing else: any logic there would go
unmeasured. This test fails when one of the Makefile's PYTHON_SOURCES puts
more under its guard."""

# cspell:words orelse
from __future__ import annotations

import ast
import re

from conftest import REPO_ROOT


def python_sources():
    makefile = (REPO_ROOT / "Makefile").read_text()
    block = re.search(r"^PYTHON_SOURCES := \\\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    return [line.strip(" \t\\") for line in block.group(1).splitlines()]


def guards(tree):
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            yield node


def test_every_python_source_is_listed():
    assert "scripts/kcov_to_sonar.py" in python_sources()
    assert len(python_sources()) > 5


def test_a_main_guard_holds_one_call_and_nothing_else():
    crowded = []
    for path in python_sources():
        tree = ast.parse((REPO_ROOT / path).read_text())
        for guard in guards(tree):
            one_call = (
                len(guard.body) == 1
                and isinstance(guard.body[0], (ast.Expr, ast.Raise))
                and not guard.orelse
            )
            if not one_call:
                crowded.append(path)
    assert crowded == []
